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

    def test_declarations_require_only_the_actual_message_header(self):
        with self._tmpdir() as tmp:
            root = Path(tmp)
            source = root / "msg.h"
            source.write_text("#define MSG_COUNT 0x0003\n", encoding="ascii")
            output = root / "declarations.h"
            command = [
                sys.executable, "-B", "-m", "scripts.localization.game_catalog", "declare",
                "--target-header", str(source), "--enabled-locales", "fr",
                "--output", str(output),
            ]
            result = subprocess.run(
                command, cwd=root, env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["declarations.h", "msg.h"])
            compiler = os.environ.get("CC", "cc")
            probe = root / "probe.c"
            probe.write_text(
                '#include "declarations.h"\n'
                "typedef char HeaderMatches[(GAME_LOCALIZATION_TARGET_COUNT == 3"
                " && GAME_LOCALIZATION_FR_ENABLED == 1 && GAME_LOCALIZATION_JA_ENABLED == 0)"
                " ? 1 : -1];\n",
                encoding="ascii",
            )
            arguments = [
                compiler, "-std=c89", "-pedantic-errors", "-I", str(TEST_DIR / "host_include"),
                "-I", str(root), "-fsyntax-only", str(probe),
            ]
            checked = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            source.write_text("#define MSG_COUNT 0x0004\n", encoding="ascii")
            subprocess.run(
                command, cwd=root, env={**os.environ, "PYTHONPATH": str(ROOT)}, check=True,
                capture_output=True, text=True, timeout=30,
            )
            checked = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(checked.returncode, 0)

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
