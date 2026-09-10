import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from scripts.modernize.tests.make_database import (
    make_database_rule,
    make_database_variable,
)


ROOT = Path(__file__).resolve().parents[3]
FRAGMENT = ROOT / "archival_dependencies.mk"
MAKE = Path("/usr/bin/make")
CPP = Path("/usr/bin/cpp")
CC = Path("/usr/bin/cc")
TMP_ROOT = ROOT / "build" / "test-tmp"


def recursive_make_targets(rule: str) -> tuple[str, ...]:
    targets = []
    for line in rule.splitlines():
        stripped = line.strip()
        if not stripped.startswith("+$(MAKE) "):
            continue
        for word in stripped.split()[1:]:
            if word.startswith("expansion-modern-"):
                targets.append(word)
                continue
            if "=" in word:
                break
    return tuple(dict.fromkeys(targets))


class ArchivalDependencyFixture:
    def __init__(self, temporary, host_goals=(), make_prelude=""):
        self._temporary = temporary
        self.root = Path(temporary.name) / "fixture"
        self.root.mkdir(parents=True)
        self.depfile = self.root / "build" / "deps" / "legacy.d"
        self.object_file = self.root / "legacy.o"
        self.generated_header = self.root / "generated" / "fixture_generated.h"
        self.generated_input = self.root / "generated" / "input.txt"
        self.static_header = self.root / "include" / "fixture.h"
        self.scan_log = self.root / "scan.log"
        self.scan_asm_source = self.root / "asm" / "scan_fixture.s"
        self.scan_data_source = self.root / "src" / "data" / "scan_fixture.c"
        self.cpp_log = self.root / "cpp.log"
        self.events_log = self.root / "events.log"
        self.goal_log = self.root / "goal.log"
        self.customalias_bin = self.root / "customalias.bin"
        self.link_check_bin = self.root / "generated-data-link-check.bin"
        self._write_fixture(host_goals, make_prelude)

    def _write_fixture(self, host_goals, make_prelude) -> None:
        extra_host_goals = " ".join(host_goals)
        indented_prelude = make_prelude.replace("\n", "\n                ")
        shutil.copyfile(FRAGMENT, self.root / "archival_dependencies.mk")
        (self.root / "build" / "deps").mkdir(parents=True)
        (self.root / "asm").mkdir()
        (self.root / "generated").mkdir()
        (self.root / "include").mkdir()
        (self.root / "src" / "data").mkdir(parents=True)

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

        (self.root / "scaninc_wrapper.py").write_text(
            dedent(
                """\
                #!/usr/bin/env python3
                import pathlib
                import sys

                root = pathlib.Path(__file__).resolve().parent
                with (root / "scan.log").open("a", encoding="utf-8") as stream:
                    stream.write(" ".join(sys.argv[1:]) + "\\n")
                sys.stdout.write("include/fixture.h")
                """
            ),
            encoding="utf-8",
        )

        self.static_header.write_text("#define FIXTURE_VALUE 1\n", encoding="utf-8")
        self.generated_input.write_text("2\n", encoding="utf-8")
        self.scan_asm_source.write_text(".byte 1\n", encoding="utf-8")
        self.scan_data_source.write_text(
            '#include "include/fixture.h"\nint scan_fixture(void) { return FIXTURE_VALUE; }\n',
            encoding="utf-8",
        )
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
                SCANINC := $(PYTHON3) $(CURDIR)/scaninc_wrapper.py
                CFILES := legacy.c
                CFILES_GENERATED :=
                FIXTURE_SCAN_ASM_OBJECTS := asm/scan_fixture.o
                FIXTURE_SCAN_DATA_OBJECTS := src/data/scan_fixture.o
                MODERN_GOALS := expansion-modern-clean
                {indented_prelude}

                include archival_dependencies.mk

                .PHONY: all expansion-modern-clean \\
                    assets-validate assets-generate assets-check assets-test \\
                    generated-data-validate generated-data-generate generated-data-check generated-data-test \\
                    localization-validate localization-generate localization-check localization-test localization-budget \\
                    game-localization-width-check game-localization-text-edits-generate \\
                    game-localization-text-edits-check game-localization-eu-check \\
                    customalias generated-data-link-check {extra_host_goals}

                all: expansion-modern-clean

                expansion-modern-clean \\
                assets-validate assets-generate assets-check assets-test \\
                generated-data-validate generated-data-generate generated-data-check generated-data-test \\
                localization-validate localization-generate localization-check localization-test localization-budget \\
                game-localization-width-check game-localization-text-edits-generate \\
                game-localization-text-edits-check game-localization-eu-check {extra_host_goals}:
                \t@printf '%s\\n' $@ >> $(CURDIR)/goal.log
                \t@touch $(CURDIR)/$@.stamp

                customalias: legacy.o
                \t@printf '%s\\n' $@ >> $(CURDIR)/goal.log
                \t@cp legacy.o $(CURDIR)/customalias.bin

                generated-data-link-check: legacy.o
                \t@printf '%s\\n' $@ >> $(CURDIR)/goal.log
                \t@cp legacy.o $(CURDIR)/generated-data-link-check.bin

                ifeq ($(NODEP),1)
                asm/%.o: data_dep :=
                else ifeq ($(ARCHIVAL_SCANINC_NODEP),1)
                asm/%.o: data_dep :=
                else
                asm/%.o: data_dep = $(shell $(SCANINC) -I include -I "" $*.s)
                endif

                ifeq ($(NODEP),1)
                src/data/%.o: data_dep :=
                else ifeq ($(ARCHIVAL_SCANINC_NODEP),1)
                src/data/%.o: data_dep :=
                else
                src/data/%.o: data_dep = $(shell $(SCANINC) -I include -I "" $(if $(wildcard $*.c),$*.c,$*.s))
                endif

                .SECONDEXPANSION:
                $(FIXTURE_SCAN_ASM_OBJECTS): %.o: %.s $$(data_dep)
                \t@printf 'scan-assemble\\n' >> $(CURDIR)/events.log
                \t@:

                $(FIXTURE_SCAN_DATA_OBJECTS): %.o: %.c $(PREPROC) $$(data_dep)
                \t@printf 'scan-compile\\n' >> $(CURDIR)/events.log
                \t@:

                generated/fixture_generated.h: generated/input.txt
                \t@printf 'generated-header\\n' >> $(CURDIR)/events.log
                \t@printf '#define GENERATED_VALUE %s\\n' "$$(cat $<)" > $@

                legacy.o: legacy.c $(DEPS_DIR)/legacy.d
                \t@printf 'compile\\n' >> $(CURDIR)/events.log
                \t$(CC) $(CFLAGS) -c $< -o $@
                """
            ),
            encoding="utf-8",
        )

    def make(self, *arguments: str, cwd=None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("MAKEFLAGS", None)
        env["TMPDIR"] = str(TMP_ROOT)
        return subprocess.run(
            [str(MAKE), "--no-print-directory", *arguments],
            cwd=self.root if cwd is None else cwd,
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

    def make_fixture(self, **options) -> ArchivalDependencyFixture:
        temporary = tempfile.TemporaryDirectory(
            prefix="archival-dependencies-", dir=TMP_ROOT
        )
        self.addCleanup(temporary.cleanup)
        return ArchivalDependencyFixture(temporary, **options)

    def root_make_database(self) -> str:
        result = self.make_fixture().make("-rR", "-np", "expansion-modern-clean", cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        return result.stdout

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
            (("game-localization-width-check",), "game-localization-width-check.stamp"),
            (("game-localization-text-edits-generate",), "game-localization-text-edits-generate.stamp"),
            (("game-localization-text-edits-check",), "game-localization-text-edits-check.stamp"),
            (("game-localization-eu-check",), "game-localization-eu-check.stamp"),
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
        scan_calls = fixture.log_lines(fixture.scan_log)
        with (fixture.root / "legacy.c").open("a", encoding="utf-8") as source:
            source.write("\nint unrelated_new_declaration;\n")
        hosted = fixture.make("assets-test", "localization-test")
        self.assertEqual(hosted.returncode, 0, hosted.stdout)
        self.assertEqual(
            (fixture.depfile.read_bytes(), fixture.depfile.stat().st_mtime_ns),
            dependency,
        )
        self.assertEqual(fixture.log_lines(fixture.cpp_log), cpp_calls)
        self.assertEqual(fixture.log_lines(fixture.scan_log), scan_calls)

    def test_safe_host_and_default_goals_skip_unrelated_scaninc_demand(self):
        for goals, stamp_name in (
            ((), "expansion-modern-clean.stamp"),
            (("assets-test",), "assets-test.stamp"),
            (("localization-test",), "localization-test.stamp"),
        ):
            with self.subTest(goals=goals or ("<default>",)):
                fixture = self.make_fixture()
                result = fixture.make(*goals)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertTrue((fixture.root / stamp_name).exists(), result.stdout)
                self.assertEqual(fixture.log_lines(fixture.scan_log), [])
                self.assertFalse(fixture.depfile.exists(), result.stdout)

    def test_clearing_scaninc_suppression_reintroduces_ordinary_make_demand(self):
        for goals, stamp_name in (
            (("assets-test", "NODEP=", "ARCHIVAL_SCANINC_NODEP="), "assets-test.stamp"),
            (("localization-test", "NODEP=", "ARCHIVAL_SCANINC_NODEP="), "localization-test.stamp"),
            (("all", "NODEP=", "ARCHIVAL_SCANINC_NODEP="), "expansion-modern-clean.stamp"),
        ):
            with self.subTest(goals=goals):
                fixture = self.make_fixture()
                result = fixture.make(*goals)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertTrue((fixture.root / stamp_name).exists(), result.stdout)
                scan_calls = fixture.log_lines(fixture.scan_log)
                self.assertGreaterEqual(len(scan_calls), 2)
                self.assertIn('-I include -I  asm/scan_fixture.s', scan_calls)
                self.assertIn('-I include -I  src/data/scan_fixture.c', scan_calls)
                self.assertFalse(fixture.depfile.exists(), result.stdout)

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

    def test_recursive_localization_host_children_skip_archival_dependencies(self):
        children = (
            "game-localization-width-check",
            "game-localization-text-edits-check",
        )
        fixture = self.make_fixture(make_prelude="PYTHON3 := true")
        database = self.root_make_database()
        rule = make_database_rule(database, "game-localization-test")
        self.assertIsNotNone(rule)
        with (fixture.root / "Makefile").open("a", encoding="utf-8") as makefile:
            makefile.write("\n.PHONY: game-localization-test\n" + rule + "\n")

        result = fixture.make("game-localization-test")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(fixture.log_lines(fixture.goal_log), list(children))
        self.assertEqual(fixture.log_lines(fixture.cpp_log), [], result.stdout)
        self.assertFalse(fixture.depfile.exists())

    def test_recursive_modern_helper_targets_are_closed_over_modern_goal_registry(self):
        database = self.root_make_database()
        headroom_rule = make_database_rule(
            database, "expansion-modern-localization-profile-headroom-check"
        )
        self.assertIsNotNone(headroom_rule)
        recursive_targets = recursive_make_targets(headroom_rule)
        self.assertEqual(
            recursive_targets,
            (
                "expansion-modern-localization-profile-en-ja",
                "expansion-modern-localization-profile-en-zh-hans",
                "expansion-modern-localization-profile-en-ja-zh-hans",
                "expansion-modern-localization-profile-en-ja-zh-hans-qps",
            ),
        )
        modern_goals = make_database_variable(database, "MODERN_GOALS")
        self.assertIsNotNone(modern_goals)
        modern_goal_set = set(re.findall(r"expansion-modern-[A-Za-z0-9_-]+", modern_goals))
        self.assertTrue(set(recursive_targets) <= modern_goal_set)
        scan_safe = make_database_variable(database, "MAKECMDGOALS_NOSCANINC")
        self.assertIsNotNone(scan_safe)
        self.assertTrue(set(recursive_targets) <= set(scan_safe.split()))

    def test_recursive_modern_helper_chain_skips_archival_scaninc(self):
        database = self.root_make_database()
        headroom_target = "expansion-modern-localization-profile-headroom-check"
        helper_targets = recursive_make_targets(
            make_database_rule(database, headroom_target)
        )
        fixture = self.make_fixture(
            make_prelude=(
                "MODERN_GOALS += expansion-modern-rom "
                "expansion-modern-localization-profile-headroom-check "
                + " ".join(helper_targets)
                + "\n"
                "MODERN_CONFIG ?= debug\n"
                "MODERN_ABI ?= aapcs\n"
                "PYTHON := /bin/true\n"
                "MODERN_READELF := /usr/bin/true\n"
                "MODERN_LOCALE_PROFILE_EN_JA_ROOT := build/en-ja\n"
                "MODERN_LOCALE_PROFILE_EN_ZH_HANS_ROOT := build/en-zh-hans\n"
                "MODERN_LOCALE_PROFILE_EN_JA_ZH_HANS_ROOT := build/en-ja-zh-hans\n"
                "MODERN_LOCALE_PROFILE_EN_JA_ZH_HANS_QPS_ROOT := build/en-ja-zh-hans-qps\n"
                "MODERN_LOCALE_PROFILE_EN_JA_OUTPUT_DIR := $(MODERN_LOCALE_PROFILE_EN_JA_ROOT)/$(MODERN_CONFIG)/$(MODERN_ABI)\n"
                "MODERN_LOCALE_PROFILE_EN_ZH_HANS_OUTPUT_DIR := $(MODERN_LOCALE_PROFILE_EN_ZH_HANS_ROOT)/$(MODERN_CONFIG)/$(MODERN_ABI)\n"
                "MODERN_LOCALE_PROFILE_EN_JA_ZH_HANS_OUTPUT_DIR := $(MODERN_LOCALE_PROFILE_EN_JA_ZH_HANS_ROOT)/$(MODERN_CONFIG)/$(MODERN_ABI)\n"
                "MODERN_LOCALE_PROFILE_EN_JA_ZH_HANS_QPS_OUTPUT_DIR := $(MODERN_LOCALE_PROFILE_EN_JA_ZH_HANS_QPS_ROOT)/$(MODERN_CONFIG)/$(MODERN_ABI)\n"
            )
        )
        with (fixture.root / "Makefile").open("a", encoding="utf-8") as makefile:
            makefile.write(
                "\n.PHONY: expansion-modern-rom "
                + " ".join((headroom_target, *helper_targets))
                + "\n"
            )
            for target in (*helper_targets, headroom_target):
                rule = make_database_rule(database, target)
                self.assertIsNotNone(rule, target)
                makefile.write(rule + "\n\n")
            makefile.write(
                "expansion-modern-rom:\n"
                "\t@printf 'rom %s\\n' \"$(MODERN_BUILD_ROOT)\" >> $(CURDIR)/goal.log\n"
                "\t@mkdir -p \"$(MODERN_BUILD_ROOT)/$(MODERN_CONFIG)/$(MODERN_ABI)\"\n"
                "\t@touch \"$(MODERN_BUILD_ROOT)/$(MODERN_CONFIG)/$(MODERN_ABI)/fireemblem8.elf\"\n"
                "\t@touch \"$(MODERN_BUILD_ROOT)/$(MODERN_CONFIG)/$(MODERN_ABI)/fireemblem8.map\"\n"
            )

        result = fixture.make(headroom_target)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(fixture.log_lines(fixture.scan_log), [])
        self.assertFalse(fixture.depfile.exists(), result.stdout)
        self.assertEqual(
            fixture.log_lines(fixture.goal_log),
            [
                "rom build/en-ja",
                "rom build/en-zh-hans",
                "rom build/en-ja-zh-hans",
                "rom build/en-ja-zh-hans-qps",
            ],
        )

        fixture.scan_log.unlink(missing_ok=True)
        control = fixture.make(
            headroom_target,
            "NODEP=",
            "ARCHIVAL_SCANINC_NODEP=",
            "MAKECMDGOALS_NOSCANINC=expansion-modern-localization-profile-headroom-check",
        )
        self.assertEqual(control.returncode, 0, control.stdout)
        scan_calls = fixture.log_lines(fixture.scan_log)
        self.assertIn('-I include -I  asm/scan_fixture.s', scan_calls)
        self.assertIn('-I include -I  src/data/scan_fixture.c', scan_calls)

    def test_mixed_modern_and_non_c_goals_preserve_incremental_scaninc_rebuilds(self):
        for tool in (Path("/usr/bin/as"), Path("/usr/bin/g++")):
            if not tool.exists():
                self.skipTest(f"missing required native tool: {tool}")
        fixture = self.make_fixture(
            host_goals=("native-midi.o",),
            make_prelude=(
                "C_OBJECTS := legacy.o\n"
                "DATA_SRC_C_OBJECTS := native-data.o\n"
                "ASM_OBJECTS := asm/native.o\n"
                "MID_OBJECTS := native-midi.o\n"
                "MODERN_GOALS += expansion-modern-clean\n"
            ),
        )
        scanner = subprocess.run(
            ["/usr/bin/g++", "-std=c++11", "-O2",
             *map(str, sorted((ROOT / "tools/scaninc").glob("*.cpp"))),
             "-o", str(fixture.root / "scaninc")],
            text=True, capture_output=True, check=False, timeout=60,
        )
        self.assertEqual(scanner.returncode, 0, scanner.stdout + scanner.stderr)
        (fixture.root / "asm").mkdir(exist_ok=True)
        (fixture.root / "asm/native.s").write_text(
            '.section .rodata\n.globl native_fixture\nnative_fixture:\n'
            '.include "include/native.inc"\n', encoding="utf-8",
        )
        include = fixture.root / "include/native.inc"
        include.write_text(".byte 1\n", encoding="utf-8")
        with (fixture.root / "Makefile").open("a", encoding="utf-8") as makefile:
            makefile.write(dedent("""\
                ifeq ($(NODEP),1)
                asm/native.o: data_dep :=
                else ifeq ($(ARCHIVAL_SCANINC_NODEP),1)
                asm/native.o: data_dep :=
                else
                asm/native.o: data_dep = $(shell ./scaninc -I include -I "" asm/native.s)
                endif
                .SECONDEXPANSION:
                asm/native.o: asm/native.s $$(data_dep)
                \t/usr/bin/as $< -o $@
                """))

        first = fixture.make("asm/native.o")
        self.assertEqual(first.returncode, 0, first.stdout)
        assembled = fixture.root / "asm/native.o"
        before = assembled.read_bytes()

        include.write_text(".byte 9\n", encoding="utf-8")
        mixed = fixture.make("expansion-modern-clean", "asm/native.o")
        self.assertEqual(mixed.returncode, 0, mixed.stdout)
        rebuilt = assembled.read_bytes()
        self.assertNotEqual(before, rebuilt)

        include.write_text(".byte 1\n", encoding="utf-8")
        forced = fixture.make("NODEP=1", "expansion-modern-clean", "asm/native.o")
        self.assertEqual(forced.returncode, 0, forced.stdout)
        self.assertEqual(assembled.read_bytes(), rebuilt)

    def test_recursive_default_non_c_requests_preserve_scanned_include_rebuilds(self):
        for tool in (Path("/usr/bin/as"), Path("/usr/bin/g++")):
            if not tool.exists():
                self.skipTest(f"missing required native tool: {tool}")
        fixture = self.make_fixture(
            host_goals=("native-midi.o", "native-banim.o"),
            make_prelude=(
                "C_OBJECTS := legacy.o\n"
                "DATA_SRC_C_OBJECTS := native-data.o\n"
                "ASM_OBJECTS := asm/native.o legacy.o native-data.o\n"
                "MID_OBJECTS := native-midi.o\n"
                "BANIM_OBJECT := native-banim.o\n"
                "MODERN_ELF_LEGACY_ASM := asm/native.o\n"
                "MODERN_ELF_LEGACY_MIDI := native-midi.o\n"
                "MODERN_GOALS += expansion-modern-boot-check expansion-modern-legacy-ready\n"
            ),
        )
        database = self.root_make_database()
        rules = [make_database_rule(database, name) for name in (
            "all", "expansion-modern-legacy-ready",
        )]
        self.assertTrue(all(rule is not None for rule in rules))
        scanner = subprocess.run(
            ["/usr/bin/g++", "-std=c++11", "-O2",
             *map(str, sorted((ROOT / "tools/scaninc").glob("*.cpp"))),
             "-o", str(fixture.root / "scaninc")],
            text=True, capture_output=True, check=False, timeout=60,
        )
        self.assertEqual(scanner.returncode, 0, scanner.stdout + scanner.stderr)
        (fixture.root / "asm").mkdir(exist_ok=True)
        (fixture.root / "asm/native.s").write_text(
            '.section .rodata\n.globl native_fixture\nnative_fixture:\n'
            '.include "include/native.inc"\n', encoding="utf-8",
        )
        include = fixture.root / "include/native.inc"
        include.write_text(".byte 1\n", encoding="utf-8")
        with (fixture.root / "Makefile").open("a", encoding="utf-8") as makefile:
            makefile.write("\n" + "\n\n".join(rules) + "\n")
            makefile.write(dedent("""\
                .PHONY: expansion-modern-boot-check expansion-modern-legacy-ready
                expansion-modern-boot-check: expansion-modern-legacy-ready
                ifeq ($(NODEP),1)
                asm/native.o: data_dep :=
                else
                asm/native.o: data_dep = $(shell ./scaninc -I include -I "" asm/native.s)
                endif
                .SECONDEXPANSION:
                asm/native.o: asm/native.s $$(data_dep)
                \t/usr/bin/as $< -o $@
                """))

        first = fixture.make("NODEP=1")
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertEqual(fixture.log_lines(fixture.cpp_log), [], first.stdout)
        self.assertFalse(fixture.depfile.exists())
        assembled = fixture.root / "asm/native.o"
        before = assembled.read_bytes()
        include.write_text(".byte 9\n", encoding="utf-8")
        second = fixture.make("all", "NODEP=1")
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertNotEqual(assembled.read_bytes(), before)
        non_c = fixture.make("native-banim.o", "native-midi.o", "NODEP=0")
        self.assertEqual(non_c.returncode, 0, non_c.stdout)
        self.assertEqual(fixture.log_lines(fixture.cpp_log), [], non_c.stdout)
        self.assertFalse(fixture.depfile.exists())

        mixed = fixture.make("asm/native.o", "legacy.o", "NODEP=0")
        self.assertEqual(mixed.returncode, 0, mixed.stdout)
        self.assertTrue(fixture.depfile.exists())
        self.assertTrue(fixture.generated_header.exists())
        self.assertGreater(len(fixture.log_lines(fixture.cpp_log)), 0)

        inventories = {}
        for name in (
            "MAKECMDGOALS_NODEP", "MAKECMDGOALS_NOSCANINC", "MODERN_ELF_LEGACY_ASM",
            "MODERN_ELF_LEGACY_MIDI", "BANIM_OBJECT", "C_OBJECTS",
            "DATA_SRC_C_OBJECTS",
        ):
            value = make_database_variable(database, name)
            self.assertIsNotNone(value, name)
            inventories[name] = set(value.split())
        non_c_goals = (
            inventories["MODERN_ELF_LEGACY_ASM"]
            | inventories["MODERN_ELF_LEGACY_MIDI"]
            | inventories["BANIM_OBJECT"]
        )
        self.assertTrue(non_c_goals)
        self.assertTrue(non_c_goals <= inventories["MAKECMDGOALS_NODEP"])
        self.assertFalse(non_c_goals & inventories["MAKECMDGOALS_NOSCANINC"])
        self.assertFalse(
            (inventories["C_OBJECTS"] | inventories["DATA_SRC_C_OBJECTS"])
            & inventories["MAKECMDGOALS_NODEP"]
        )
