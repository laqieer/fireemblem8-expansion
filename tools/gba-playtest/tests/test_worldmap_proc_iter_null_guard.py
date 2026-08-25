"""
Regression tests for the world-map ``Proc_FindNext()`` iterator NULL guard.

``Proc_FindNext()`` (src/proc.c) returns NULL once a ``ProcFindIterator`` is
exhausted.  Several world-map helpers used to dereference that result *before*
testing it against NULL::

    do
    {
        proc = Proc_FindNext(&procIter);
        if (proc->index == index)   /* NULL dereference on the last iteration */
            return 1;
    } while (proc != NULL);

That is undefined behaviour, and it is not benign.  Because the dereference
happens first, an optimising compiler is entitled to conclude that ``proc``
can never be NULL and to delete the ``while (proc != NULL)`` test -- together
with the loop's only non-``return 1`` exit.  ``arm-none-eabi-gcc -O2`` (the
supported modern *release* configuration) does exactly that: the release build
of ``GmapRmBorder1Exists()`` lost its ``cmp r0, #0`` / ``bne`` exit and could
only ever ``return 1``.

``EventBA_WmRemoveHighlightNationPart2()`` (src/eventscr_gmap.c) does::

    if (!GmapRmBorder1Exists(a))
        return EVC_ADVANCE_YIELD;
    return EVC_STOP_YIELD;

so a ``GmapRmBorder1Exists()`` that can only return 1 makes the world-map
opening event yield forever -- the release ROM hard-locked on a static world
map during the opening tour and never reached a battle map.  The ``-Og`` debug
configuration and the archival agbcc build kept the test and therefore did not
lock, which is why this only ever reproduced on release builds.

These tests pin the fix from both ends: the source-level invariant (never use
the iterator result before the NULL check) and the codegen consequence (the
optimised release build must still be able to leave the loop).
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "src"
INCLUDE_DIRS = [REPO_ROOT / "include", REPO_ROOT / "include" / "generated"]

ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump")
WORLD_MAP_SOURCES = tuple(sorted(SRC_DIR.glob("worldmap*.c")))
_INSTRUCTION_RE = re.compile(
    r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2,4}\s+){1,2}"
    r"([a-z][a-z0-9.]*)\s*(.*)$"
)
_FUNCTION_RE = re.compile(r"^[0-9a-f]+ <([^+>]+)>:$", re.MULTILINE)


def _include_flags():
    flags = []
    for path in INCLUDE_DIRS:
        flags += ["-I", str(path)]
    return flags


def _instructions(text):
    return [
        (int(match.group(1), 16), match.group(2), match.group(3))
        for line in text.splitlines()
        if (match := _INSTRUCTION_RE.match(line))
    ]


def _functions(text):
    parts = _FUNCTION_RE.split(text)
    return zip(parts[1::2], map(_instructions, parts[2::2]))


def _branch_target(operands):
    match = re.search(r"\b([0-9a-f]+)\s+<", operands)
    return int(match.group(1), 16) if match else None


def _register_written(instruction, register):
    base, operands = instruction[1].split(".", 1)[0], instruction[2]
    destination = re.match(r"\s*(r[0-9]+)", operands)
    return (base not in {"cmp", "cmn", "tst", "push"}
            and not base.startswith(("b", "str"))
            and destination is not None and destination.group(1) == register)


def _bounded_cycle(cycle, instructions, indexes):
    component = set(cycle)
    operations = {address: instructions[indexes[address]][1].split(".", 1)[0] for address in cycle}
    if any(operations[address] in {"bl", "blx", "pop", "ldmia"}
           or "!" in instructions[indexes[address]][2] for address in cycle):
        return False
    for guard in cycle:
        index = indexes[guard]
        if operations[guard] not in {"beq", "bne"} or index == 0:
            continue
        comparison = instructions[index - 1]
        if comparison[0] not in component or comparison[1].split(".", 1)[0] != "cmp":
            continue
        target = _branch_target(instructions[index][2])
        fallthrough = instructions[index + 1][0] if index + 1 < len(instructions) else None
        equality, repeat = (target, fallthrough) if operations[guard] == "beq" else (fallthrough, target)
        if equality in component or repeat not in component:
            continue
        compared = re.findall(r"\br[0-9]+\b", comparison[2])
        for register in compared:
            update_re = re.compile(r"\s*%s,\s*#(?:0x)?0*1\s*" % register)
            updates = [address for address in cycle
                       if operations[address] in {"add", "adds", "sub", "subs"}
                       and update_re.fullmatch(instructions[indexes[address]][2])]
            if (len(updates) == 1 and cycle.index(updates[0]) < cycle.index(guard)
                    and all(address == updates[0] or not _register_written(
                        instructions[indexes[address]], register) for address in cycle)
                    and all(not _register_written(instructions[indexes[address]], bound)
                            for address in cycle for bound in compared if bound != register)):
                return True
    return False


def _all_paths_exit(instructions, start, iterator_calls):
    indexes = {instruction[0]: index for index, instruction in enumerate(instructions)}

    def walk(address, trail):
        if address in iterator_calls:
            return False
        if address in trail:
            return _bounded_cycle(trail[trail.index(address):], instructions, indexes)
        index = indexes.get(address)
        if index is None:
            return False
        _address, mnemonic, operands = instructions[index]
        base = mnemonic.split(".", 1)[0]
        if base == "bx" or (base == "pop" and "pc" in operands):
            return True
        fallthrough = instructions[index + 1][0] if index + 1 < len(instructions) else None
        target = _branch_target(operands)
        successors = ((fallthrough,) if base in {"bl", "blx"} else
                      (target,) if base == "b" else
                      (target, fallthrough) if base.startswith("b") else (fallthrough,))
        return None not in successors and all(
            walk(successor, trail + (address,)) for successor in successors)

    return walk(start, ())


def _iterator_null_paths(text):
    paths = {}
    for function, instructions in _functions(text):
        iterator_calls = {
            address
            for address, mnemonic, operands in instructions
            if mnemonic.split(".", 1)[0] in {"bl", "blx"} and "Proc_FindNext" in operands
        }
        for index, (address, _mnemonic, _operands) in enumerate(instructions):
            if address not in iterator_calls:
                continue
            site = (function, address)
            paths[site] = False
            null_registers = {"r0"}
            for candidate in range(index + 1, len(instructions)):
                _candidate_address, mnemonic, operands = instructions[candidate]
                base = mnemonic.split(".", 1)[0]
                move = re.fullmatch(r"\s*(r[0-9]+),\s*(r[0-9]+)\s*", operands)
                if base in {"mov", "movs"} and move and move.group(2) in null_registers:
                    null_registers.add(move.group(1))
                    continue
                comparison = re.fullmatch(r"\s*(r[0-9]+),\s*#0\s*", operands)
                if base == "cmp" and comparison and comparison.group(1) in null_registers:
                    branch_index = candidate + 1
                    if branch_index >= len(instructions):
                        break
                    _branch_address, branch_mnemonic, branch_operands = instructions[branch_index]
                    branch = branch_mnemonic.split(".", 1)[0]
                    target = _branch_target(branch_operands)
                    fallthrough = instructions[branch_index + 1][0] if branch_index + 1 < len(instructions) else None
                    null_path = target if branch == "beq" else fallthrough if branch == "bne" else None
                    if null_path is not None:
                        paths[site] = _all_paths_exit(instructions, null_path, iterator_calls)
                    break
                if base.startswith("b") or set(re.findall(r"\br[0-9]+\b", operands)) & null_registers:
                    break
    return paths


class ProcFindNextSourceGuardTests(unittest.TestCase):
    """Source invariant: the iterator result is NULL-checked before any use."""

    def test_named_helpers_contain_the_guard(self):
        if ARM_CC is None or ARM_OBJDUMP is None:
            raise unittest.SkipTest("arm-none-eabi-gcc/objdump not available")
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = ("if (proc == 0) break;", "if (proc == 0) continue;",
                        "if (proc == 0) for (;;) {}",
                        "if (proc == 0) while (*flag) *flag += 2;",
                        "Observe(proc); if (proc == 0) break;")
            listings = []
            for guard in fixtures:
                source = Path(tmp) / ("%d.c" % len(listings))
                obj = source.with_suffix(".o")
                source.write_text(
                    "extern void *Proc_FindNext(void *);\nextern void Observe(void *);\n"
                    "int Iterator(void *iter, volatile int *flag) { for (;;) { void *proc = Proc_FindNext(iter); "
                    f"{guard} Observe(iter); return 1; }} return 0; }}\n",
                    encoding="utf-8")
                compiled = subprocess.run(
                    [ARM_CC, "-mthumb", "-mcpu=arm7tdmi", "-mabi=aapcs",
                     "-std=gnu89", "-O2", "-c", "-w", str(source), "-o", str(obj)],
                    capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                listing = subprocess.run(
                    [ARM_OBJDUMP, "-dr", str(obj)],
                    capture_output=True, text=True)
                self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
                listings.append(_iterator_null_paths(listing.stdout))

        self.assertTrue(all(listings), "compiled fixture lost its Proc_FindNext call")
        self.assertEqual(
            [all(paths.values()) for paths in listings],
            [True, False, False, False, False],
        )


class ProcFindNextCodegenTests(unittest.TestCase):
    """Codegen consequence: -O2 must keep a reachable 'not found' exit."""

    def _compile_o2(self, work_dir, source):
        obj = Path(work_dir) / (source.stem + ".o")
        cmd = [ARM_CC, "-mthumb", "-mcpu=arm7tdmi", "-mabi=aapcs",
               "-std=gnu89", "-O2", "-c", "-w"] + _include_flags() + [
            str(source), "-o", str(obj)]
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT),
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "arm -O2 compile of %s failed:\n%s"
                         % (source.name, proc.stdout + proc.stderr))
        return obj

    def _disassemble(self, obj):
        proc = subprocess.run(
            [ARM_OBJDUMP, "-dr", str(obj)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "objdump failed:\n%s" % (proc.stdout + proc.stderr))
        return proc.stdout

    def test_release_build_can_still_leave_the_iterator_loop(self):
        if ARM_CC is None or ARM_OBJDUMP is None:
            raise unittest.SkipTest(
                "arm-none-eabi-gcc/objdump not available")
        with tempfile.TemporaryDirectory() as tmp:
            null_exit_paths = []
            for source in WORLD_MAP_SOURCES:
                text = self._disassemble(self._compile_o2(tmp, source))
                paths = _iterator_null_paths(text)
                null_exit_paths.extend(
                    (source.name, function, address, exits)
                    for (function, address), exits in paths.items()
                )
        self.assertTrue(null_exit_paths, "no world-map Proc_FindNext relocations found")
        self.assertTrue(all(exits for _source, _function, _address, exits in null_exit_paths),
                        "exhausted Proc_FindNext path lacks a function exit: %r" % (null_exit_paths,))


if __name__ == "__main__":
    unittest.main()
