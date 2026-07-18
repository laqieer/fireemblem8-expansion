"""Tests for the expansion-modern-elf Make target wiring.

Covers legacy-assembly filtering, preflight behaviour, generator
re-invocation, and output isolation.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class ModernElfTargetTests(unittest.TestCase):

    def make(self, *args, cwd=None, env=None):
        return subprocess.run(
            ["make", "--no-print-directory", *args],
            cwd=cwd or ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            env=env,
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

    def test_legacy_asm_filter_excludes_replaced_objects(self):
        """MODERN_ELF_LEGACY_ASM must not contain any modern-replaced asm."""
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")

        result = self.make(
            "print-MODERN_ELF_LEGACY_ASM",
            "print-MODERN_ELF_REPLACED_ASM",
            *overrides,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        lines = result.stdout.strip().splitlines()
        legacy_line = ""
        replaced_line = ""
        for line in lines:
            if line.startswith("MODERN_ELF_LEGACY_ASM "):
                legacy_line = line
            elif line.startswith("MODERN_ELF_REPLACED_ASM "):
                replaced_line = line
        legacy_objs = set(legacy_line.split("[")[1].rstrip("]").split()) if "[" in legacy_line else set()
        replaced_objs = set(replaced_line.split("[")[1].rstrip("]").split()) if "[" in replaced_line else set()

        expected_replaced = {
            "src/libagbsyscall.o", "asm/arm.o", "asm/arm_call.o",
            "src/rom_header.o", "src/crt0.o", "src/m4a_1.o",
        }
        self.assertTrue(
            expected_replaced.issubset(replaced_objs),
            f"replaced missing expected: {expected_replaced - replaced_objs}",
        )
        overlap = legacy_objs & replaced_objs
        self.assertEqual(
            overlap, set(),
            f"legacy asm contains replaced objects: {overlap}",
        )

    # -- Fix 2: preflight FE6 SIO ------------------------------------------

    def test_fe6sio_preflight_fails_without_object(self):
        """Missing prebuilt object must fail preflight."""
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Create a minimal manifest so the preflight recipe runs.
            link_dir = tmp_path / "link"
            link_dir.mkdir()
            manifest = link_dir / "manifest.txt"
            manifest.write_text("src/stub.o\n", encoding="utf-8")
            result = self.make(
                "expansion-modern-link-prepare",
                f"MODERN_ELF_MANIFEST={manifest}",
                f"MODERN_ELF_LINK_DIR={link_dir}",
                f"MODERN_ELF_PREBUILT={tmp}/missing.o",
                f"MODERN_OUTPUT_DIR={tmp_path}",
                *overrides,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pre-existing object missing", result.stdout)

    # -- Fix 4: cohort/all unaffected ---------------------------------------

    def test_cohort_target_unaffected(self):
        """expansion-modern-cohort dry-run must not mention ELF targets."""
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")

        result = self.make(
            "-n", "expansion-modern-cohort", *overrides,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("fireemblem8.elf", result.stdout)
        self.assertNotIn("prepare_modern_link", result.stdout)

    def test_all_target_unaffected(self):
        """expansion-modern-all dry-run must not mention ELF/link targets."""
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")

        result = self.make(
            "-n", "expansion-modern-all", *overrides,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("fireemblem8.elf", result.stdout)
        self.assertNotIn("prepare_modern_link", result.stdout)


if __name__ == "__main__":
    unittest.main()
