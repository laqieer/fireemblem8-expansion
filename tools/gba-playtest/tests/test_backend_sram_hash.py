"""Executable regression coverage for backend.c's SRAM hash source."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PLAYTEST = ROOT / "tools" / "gba-playtest"
sys.path.insert(0, str(PLAYTEST))

import gba_playtest  # noqa: E402


class BackendSramHashTests(unittest.TestCase):
    def test_clone_failure_reads_live_bus_and_clone_path_stays_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "backend-sram-hash"
            command = gba_playtest._compiler_command(
                PLAYTEST / "tests" / "c" / "backend_sram_hash_driver.c",
                executable,
            )
            command.extend(("-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections"))
            result = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            result = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
