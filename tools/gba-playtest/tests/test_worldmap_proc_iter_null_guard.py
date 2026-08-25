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

These tests derive the optimized ARM release control flow for every world-map
``Proc_FindNext`` call. A null branch following each relocation proves every
iterator result can reach its exhausted-list behavior without relying on
helper names or source order.
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
HOST_CC = shutil.which("cc") or shutil.which("gcc")
WORLD_MAP_SOURCES = tuple(sorted(SRC_DIR.glob("worldmap*.c")))
_INSTRUCTION_RE = re.compile(
    r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2,4}\s+){1,2}"
    r"([a-z][a-z0-9.]*)\s*(.*)$"
)
_FUNCTION_RE = re.compile(r"^([0-9a-f]+) <[^+>]+>:$")


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


def _branch_target(operands):
    match = re.search(r"\b([0-9a-f]+)\s+<", operands)
    return int(match.group(1), 16) if match else None


def _iterator_null_paths(text):
    instructions = _instructions(text)
    iterator_calls = {
        address
        for address, mnemonic, operands in instructions
        if mnemonic.split(".", 1)[0] == "bl" and "Proc_FindNext" in operands
    }
    paths = []
    for index, (address, _mnemonic, _operands) in enumerate(instructions):
        if address not in iterator_calls:
            continue
        null_registers = {"r0"}
        branch_index = None
        for candidate in range(index + 1, min(index + 6, len(instructions) - 1)):
            _candidate_address, mnemonic, operands = instructions[candidate]
            base = mnemonic.split(".", 1)[0]
            move = re.fullmatch(r"\s*(r[0-9]+),\s*(r[0-9]+)\s*", operands)
            if base in {"mov", "movs"} and move and move.group(2) in null_registers and move.group(1) not in null_registers:
                null_registers.add(move.group(1))
                continue
            comparison = re.fullmatch(r"\s*(r[0-9]+),\s*#0\s*", operands)
            if base == "cmp" and comparison and comparison.group(1) in null_registers:
                branch_index = candidate + 1
                break
            if base in {"bl", "blx"} or set(re.findall(r"\br[0-9]+\b", operands)) & null_registers:
                break
        if branch_index is None or branch_index >= len(instructions):
            continue
        _branch_address, branch_mnemonic, branch_operands = instructions[branch_index]
        branch = branch_mnemonic.split(".", 1)[0]
        target = _branch_target(branch_operands)
        fallthrough = instructions[branch_index + 1][0] if branch_index + 1 < len(instructions) else None
        if branch == "beq":
            null_path = target
        elif branch == "bne":
            null_path = fallthrough
        else:
            continue
        paths.append((address, null_path is not None))
    return iterator_calls, paths


class ProcFindNextSourceGuardTests(unittest.TestCase):
    """Stable audit ID owns the compiled exhausted-iterator mutation control."""

    def test_named_helpers_contain_the_guard(self):
        if ARM_CC is None or ARM_OBJDUMP is None:
            raise unittest.SkipTest("arm-none-eabi-gcc/objdump not available")
        with tempfile.TemporaryDirectory() as tmp:
            listings = []
            for before, statement in (("proc = Proc_FindNext(iter);", "break;"), ("", "continue;"),
                                      ("", "if (*flag) break; for (;;) {}"),
                                      ("Observe(proc);", "break;"),
                                      ("", "while (*flag) *flag += 2;")):
                source = Path(tmp) / f"{len(listings)}.c"
                obj = source.with_suffix(".o")
                source.write_text(
                    "extern void *Proc_FindNext(void *);\nextern void Observe(void *);\n"
                    "int Iterator(void *iter, volatile int *flag) { for (;;) { void *proc = Proc_FindNext(iter); "
                    f"{before} if (proc == 0) {statement} Observe(iter); return 1; }} return 0; }}\n",
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

        break_calls, break_paths = listings[0]
        continue_calls, continue_paths = listings[1]
        loop_calls, loop_paths = listings[2]
        precheck_calls, precheck_paths = listings[3]
        parity_calls, parity_paths = listings[4]
        self.assertTrue(break_calls)
        self.assertEqual(len(break_calls), 2)
        self.assertEqual([exits for _address, exits in break_paths], [True])
        self.assertTrue(continue_calls)
        self.assertEqual([exits for _address, exits in continue_paths], [True])
        self.assertTrue(loop_calls)
        self.assertEqual([exits for _address, exits in loop_paths], [True])
        self.assertTrue(precheck_calls)
        self.assertEqual(precheck_paths, [])
        self.assertTrue(parity_calls)
        self.assertEqual([exits for _address, exits in parity_paths], [True])


class ProcFindNextCodegenTests(unittest.TestCase):
    """Codegen consequence: -O2 must keep a reachable 'not found' exit."""

    def _exhausted_harness(self, work):
        source, output = Path(work) / "iterator.c", Path(work) / "iterator"
        source.write_text(
            "static int calls; static void *next(void){++calls;return 0;}\n"
            "static int choose(int *v,int n){int i,j;for(i=0;i<3;i++){for(j=0;j<n&&i!=v[j];j++);if(j==n)return i;}return -1;}\n"
            "static int run(int spin){for(;;)if(!next()){if(spin&&calls<4)continue;return spin?-2:0;}}\n"
            "int main(void){int v[2]={0,2};return run(0)||run(1)!=-2||choose(v,2)!=1;}\n",
            encoding="utf-8")
        self.assertEqual(subprocess.run([HOST_CC, str(source), "-o", str(output)]).returncode, 0)
        self.assertEqual(subprocess.run([str(output)]).returncode, 0)

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
        if ARM_CC is None or ARM_OBJDUMP is None or HOST_CC is None:
            raise unittest.SkipTest("required host or ARM compiler is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            self._exhausted_harness(tmp)
            calls = []
            null_exit_paths = []
            for source in WORLD_MAP_SOURCES:
                text = self._disassemble(self._compile_o2(tmp, source))
                iterator_calls, paths = _iterator_null_paths(text)
                calls.extend((source.name, address) for address in iterator_calls)
                null_exit_paths.extend((source.name, address, exits) for address, exits in paths)
        self.assertTrue(calls, "no world-map Proc_FindNext relocations found")
        self.assertEqual(
            len(null_exit_paths),
            len(calls),
            "each optimized Proc_FindNext call must expose a null path: "
            "calls=%r paths=%r" % (calls, null_exit_paths),
        )
        self.assertTrue(all(exits for _source, _address, exits in null_exit_paths),
                        "exhausted Proc_FindNext path lacks a function exit: %r" % (null_exit_paths,))


if __name__ == "__main__":
    unittest.main()
