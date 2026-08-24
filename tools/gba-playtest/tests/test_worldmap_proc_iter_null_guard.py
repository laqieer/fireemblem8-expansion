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

These tests derive every `Proc_FindNext` relocation from the optimized
world-map objects and require a one-to-one immediate NULL branch for each
call. That object/control-flow evidence proves the optimized release build
can still leave every iterator loop.
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

def _include_flags():
    flags = []
    for path in INCLUDE_DIRS:
        flags += ["-I", str(path)]
    return flags


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

    def test_every_release_iterator_call_has_one_immediate_null_branch(self):
        if ARM_CC is None or ARM_OBJDUMP is None:
            raise unittest.SkipTest(
                "arm-none-eabi-gcc/objdump not available")
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            guards = []
            for source in WORLD_MAP_SOURCES:
                text = self._disassemble(self._compile_o2(tmp, source))
                calls.extend(
                    (source.name, match.start())
                    for match in re.finditer(r"\bbl\s+0\s+<Proc_FindNext>", text)
                )
                guards.extend(
                    (source.name, match.start())
                    for match in re.finditer(
                        r"\bbl\s+0\s+<Proc_FindNext>\s*\n"
                        r"\s*[0-9a-f]+:\s+R_ARM_[A-Z_]+\s+Proc_FindNext\s*\n"
                        r"\s*[0-9a-f]+:\s+[0-9a-f ]+\s+cmp\s+r0,\s*#0\s*\n"
                        r"\s*[0-9a-f]+:\s+[0-9a-f ]+\s+b(?:eq|ne)\S*",
                        text,
                        flags=re.DOTALL,
                    )
                )
        self.assertTrue(calls, "no world-map Proc_FindNext relocations found")
        self.assertEqual(
            len(guards),
            len(calls),
            "every Proc_FindNext relocation must immediately compare and branch on NULL: "
            "calls=%r guards=%r" % (calls, guards),
        )


if __name__ == "__main__":
    unittest.main()
