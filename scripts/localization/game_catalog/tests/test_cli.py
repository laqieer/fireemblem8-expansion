import ctypes
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_native_context_refuses_ambiguous_source_paths_before_replay(self):
        with self._tmpdir() as tmp:
            root = Path(tmp)
            source = root / "plan.mk"
            prerequisite = root / "source-input"
            executed = root / "source-regenerated"
            prerequisite.write_text("native input\n", encoding="ascii")
            source.write_text(
                f"{source}: {prerequisite}\n"
                f"\t@touch {executed}\n\t@touch {source}\n"
                ".PHONY: native-plan\nnative-plan:\n"
                "\tcc -c sample.c -o out/sample.o\n"
                "\t: fe8-selected-text-objects out out/sample.o\n"
                "\t: fe8-selected-text-data \n"
                "\t: fe8-selected-text-assembly \n"
                "\t: fe8-selected-text-linker-default ld\n",
                encoding="ascii",
            )
            for name in ("left.mk", "right.mk", "left.mk right.mk"):
                (root / name).write_text("# existing native source\n", encoding="ascii")
            inputs = root / "makefiles.list"
            output = root / "context.json"
            for raw, valid in ((str(source), True), ("left.mk right.mk", False)):
                with self.subTest(valid=valid):
                    inputs.write_text(raw + "\n", encoding="ascii")
                    before = output.read_bytes() if output.exists() else None
                    result = subprocess.run(
                        [
                            sys.executable, "-B", "-m", "scripts.localization.game_catalog",
                            "describe-make-context", "--output", str(output),
                            "--makefile-list-file", str(inputs), "--", "make",
                            "--no-print-directory", "-n", "-B", "-f", str(source), "native-plan",
                        ],
                        cwd=root, env={**os.environ, "PYTHONPATH": str(ROOT)},
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertFalse(executed.exists(), "capture executed a source recipe")
                    if valid:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(len(json.loads(output.read_text())["units"]), 1)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("ambiguous", result.stderr)
                        self.assertEqual(output.read_bytes(), before)

    def test_native_context_selects_and_validates_actual_linker_scopes(self):
        with self._tmpdir() as tmp:
            root = Path(tmp)
            makefile = root / "scopes.mk"
            output = root / "context.json"
            for scopes, expected in (
                ((), "global-linker"),
                (("selected-linker", "selected-linker"), "selected-linker"),
                (("selected-linker", "other-linker"), None),
            ):
                with self.subTest(scoped_linkers=scopes):
                    makefile.write_text(
                        ".PHONY: native-plan\nnative-plan:\n"
                        "\tcc -c sample.c -o out/sample.o\n"
                        "\t: fe8-selected-text-objects out out/sample.o\n"
                        "\t: fe8-selected-text-data \n"
                        "\t: fe8-selected-text-assembly \n"
                        "\t: fe8-selected-text-linker-default global-linker\n"
                        + "".join(f"\t: fe8-selected-text-linker {linker}\n" for linker in scopes),
                        encoding="ascii",
                    )
                    before = output.read_bytes() if output.exists() else None
                    result = run_cli([
                        "describe-make-context", "--output", str(output),
                        "--compiler", "cc", "--preprocessor", "pp",
                        "--", "make", "--no-print-directory", "-n", "-f", str(makefile), "native-plan",
                    ])
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("conflicting linker scopes", result.stdout)
                        self.assertEqual(output.read_bytes(), before)
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout)
                        self.assertEqual(json.loads(output.read_text())["linker"], expected)

    def test_native_font_dependency_migration_preserves_real_make_edges(self):
        with self._tmpdir() as tmp:
            root = Path(tmp)
            object_path = root / "font output.o"
            source = root / "real header # value.txt"
            extra = root / 'quoted"header.txt'
            source.write_text("first\n", encoding="ascii")
            extra.write_text("extra\n", encoding="ascii")
            legacy = root / "localized_game_text_data.h"

            def escaped(path):
                return str(path).replace("\\", "\\\\").replace("$", "$$").replace(
                    " ", "\\ "
                ).replace("#", "\\#").replace(":", "\\:")

            for suffix in (".d", ".headers.d"):
                object_path.with_suffix(suffix).write_text(
                    f"{escaped(object_path)}: {escaped(source)} \\\n"
                    f" {escaped(legacy)} {escaped(extra)}\n"
                    f"{escaped(legacy)}:\n"
                    f"{escaped(source)}:\n",
                    encoding="utf-8",
                )
            makefile = root / "dependencies.mk"
            makefile.write_text(
                f"include {escaped(object_path.with_suffix('.d'))}\n"
                f"include {escaped(object_path.with_suffix('.headers.d'))}\n"
                f"{escaped(object_path)}:\n"
                '\t@$(if $(filter %localized_game_text_data.h,$^),$(error obsolete measured-header edge),true)\n'
                '\tcat "$<" > "$@"\n',
                encoding="utf-8",
            )
            command = ["make", "--no-print-directory", "-f", str(makefile), str(object_path)]
            original = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(original.returncode, 0)
            migrated = run_cli([
                "prune-native-font-dependencies", "--objects", str(object_path),
            ])
            self.assertEqual(migrated.returncode, 0, migrated.stdout)
            for value in ("first\n", "second\n"):
                source.write_text(value, encoding="ascii")
                built = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                self.assertEqual(object_path.read_text(encoding="ascii"), value)

    def test_native_context_replays_macos_invoker_without_procfs(self):
        from scripts.localization.game_catalog import cli

        with self._tmpdir() as tmp:
            root = Path(tmp)
            primary = root / "first makefile.mk"
            secondary = root / "second makefile.mk"
            primary.write_text(
                "WIDTH := 9\n"
                ".PHONY: native-plan\n"
                "native-plan:\n"
                "\tcc -DWIDTH=$(WIDTH) -c sample.c -o out/sample.o\n"
                "\t: fe8-selected-text-objects out out/sample.o\n"
                "\t: fe8-selected-text-data \n"
                "\t: fe8-selected-text-assembly \n"
                "\t: fe8-selected-text-linker ld\n",
                encoding="ascii",
            )
            secondary.write_text("WIDTH := 11\n", encoding="ascii")
            output = root / "context.json"
            arguments = [
                "gmake", "--file", str(primary), "--makefile=" + str(secondary), "native-plan",
            ]
            wire = (
                struct.pack("=i", len(arguments)) + b"/opt/homebrew/bin/gmake\0\0\0"
                + b"\0".join(os.fsencode(value) for value in arguments)
                + b"\0UNRELATED_ENV=not-an-argument\0"
            )

            def native_sysctl(names, count, value, size, new_value, new_size):
                self.assertIsNone(new_value)
                self.assertEqual(new_size, 0)
                lengths = ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))
                selector = tuple(names[index] for index in range(count))
                if selector == (1, 8):
                    ctypes.cast(value, ctypes.POINTER(ctypes.c_int))[0] = len(wire) + 32
                    lengths[0] = ctypes.sizeof(ctypes.c_int)
                else:
                    self.assertEqual(selector, (1, 49, 424242))
                    self.assertGreaterEqual(lengths[0], len(wire))
                    ctypes.memmove(value, wire, len(wire))
                    lengths[0] = len(wire)
                return 0

            with mock.patch.object(sys, "platform", "darwin"), mock.patch(
                "ctypes.CDLL", return_value=mock.Mock(sysctl=native_sysctl),
            ):
                command = [
                    "describe-make-context", "--output", str(output),
                    "--compiler", "cc", "--preprocessor", "pp",
                    "--invoker-pid", "424242", "--", "make", "-n",
                    "-f", str(primary), "native-plan",
                ]
                self.assertEqual(cli.main(command), 0)
                captured = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(len(captured["units"]), 1)
                self.assertIn("-DWIDTH=11", captured["units"][0]["compiler_argv"])
                self.assertNotIn("UNRELATED_ENV", output.read_text(encoding="utf-8"))
                for malformed in (b"\x01", struct.pack("=i", 99) + b"make\0\0make\0"):
                    with self.subTest(malformed_native_arguments=malformed):
                        wire = malformed
                        output.unlink(missing_ok=True)
                        self.assertEqual(cli.main(command), 1)
                        self.assertFalse(output.exists())

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
