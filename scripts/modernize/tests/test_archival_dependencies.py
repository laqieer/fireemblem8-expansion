import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[3]
FRAGMENT = ROOT / "archival_dependencies.mk"
MAKE = Path("/usr/bin/make")
CPP = Path("/usr/bin/cpp")
CC = Path("/usr/bin/cc")
TMP_ROOT = ROOT / "build" / "test-tmp"


class ArchivalDependencyFixture:
    def __init__(self, temporary):
        self._temporary = temporary
        self.root = Path(temporary.name) / "fixture"
        self.root.mkdir(parents=True)
        self.depfile = self.root / "build" / "deps" / "legacy.d"
        self.object_file = self.root / "legacy.o"
        self.generated_header = self.root / "generated" / "fixture_generated.h"
        self.generated_input = self.root / "generated" / "input.txt"
        self.static_header = self.root / "include" / "fixture.h"
        self.cpp_log = self.root / "cpp.log"
        self.events_log = self.root / "events.log"
        self.goal_log = self.root / "goal.log"
        self.customalias_bin = self.root / "customalias.bin"
        self.link_check_bin = self.root / "generated-data-link-check.bin"
        self._write_fixture()

    def _write_fixture(self) -> None:
        shutil.copyfile(FRAGMENT, self.root / "archival_dependencies.mk")
        (self.root / "build" / "deps").mkdir(parents=True)
        (self.root / "generated").mkdir()
        (self.root / "include").mkdir()

        (self.root / "cpp_wrapper.py").write_text(
            dedent(
                f"""\
                #!/usr/bin/env python3
                import os
                import pathlib
                import subprocess
                import sys

                with pathlib.Path(os.environ["CPP_LOG"]).open("a", encoding="utf-8") as stream:
                    stream.write(" ".join(sys.argv[1:]) + "\\n")
                raise SystemExit(subprocess.call([{str(CPP)!r}, *sys.argv[1:]]))
                """
            ),
            encoding="utf-8",
        )

        self.static_header.write_text("#define FIXTURE_VALUE 1\n", encoding="utf-8")
        self.generated_input.write_text("2\n", encoding="utf-8")
        (self.root / "legacy.c").write_text(
            dedent(
                """\
                #include "include/fixture.h"
                #include "generated/fixture_generated.h"

                int legacy_fixture(void)
                {
                    return FIXTURE_VALUE + GENERATED_VALUE;
                }
                """
            ),
            encoding="utf-8",
        )

        (self.root / "Makefile").write_text(
            dedent(
                f"""\
                .DEFAULT_GOAL := all

                PYTHON3 := {sys.executable}
                CPP := env CPP_LOG=$(CURDIR)/cpp.log $(PYTHON3) $(CURDIR)/cpp_wrapper.py
                CPPFLAGS := -I. -Iinclude
                CC := {CC}
                CFLAGS := -I. -Iinclude -O0
                DEPS_DIR := build/deps
                CFILES := legacy.c
                CFILES_GENERATED :=
                MODERN_GOALS := expansion-modern-clean

                include archival_dependencies.mk

                .PHONY: all expansion-modern-clean \\
                    assets-validate assets-generate assets-check assets-test \\
                    generated-data-validate generated-data-generate generated-data-check generated-data-test \\
                    localization-validate localization-generate localization-check localization-test localization-budget \\
                    customalias generated-data-link-check

                all: expansion-modern-clean

                expansion-modern-clean \\
                assets-validate assets-generate assets-check assets-test \\
                generated-data-validate generated-data-generate generated-data-check generated-data-test \\
                localization-validate localization-generate localization-check localization-test localization-budget:
                	@printf '%s\\n' $@ >> $(CURDIR)/goal.log
                	@touch $(CURDIR)/$@.stamp

                customalias: legacy.o
                	@printf '%s\\n' $@ >> $(CURDIR)/goal.log
                	@cp legacy.o $(CURDIR)/customalias.bin

                generated-data-link-check: legacy.o
                	@printf '%s\\n' $@ >> $(CURDIR)/goal.log
                	@cp legacy.o $(CURDIR)/generated-data-link-check.bin

                generated/fixture_generated.h: generated/input.txt
                	@printf 'generated-header\\n' >> $(CURDIR)/events.log
                	@printf '#define GENERATED_VALUE %s\\n' "$$(cat $<)" > $@

                legacy.o: legacy.c $(DEPS_DIR)/legacy.d
                	@printf 'compile\\n' >> $(CURDIR)/events.log
                	$(CC) $(CFLAGS) -c $< -o $@
                """
            ),
            encoding="utf-8",
        )

    def make(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("MAKEFLAGS", None)
        env["TMPDIR"] = str(TMP_ROOT)
        return subprocess.run(
            [str(MAKE), "--no-print-directory", *arguments],
            cwd=self.root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )

    def log_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()


class ArchivalDependencyFragmentTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        missing = [tool for tool in (MAKE, CPP, CC) if not tool.exists()]
        if missing:
            raise unittest.SkipTest(f"missing required host tools: {missing}")

    def make_fixture(self) -> ArchivalDependencyFixture:
        temporary = tempfile.TemporaryDirectory(
            prefix="archival-dependencies-", dir=TMP_ROOT
        )
        self.addCleanup(temporary.cleanup)
        return ArchivalDependencyFixture(temporary)

    def test_safe_host_and_default_goals_skip_archival_dependency_generation(self):
        cases = [
            ((), "expansion-modern-clean.stamp"),
            (("all",), "expansion-modern-clean.stamp"),
            (("assets-validate",), "assets-validate.stamp"),
            (("assets-generate",), "assets-generate.stamp"),
            (("assets-check",), "assets-check.stamp"),
            (("assets-test",), "assets-test.stamp"),
            (("generated-data-validate",), "generated-data-validate.stamp"),
            (("generated-data-generate",), "generated-data-generate.stamp"),
            (("generated-data-check",), "generated-data-check.stamp"),
            (("generated-data-test",), "generated-data-test.stamp"),
            (("localization-validate",), "localization-validate.stamp"),
            (("localization-generate",), "localization-generate.stamp"),
            (("localization-check",), "localization-check.stamp"),
            (("localization-test",), "localization-test.stamp"),
            (("localization-budget",), "localization-budget.stamp"),
        ]

        for goals, stamp_name in cases:
            with self.subTest(goals=goals or ("<default>",)):
                fixture = self.make_fixture()
                result = fixture.make(*goals)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertTrue((fixture.root / stamp_name).exists(), result.stdout)
                self.assertFalse(fixture.depfile.exists(), result.stdout)
                self.assertFalse(fixture.object_file.exists(), result.stdout)
                self.assertEqual(fixture.log_lines(fixture.cpp_log), [])

    def test_explicit_legacy_and_unsafe_aliases_generate_and_load_dependencies(self):
        cases = [
            (("legacy.o",), "legacy.o"),
            (("customalias",), "customalias.bin"),
            (("generated-data-link-check",), "generated-data-link-check.bin"),
        ]

        for goals, artifact_name in cases:
            with self.subTest(goals=goals):
                fixture = self.make_fixture()
                result = fixture.make(*goals)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertTrue(fixture.depfile.exists(), result.stdout)
                self.assertTrue((fixture.root / artifact_name).exists(), result.stdout)
                self.assertTrue(fixture.generated_header.exists(), result.stdout)
                self.assertEqual(
                    fixture.log_lines(fixture.events_log),
                    ["generated-header", "compile"],
                )
                depfile = fixture.depfile.read_text(encoding="utf-8")
                self.assertIn("generated/fixture_generated.h", depfile)
                self.assertIn("include/fixture.h", depfile)
                self.assertGreaterEqual(len(fixture.log_lines(fixture.cpp_log)), 1)

    def test_mixed_safe_and_unsafe_goals_still_load_archival_dependencies(self):
        for goals in (
            ("expansion-modern-clean", "legacy.o"),
            ("legacy.o", "expansion-modern-clean"),
            ("assets-test", "legacy.o"),
            ("legacy.o", "assets-test"),
        ):
            with self.subTest(goals=goals):
                fixture = self.make_fixture()
                result = fixture.make(*goals)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertTrue(fixture.depfile.exists(), result.stdout)
                self.assertTrue(fixture.object_file.exists(), result.stdout)
                safe_goal = next(goal for goal in goals if goal != "legacy.o")
                self.assertTrue(
                    (fixture.root / (safe_goal + ".stamp")).exists(), result.stdout
                )
                self.assertEqual(
                    fixture.log_lines(fixture.events_log),
                    ["generated-header", "compile"],
                )

    def test_header_edit_rebuilds_existing_object(self):
        fixture = self.make_fixture()
        first = fixture.make("legacy.o")
        self.assertEqual(first.returncode, 0, first.stdout)
        original = fixture.object_file.read_bytes()

        fixture.static_header.write_text("#define FIXTURE_VALUE 9\n", encoding="utf-8")

        second = fixture.make("legacy.o")
        self.assertEqual(second.returncode, 0, second.stdout)
        rebuilt = fixture.object_file.read_bytes()
        self.assertNotEqual(original, rebuilt)
        self.assertEqual(
            fixture.log_lines(fixture.events_log),
            ["generated-header", "compile", "compile"],
        )

    def test_host_goals_do_not_refresh_existing_unrelated_dependency_files(self):
        fixture = self.make_fixture()
        first = fixture.make("legacy.o")
        self.assertEqual(first.returncode, 0, first.stdout)
        dependency = fixture.depfile.read_bytes(), fixture.depfile.stat().st_mtime_ns
        cpp_calls = fixture.log_lines(fixture.cpp_log)
        with (fixture.root / "legacy.c").open("a", encoding="utf-8") as source:
            source.write("\nint unrelated_new_declaration;\n")
        hosted = fixture.make("assets-test", "localization-test")
        self.assertEqual(hosted.returncode, 0, hosted.stdout)
        self.assertEqual(
            (fixture.depfile.read_bytes(), fixture.depfile.stat().st_mtime_ns),
            dependency,
        )
        self.assertEqual(fixture.log_lines(fixture.cpp_log), cpp_calls)

    def test_missing_depfile_and_generated_header_still_rebuild_existing_object(self):
        fixture = self.make_fixture()
        first = fixture.make("legacy.o")
        self.assertEqual(first.returncode, 0, first.stdout)
        original = fixture.object_file.read_bytes()

        fixture.generated_input.write_text("7\n", encoding="utf-8")
        fixture.depfile.unlink()
        fixture.generated_header.unlink()

        second = fixture.make("legacy.o")
        self.assertEqual(second.returncode, 0, second.stdout)
        rebuilt = fixture.object_file.read_bytes()
        self.assertNotEqual(original, rebuilt)
        self.assertTrue(fixture.depfile.exists())
        self.assertEqual(
            fixture.generated_header.read_text(encoding="utf-8"),
            "#define GENERATED_VALUE 7\n",
        )
        self.assertEqual(
            fixture.log_lines(fixture.events_log),
            ["generated-header", "compile", "generated-header", "compile"],
        )
