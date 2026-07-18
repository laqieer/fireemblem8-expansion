"""Tests for the expansion-modern-elf Make target wiring.

Covers legacy-assembly filtering, FE6 SIO preflight, generator
re-invocation, output isolation, and non-interaction with fast/all
targets.

Tests using print-* and make -n run unconditionally.  Tests invoking
real cross tools skip only when the exact required executable is absent.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class ModernElfTargetTests(unittest.TestCase):

    def make(self, *args, cwd=None):
        return subprocess.run(
            ["make", "--no-print-directory", *args],
            cwd=cwd or ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def tool_overrides(self):
        prefix = os.environ.get("PREFIX", "arm-none-eabi-")
        cc = os.environ.get("MODERN_CC", "")
        if not cc:
            root = os.environ.get("MODERN_TOOLCHAIN_ROOT", "")
            if root:
                cc = str(Path(root) / "bin" / f"{prefix}gcc")
            else:
                cc = shutil.which(f"{prefix}gcc") or ""
        objdump = os.environ.get("MODERN_OBJDUMP", "")
        if not objdump:
            objdump = shutil.which(f"{prefix}objdump") or ""
        ld = os.environ.get("MODERN_LD", "")
        if not ld:
            ld = shutil.which(f"{prefix}ld") or ""
        if not cc or not objdump or not ld:
            return None
        overrides = [
            f"MODERN_CC={cc}",
            f"MODERN_OBJDUMP={objdump}",
            f"MODERN_LD={ld}",
        ]
        for var in ("MODERN_BINUTILS_DIR", "MODERN_NEWLIB_INCLUDE"):
            val = os.environ.get(var)
            if val:
                overrides.append(f"{var}={val}")
        return overrides

    # -- Fix 1: legacy-asm filter -------------------------------------------

    def test_legacy_asm_filter_excludes_all_replaced(self):
        """MODERN_ELF_REPLACED_ASM must list all six; none in legacy."""
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")
        result = self.make(
            "print-MODERN_ELF_LEGACY_ASM",
            "print-MODERN_ELF_REPLACED_ASM",
            *overrides,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        legacy_line = ""
        replaced_line = ""
        for line in result.stdout.strip().splitlines():
            if line.startswith("MODERN_ELF_LEGACY_ASM "):
                legacy_line = line
            elif line.startswith("MODERN_ELF_REPLACED_ASM "):
                replaced_line = line
        legacy = set(
            legacy_line.split("[")[1].rstrip("]").split()
        ) if "[" in legacy_line else set()
        replaced = set(
            replaced_line.split("[")[1].rstrip("]").split()
        ) if "[" in replaced_line else set()
        expected = {
            "src/libagbsyscall.o", "asm/arm.o", "asm/arm_call.o",
            "src/rom_header.o", "src/crt0.o", "src/m4a_1.o",
        }
        self.assertTrue(
            expected.issubset(replaced),
            f"replaced missing: {expected - replaced}",
        )
        self.assertEqual(
            legacy & replaced, set(),
            f"overlap: {legacy & replaced}",
        )

    # -- Fix 3: FE6 preflight (lightweight, no deps) ------------------------

    def test_fe6sio_check_fails_without_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.make(
                "expansion-modern-fe6sio-check",
                f"MODERN_ELF_FE6SIO={tmp}/missing.o",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pre-existing object missing", result.stdout)

    def test_fe6sio_check_succeeds_with_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "stub.o").write_bytes(b"\x00")
            result = self.make(
                "expansion-modern-fe6sio-check",
                f"MODERN_ELF_FE6SIO={tmp}/stub.o",
            )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_fe6sio_check_no_toolchain_invocation(self):
        result = self.make("-n", "expansion-modern-fe6sio-check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("gcc", result.stdout.lower())

    # -- Fix 4: cohort/all non-interaction ----------------------------------

    def test_cohort_dry_run_no_elf(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")
        result = self.make("-n", "expansion-modern-cohort", *overrides)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("fireemblem8.elf", result.stdout)
        self.assertNotIn("prepare_modern_link", result.stdout)

    def test_all_dry_run_no_elf(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")
        result = self.make("-n", "expansion-modern-all", *overrides)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("fireemblem8.elf", result.stdout)
        self.assertNotIn("prepare_modern_link", result.stdout)


if __name__ == "__main__":
    unittest.main()
