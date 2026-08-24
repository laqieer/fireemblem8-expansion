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
ITERATOR_HELPERS = {
    "worldmap_rm.c": (
        "StartGmapRmBorder1",
        "EndGmapRmBorder1",
        "GmapRmBorder1Exists",
        "RequestGmapRmBorder1Remove",
        "EndWmPlaceDotByIndex",
        "IsWmPlaceDotActiveAtIndex",
        "SetWmPlaceDotFlagForIndex",
    ),
    "worldmap_automu.c": (
        "EndGmAutoMuFor",
        "IsGmAutoMuActiveFor",
    ),
}

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

    def test_every_release_iterator_checks_null_before_dereference(self):
        if ARM_CC is None or ARM_OBJDUMP is None:
            raise unittest.SkipTest(
                "arm-none-eabi-gcc/objdump not available")
        with tempfile.TemporaryDirectory() as tmp:
            for source_name, functions in ITERATOR_HELPERS.items():
                obj = self._compile_o2(tmp, SRC_DIR / source_name)
                for function in functions:
                    with self.subTest(source=source_name, function=function):
                        text = self._disassemble(obj, function)
                        calls = re.findall(
                            r"bl\s+0\s+<Proc_FindNext>\s*\n"
                            r"(?:\s*.*R_ARM.*\n)?"
                            r"\s*[0-9a-f]+:\s+[0-9a-f ]+\s+cmp\s+r0,\s*#0\s*\n"
                            r"\s*[0-9a-f]+:\s+[0-9a-f ]+\s+b(?:eq|ne)\S*",
                            text,
                            flags=re.DOTALL,
                        )
                        self.assertTrue(
                            calls,
                            "%s must branch on Proc_FindNext()'s NULL result "
                            "before reading the returned proc" % function,
                        )


if __name__ == "__main__":
    unittest.main()
