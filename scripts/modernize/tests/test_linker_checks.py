"""Tests for modern expansion-linker verification target wiring."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODERN_MK = ROOT / "modern.mk"


class LinkerCheckTargetTests(unittest.TestCase):
    def make(self, *args):
        overrides = []
        toolchain_root = ROOT / "build" / "toolchain-root" / "usr"
        newlib = toolchain_root / "lib" / "arm-none-eabi" / "newlib"
        if (toolchain_root / "bin" / "arm-none-eabi-gcc").is_file():
            overrides.extend([
                f"MODERN_TOOLCHAIN_ROOT={toolchain_root}",
                f"MODERN_NEWLIB_LIB={newlib}",
            ])
        return subprocess.run(
            ["make", "--no-print-directory", *args, *overrides],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_all_linker_checks_are_modern_goals(self):
        text = MODERN_MK.read_text(encoding="utf-8")
        goals = text.split("MODERN_GOALS :=", 1)[1].split("ifneq", 1)[0]
        for target in (
            "expansion-modern-title-check",
            "expansion-modern-budget-check",
            "expansion-modern-relocs",
            "expansion-modern-overlay-audit",
            "expansion-modern-shifted-check",
            "expansion-modern-linker-check",
        ):
            self.assertIn(target, goals)

    def test_linker_check_dry_run_wires_every_gate(self):
        result = self.make("-n", "expansion-modern-linker-check")
        self.assertEqual(result.returncode, 0, result.stdout[-1000:])
        for expected in (
            "budget.py",
            "modern_emit_relocs.sh",
            "overlay_audit.py",
            "boot.json",
            "title-progression.json",
            "modern_shifted_boot.sh",
            "scan_build_addrs.py",
            "scan_raw_casts.sh",
        ):
            self.assertIn(expected, result.stdout)

    def test_nonzero_shift_is_forwarded_to_linker(self):
        result = self.make(
            "-n",
            "expansion-modern-elf",
            "MODERN_TEXT_SHIFT=0x40000",
        )
        self.assertEqual(result.returncode, 0, result.stdout[-1000:])
        self.assertIn("--defsym=__text_shift=0x40000", result.stdout)
        self.assertIn("text_shift=0x40000", result.stdout)

    def test_title_fingerprint_is_configuration_specific(self):
        debug = self.make(
            "print-MODERN_TITLE_FINGERPRINT", "MODERN_CONFIG=debug"
        )
        release = self.make(
            "print-MODERN_TITLE_FINGERPRINT", "MODERN_CONFIG=release"
        )
        self.assertIn("title-progression-modern-debug.json", debug.stdout)
        self.assertIn("title-progression-modern-release.json", release.stdout)


if __name__ == "__main__":
    unittest.main()
