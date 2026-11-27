"""Executable regression coverage for backend.c's SRAM, region and pixel probes."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PLAYTEST = ROOT / "tools" / "gba-playtest"
sys.path.insert(0, str(PLAYTEST))

import gba_playtest  # noqa: E402


class BackendProbeTests(unittest.TestCase):
    def test_sram_region_and_pixel_probe_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "backend-probes"
            command = gba_playtest._compiler_command(
                PLAYTEST / "tests" / "c" / "backend_probes_driver.c",
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
