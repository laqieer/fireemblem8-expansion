"""Tests for the modern shifted-boot verification pipeline."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "shiftcheck" / "modern_shifted_boot.sh"
MODERN_MAP = ROOT / "build" / "expansion-modern" / "debug" / "aapcs" / "fireemblem8.map"
OBJECTS_LST = ROOT / "build" / "expansion-modern" / "debug" / "aapcs" / "link" / "objects.lst"
BANIM_SYM = ROOT / "banim" / "data_banim.o.sym.o"
LDSCRIPT = ROOT / "linker" / "expansion.ld"


def _has_modern_artifacts():
    return all(p.exists() for p in [MODERN_MAP, OBJECTS_LST, BANIM_SYM, LDSCRIPT])


def _has_gcc():
    try:
        r = subprocess.run(
            ["arm-none-eabi-gcc", "--version"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Try alternate location
        gcc = Path.home() / "arm-gnu-toolchain-13.3.rel1-x86_64-arm-none-eabi" / "bin" / "arm-none-eabi-gcc"
        return gcc.exists()


def _has_libmgba():
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "gba-playtest" / "gba_playtest.py"), "backend-check"],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class TestShiftedBoot(unittest.TestCase):

    def test_script_is_executable(self):
        self.assertTrue(SCRIPT.exists())
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_script_fails_without_artifacts(self):
        """Script exits 2 (setup error) when objects list is missing."""
        env = os.environ.copy()
        env["SHIFTCHECK_OBJECTS_LST"] = "/nonexistent/objects.lst"
        r = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("error", r.stderr)

    @unittest.skipUnless(
        _has_modern_artifacts() and _has_gcc() and _has_libmgba(),
        "requires modern build artifacts, arm-none-eabi-gcc, and libmgba"
    )
    def test_shifted_boot_passes(self):
        """Full pipeline: shifted ROM passes boot behavior verification."""
        env = os.environ.copy()
        # Ensure gcc is findable
        gcc_path = Path.home() / "arm-gnu-toolchain-13.3.rel1-x86_64-arm-none-eabi" / "bin"
        if gcc_path.exists():
            env["PATH"] = str(gcc_path) + ":" + env.get("PATH", "")
        env["SHIFTCHECK_OUTDIR"] = "/tmp/test_shifted_boot"

        r = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        self.assertEqual(r.returncode, 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}")
        self.assertIn("PASS", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
