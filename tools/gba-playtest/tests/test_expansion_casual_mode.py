"""Issue #34 executable host tests for the optional casual defeat policy."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INCLUDES = ["-I", str(ROOT / "include"), "-I", str(ROOT / "include" / "generated")]
SOURCE = ROOT / "src" / "expansion_casual_mode.c"
DRIVER = Path(__file__).resolve().parent / "c" / "expansion_casual_mode_driver.c"
CC = shutil.which("gcc") or shutil.which("cc")


class CasualModeHostTests(unittest.TestCase):
    """Execute the real policy with both its enabled and disabled compile paths."""

    def setUp(self):
        if CC is None:
            self.skipTest("no host C compiler")

    def _build_and_run(self, defines, executable_name):
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            work = Path(temporary)
            objects = []
            for source in (SOURCE, DRIVER):
                object_path = work / (source.stem + ".o")
                completed = subprocess.run(
                    [
                        CC,
                        "-std=gnu89",
                        "-Werror=declaration-after-statement",
                        "-Werror=implicit-function-declaration",
                        "-Werror=implicit-int",
                        *INCLUDES,
                        *defines,
                        "-c",
                        str(source),
                        "-o",
                        str(object_path),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"compile failed for {source.name}:\n"
                    + completed.stdout
                    + completed.stderr,
                )
                objects.append(object_path)
            executable = work / executable_name
            completed = subprocess.run(
                [CC, *(str(path) for path in objects), "-o", str(executable)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                "link failed:\n" + completed.stdout + completed.stderr,
            )
            return subprocess.run([str(executable)], capture_output=True, text=True)

    def test_enabled_policy_marks_and_restores_only_eligible_defeats(self):
        completed = self._build_and_run(
            ["-DFE8_EXPANSION_CASUAL_MODE=1"], "casual-enabled"
        )
        self.assertEqual(
            completed.returncode,
            0,
            "enabled casual policy failed:\n" + completed.stdout + completed.stderr,
        )
        self.assertIn("CASUAL_MODE_HOST_TEST: PASS", completed.stdout)

    def test_disabled_policy_preserves_permadeath_and_marker_state(self):
        completed = self._build_and_run([], "casual-disabled")
        self.assertEqual(
            completed.returncode,
            0,
            "disabled casual policy failed:\n" + completed.stdout + completed.stderr,
        )
        self.assertIn("CASUAL_MODE_HOST_TEST: PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
