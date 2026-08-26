"""Cross-check the C fixture host stub against shared save-checksum vectors."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "tools" / "gba-playtest" / "tests"
sys.path.insert(0, str(TESTS))

import sram_fixture


class DebugSaveFixtureChecksumTests(unittest.TestCase):
    def test_c_host_stub_and_python_fixture_share_actual_checksum_vectors(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler (gcc/cc) available")

        vectors = json.loads(
            (
                TESTS / "fixtures" / "debug_save_checksum32_vectors.json"
            ).read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "checksum-vectors"
            result = subprocess.run(
                [
                    compiler,
                    "-w",
                    "-I",
                    str(ROOT / "include"),
                    "-I",
                    str(ROOT / "include" / "generated"),
                    str(TESTS / "c" / "debug_save_fixture_host_stubs.c"),
                    str(TESTS / "c" / "debug_save_fixture_checksum_driver.c"),
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            for vector in vectors:
                with self.subTest(bytes_hex=vector["bytes_hex"]):
                    expected = int(vector["checksum32"], 0)
                    data = bytes.fromhex(vector["bytes_hex"])
                    self.assertEqual(sram_fixture._save_checksum32(data), expected)
                    native = subprocess.run(
                        [str(executable), vector["bytes_hex"]],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    self.assertEqual(int(native.stdout.strip(), 16), expected)

            for invalid in ("aa", "aabbcc"):
                with self.subTest(invalid_bytes_hex=invalid):
                    native = subprocess.run(
                        [str(executable), invalid],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(native.returncode, 3)


if __name__ == "__main__":
    unittest.main()
