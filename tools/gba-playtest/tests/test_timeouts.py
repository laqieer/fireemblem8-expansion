import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gba_playtest


class TimeoutTests(unittest.TestCase):
    def test_pkg_config_timeout_is_actionable(self):
        with mock.patch.object(
            gba_playtest.shutil, "which", return_value="/present"
        ), mock.patch.object(
            gba_playtest.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("pkg-config", 10),
        ):
            with self.assertRaisesRegex(gba_playtest.PlaytestError, "pkg-config timed out"):
                gba_playtest._compiler_command(Path("backend.c"), Path("backend"))

    def test_compiler_timeout_is_actionable(self):
        with mock.patch.object(
            gba_playtest, "_compiler_command", return_value=["cc"]
        ), mock.patch.object(
            gba_playtest.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("cc", 60),
        ):
            with self.assertRaisesRegex(
                gba_playtest.PlaytestError, "compilation timed out after 60s"
            ):
                gba_playtest.build_backend(Path("backend"))

    def test_backend_timeout_names_frame_and_bound(self):
        scenario = gba_playtest.parse_scenario_data(
            {
                "schema_version": 1,
                "name": "timeout",
                "frames": [],
                "checkpoints": [
                    {
                        "name": "late",
                        "frame": 600,
                        "framebuffer": True,
                        "probes": [],
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            rom = Path(temporary) / "fixture.gba"
            data = bytearray(0xB0)
            data[0xA0:0xAC] = b"TIMEOUT".ljust(12, b"\0")
            data[0xAC:0xB0] = b"TMO0"
            rom.write_bytes(data)
            with mock.patch.object(
                gba_playtest, "build_backend"
            ), mock.patch.object(
                gba_playtest.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("backend", 30),
            ):
                with self.assertRaisesRegex(
                    gba_playtest.PlaytestError,
                    r"timed out after 30s while running through frame 600",
                ):
                    gba_playtest.capture(rom, scenario)


if __name__ == "__main__":
    unittest.main()
