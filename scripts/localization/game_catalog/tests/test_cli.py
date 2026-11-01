import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TEST_DIR = Path(__file__).resolve().parent


def run_cli(args):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "scripts.localization.game_catalog", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=env,
    )


class CliTests(unittest.TestCase):
    def _tmpdir(self):
        return tempfile.TemporaryDirectory(dir=TEST_DIR)

    def test_describe_compile_preserves_argv_without_loading_generated_inputs(self):
        with self._tmpdir() as tmp:
            output = Path(tmp) / "unit.json"
            command = ["arm-none-eabi-gcc", "-DMODERN", "-Iinclude with spaces", "-Os"]
            result = run_cli([
                "describe-compile", "--output", str(output),
                "--object", "build/profile/source.o",
                "--logical-source", "src/source.c",
                "--selected-source", "examples/project/source.c",
                "--cc-input", "build/profile/source.pre.c",
                "--preprocessor-arg", "tools/preproc/preproc",
                "--preprocessor-arg=-Iasset includes",
                "--", *command,
            ])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(json.loads(output.read_text()), {
                "schema_version": 1,
                "kind": "fe8-selected-text-compile",
                "object": "build/profile/source.o",
                "logical_source": "src/source.c",
                "selected_source": "examples/project/source.c",
                "compiler_input": "build/profile/source.pre.c",
                "compiler_argv": command,
                "preprocessor_argv": ["tools/preproc/preproc", "-Iasset includes"],
            })

    def test_describe_compile_refuses_missing_compiler_without_replacing_output(self):
        with self._tmpdir() as tmp:
            output = Path(tmp) / "unit.json"
            output.write_bytes(b"existing descriptor\n")
            result = run_cli([
                "describe-compile", "--output", str(output),
                "--object", "build/profile/source.o",
                "--logical-source", "src/source.c",
                "--selected-source", "examples/project/source.c",
                "--cc-input", "build/profile/source.pre.c", "--",
            ])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), b"existing descriptor\n")

    def test_bad_compile_context_reports_an_error_without_publishing(self):
        with self._tmpdir() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            sentinel = output / "game_localization_report.json"
            sentinel.write_bytes(b"previous output\n")
            for name, payload in (("bad-json", b"{bad"), ("bad-utf8", b"\xff")):
                with self.subTest(name=name):
                    context = root / (name + ".json")
                    context.write_bytes(payload)
                    result = run_cli([
                        "generate", "--out-dir", str(output),
                        "--compile-context", str(context),
                    ])
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("error:", result.stdout)
                    self.assertIn(str(context), result.stdout)
                    self.assertNotIn("Traceback", result.stdout)
                    self.assertEqual(sentinel.read_bytes(), b"previous output\n")
                    self.assertEqual(list(output.iterdir()), [sentinel])

    def test_validate_reports_committed_counts(self):
        result = run_cli(["validate"])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("authored=329", result.stdout)
        self.assertIn("fallback=0", result.stdout)
        self.assertIn("unresolved=0", result.stdout)
        self.assertIn("en.present=3414", result.stdout)

    def test_generate_writes_outputs(self):
        with self._tmpdir() as tmp:
            result = run_cli(["generate", "--out-dir", tmp])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((Path(tmp) / "localized_game_text_data.h").is_file())
            self.assertTrue((Path(tmp) / "game_localization_catalog.h").is_file())
            self.assertTrue((Path(tmp) / "game_localization_catalog.c").is_file())
            self.assertTrue((Path(tmp) / "game_localization_report.json").is_file())
            self.assertTrue((Path(tmp) / "game_localization_budget.json").is_file())
            self.assertTrue(
                (Path(tmp) / "game_localization_latin_span_audit.json").is_file()
            )

    def test_budget_prints_json(self):
        with self._tmpdir() as tmp:
            result = run_cli(["budget", "--out-dir", tmp])
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)
            self.assertEqual(data["mapping_source_counts"]["authored"], 329)
            self.assertEqual(data["mapping_source_counts"]["english_fallback"], 0)
            self.assertEqual(data["shared_english"]["present_count"], 3414)

    def test_validate_rejects_duplicate_authored_mapping(self):
        result = run_cli([
            "validate",
            "--authored",
            "ja=a.json",
            "--authored",
            "ja=b.json",
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate locale mapping", result.stdout)


if __name__ == "__main__":
    unittest.main()
