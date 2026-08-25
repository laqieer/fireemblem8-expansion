"""Enable-state and hook-compilation coverage for chapter objectives."""

import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.generated_data.chapterobjectives import enabled


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "scripts" / "generated_data" / "tests" / "fixtures" / "chapterobjectives"
SCRATCH_ROOT = ROOT / "build" / "test-chapterobjectives-enable"
NM = shutil.which("arm-none-eabi-nm")
CC = shutil.which("arm-none-eabi-gcc")


class ChapterObjectivesEnableTests(unittest.TestCase):
    def _enabled_output(self, source):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = enabled.main(["--source", str(source)])
        return code, stderr.getvalue()

    def test_loader_enable_state_handles_file_directory_empty_and_malformed_inputs(self):
        self.assertTrue(enabled.is_enabled(FIXTURES / "valid.json"))
        self.assertTrue(enabled.is_enabled(FIXTURES / "source_identity_objectives"))
        self.assertFalse(enabled.is_enabled(ROOT / "src" / "data" / "chapter_objectives.json"))

        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as temporary:
            temporary_path = Path(temporary)
            malformed_file = temporary_path / "bad_objectives.json"
            malformed_file.write_text("{", encoding="utf-8")
            malformed_directory = temporary_path / "malformed"
            malformed_directory.mkdir()
            (malformed_directory / "bad_objectives.json").write_text("{", encoding="utf-8")

            for source in (malformed_file, malformed_directory):
                code, stderr = self._enabled_output(source)
                self.assertEqual(code, 1)
                self.assertIn("error:", stderr)
                make = subprocess.run(
                    [
                        "make",
                        "--no-print-directory",
                        "-n",
                        "expansion-modern-cohort",
                        "GENERATED_DATA_CHAPTEROBJECTIVES_SOURCE={}".format(source),
                    ],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertNotEqual(make.returncode, 0)
                self.assertIn("unable to resolve chapter objective enablement", make.stdout)

    def _compile_hook_objects(self, name, source):
        build_root = SCRATCH_ROOT / name
        shutil.rmtree(build_root, ignore_errors=True)
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                str(build_root / "debug" / "aapcs" / "src" / "bm.o"),
                str(build_root / "debug" / "aapcs" / "src" / "bmio.o"),
                "MODERN_BUILD_ROOT={}".format(build_root),
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "GENERATED_DATA_CHAPTEROBJECTIVES_SOURCE={}".format(source),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        symbols = subprocess.run(
            [
                NM,
                str(build_root / "debug" / "aapcs" / "src" / "bm.o"),
                str(build_root / "debug" / "aapcs" / "src" / "bmio.o"),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(symbols.returncode, 0, symbols.stdout)
        return symbols.stdout

    @unittest.skipIf(CC is None or NM is None, "ARM compiler/binutils unavailable")
    def test_directory_source_compiles_hooks_and_empty_source_omits_them(self):
        enabled_symbols = self._compile_hook_objects(
            "directory", FIXTURES / "source_identity_objectives"
        )
        self.assertIn("ExpansionChapterObjectives_RefreshTelemetry", enabled_symbols)

        disabled_symbols = self._compile_hook_objects(
            "empty", ROOT / "src" / "data" / "chapter_objectives.json"
        )
        self.assertNotIn("ExpansionChapterObjectives_RefreshTelemetry", disabled_symbols)


if __name__ == "__main__":
    unittest.main()
