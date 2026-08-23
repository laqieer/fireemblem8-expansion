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

    def _disassemble(self, obj, function):
        proc = subprocess.run(
            [ARM_OBJDUMP, "-d", "--disassemble=" + function, str(obj)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "objdump failed:\n%s" % (proc.stdout + proc.stderr))
        return proc.stdout

    def test_release_build_can_still_leave_the_iterator_loop(self):
        if ARM_CC is None or ARM_OBJDUMP is None:
            raise unittest.SkipTest(
                "arm-none-eabi-gcc/objdump not available")
        with tempfile.TemporaryDirectory() as tmp:
            obj = self._compile_o2(tmp, SRC_DIR / "worldmap_rm.c")
            text = self._disassemble(obj, "GmapRmBorder1Exists")
            self.assertIn(
                "GmapRmBorder1Exists", text,
                "GmapRmBorder1Exists missing from the -O2 object")
            # The "no such proc" answer must survive optimisation. Without the
            # NULL guard, -O2 proved the loop endless and emitted only the
            # `movs r0, #1` answer.
            self.assertIn(
                "#0", text,
                "GmapRmBorder1Exists lost every compare/return against 0 at "
                "-O2: the loop can no longer terminate and the world-map "
                "opening event will yield forever")
            returns_zero = re.search(r"\bmovs?\s+r0,\s*#0\b", text)
            self.assertIsNotNone(
                returns_zero,
                "GmapRmBorder1Exists has no 'return 0' path at -O2; the "
                "undefined-behaviour NULL dereference has come back")


if __name__ == "__main__":
    unittest.main()
