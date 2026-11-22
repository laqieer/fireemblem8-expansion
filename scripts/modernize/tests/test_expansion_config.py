"""Tests for scripts/modernize/expansion_config.py (issue #8).

Covers: field validators (valid + malformed title/game code/maker code/
revision/ROM size/semantic version/build-id/preset/ABI/text shift),
config.mk parsing, the override-parameter precedence in load_identity()
(an explicit override always wins over config.mk's parsed value -- this is
the fix for the "two sources of truth" bug), build-commit resolution
precedence (explicit override > git rev-parse HEAD > "unknown" sentinel),
fingerprint determinism, generate_metadata_files()'s write-if-changed
behavior, and the validate/resolve/generate CLI subcommands (including
early, actionable rejection of invalid inputs and incompatible
combinations before any file is written).
"""

import itertools
import json
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modernize"))
sys.path.insert(0, str(ROOT / "tools/gba-playtest/tests"))

import expansion_config as ec  # noqa: E402
from host_mode import host_only_enabled  # noqa: E402
from scripts.modernize.tests.make_database import (
    make_database_rule,
    make_database_variable,
)


def find_modern_compiler():
    selected = os.environ.get("MODERN_CC")
    compiler = shutil.which(selected or "arm-none-eabi-gcc")
    if selected and compiler is None:
        raise FileNotFoundError(f"MODERN_CC is not executable: {selected}")
    return compiler


def preprocess_expansion_header(header, *defines):
    if os.environ.get("MODERN_CC"):
        compiler = find_modern_compiler()
        driver_args = ["-E"]
    else:
        compiler = shutil.which("arm-none-eabi-cpp")
        driver_args = []
        if compiler is None:
            compiler = find_modern_compiler()
            driver_args = ["-E"]
    if compiler is None:
        raise unittest.SkipTest("arm-none-eabi-cpp/-gcc is not available")
    return subprocess.run(
        [
            compiler,
            *driver_args,
            "-dM",
            "-I",
            str(ROOT / "include"),
            *(f"-D{definition}" for definition in defines),
            "-x",
            "c",
            "-",
        ],
        input=f'#include "{header}"\n',
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def preprocessor_macros(output):
    values = {}
    for line in output.splitlines():
        if not line.startswith("#define "):
            continue
        definition = line[len("#define "):].split(maxsplit=1)
        name = definition[0]
        values[name] = definition[1] if len(definition) == 2 else ""
    return values


def assert_rejects_wrong_length(test_case, validator, values):
    for value in values:
        with test_case.subTest(value=value):
            with test_case.assertRaises(ec.ConfigError):
                validator(value)


def write_config_mk(
    directory: Path,
    version_major="0",
    version_minor="1",
    version_patch="0",
    rom_title="FIREEMBLEM2E",
    rom_game_code="BE8E",
    rom_maker_code="01",
    rom_revision="0",
    build_id="",
    save_compat_epoch="1",
    enabled_locales="en",
    default_locale="en",
    pseudo_locale="0",
    mechanics_hooks=None,
    mechanics_sample=None,
    danger_overlay_menu=None,
    autoplay_strategies=None,
    blue_phase_delegate=None,
) -> Path:
    path = directory / "config.mk"
    # The issue #6 starter-feature flags are optional config.mk keys: a
    # fixture omits them (they default to 0, exercising the absent path)
    # unless a test passes an explicit value.
    feature_lines = []
    if mechanics_hooks is not None:
        feature_lines.append(f"EXPANSION_MECHANICS_HOOKS := {mechanics_hooks}")
    if mechanics_sample is not None:
        feature_lines.append(f"EXPANSION_MECHANICS_SAMPLE := {mechanics_sample}")
    if danger_overlay_menu is not None:
        feature_lines.append(
            f"EXPANSION_DANGER_OVERLAY_MENU := {danger_overlay_menu}"
        )
    if autoplay_strategies is not None:
        feature_lines.append(
            f"EXPANSION_AUTOPLAY_STRATEGIES := {autoplay_strategies}"
        )
    if blue_phase_delegate is not None:
        feature_lines.append(
            f"EXPANSION_BLUE_PHASE_DELEGATE := {blue_phase_delegate}"
        )
    path.write_text(
        "\n".join(
            [
                f"EXPANSION_VERSION_MAJOR := {version_major}",
                f"EXPANSION_VERSION_MINOR := {version_minor}",
                f"EXPANSION_VERSION_PATCH := {version_patch}",
                f"EXPANSION_ROM_TITLE := {rom_title}",
                f"EXPANSION_ROM_GAME_CODE := {rom_game_code}",
                f"EXPANSION_ROM_MAKER_CODE := {rom_maker_code}",
                f"EXPANSION_ROM_REVISION := {rom_revision}",
                f"EXPANSION_BUILD_ID := {build_id}",
                f"EXPANSION_SAVE_COMPAT_EPOCH := {save_compat_epoch}",
                f"EXPANSION_ENABLED_LOCALES := {enabled_locales}",
                f"EXPANSION_DEFAULT_LOCALE := {default_locale}",
                f"EXPANSION_PSEUDO_LOCALE := {pseudo_locale}",
                *feature_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def assert_declaration_count(test_case, header, count):
    checked = subprocess.run(
        [
            os.environ.get("CC", "cc"), "-std=c89", "-pedantic-errors",
            "-I", str(ROOT / "scripts/localization/game_catalog/tests/host_include"),
            "-I", str(header.parent), "-x", "c", "-fsyntax-only", "-",
        ],
        input=(
            '#include "game_localization_declarations.h"\n'
            f"typedef char HeaderMatches[(GAME_LOCALIZATION_TARGET_COUNT == {count}) ? 1 : -1];\n"
        ),
        capture_output=True, text=True, timeout=30,
    )
    test_case.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


class ProjectSelectionIdentityTests(unittest.TestCase):
    def setUp(self):
        projects = ROOT / "examples"
        projects.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="selection-", dir=projects)
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        (self.project / "project.mk").write_text(
            "FE8_PROJECT_PROFILES := primary alternate\n"
            "FE8_PROJECT_DEFAULT_PROFILE := primary\n"
            "FE8_PROJECT_REQUIRED_SETTINGS := MODERN_ROM_SIZE EXPANSION_ROM_TITLE\n"
            "FE8_PROJECT_primary_MODERN_ROM_SIZE := 32M\n"
            "FE8_PROJECT_primary_EXPANSION_ROM_TITLE := SELECTEDGAME\n"
            "FE8_PROJECT_alternate_MODERN_ROM_SIZE := 16M\n"
            "FE8_PROJECT_alternate_EXPANSION_ROM_TITLE := ALTERNATE\n"
            "FE8_PROJECT_INHERIT := $(FE8_PROJECT_MODULES)\n",
            encoding="utf-8",
        )

    def test_source_only_make_declarations_cover_english_and_pseudo(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="source-only-declarations-", dir=ROOT / "build") as directory:
            selected = Path(directory) / "selected-msg.h"
            selected.write_text("#define MSG_COUNT 0x0003\n", encoding="ascii")
            unrelated = Path(directory) / "unrelated-msg.h"
            unrelated.write_text("#define MSG_COUNT 0x0007\n", encoding="ascii")

            for locales, pseudo in (("en", "0"), ("en,qps-ploc", "1"), ("en,ja", "0")):
                with self.subTest(locales=locales):
                    output = Path(directory) / locales.replace(",", "-")
                    result = subprocess.run(
                        [
                            "make", "--no-print-directory", "NODEP=1",
                            "expansion-modern-game-localization-declarations",
                            f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug", "MODERN_ROM_SIZE=32M",
                            "FE8_PROJECT=default", f"EXPANSION_ENABLED_LOCALES={locales}",
                            f"EXPANSION_PSEUDO_LOCALE={pseudo}", "MODERN_CC=/missing/compiler",
                            "MODERN_SCANINC=/missing/scaninc",
                            f"PYTHON={sys.executable}", f"TEXT_HEADER={selected}",
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=60,
                        env={**os.environ, "MSG_HEADER": str(unrelated)},
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    header = output / "game-localization/generated/game_localization_declarations.h"
                    self.assertTrue(header.is_file(), result.stdout + result.stderr)
                    self.assertEqual(list(output.rglob("game_localization_catalog.c")), [])
                    assert_declaration_count(self, header, 3)
            source = Path(directory) / "fallback-input.h"
            fallback = Path(directory) / "generated-msg.h"
            makefile = self.project / "fallback-header.mk"
            makefile.write_text(
                "include Makefile\n"
                f"TEXT_HEADER := {fallback}\n"
                f"$(TEXT_HEADER): {source}\n"
                '\tcp "$<" "$@"\n',
                encoding="utf-8",
            )
            output = Path(directory) / "fallback"
            command = [
                argument for argument in result.args
                if not argument.startswith(("TEXT_HEADER=", "MODERN_BUILD_ROOT="))
            ]
            command[1:1] = ["-f", str(makefile)]
            command.append(f"MODERN_BUILD_ROOT={output}")
            for count in (5, 6):
                with self.subTest(generated_fallback_count=count):
                    source.write_text(f"#define MSG_COUNT 0x{count:04X}\n", encoding="ascii")
                    built = subprocess.run(
                        command, cwd=ROOT, capture_output=True, text=True, timeout=60,
                        env={**os.environ, "MSG_HEADER": str(unrelated)},
                    )
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    assert_declaration_count(
                        self, output / "game-localization/generated/game_localization_declarations.h",
                        count,
                    )

    def test_native_renderer_make_graph_owns_its_generated_debug_font(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-font-graph-", dir=ROOT / "build") as directory:
            output = Path(directory)
            renderer = output / "debug/aapcs/src/fontgrp.o"
            result = subprocess.run(
                [
                    "make", "--no-print-directory", "-pq", "NODEP=1",
                    "-o", "expansion-modern-toolchain-check", str(renderer),
                    f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                    "FE8_PROJECT=default", "MODERN_CC=/missing/compiler",
                    f"PYTHON={sys.executable}",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
            prerequisites = next(
                shlex.split(line.partition(":")[2])
                for line in result.stdout.splitlines()
                if line.startswith(str(renderer) + ":")
                and "src/fontgrp.c" in shlex.split(line.partition(":")[2])
            )
            self.assertIn("graphics/debug_font.4bpp.h", prerequisites)

    def test_native_context_keeps_implicit_makefile_with_preloaded_input(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-preloaded-", dir=ROOT / "build") as directory:
            output = Path(directory)
            preload = self.project / "preloaded.mk"
            preload.write_text("NATIVE_PRELOADED_INPUT := 1\n", encoding="ascii")
            result = subprocess.run(
                [
                    "make", "--no-print-directory", "expansion-modern-text-inputs",
                    f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug", "FE8_PROJECT=default",
                    "MODERN_CC=/missing/compiler", "MODERN_PREPROC=/missing/preprocessor",
                    "MODERN_DRY_RUN=1", "NODEP=1", f"PYTHON={sys.executable}",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
                env={**os.environ, "MAKEFILES": str(preload)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            context = json.loads((output / "debug/aapcs/selected-text-inputs.json").read_text())
            self.assertTrue(context["units"])
            self.assertTrue(all(unit["logical_source"].endswith(".c") for unit in context["units"]))
            self.assertEqual(list(output.rglob("*.o")), [])

    def test_native_context_distinguishes_make_flags_from_option_values(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-make-flags-", dir=ROOT / "build") as directory:
            for index, (flags, dry) in enumerate((
                ((), False),
                (("-Otarget",), False),
                (("-Oline",), False),
                (("-Onone",), False),
                (("--output-sync=line",), False),
                (("-Iinclude",), False),
                (("--include-dir=include",), False),
                (("--warn-undefined-variables",), False),
                (("-sn",), True),
                (("-n", "-Oline"), True),
                (("--just-print", "--output-sync=line"), True),
                (("--dry-run", "--include-dir=include"), True),
            )):
                with self.subTest(flags=flags):
                    output = Path(directory) / str(index)
                    result = subprocess.run(
                        [
                            "make", *flags, "--no-print-directory", "expansion-modern-text-inputs",
                            f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                            "FE8_PROJECT=default", "MODERN_CC=/missing/compiler",
                            "MODERN_PREPROC=/missing/preprocessor", "NODEP=1",
                            f"PYTHON={sys.executable}",
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=60,
                        env={
                            key: value for key, value in os.environ.items()
                            if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"}
                        },
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    context = output / "debug/aapcs/selected-text-inputs.json"
                    if dry:
                        self.assertFalse(context.exists(), result.stdout + result.stderr)
                    else:
                        self.assertTrue(json.loads(context.read_text())["units"])
                    self.assertEqual(list(output.rglob("*.makefiles")), [])
                    self.assertEqual(list(output.rglob("*.o")), [])

    def test_native_context_preserves_original_make_level(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-level-", dir=ROOT / "build") as directory:
            for index, (goal, level) in enumerate((
                ("expansion-modern-text-inputs", 0), ("native-level-parent", 0),
                ("native-recurse-one", 1), ("native-recurse-two", 2),
            )):
                with self.subTest(goal=goal, level=level):
                    output = Path(directory) / str(index)
                    original = Path(directory) / ("flags-" + str(index))
                    entrypoint = Path(directory) / ("level-" + str(index) + ".mk")
                    entrypoint.write_text(
                        "include Makefile\n"
                        "ifeq ($(origin MAKELEVEL),environment)\n"
                        + "".join(
                            f"ifeq ($(MAKELEVEL),{value})\n"
                            f"MODERN_CFLAGS += -DSELECTED_FONT_WIDTH={9 + value}\n"
                            "endif\n" for value in range(3)
                        )
                        + "endif\n"
                        ".PHONY: native-level-live native-level-parent "
                        "native-recurse-one native-recurse-two\n"
                        "native-level-live:\n"
                        f"\t@printf '%s\\n' '$(MODERN_CFLAGS)' > {shlex.quote(str(original))}\n"
                        "native-level-parent: native-level-live expansion-modern-text-inputs\n"
                        "native-recurse-one:\n"
                        f"\t+$(MAKE) --no-print-directory -f {shlex.quote(str(entrypoint))} native-level-parent\n"
                        "native-recurse-two:\n"
                        f"\t+$(MAKE) --no-print-directory -f {shlex.quote(str(entrypoint))} native-recurse-one\n",
                        encoding="ascii",
                    )
                    result = subprocess.run(
                        [
                            "make", "--no-print-directory", "-f", str(entrypoint),
                            *(["native-level-live"] if index == 0 else []), goal,
                            "NODEP=1", "MODERN_DRY_RUN=1", f"MODERN_BUILD_ROOT={output}",
                            "MODERN_CONFIG=debug", "FE8_PROJECT=default",
                            "MODERN_CC=/missing/compiler", "MODERN_PREPROC=/missing/preprocessor",
                            f"PYTHON={sys.executable}",
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=60,
                        env={
                            key: value for key, value in os.environ.items()
                            if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"}
                        },
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    width = "-DSELECTED_FONT_WIDTH=" + str(9 + level)
                    self.assertIn(width, shlex.split(original.read_text()))
                    context = json.loads(
                        (output / "debug/aapcs/selected-text-inputs.json").read_text(),
                    )
                    self.assertTrue(context["units"])
                    self.assertTrue(all(
                        width in unit["compiler_argv"] for unit in context["units"]
                    ))
                    self.assertEqual(list(output.rglob("*.o")), [])

    def test_native_context_preserves_regenerated_source_makefiles(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-source-replay-", dir=ROOT / "build") as directory:
            output = Path(directory)
            for name, included, in_output in (
                ("entrypoint selected flags.mk", False, False),
                ("nested  selected flags.mk", True, False),
                ("entrypoint 'selected' flags.mk", False, False),
                ("selected-flags.d", True, True),
                ("selected  flags.d", True, True),
            ):
                with self.subTest(source=name, included=included):
                    source = (output / "debug/aapcs" if in_output else self.project) / name
                    source.parent.mkdir(parents=True, exist_ok=True)
                    prerequisite = self.project / "source-prerequisite"
                    executed = self.project / "source-regenerated"
                    prerequisite.write_text("native source input\n", encoding="ascii")
                    executed.unlink(missing_ok=True)
                    escaped = str(source).replace(" ", "\\ ")
                    source.write_text(
                        ("" if included else "include Makefile\n")
                        + "MODERN_CFLAGS += -DNATIVE_SOURCE_INPUT=7\n"
                        + f"{escaped}: {prerequisite}\n"
                        + f"\t@printf regenerated > {shlex.quote(str(executed))}\n"
                        + f"\t@touch {shlex.quote(str(source))}\n",
                        encoding="ascii",
                    )
                    entrypoint = self.project / "source-entry.mk" if included else source
                    if included:
                        entrypoint.write_text("include Makefile\n" + f"include {escaped}\n")
                    before = (source.read_bytes(), source.stat().st_ino, source.stat().st_mtime_ns)
                    result = subprocess.run(
                        [
                            "make", "--no-print-directory", "-f", str(entrypoint),
                            "expansion-modern-text-inputs", "NODEP=1", "MODERN_DRY_RUN=1",
                            f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                            "FE8_PROJECT=default", f"PYTHON={sys.executable}",
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse(executed.exists(), "capture replay executed source regeneration")
                    self.assertEqual(
                        (source.read_bytes(), source.stat().st_ino, source.stat().st_mtime_ns),
                        before,
                    )
                    context = json.loads((output / "debug/aapcs/selected-text-inputs.json").read_text())
                    self.assertTrue(context["units"])
                    self.assertTrue(all(
                        "-DNATIVE_SOURCE_INPUT=7" in unit["compiler_argv"]
                        for unit in context["units"]
                    ))
                    self.assertEqual(list(output.rglob("*.o")), [])
                    if name == "entrypoint selected flags.mk":
                        dry_output = output / "ordinary-dry"
                        dry = subprocess.run(
                            [
                                "make", "--no-print-directory", "-n", "-f", str(entrypoint),
                                "expansion-modern-text-inputs", "NODEP=1", "MODERN_DRY_RUN=1",
                                f"MODERN_BUILD_ROOT={dry_output}", "FE8_PROJECT=default",
                                f"PYTHON={sys.executable}",
                            ],
                            cwd=ROOT, capture_output=True, text=True, timeout=60,
                        )
                        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
                        self.assertFalse((dry_output / "debug/aapcs/selected-text-inputs.json").exists())
                        self.assertEqual(list(dry_output.rglob("*.makefiles")), [])
                        self.assertFalse(executed.exists())

            sources = [self.project / name for name in ("left.mk", "right.mk")]
            names = [str(path.relative_to(ROOT)) for path in sources]
            for path in sources:
                path.write_text("# native included source\n", encoding="ascii")
            ambiguous = ROOT / " ".join(names)
            ambiguous.parent.mkdir(parents=True)
            ambiguous.write_text("# alternative literal source name\n", encoding="ascii")
            entrypoint = self.project / "ambiguous-native.mk"
            entrypoint.write_text("include Makefile\n" + "".join(f"include {name}\n" for name in names))
            context_path = output / "debug/aapcs/selected-text-inputs.json"
            before_context = context_path.read_bytes()
            refused = subprocess.run(
                [
                    "make", "--no-print-directory", "-f", str(entrypoint),
                    "expansion-modern-text-inputs", "NODEP=1", "MODERN_DRY_RUN=1",
                    f"MODERN_BUILD_ROOT={output}", "FE8_PROJECT=default", f"PYTHON={sys.executable}",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("ambiguous", refused.stderr)
            self.assertEqual(context_path.read_bytes(), before_context)
            self.assertFalse(context_path.with_suffix(".json.makefiles").exists())

    def test_native_context_preserves_complete_warm_dependency_inputs(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-warm-replay-", dir=ROOT / "build") as directory:
            temporary = Path(directory)
            output = temporary / ("native-" + "x" * max(1, 122 - len(str(temporary))))
            entrypoint = self.project / "warm-native.mk"
            entrypoint.write_text(
                "include Makefile\n.ONESHELL:\n"
                ".PHONY: native-dependency-inventory\nnative-dependency-inventory:\n"
                "\t@: native-object-dependencies $(MODERN_ALL_DEPS)\n"
                "\t@: native-header-dependencies $(MODERN_ALL_C_HEADER_DEPS)\n"
                "\t@: native-asset-dependencies $(MODERN_ALL_DATA_ASSET_DEPS)\n",
                encoding="ascii",
            )
            arguments = [
                "make", "--no-print-directory", "-f", str(entrypoint), "NODEP=1",
                "MODERN_DRY_RUN=1", "FE8_PROJECT=default", "MODERN_ROM_SIZE=32M",
                "EXPANSION_ENABLED_LOCALES=en,ja", f"MODERN_BUILD_ROOT={output}",
                f"PYTHON={sys.executable}", f"MODERN_CC={temporary / 'unavailable-compiler'}",
                "--old-file=expansion-modern-toolchain-check",
            ]
            query = subprocess.run(
                [*arguments, "-n", "native-dependency-inventory"],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(query.returncode, 0, query.stdout + query.stderr)
            inventories = {}
            for line in query.stdout.splitlines():
                if line.startswith(": native-"):
                    words = shlex.split(line)
                    inventories[words[1]] = words[2:]
            suffixes = {
                "native-object-dependencies": (".d", ".o"),
                "native-header-dependencies": (".headers.d", ".o"),
                "native-asset-dependencies": (".assets.d", ".pre.c"),
            }
            self.assertEqual(set(inventories), set(suffixes))
            self.assertTrue(all(inventories.values()))
            cold = subprocess.run(
                [*arguments, "expansion-modern-text-inputs"],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(cold.returncode, 0, cold.stdout + cold.stderr)
            context_path = output / "debug/aapcs/selected-text-inputs.json"
            cold_units = json.loads(context_path.read_text())["units"]
            snapshots = {}
            for kind, paths in inventories.items():
                suffix, target_suffix = suffixes[kind]
                for value in paths:
                    path = Path(value)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    target = value.removesuffix(suffix) + target_suffix
                    path.write_text(f"{target}: include/global.h\n", encoding="ascii")
                    snapshots[path] = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for _ in range(2):
                result = subprocess.run(
                    [
                        *arguments, "MODERN_DRY_RUN=", "MAKECMDGOALS=expansion-modern-all",
                        "expansion-modern-text-inputs",
                    ],
                    cwd=ROOT, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(context_path.read_text())["units"], cold_units)
                self.assertEqual(
                    {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                     for path in snapshots},
                    snapshots,
                )
                self.assertFalse(context_path.with_suffix(".json.makefiles").exists())
            self.assertEqual(list(output.rglob("*.o")), [])

    def test_native_parallel_capture_reads_only_complete_dependency_products(self):
        from scripts.workflow_pilot import raw_diff_check as raw

        if host_only_enabled():
            self.skipTest("native dependency publication belongs to the target lane")
        compiler = shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc"))
        if compiler is None:
            self.skipTest("selected ARM compiler is unavailable")
        if not hasattr(os, "mkfifo"):
            self.skipTest("the deterministic native parallel writer uses POSIX FIFOs")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-publish-", dir=ROOT / "build") as directory:
            temporary = Path(directory)
            headers = temporary / "headers"
            headers.mkdir()
            for index in range(160):
                (headers / f"native_{index:03}.h").write_text(f"#define NATIVE_{index:03} 1\n")
            selected = headers / "selected.h"
            selected.write_text("".join(
                f'#include "native_{index:03}.h"\n' for index in range(160)
            ))
            for case_index, (slot, fails, oneshell, relative) in enumerate(itertools.product(
                ("src/bmunit.o", "src/data/data_8B363C.o", "asm/arm_call.o", "asm/fe6sio.o"),
                (False, True), (False, True), (False, True),
            )):
                with self.subTest(
                    slot=slot, compiler_failure=fails, oneshell=oneshell, relative_build_root=relative,
                ):
                    # Long semantic labels exceed asset-discovery filenames in hosted checkouts.
                    work = temporary / str(case_index)
                    work.mkdir()
                    output = work / "native"
                    if relative:
                        output = output.relative_to(ROOT)
                    object_path = output / "debug/aapcs" / slot
                    ready, done = work / "ready", work / "done"
                    os.mkfifo(ready)
                    os.mkfifo(done)
                    observed = work / "producer.json"
                    fixture = work / "fixture.py"
                    fixture.write_text(
                        f"#!{sys.executable}\n"
                        "import json, os, subprocess, sys\nfrom pathlib import Path\n"
                        f"compiler={compiler!r}; python={sys.executable!r}\n"
                        f"ready={str(ready)!r}; done={str(done)!r}\n"
                        "mode=Path(sys.argv[0]).name; args=sys.argv[1:]\n"
                        "if mode=='native-wait':\n"
                        "    with open(ready,'rb') as stream: assert stream.read(1)==b'1'\n"
                        "    raise SystemExit(0)\n"
                        "if mode=='native-python':\n"
                        "    if 'describe-make-context' not in args: os.execv(python,[python,*args])\n"
                        "    try: result=subprocess.run([python,*args])\n"
                        "    finally:\n"
                        "        with open(done,'wb') as stream: stream.write(b'1')\n"
                        "    raise SystemExit(result.returncode)\n"
                        "if '-MF' in args:\n"
                        "    position=args.index('-MF')+1; target=Path(args[position]); gas=False\n"
                        "else:\n"
                        "    positions=[i for i,arg in enumerate(args) if arg.startswith('-Wa,--MD,')]\n"
                        "    if not positions: os.execv(compiler,[compiler,*args])\n"
                        "    position=positions[0]; target=Path(args[position].split(',',2)[2]); gas=True\n"
                        "captured=target.with_name(target.name+'.producer')\n"
                        "args[position]=('-Wa,--MD,' if gas else '')+str(captured)\n"
                        "result=subprocess.run([compiler,*args])\n"
                        "if result.returncode: raise SystemExit(result.returncode)\n"
                        "data=captured.read_bytes(); prefix=min(4096,max(1,len(data)//2))\n"
                        "with target.open('wb') as stream:\n"
                        "    stream.write(data[:prefix]); stream.flush()\n"
                        "    with open(ready,'wb') as signal: signal.write(b'1')\n"
                        "    with open(done,'rb') as signal: assert signal.read(1)==b'1'\n"
                        "    stream.write(data[prefix:])\n"
                        f"Path({str(observed)!r}).write_text(json.dumps({{'requested_mf':str(target),"
                        "'bytes':len(data),'prefix':prefix,'complete':target.read_bytes()==data}))\n"
                        f"raise SystemExit({73 if fails else 0})\n",
                        encoding="ascii",
                    )
                    fixture.chmod(0o755)
                    programs = {}
                    for name in ("native-gcc", "native-python", "native-wait"):
                        path = work / name
                        path.symlink_to(fixture)
                        programs[name] = path
                    entrypoint = work / "native.mk"
                    entrypoint.write_text(
                        "include Makefile\n" + (".ONESHELL:\n" if oneshell else "")
                        + f"{object_path}: private MODERN_CFLAGS += -include {selected}\n"
                        + f"{object_path}: FORCE\n"
                        + "$(MODERN_TEXT_COMPILE_CONTEXT): native-writer-ready\n"
                        + ".PHONY: native-race native-writer-ready\n"
                        + f"native-race: {object_path} expansion-modern-text-inputs\n"
                        + f"native-writer-ready:\n\t@{programs['native-wait']}\n",
                        encoding="ascii",
                    )
                    arguments = [
                        "make", "--no-print-directory", "-f", str(entrypoint), "NODEP=1",
                        f"MODERN_BUILD_ROOT={output}", "FE8_PROJECT=default",
                    ]
                    if slot == "asm/fe6sio.o":
                        sio = work / "sio"
                        sio.mkdir()
                        payload = sio / "fe6sio_payload.bin.lz"
                        payload.write_bytes(bytes((0x10, 0, 0, 0)))
                        arguments.extend((
                            f"MODERN_MGFEMBP_DIR={sio}",
                            f"MODERN_MGFEMBP_PAYLOAD={payload}",
                            "--old-file=" + str(payload),
                        ))
                        if relative:
                            arguments.append(
                                f"MODERN_MGFEMBP_BUILDER={sys.executable} "
                                + str(ROOT / "scripts/modernize/build_mgfembp.py")
                            )
                    built = raw.run_process(
                        [*arguments, str(object_path), f"PYTHON={sys.executable}",
                         f"MODERN_CC={compiler}"],
                        cwd=ROOT, env=raw.git_environment(), timeout=90,
                    )
                    self.assertEqual(built.returncode, 0, (built.stdout + built.stderr).decode())
                    dependency = object_path.with_suffix(".d")
                    before = dependency.read_bytes()
                    raced = raw.run_process(
                        [
                            *arguments, "-j2", "native-race", "MODERN_DRY_RUN=1",
                            f"PYTHON={programs['native-python']}",
                            f"MODERN_CC={programs['native-gcc']}",
                        ],
                        cwd=ROOT, env=raw.git_environment(), timeout=90,
                    )
                    facts = json.loads(observed.read_text())
                    self.assertTrue(facts["complete"])
                    self.assertNotEqual(Path(facts["requested_mf"]).resolve(), dependency.resolve())
                    self.assertEqual(dependency.read_bytes(), before)
                    if fails:
                        self.assertEqual(raced.returncode, 2)
                        self.assertIn(b"Error 73", raced.stderr)
                    else:
                        self.assertEqual(raced.returncode, 0, (raced.stdout + raced.stderr).decode())
                    context = json.loads((output / "debug/aapcs/selected-text-inputs.json").read_text())
                    self.assertTrue(context["units"])
                    for unit in context["units"]:
                        argv = unit["compiler_argv"]
                        self.assertIn("-MF", argv)
                        self.assertNotEqual(
                            Path(argv[argv.index("-MF") + 1]),
                            Path(unit["object"]).with_suffix(".d"),
                        )

    def test_host_only_skips_native_dependency_publication(self):
        case = ProjectSelectionIdentityTests(
            "test_native_parallel_capture_reads_only_complete_dependency_products",
        )
        result = unittest.TestResult()
        with mock.patch.dict(os.environ, {"GBA_PLAYTEST_HOST_ONLY": "1"}), mock.patch.object(
            shutil, "which", side_effect=AssertionError("host-only request resolved a native tool"),
        ):
            case.run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.skipped), 1)

    def test_native_context_captures_target_specific_native_tools(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-private-tools-", dir=ROOT / "build") as directory:
            for label, separator, present in (
                ("plain", "-", True), ("spaced", " ", True), ("absent", " ", False),
            ):
                with self.subTest(tool_paths=label):
                    output = Path(directory) / label
                    namespace = output / "debug/aapcs"
                    executed = output / "unexpected-native-execution"
                    compiler = self.project / f"{label}{separator}compiler"
                    preprocessor = self.project / f"{label}{separator}preprocessor"
                    scanner = self.project / f"{label}{separator}scanner"
                    if present:
                        for tool in (compiler, preprocessor, scanner):
                            tool.write_text(
                                "#!/bin/sh\n"
                                f"printf called > {shlex.quote(str(executed))}\nexit 71\n",
                                encoding="ascii",
                            )
                            tool.chmod(0o755)
                    makefile = self.project / f"private-tools-{label}.mk"
                    makefile.write_text(
                        "include Makefile\n"
                        "MODERN_CC := /missing/global compiler\n"
                        "MODERN_PREPROC := /missing/global preprocessor\n"
                        f"{namespace}/src/fontgrp-data.o: private MODERN_CC := {compiler}\n"
                        f"{namespace}/src/data/localized_font_data.pre.c: "
                        f"private MODERN_PREPROC := {preprocessor}\n"
                        f"{namespace}/src/data/localized_font_data.assets.d: "
                        f"private MODERN_SCANINC := {scanner}\n",
                        encoding="ascii",
                    )
                    result = subprocess.run(
                        [
                            "make", "--no-print-directory", "-f", str(makefile),
                            "expansion-modern-text-inputs", f"MODERN_BUILD_ROOT={output}",
                            "MODERN_CONFIG=debug", "FE8_PROJECT=default", "MODERN_DRY_RUN=1",
                            "NODEP=1", f"PYTHON={sys.executable}",
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    context = json.loads((namespace / "selected-text-inputs.json").read_text())
                    units = {unit["logical_source"]: unit for unit in context["units"]}
                    self.assertEqual(units["src/fontgrp-data.c"]["compiler_argv"][0], str(compiler))
                    self.assertEqual(
                        units["src/data/localized_font_data.c"]["preprocessor_argv"][0],
                        str(preprocessor),
                    )
                    self.assertFalse(executed.exists())

    def test_native_data_dependencies_follow_target_specific_tools(self):
        if host_only_enabled() or shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")) is None:
            self.skipTest("native data tool dependency proof belongs to the target lane")
        from scripts.localization.game_catalog.compiled_fonts import _ArmObject

        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-tool-inputs-", dir=ROOT / "build") as directory:
            for label, separator in (("plain", "-"), ("spaced", " ")):
                output = Path(directory) / label
                namespace = output / "debug/aapcs"
                object_path = namespace / "src/data/localized_font_data.o"
                dependency = object_path.with_suffix(".assets.d")
                preprocessor = self.project / f"{label}{separator}preprocessor"
                scanner = self.project / f"{label}{separator}scanner"
                makefile = self.project / f"tool-inputs-{label}.mk"
                makefile.write_text(
                    "include Makefile\n"
                    f"{object_path.with_suffix('.pre.c')}: private MODERN_PREPROC := {preprocessor}\n"
                    f"{dependency}: private MODERN_SCANINC := {scanner}\n",
                    encoding="ascii",
                )
                command = [
                    "make", "--no-print-directory", "-f", str(makefile), "NODEP=1",
                    f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                    "FE8_PROJECT=default", "EXPANSION_ENABLED_LOCALES=en", f"PYTHON={sys.executable}",
                ]
                for version in (14, 15):
                    with self.subTest(tool_paths=label, preprocessor_version=version):
                        preprocessor.write_text(
                            f"#!{sys.executable}\n"
                            "import os, sys\n"
                            f"sys.stdout.write('const unsigned int gPrivatePreprocessorVersion = {version};\\n')\n"
                            "sys.stdout.flush()\n"
                            f"os.execv({str(ROOT / 'tools/preproc/preproc')!r}, "
                            f"[{str(ROOT / 'tools/preproc/preproc')!r}, *sys.argv[1:]])\n",
                            encoding="ascii",
                        )
                        preprocessor.chmod(0o755)
                        built = subprocess.run(
                            [*command, str(object_path)], cwd=ROOT,
                            capture_output=True, text=True, timeout=60,
                        )
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        native = _ArmObject(object_path)
                        self.assertEqual(int.from_bytes(native.symbol_bytes(
                            native.symbol("gPrivatePreprocessorVersion"),
                        ), "little"), version)
                for version in (14, 15):
                    with self.subTest(tool_paths=label, scanner_version=version):
                        asset = self.project / f"{label}-scanner-input-{version}.bin"
                        asset.write_bytes(bytes((version,)))
                        scanner.write_text(
                            f"#!{sys.executable}\n"
                            "import os, subprocess, sys\n"
                            f"result = subprocess.run([{str(ROOT / 'tools/scaninc/scaninc')!r}, "
                            "*sys.argv[1:]], check=True, stdout=subprocess.PIPE)\n"
                            "sys.stdout.buffer.write(result.stdout)\n"
                            f"sys.stdout.buffer.write({os.fsencode(str(asset))!r} + b'\\n')\n",
                            encoding="ascii",
                        )
                        scanner.chmod(0o755)
                        built = subprocess.run(
                            [*command, str(dependency)], cwd=ROOT,
                            capture_output=True, text=True, timeout=60,
                        )
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        self.assertIn(str(asset), dependency.read_text())

    def test_native_text_context_records_actual_recipes_without_catalog(self):
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="native-text-inputs-", dir=build)
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name)
        namespace = output / "debug/aapcs"
        context = namespace / "selected-text-inputs.json"
        inventory_path = output / "native-object-inventory"
        counter = self.project / "native-make-calls"
        wrapper = self.project / "native-make"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' call >> {shlex.quote(str(counter))}\n"
            f'exec {shlex.quote(shutil.which("make"))} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        makefile = self.project / "native-inputs.mk"
        secondary = self.project / "second-entrypoint.mk"
        secondary.write_text("MODERN_CFLAGS += -DNATIVE_SECOND_ENTRYPOINT=1\n", encoding="ascii")
        late = self.project / "late-native.d"
        late.write_text(
            f"{namespace}/src/action_semantics.o: private MODERN_CFLAGS += -DNATIVE_SLOT_ONLY=1\n",
            encoding="ascii",
        )
        warm_dependency = namespace / "src/fontgrp-data.d"
        warm_dependency.parent.mkdir(parents=True)
        warm_input = (
            f"{namespace}/src/fontgrp-data.o: src/fontgrp-data.c localized_game_text_data.h\n"
        ).encode()
        warm_dependency.write_bytes(warm_input)
        makefile.write_text(
            "include Makefile\n"
            'MODERN_CFLAGS += -DNATIVE_LATE_FLAG=1 -I"include with spaces"\n'
            "MODERN_CFLAGS += -DNATIVE_EVAL_FLAG=$(NATIVE_EVAL_FLAG)\n"
            "ifneq ($(filter expansion-modern-text-inputs,$(MAKECMDGOALS)),)\n"
            "MODERN_CFLAGS += -DNATIVE_INVOKED_GOAL=1\n"
            "endif\n"
            f"include {late}\n"
            ".PHONY: native-object-inventory\n"
            "native-object-inventory:\n"
            f'\t@printf \'%s\\n\' $(MODERN_ALL_C_OBJECTS) $(MODERN_ALL_DATA_OBJECTS) > "{inventory_path}"\n',
            encoding="utf-8",
        )
        settings = [
            f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug", "FE8_PROJECT=default",
            "MODERN_ROM_SIZE=32M", "EXPANSION_ENABLED_LOCALES=en,ja",
            "MODERN_CC=/missing/compiler with spaces",
            "MODERN_PREPROC=/missing/preprocessor with spaces", "MODERN_SCANINC=/missing/scaninc",
            "MODERN_DATA_LAYOUT_FLAGS=-DNATIVE_DATA_LAYOUT=1",
            f"PYTHON={sys.executable}", f"MAKE={wrapper}",
        ]
        result = subprocess.run(
            ["make", "--no-print-directory", "-f", str(makefile), "-f", str(secondary),
             "-E", "NATIVE_EVAL_FLAG:=1",
             "expansion-modern-text-inputs", *settings],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(warm_dependency.read_bytes(), warm_input)
        captured = json.loads(context.read_bytes())
        self.assertEqual(captured["kind"], "fe8-selected-text-inputs")
        self.assertEqual(counter.read_text().splitlines(), ["call"])
        inventory = subprocess.run(
            ["make", "--no-print-directory", "-f", str(makefile), "-f", str(secondary),
             "-E", "NATIVE_EVAL_FLAG:=1",
             "native-object-inventory", "MODERN_DRY_RUN=1", *settings],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(inventory.returncode, 0, inventory.stderr)
        self.assertEqual(
            {unit["object"] for unit in captured["units"]},
            set(inventory_path.read_text(encoding="utf-8").splitlines()),
        )
        units = {unit["logical_source"]: unit for unit in captured["units"]}
        self.assertIn("-DNATIVE_SLOT_ONLY=1", units["src/action_semantics.c"]["compiler_argv"])
        self.assertIn("-Iinclude with spaces", units["src/action_semantics.c"]["compiler_argv"])
        self.assertTrue(all("-DNATIVE_LATE_FLAG=1" in unit["compiler_argv"]
                            for unit in captured["units"]))
        self.assertTrue(all("-DNATIVE_SECOND_ENTRYPOINT=1" in unit["compiler_argv"]
                            for unit in captured["units"]))
        for flag in ("-DNATIVE_INVOKED_GOAL=1", "-DNATIVE_EVAL_FLAG=1"):
            with self.subTest(invocation_flag=flag):
                self.assertTrue(all(flag in unit["compiler_argv"] for unit in captured["units"]))
        self.assertIn(
            "-DNATIVE_DATA_LAYOUT=1", units["src/data/localized_font_data.c"]["compiler_argv"],
        )
        self.assertTrue(units["src/data/localized_font_data.c"]["preprocessor_argv"])
        for pattern in ("*.o", "*.pre.c", "*.text-input.json", "game_localization_*"):
            self.assertEqual(list(output.rglob(pattern)), [], pattern)
        for primary, other in (
            (["--file=" + str(makefile)], ["--makefile", str(secondary)]),
            (["-sf" + str(makefile)], ["--makefile=" + str(secondary)]),
        ):
            with self.subTest(entrypoint_options=(primary, other)):
                replayed = subprocess.run(
                    [
                        "make", "--no-print-directory", *primary, *other,
                        "-E", "NATIVE_EVAL_FLAG:=1", "expansion-modern-text-inputs", *settings,
                    ],
                    cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(replayed.returncode, 0, replayed.stdout + replayed.stderr)
                selected = json.loads(context.read_text(encoding="utf-8"))
                for flag in (
                    "-DNATIVE_SECOND_ENTRYPOINT=1", "-DNATIVE_EVAL_FLAG=1",
                    "-DNATIVE_INVOKED_GOAL=1", "-DNATIVE_SLOT_ONLY=1",
                ):
                    if flag == "-DNATIVE_SLOT_ONLY=1":
                        unit = next(row for row in selected["units"]
                                    if row["logical_source"] == "src/action_semantics.c")
                        self.assertIn(flag, unit["compiler_argv"])
                    else:
                        self.assertTrue(all(flag in unit["compiler_argv"] for unit in selected["units"]))
        self.assertEqual(counter.read_text().splitlines(), ["call", "call", "call"])
        from scripts.localization.game_catalog.compiled_inputs import (
            CompiledInputError, load_compile_context,
        )
        self.assertEqual(len(load_compile_context(context)["units"]), len(captured["units"]))
        for defect in ("duplicate-object", "wrong-output", "relative-root", "missing-input"):
            with self.subTest(context_defect=defect):
                invalid = json.loads(json.dumps(captured))
                if defect == "duplicate-object":
                    extra = dict(invalid["units"][0])
                    extra["logical_source"] = "src/duplicate_native_slot.c"
                    invalid["units"].append(extra)
                elif defect == "wrong-output":
                    invalid["units"][0]["object"] += ".wrong"
                elif defect == "relative-root":
                    invalid["source_root"] = "."
                else:
                    invalid["units"][0]["object"] = "-c"
                    invalid["units"][0]["compiler_argv"] = ["cc", "-g", "source.c", "-o", "-c"]
                context.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(CompiledInputError):
                    load_compile_context(context)

    def test_native_public_build_entrypoints_capture_only_c_without_execution(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-public-inputs-", dir=ROOT / "build") as directory:
            output = Path(directory)
            calls = output / "make-calls"
            executed = output / "unexpected-native-execution"
            make = output / "native-make"
            make.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' call >> {shlex.quote(str(calls))}\n"
                f'exec {shlex.quote(shutil.which("make"))} "$@"\n',
                encoding="ascii",
            )
            make.chmod(0o755)
            compiler = output / "no-native-execution"
            compiler.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' called >> {shlex.quote(str(executed))}\n"
                "exit 73\n",
                encoding="ascii",
            )
            compiler.chmod(0o755)
            entrypoint = self.project / "public-inputs.mk"
            for goal in (
                "expansion-modern-all", "expansion-modern-elf", "expansion-modern-rom",
                "expansion-modern-linker-check",
            ):
                with self.subTest(public_build_goal=goal):
                    selected_linker = output / (goal + "-selected-linker")
                    scope = "$(MODERN_ELF)" if goal == "expansion-modern-elf" else goal
                    private = "private " if goal == "expansion-modern-elf" else ""
                    entrypoint.write_text(
                        "include Makefile\n"
                        "expansion-modern-all expansion-modern-elf expansion-modern-rom "
                        "expansion-modern-linker-check: MODERN_CFLAGS += -fpack-struct=1\n"
                        f"{scope}: {private}MODERN_LD := {selected_linker}\n",
                        encoding="ascii",
                    )
                    calls.unlink(missing_ok=True)
                    build = output / goal
                    result = subprocess.run(
                        [
                            "make", "--no-print-directory", "-f", str(entrypoint),
                            "expansion-modern-text-inputs", f"MAKECMDGOALS={goal}",
                            "MODERN_DRY_RUN=1", "NODEP=1", f"MODERN_BUILD_ROOT={build}",
                            "MODERN_CONFIG=debug", "MODERN_ABI=aapcs", "MODERN_ROM_SIZE=32M",
                            "EXPANSION_ENABLED_LOCALES=en,ja", "FE8_PROJECT=default",
                            f"MODERN_CC={compiler}", f"MODERN_PREPROC={compiler}",
                            f"PYTHON={sys.executable}", f"MAKE={make}",
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=90,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    context = json.loads((build / "debug/aapcs/selected-text-inputs.json").read_text())
                    self.assertTrue(context["units"])
                    self.assertTrue(all(unit["logical_source"].endswith(".c") for unit in context["units"]))
                    self.assertTrue(all("-fpack-struct=1" in unit["compiler_argv"] for unit in context["units"]))
                    self.assertEqual(calls.read_text().splitlines(), ["call"])
                    self.assertFalse(executed.exists(), result.stdout + result.stderr)
                    if goal != "expansion-modern-all":
                        self.assertEqual(context["linker"], str(selected_linker))

            normal_calls = output / "normal-recursive-arguments"
            normal_make = output / "normal-recursive-make"
            normal_make.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$@\" > {shlex.quote(str(normal_calls))}\n",
                encoding="ascii",
            )
            normal_make.chmod(0o755)
            normal = subprocess.run(
                [
                    "make", "--no-print-directory", "-n",
                    "expansion-modern-hq-mixer-profile-rom", "NODEP=1",
                    f"MAKE={normal_make}", f"PYTHON={sys.executable}",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)
            normal_arguments = normal_calls.read_text().splitlines()
            self.assertEqual(normal_arguments[0], "expansion-modern-rom")
            self.assertIn("EXPANSION_HQ_MIXER=1", normal_arguments)

    def test_native_text_metrics_read_selected_private_font_objects(self):
        compiler = os.environ.get("MODERN_CC", "arm-none-eabi-gcc")
        if shutil.which(compiler) is None:
            self.skipTest("selected ARM compiler is unavailable")
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="native-font-metrics-", dir=build)
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name)
        headers = self.project / "headers"
        glyphs = headers / "src/data/fonts"
        glyphs.mkdir(parents=True)
        for number, style, width in ((1, "System", "SELECTED_FONT_WIDTH"), (2, "Talk", "11")):
            (glyphs / f"glyphs_{number}.h").write_text(
                f"struct Glyph sSelected{style} = {{0, 0, {width}, {{0}}}};\n"
                f"struct Glyph *TextGlyphs_{style}[128] = {{\n"
                f"    [63] = &sSelected{style}, [65] = &sSelected{style}\n"
                "};\n",
                encoding="ascii",
            )
        namespace = output / "debug/aapcs"
        makefile = self.project / "native-font.mk"
        makefile.write_text(
            "include Makefile\n"
            "ifeq ($(MAKELEVEL),0)\nifeq ($(origin MAKELEVEL),environment)\n"
            f"{namespace}/src/fontgrp-data.o: private MODERN_CFLAGS := "
            f"-I{headers} $(MODERN_CFLAGS) -DSELECTED_FONT_WIDTH=9\n"
            "endif\nendif\n",
            encoding="utf-8",
        )
        context = namespace / "selected-text-inputs.json"
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-f", str(makefile), "expansion-modern-text-inputs",
                "graphics/debug_font.4bpp.h", "NODEP=1",
                str(namespace / "src/fontgrp-data.o"), str(namespace / "src/fontgrp.o"),
                str(namespace / "src/data/localized_font_data.o"),
                f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                "FE8_PROJECT=default", "EXPANSION_ENABLED_LOCALES=en",
                f"PYTHON={sys.executable}",
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        from scripts.localization.game_catalog.compiled_fonts import measure_selected_fonts
        self.assertIn("-DSELECTED_FONT_WIDTH=9", next(
            unit for unit in json.loads(context.read_text())["units"]
            if unit["logical_source"] == "src/fontgrp-data.c"
        )["compiler_argv"])
        metrics = measure_selected_fonts(context, output_dir=output / "metrics")
        self.assertEqual(metrics["ascii"], {
            "system": {63: 9, 65: 9}, "talk": {63: 11, 65: 11},
        })
        self.assertEqual(metrics["localized"], {})
        self.assertEqual(list(output.rglob("localized_game_text_data.h")), [])
        self.assertEqual(list(output.rglob("game_localization_catalog.c")), [])
        from scripts.localization.game_catalog.control_streams import (
            ControlStreamError, load_talk_font_metrics, tokenize_payload,
            validate_talk_line_widths,
        )
        from scripts.localization.game_locales.fixed_width_labels import (
            ALIASES_PATH, SURFACES, _message_ids, build_fixed_width_label_metrics,
        )
        from scripts.localization.game_locales.width_contract import (
            TextWidthContractError, load_width_metrics, validate_payload_width,
        )
        with self.subTest(consumer="system-width"):
            selected = load_width_metrics(
                ROOT, locale="en", style="system", font_metrics=metrics,
            )
            with self.assertRaises(TextWidthContractError):
                validate_payload_width(
                    b"A" * 27 + b"\0", metrics=selected, max_pixels=240,
                    source_name="selected system glyphs",
                )
        with self.subTest(consumer="talk-width"):
            selected = load_talk_font_metrics("en", repo_root=ROOT, font_metrics=metrics)
            tokens = tokenize_payload(b"\x08" + b"A" * 23 + b"\0", source_name="selected Talk")
            with self.assertRaises(ControlStreamError):
                validate_talk_line_widths(tokens, source_name="selected Talk", metrics=selected)
        with self.subTest(consumer="fixed-labels"):
            aliases = json.loads((ROOT / ALIASES_PATH).read_text(encoding="utf-8"))
            aliases["aliases"] = {
                locale: {surface: {} for surface in SURFACES} for locale in ("ja", "zh-Hans")
            }
            aliases_path = output / "aliases.json"
            aliases_path.write_text(json.dumps(aliases), encoding="utf-8")
            labels = {
                target: "AAAA" for surface in SURFACES.values()
                for target in _message_ids(ROOT / surface["data_path"])
            }
            measured = build_fixed_width_label_metrics(
                ROOT, localized_payloads={"ja": labels}, aliases_path=aliases_path,
                font_metrics=metrics,
            )
            self.assertTrue(all(
                row["canonical_width"] == 36
                for surface in measured["locales"]["ja"]["surfaces"].values()
                for row in surface["records"]
            ))
        unusual = self.project / '描画\\font"source.c'
        copied = json.loads(context.read_text(encoding="utf-8"))
        selected = next(unit for unit in copied["units"] if unit["logical_source"] == "src/fontgrp.c")
        unusual.write_bytes((ROOT / selected["compiler_input"]).read_bytes())
        selected["selected_source"] = str(unusual)
        selected["compiler_input"] = str(unusual)
        selected["compiler_argv"][selected["compiler_argv"].index("-c") + 1] = str(unusual)
        unusual_context = output / "unusual-context.json"
        unusual_context.write_text(json.dumps(copied), encoding="utf-8")
        unusual_metrics = measure_selected_fonts(unusual_context, output_dir=output / "unusual-metrics")
        self.assertEqual(unusual_metrics["ascii"], metrics["ascii"])
        pseudo_command = [
            "EXPANSION_ENABLED_LOCALES=en,qps-ploc"
            if argument.startswith("EXPANSION_ENABLED_LOCALES=") else argument
            for argument in result.args
        ]
        pseudo = subprocess.run(
            [*pseudo_command, "EXPANSION_PSEUDO_LOCALE=1"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(pseudo.returncode, 0, pseudo.stdout + pseudo.stderr)
        pseudo_metrics = measure_selected_fonts(context, output_dir=output / "pseudo-metrics")
        self.assertEqual(pseudo_metrics["ascii"], metrics["ascii"])
        self.assertEqual(pseudo_metrics["localized"], {})
        self.assertEqual(pseudo_metrics["inputs"]["locale_mask"], 0x81)
        from scripts.localization.game_catalog.compiled_fonts import (
            CompiledFontError, read_ascii_widths,
        )
        wrong_machine = output / "wrong-machine.o"
        binary = (output / "metrics/selected-font-data.o").read_bytes()
        wrong_machine.write_bytes(binary[:18] + b"\x03\0" + binary[20:])
        with self.assertRaises(CompiledFontError):
            read_ascii_widths(
                wrong_machine, "TextGlyphs_System",
                layout_object=output / "metrics/selected-font-layout.o",
            )
        noop = output / "no-native-output"
        noop.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        noop.chmod(0o755)
        invalid = json.loads(context.read_text(encoding="utf-8"))
        renderer = next(unit for unit in invalid["units"] if unit["logical_source"] == "src/fontgrp.c")
        real_compiler = renderer["compiler_argv"][0]
        renderer["compiler_argv"][0] = str(noop)
        context.write_text(json.dumps(invalid), encoding="utf-8")
        for folder in ("no-measurement", "metrics"):
            with self.subTest(noop_compiler=folder), self.assertRaises(CompiledFontError):
                measure_selected_fonts(context, output_dir=output / folder)
        renderer["compiler_argv"][0] = real_compiler
        invalid["linker"] = str(noop)
        context.write_text(json.dumps(invalid), encoding="utf-8")
        for folder in ("no-link-output", "pseudo-metrics"):
            with self.subTest(noop_linker=folder), self.assertRaises(CompiledFontError):
                measure_selected_fonts(context, output_dir=output / folder)
        makefile.write_text(
            makefile.read_text(encoding="utf-8").replace("SELECTED_FONT_WIDTH=9", "SELECTED_FONT_WIDTH=10"),
            encoding="utf-8",
        )
        warm = subprocess.run(
            pseudo.args, cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(warm.returncode, 0, warm.stdout + warm.stderr)
        self.assertIn("-DSELECTED_FONT_WIDTH=10", next(
            unit for unit in json.loads(context.read_text())["units"]
            if unit["logical_source"] == "src/fontgrp-data.c"
        )["compiler_argv"])
        measured = measure_selected_fonts(context, output_dir=output / "warm-metrics")
        self.assertEqual(measured["ascii"], {
            "system": {63: 10, 65: 10}, "talk": {63: 11, 65: 11},
        })
        objects = {
            str(namespace / f"src/{name}.o")
            for name in ("fontgrp", "fontgrp-data", "data/localized_font_data")
        }
        inherited_command = [argument for argument in pseudo.args if argument not in objects]
        inherited_command.append("native-font-parent")
        for width in (12, 13):
            with self.subTest(inherited_compiler_width=width):
                makefile.write_text(
                    "include Makefile\n"
                    f"MODERN_CFLAGS := -I{headers} $(MODERN_CFLAGS)\n"
                    ".PHONY: native-font-parent\n"
                    f"native-font-parent: MODERN_CFLAGS += -DSELECTED_FONT_WIDTH={width}\n"
                    f"native-font-parent: {' '.join(sorted(objects))}\n",
                    encoding="utf-8",
                )
                rebuilt = subprocess.run(
                    inherited_command, cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
                inherited = measure_selected_fonts(context, output_dir=output / f"inherited-{width}")
                self.assertEqual(inherited["ascii"]["system"], {63: width, 65: width})
        default_command = [
            "EXPANSION_ENABLED_LOCALES=en,ja" if argument.startswith("EXPANSION_ENABLED_LOCALES=")
            else "EXPANSION_PSEUDO_LOCALE=0" if argument.startswith("EXPANSION_PSEUDO_LOCALE=")
            else argument
            for argument in inherited_command
            if argument not in (
                "expansion-modern-text-inputs", "graphics/debug_font.4bpp.h", "native-font-parent",
            )
        ]
        default_command.append("MODERN_ROM_SIZE=32M")
        for locales, pseudo, label, widths in (
            ("en,ja", "0", "default", (14, 15)),
            ("en", "0", "default-en", (17, 18)),
            ("en,qps-ploc", "1", "default-pseudo", (19, 20)),
        ):
            selected_command = [
                f"EXPANSION_ENABLED_LOCALES={locales}"
                if argument.startswith("EXPANSION_ENABLED_LOCALES=") else
                f"EXPANSION_PSEUDO_LOCALE={pseudo}"
                if argument.startswith("EXPANSION_PSEUDO_LOCALE=") else argument
                for argument in default_command
            ]
            for width in widths:
                with self.subTest(default_parent_width=width, locales=locales):
                    makefile.write_text(
                        "include Makefile\n"
                        f"MODERN_CFLAGS := -I{headers} $(MODERN_CFLAGS)\n"
                        "ifeq ($(strip $(MAKECMDGOALS)),)\n"
                        "MODERN_CFLAGS += -DNATIVE_EMPTY_GOALS=1\n"
                        "endif\n"
                        ".DEFAULT_GOAL := native-font-parent\n"
                        ".PHONY: native-font-parent\n"
                        f"native-font-parent: MODERN_CFLAGS += -DSELECTED_FONT_WIDTH={width}\n"
                        f"native-font-parent: expansion-modern-text-inputs {' '.join(sorted(objects))}\n",
                        encoding="utf-8",
                    )
                    rebuilt = subprocess.run(
                        selected_command, cwd=ROOT, capture_output=True, text=True, timeout=120,
                    )
                    self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
                    selected = json.loads(context.read_text())
                    for unit in selected["units"]:
                        self.assertIn("-DNATIVE_EMPTY_GOALS=1", unit["compiler_argv"])
                        if unit["logical_source"] in (
                            "src/fontgrp.c", "src/fontgrp-data.c", "src/data/localized_font_data.c",
                        ):
                            self.assertIn(f"-DSELECTED_FONT_WIDTH={width}", unit["compiler_argv"])
                    metrics_output = output / f"{label}-{width}"
                    measured = measure_selected_fonts(context, output_dir=metrics_output)
                    self.assertEqual(measured["ascii"]["system"], {63: width, 65: width})
                    self.assertEqual(
                        read_ascii_widths(
                            namespace / "src/fontgrp-data.o", "TextGlyphs_System",
                            layout_object=metrics_output / "selected-font-layout.o",
                        ),
                        {63: width, 65: width},
                    )
                    if width == widths[-1]:
                        before = (namespace / "src/fontgrp-data.o").stat().st_mtime_ns
                        unchanged = subprocess.run(
                            selected_command, cwd=ROOT, capture_output=True, text=True, timeout=120,
                        )
                        self.assertEqual(unchanged.returncode, 0, unchanged.stdout + unchanged.stderr)
                        self.assertEqual(
                            (namespace / "src/fontgrp-data.o").stat().st_mtime_ns, before,
                        )
        for locales, pseudo, label, widths in (
            ("en", "0", "inherited-en", (21, 22)),
            ("en,qps-ploc", "1", "inherited-pseudo", (23, 24)),
        ):
            selected_command = [
                f"EXPANSION_ENABLED_LOCALES={locales}"
                if argument.startswith("EXPANSION_ENABLED_LOCALES=") else
                f"EXPANSION_PSEUDO_LOCALE={pseudo}"
                if argument.startswith("EXPANSION_PSEUDO_LOCALE=") else argument
                for argument in default_command
            ]
            for width in widths:
                with self.subTest(inherited_parent_width=width, locales=locales):
                    makefile.write_text(
                        "include Makefile\n"
                        f"MODERN_CFLAGS := -I{headers} $(MODERN_CFLAGS)\n"
                        ".PHONY: native-font-parent\n"
                        f"native-font-parent: MODERN_CFLAGS += -DSELECTED_FONT_WIDTH={width}\n"
                        f"native-font-parent: expansion-modern-text-inputs {' '.join(sorted(objects))}\n",
                        encoding="utf-8",
                    )
                    rebuilt = subprocess.run(
                        [*selected_command, "native-font-parent"], cwd=ROOT,
                        capture_output=True, text=True, timeout=120,
                    )
                    self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
                    metrics_output = output / f"{label}-{width}"
                    measured = measure_selected_fonts(context, output_dir=metrics_output)
                    self.assertEqual(measured["ascii"]["system"], {63: width, 65: width})
                    self.assertEqual(
                        read_ascii_widths(
                            namespace / "src/fontgrp-data.o", "TextGlyphs_System",
                            layout_object=metrics_output / "selected-font-layout.o",
                        ),
                        {63: width, 65: width},
                    )
        direct_command = [
            "EXPANSION_ENABLED_LOCALES=en" if argument.startswith("EXPANSION_ENABLED_LOCALES=")
            else "EXPANSION_PSEUDO_LOCALE=0" if argument.startswith("EXPANSION_PSEUDO_LOCALE=")
            else argument for argument in default_command
        ]
        for width in (25, 26):
            with self.subTest(direct_object_width=width):
                makefile.write_text(
                    "include Makefile\n"
                    f"{namespace}/src/fontgrp-data.o: private MODERN_CFLAGS := "
                    f"-I{headers} $(MODERN_CFLAGS) -DSELECTED_FONT_WIDTH={width}\n",
                    encoding="utf-8",
                )
                rebuilt = subprocess.run(
                    [*direct_command, str(namespace / "src/fontgrp-data.o")],
                    cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
                metrics_output = output / f"direct-en-{width}"
                measured = measure_selected_fonts(context, output_dir=metrics_output)
                self.assertEqual(measured["ascii"]["system"], {63: width, 65: width})
                self.assertEqual(
                    read_ascii_widths(
                        namespace / "src/fontgrp-data.o", "TextGlyphs_System",
                        layout_object=metrics_output / "selected-font-layout.o",
                    ),
                    {63: width, 65: width},
                )

    def test_native_payload_preserves_selected_declarations_on_source_mismatch(self):
        if host_only_enabled() or shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")) is None:
            self.skipTest("native catalog/header agreement belongs to the target lane")
        source = (ROOT / "include/constants/msg.h").read_text()
        match = re.search(r"^#define MSG_COUNT (0x[0-9A-Fa-f]+|[0-9]+)$", source, re.MULTILINE)
        self.assertIsNotNone(match)
        count = int(match.group(1), 0) + 1
        selected = self.project / "selected-msg.h"
        selected.write_text(
            source[:match.start(1)] + hex(count) + source[match.end(1):], encoding="utf-8",
        )
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-selected-msg-", dir=ROOT / "build") as directory:
            output = Path(directory)
            header = output / "game-localization/generated/game_localization_declarations.h"
            command = [
                "make", "--no-print-directory", "NODEP=1", f"MODERN_BUILD_ROOT={output}",
                "MODERN_CONFIG=debug", "MODERN_ABI=aapcs", "MODERN_ROM_SIZE=32M",
                "FE8_PROJECT=default", "EXPANSION_ENABLED_LOCALES=en,ja",
                f"TEXT_HEADER={selected}", f"PYTHON={sys.executable}",
            ]
            declared = subprocess.run(
                [*command, "expansion-modern-game-localization-declarations"],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(declared.returncode, 0, declared.stdout + declared.stderr)
            assert_declaration_count(self, header, count)
            before = header.read_bytes()
            generated = subprocess.run(
                [*command, str(output / "debug/aapcs/src/localized_game_text.o"),
                 "game_localization_catalog.h"],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            self.assertNotEqual(generated.returncode, 0)
            self.assertIn("English source IDs do not match target range", generated.stderr)
            self.assertEqual(header.read_bytes(), before)
            self.assertFalse((header.parent / "game_localization_catalog.c").exists())
            assert_declaration_count(self, header, count)

    def test_native_warm_presplit_dependencies_use_rebuilt_font_metrics(self):
        if shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")) is None:
            self.skipTest("selected ARM compiler is unavailable")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-warm-font-", dir=ROOT / "build") as directory:
            output = Path(directory)
            namespace = output / "debug/aapcs"
            headers = output / "headers"
            glyph = headers / "src/data/fonts/glyphs_1.h"
            glyph.parent.mkdir(parents=True)
            original = (ROOT / "src/data/fonts/glyphs_1.h").read_text(encoding="utf-8")
            before, separator, after = original.partition("struct Glyph *TextGlyphs_System[] =\n{\n    NULL,")
            self.assertTrue(separator)

            def glyph_source(width):
                return (
                    before + f"struct Glyph sWarmProbe = {{0, 0, {width}, {{0}}}};\n"
                    + "struct Glyph *TextGlyphs_System[] =\n{\n    &sWarmProbe," + after
                )

            glyph.write_text(glyph_source(9), encoding="utf-8")
            font_object = namespace / "src/fontgrp-data.o"
            makefile = self.project / "warm-font.mk"
            makefile.write_text(
                "include Makefile\n"
                f"{font_object}: private MODERN_CFLAGS := -I{headers} $(MODERN_CFLAGS)\n",
                encoding="utf-8",
            )
            observed = output / "measured-widths.jsonl"
            python = output / "python3"
            python.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "if sys.argv[1:4] != ['-m', 'scripts.localization.game_catalog', 'generate']:\n"
                f"    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                "from scripts.localization.game_catalog import cli\n"
                "original = cli.measure_selected_fonts\n"
                "def measured(context, *, output_dir):\n"
                "    result = original(context, output_dir=output_dir)\n"
                f"    with open({str(observed)!r}, 'a') as stream:\n"
                "        stream.write(json.dumps(result['ascii']['system'].get(0)) + '\\n')\n"
                "    return result\n"
                "cli.measure_selected_fonts = measured\n"
                "raise SystemExit(cli.main(sys.argv[3:]))\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            catalog = output / "game-localization/generated/game_localization_catalog.c"
            command = [
                "make", "--no-print-directory", "-j2", "-f", str(makefile), "NODEP=1",
                str(catalog), f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs", "MODERN_ROM_SIZE=32M", "FE8_PROJECT=default",
                "EXPANSION_ENABLED_LOCALES=en,ja", f"PYTHON={python}",
                f"MODERN_ALL_SOURCE_GOALS={catalog} {font_object}",
                f"MODERN_ALL_C_HEADER_DEPS={font_object.with_suffix('.headers.d')}",
                "MODERN_ALL_DATA_ASSET_DEPS=",
            ]
            cold = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(cold.returncode, 0, cold.stdout + cold.stderr)
            self.assertEqual([json.loads(line) for line in observed.read_text().splitlines()], [9])
            warm_command = list(command)
            warm_command.insert(warm_command.index(str(catalog)), str(font_object))
            inherited_command = [
                argument for argument in command if argument != str(catalog)
            ] + ["expansion-modern-text-inputs", "native-warm-parent"]
            for width, edge in (
                (10, str(catalog.parent / "localized_game_text_data.h")),
                (11, "localized_game_text_data.h"),
            ):
                with self.subTest(presplit_header=edge):
                    dependency = font_object.with_suffix(".d")
                    dependency.write_text(
                        f"{font_object}: {edge}\n" + dependency.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
                    font_object.with_suffix(".headers.d").write_text(
                        f"{font_object}: {edge} {glyph}\n", encoding="utf-8",
                    )
                    if width == 11:
                        makefile.write_text(
                            "include Makefile\n"
                            ".PHONY: native-warm-parent\n"
                            "native-warm-parent: MODERN_CFLAGS := "
                            f"-I{headers} -DNATIVE_WARM_WIDTH={width} $(MODERN_CFLAGS)\n"
                            f"native-warm-parent: {font_object} {catalog}\n",
                            encoding="utf-8",
                        )
                        glyph.write_text(glyph_source("NATIVE_WARM_WIDTH"), encoding="utf-8")
                    else:
                        glyph.write_text(glyph_source(width), encoding="utf-8")
                    observed.unlink()
                    built = subprocess.run(
                        inherited_command if width == 11 else warm_command,
                        cwd=ROOT, capture_output=True, text=True, timeout=180,
                    )
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    widths = [json.loads(line) for line in observed.read_text().splitlines()]
                    self.assertTrue(widths)
                    self.assertEqual(set(widths), {width})
                    if width == 11:
                        context = json.loads(
                            (namespace / "selected-text-inputs.json").read_text(),
                        )
                        selected = next(
                            unit for unit in context["units"]
                            if unit["logical_source"] == "src/fontgrp-data.c"
                        )
                        self.assertIn("-DNATIVE_WARM_WIDTH=11", selected["compiler_argv"])
            glyph.write_text(glyph_source(12), encoding="utf-8")
            observed.unlink()
            built = subprocess.run(
                inherited_command, cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            self.assertEqual(
                set(json.loads(line) for line in observed.read_text().splitlines()), {12},
            )

    def test_native_cleanup_does_not_import_the_catalog_migrator(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-cleanup-", dir=ROOT / "build") as directory:
            output = Path(directory)
            build = output / "owned"
            dependency = build / "debug/aapcs/src/fontgrp-data.d"
            dependency.parent.mkdir(parents=True)
            dependency.write_text(
                f"{dependency.with_suffix('.o')}: localized_game_text_data.h\n",
                encoding="ascii",
            )
            python = output / "no-catalog-migration"
            python.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "if sys.argv[1:4] == ['-m', 'scripts.localization.game_catalog', "
                "'prune-native-font-dependencies']:\n"
                "    print('catalog helper is unavailable', file=sys.stderr)\n"
                "    raise SystemExit(79)\n"
                f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
                encoding="ascii",
            )
            python.chmod(0o755)
            cleaned = subprocess.run(
                [
                    "make", "--no-print-directory", "expansion-modern-clean", "NODEP=1",
                    f"MODERN_BUILD_ROOT={build}", "MODERN_CONFIG=debug", "MODERN_ABI=aapcs",
                    "FE8_PROJECT=default", "EXPANSION_ENABLED_LOCALES=en,ja",
                    f"PYTHON={python}",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stdout + cleaned.stderr)
            self.assertFalse(build.exists())

    def test_native_archival_requests_preserve_warm_modern_dependencies(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-archival-", dir=ROOT / "build") as directory:
            output = Path(directory)
            build = output / "owned"
            dependency = build / "debug/aapcs/src/fontgrp-data.d"
            dependency.parent.mkdir(parents=True)
            dependency.write_text(
                f"{dependency.with_suffix('.o')}: localized_game_text_data.h\n",
                encoding="ascii",
            )
            before = dependency.read_bytes()
            before_stat = dependency.stat()
            calls = output / "make-calls"
            sentinel = output / "unexpected-native-execution"
            python = output / "no-catalog-migration"
            python.write_text(
                f"#!{sys.executable}\nimport os, sys\n"
                "if sys.argv[1:4] == ['-m', 'scripts.localization.game_catalog', "
                "'prune-native-font-dependencies']:\n"
                f"    open({str(calls)!r}, 'a').write(repr(sys.argv[1:]) + '\\n')\n"
                f"    open({str(sentinel)!r}, 'w').write('called')\n"
                "    raise SystemExit(79)\n"
                f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
                encoding="ascii",
            )
            python.chmod(0o755)
            command = [
                "make", "--no-print-directory", "NODEP=1", f"MODERN_BUILD_ROOT={build}",
                "FE8_PROJECT=default", "MODERN_CONFIG=debug", f"PYTHON={python}",
            ]
            database = subprocess.run(
                [*command, "-pn", "expansion-modern-toolchain-check"],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(database.returncode, 0, database.stdout + database.stderr)
            known = make_database_variable(database.stdout, "ARCHIVAL_KNOWN_GOALS").split()
            objects = make_database_variable(database.stdout, "ALL_OBJECTS").split()
            self.assertIn("legacy", known)
            self.assertTrue(objects)
            for label, goals in (("canonical-goals", known), ("canonical-objects", objects)):
                with self.subTest(archival_request=label):
                    calls.write_text("")
                    queried = subprocess.run(
                        [*command, "-q", *(f"--old-file={goal}" for goal in goals), *goals],
                        cwd=ROOT, capture_output=True, text=True, timeout=60,
                    )
                    self.assertIn(queried.returncode, (0, 1), queried.stdout + queried.stderr)
                    self.assertEqual(calls.read_text(), "")
                    self.assertFalse(sentinel.exists())
                    self.assertEqual(dependency.read_bytes(), before)
                    after = dependency.stat()
                    self.assertEqual(
                        (after.st_ino, after.st_mtime_ns),
                        (before_stat.st_ino, before_stat.st_mtime_ns),
                    )
            calls.write_text("")
            mixed = subprocess.run(
                [*command, "-q", "--old-file=fireemblem8.gba", "fireemblem8.gba",
                 str(dependency.with_suffix(".o"))],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(mixed.returncode, 2, mixed.stdout + mixed.stderr)
            self.assertTrue(sentinel.exists())
            self.assertIn("prune-native-font-dependencies", calls.read_text())
            self.assertIn("Native font dependency migration failed", mixed.stderr)

    def test_native_english_object_does_not_force_declarations(self):
        if host_only_enabled() or shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")) is None:
            self.skipTest("native compile/no-op proof belongs to the target lane")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-en-object-", dir=ROOT / "build") as directory:
            temporary = Path(directory)
            python = temporary / "no-unused-declarations"
            python.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "if sys.argv[1:4] == ['-m', 'scripts.localization.game_catalog', 'declare']:\n"
                "    print('unused declaration generation', file=sys.stderr)\n"
                "    raise SystemExit(78)\n"
                f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
                encoding="ascii",
            )
            python.chmod(0o755)
            for locales, pseudo in (("en", "0"), ("en,qps-ploc", "1")):
                with self.subTest(locales=locales):
                    output = temporary / pseudo
                    object_path = output / "debug/aapcs/src/localized_game_text.o"
                    command = [
                        "make", "--no-print-directory", str(object_path), "NODEP=1",
                        f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                        "MODERN_ABI=aapcs", "MODERN_ROM_SIZE=32M", "FE8_PROJECT=default",
                        f"EXPANSION_ENABLED_LOCALES={locales}",
                        f"EXPANSION_PSEUDO_LOCALE={pseudo}", f"PYTHON={python}",
                    ]
                    built = subprocess.run(
                        command, cwd=ROOT, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    self.assertTrue(object_path.is_file())
                    before = object_path.stat().st_mtime_ns
                    repeated = subprocess.run(
                        command, cwd=ROOT, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
                    self.assertEqual(object_path.stat().st_mtime_ns, before)
                    self.assertEqual(list(output.rglob("game_localization_declarations.h")), [])

    def test_native_catalog_tracks_selected_raw_font_assets_for_direct_and_parent_goals(self):
        if host_only_enabled() or shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")) is None:
            self.skipTest("native selected-asset proof belongs to the target lane")
        source_asset = ROOT / "graphics/fonts/cjk/ja.system.widths.u8"
        original = source_asset.read_bytes()
        index = max(range(len(original)), key=original.__getitem__)
        self.assertGreaterEqual(original[index], 3)
        scalars = (ROOT / "graphics/fonts/cjk/ja.system.codepoints.u32le").read_bytes()
        scalar = int.from_bytes(scalars[index * 4:index * 4 + 4], "little")
        asset = self.project / "system.widths.u8"
        asset.write_bytes(original)
        latin = self.project / "latin.c"
        latin.write_text((ROOT / "src/fontgrp-data.c").read_text(), encoding="utf-8")
        regional = self.project / "regional.c"
        regional.write_text(
            (ROOT / "src/data/localized_font_data.c").read_text().replace(
                "graphics/fonts/cjk/ja.system.widths.u8", str(asset.relative_to(ROOT)),
            ),
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="ascii") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := fonts\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_FONT_REPLACEMENTS := src/fontgrp-data.c={latin.relative_to(ROOT)} "
                f"src/data/localized_font_data.c={regional.relative_to(ROOT)}\n",
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        observed = self.project / "native-asset-widths.jsonl"
        python = self.project / "record-native-metric"
        python.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "if sys.argv[1:4] != ['-m', 'scripts.localization.game_catalog', 'generate']:\n"
            f"    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from scripts.localization.game_catalog import cli\n"
            "original = cli.measure_selected_fonts\n"
            "def measured(context, *, output_dir):\n"
            "    result = original(context, output_dir=output_dir)\n"
            f"    with open({str(observed)!r}, 'a') as stream:\n"
            "        stream.write(json.dumps(result['localized']['ja']['system']"
            f"[{scalar}]) + '\\n')\n"
            "    return result\n"
            "cli.measure_selected_fonts = measured\n"
            "raise SystemExit(cli.main(sys.argv[3:]))\n",
            encoding="utf-8",
        )
        python.chmod(0o755)
        makefile = self.project / "native-assets.mk"
        makefile.write_text(
            "include Makefile\n"
            ".PHONY: native-assets-parent\n"
            "native-assets-parent: $(MODERN_GAME_LOCALIZATION_C)\n",
            encoding="ascii",
        )
        command = [
            "make", "--no-print-directory", "-f", str(makefile), "NODEP=1",
            f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=debug",
            "EXPANSION_ENABLED_LOCALES=en,ja", f"PYTHON={python}",
        ]
        for change, goal in enumerate((
            "game_localization_catalog.h", "localized_game_text_data.h", "native-assets-parent",
        )):
            with self.subTest(direct_or_inherited_goal=goal):
                current = bytearray(original)
                current[index] -= change
                asset.write_bytes(current)
                observed.unlink(missing_ok=True)
                built = subprocess.run(
                    [*command, *(["--output-sync=line"] if change == 1 else ["-j2"]), goal],
                    cwd=ROOT, capture_output=True, text=True, timeout=180,
                )
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                widths = [json.loads(line) for line in observed.read_text().splitlines()]
                self.assertTrue(widths)
                self.assertEqual(set(widths), {current[index]})

    def test_native_default_catalog_header_aliases_resolve_cjk_locales(self):
        if host_only_enabled() or shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")) is None:
            self.skipTest("native catalog header proof belongs to the target lane")
        from scripts.localization.game_catalog.compiled_fonts import measure_selected_fonts

        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-default-headers-", dir=ROOT / "build") as directory:
            makefile = Path(directory) / "parent.mk"
            makefile.write_text(
                "include Makefile\n"
                ".PHONY: native-header-parent\n"
                "native-header-parent: $(MODERN_GAME_LOCALIZATION_C)\n",
                encoding="ascii",
            )
            for locale in ("ja", "zh-Hans"):
                for index, goal in enumerate((
                    "game_localization_catalog.h", "localized_game_text_data.h", "native-header-parent",
                )):
                    with self.subTest(locale=locale, goal=goal):
                        output = Path(directory) / locale / str(index)
                        built = subprocess.run(
                            [
                                "make", "--no-print-directory", "-f", str(makefile), goal, "NODEP=1",
                                f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                                "MODERN_ABI=aapcs", "MODERN_ROM_SIZE=32M", "FE8_PROJECT=default",
                                f"EXPANSION_ENABLED_LOCALES=en,{locale}", f"PYTHON={sys.executable}",
                            ],
                            cwd=ROOT, capture_output=True, text=True, timeout=180,
                        )
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        context = output / "debug/aapcs/selected-text-inputs.json"
                        metrics = measure_selected_fonts(context, output_dir=output / "metrics")
                        for style in ("system", "talk"):
                            codepoints = (
                                ROOT / f"graphics/fonts/cjk/{locale}.{style}.codepoints.u32le"
                            ).read_bytes()
                            widths = (ROOT / f"graphics/fonts/cjk/{locale}.{style}.widths.u8").read_bytes()
                            expected = {
                                int.from_bytes(codepoints[index * 4:index * 4 + 4], "little"): width
                                for index, width in enumerate(widths)
                            }
                            self.assertEqual(metrics["localized"][locale][style], expected)
                        generated = output / "game-localization/generated"
                        self.assertTrue((generated / "game_localization_catalog.h").is_file())
                        self.assertTrue((generated / "localized_game_text_data.h").is_file())

    def test_native_cjk_fonts_bootstrap_without_payload_generation(self):
        if shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")) is None:
            self.skipTest("selected ARM compiler is unavailable")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-cjk-bootstrap-", dir=ROOT / "build") as directory:
            output = Path(directory)
            namespace = output / "debug/aapcs"
            context = namespace / "selected-text-inputs.json"
            python = output / "python3"
            python.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = -m ] && [ "$2" = scripts.localization.game_catalog ] '
                '&& [ "$3" = generate ]; then\n'
                '    echo "payload generation requested during font bootstrap" >&2\n'
                "    exit 97\n"
                "fi\n"
                f'exec {shlex.quote(sys.executable)} "$@"\n',
                encoding="ascii",
            )
            python.chmod(0o755)
            result = subprocess.run(
                [
                    "make", "--no-print-directory", str(context),
                    "graphics/debug_font.4bpp.h",
                    str(namespace / "src/fontgrp-data.o"), str(namespace / "src/fontgrp.o"),
                    str(namespace / "src/data/localized_font_data.o"),
                    str(namespace / "src/localized_game_text.o"),
                    str(namespace / "src/msg.o"),
                    f"MODERN_BUILD_ROOT={output}", "MODERN_CONFIG=debug",
                    "FE8_PROJECT=default", "EXPANSION_ENABLED_LOCALES=en,ja",
                    "MODERN_ROM_SIZE=32M", "NODEP=1", f"PYTHON={python}",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
                env={**os.environ, "PATH": str(output) + os.pathsep + os.environ["PATH"]},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            from scripts.localization.game_catalog.compiled_fonts import measure_selected_fonts
            metrics = measure_selected_fonts(context, output_dir=output / "metrics")
            for style in ("system", "talk"):
                with self.subTest(style=style):
                    codepoints = (ROOT / f"graphics/fonts/cjk/ja.{style}.codepoints.u32le").read_bytes()
                    widths = (ROOT / f"graphics/fonts/cjk/ja.{style}.widths.u8").read_bytes()
                    expected = {
                        int.from_bytes(codepoints[index * 4:index * 4 + 4], "little"): width
                        for index, width in enumerate(widths)
                    }
                    self.assertEqual(metrics["localized"]["ja"][style], expected)
            self.assertEqual(list(output.rglob("game_localization_catalog.c")), [])
            self.assertEqual(list(output.rglob("localized_game_text_data.h")), [])

    def test_selected_regional_metrics_and_cross_slot_relocations_are_native(self):
        if shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")) is None:
            self.skipTest("selected ARM compiler is unavailable")
        latin = self.project / "latin.c"
        latin.write_text(
            '#include "global.h"\n#include "fontgrp.h"\n'
            "extern struct Glyph gCrossSystemGlyph;\n"
            "struct Glyph gPrivateTalkGlyph = {0, 0, 11, {0}};\n"
            "struct Glyph *TextGlyphs_System[128] = {[65] = &gCrossSystemGlyph};\n"
            "struct Glyph *TextGlyphs_Talk[128] = {[65] = &gPrivateTalkGlyph};\n",
            encoding="ascii",
        )
        regional = self.project / "regional.c"
        source = (
            '#include "global.h"\n#include "fontgrp.h"\n#include "localized_font.h"\n'
            "struct Glyph gCrossSystemGlyph = {0, 0, 9, {0}};\n"
        )
        for stem, scalars, widths in (
            ("Ja", (0x4E00, 0x4E8C), ("PRIVATE_SYSTEM_WIDTH", "7")),
            ("Eu", (0xE9, 0xE9), ("5", "6")),
        ):
            for style, scalar, width in zip(("System", "Talk"), scalars, widths):
                name = "gLocalizedFont" + stem + style
                values = ", ".join(str(byte) for byte in scalar.to_bytes(4, "little"))
                source += (
                    f"const u8 {name}Codepoints[] = {{{values}}};\n"
                    f"const u8 {name}Widths[] = {{{width}}};\n"
                    f"const u8 {name}Bitmaps[LOCALIZED_FONT_BITMAP_STRIDE] = {{0}};\n"
                    f"const u32 {name}GlyphCount = 1;\n"
                )
        regional.write_text(source, encoding="ascii")
        with (self.project / "project.mk").open("a", encoding="ascii") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := fonts\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_FONT_REPLACEMENTS := src/fontgrp-data.c={latin.relative_to(ROOT)} "
                f"src/data/localized_font_data.c={regional.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        namespace = output / "primary/debug/aapcs"
        makefile = self.project / "regional.mk"
        makefile.write_text(
            "include Makefile\n"
            f"{(namespace / 'src/data/localized_font_data.o').relative_to(ROOT)}: "
            "private MODERN_CFLAGS += -DPRIVATE_SYSTEM_WIDTH=13\n",
            encoding="ascii",
        )
        objects = [namespace / f"src/{name}.o" for name in (
            "fontgrp", "fontgrp-data", "data/localized_font_data",
        )]
        command = [
            "make", "--no-print-directory", "-f", str(makefile), "NODEP=1",
            f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=debug",
            "EXPANSION_ENABLED_LOCALES=en,ja,fr", f"PYTHON={sys.executable}",
        ]
        compiled = subprocess.run(
            [*command, "expansion-modern-text-inputs",
             *(str(path.relative_to(ROOT)) for path in objects)],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        from scripts.localization.game_catalog.compiled_fonts import (
            CompiledFontError, measure_selected_fonts,
        )
        context = namespace / "selected-text-inputs.json"
        metrics = measure_selected_fonts(context, output_dir=namespace / "metrics")
        self.assertEqual(metrics["ascii"], {"system": {65: 9}, "talk": {65: 11}})
        self.assertEqual(metrics["localized"], {
            "ja": {"system": {0x4E00: 13}, "talk": {0x4E8C: 7}},
            "fr": {"system": {0xE9: 5}, "talk": {0xE9: 6}},
        })
        from scripts.localization.game_locales.width_contract import (
            TextWidthContractError, load_width_metrics, validate_payload_width,
        )
        system = load_width_metrics(ROOT, locale="ja", style="system", font_metrics=metrics)
        for count, succeeds in ((3, True), (4, False)):
            payload = ("一" * count).encode("utf-8") + b"\0"
            if succeeds:
                self.assertEqual(validate_payload_width(
                    payload, metrics=system, max_pixels=40, source_name="native JA",
                )["max_line_width"], 39)
            else:
                with self.assertRaises(TextWidthContractError):
                    validate_payload_width(
                        payload, metrics=system, max_pixels=40, source_name="native JA",
                    )
        from scripts.localization.game_catalog.control_streams import (
            ControlStreamError, load_talk_font_metrics, tokenize_payload, validate_talk_line_widths,
        )
        talk = load_talk_font_metrics("ja", repo_root=ROOT, font_metrics=metrics)
        for count, succeeds in ((34, True), (35, False)):
            tokens = tokenize_payload(
                b"\x08" + ("二" * count).encode("utf-8") + b"\0", source_name="native JA Talk",
            )
            if succeeds:
                self.assertEqual(validate_talk_line_widths(
                    tokens, source_name="native JA Talk", metrics=talk,
                )["max_talk_line_width"], 238)
            else:
                with self.assertRaises(ControlStreamError):
                    validate_talk_line_widths(tokens, source_name="native JA Talk", metrics=talk)
        from scripts.localization.game_locales.fixed_width_labels import (
            ALIASES_PATH, SURFACES, FixedWidthLabelError, _message_ids, build_fixed_width_label_metrics,
        )
        aliases = json.loads((ROOT / ALIASES_PATH).read_text(encoding="utf-8"))
        aliases["aliases"] = {
            locale: {surface: {} for surface in SURFACES} for locale in ("ja", "zh-Hans")
        }
        aliases_path = self.project / "native-aliases.json"
        aliases_path.write_text(json.dumps(aliases), encoding="utf-8")
        labels = {
            target: "一" * 3 for surface in SURFACES.values()
            for target in _message_ids(ROOT / surface["data_path"])
        }
        fixed = build_fixed_width_label_metrics(
            ROOT, localized_payloads={"ja": labels}, aliases_path=aliases_path, font_metrics=metrics,
        )
        self.assertTrue(all(
            row["canonical_width"] == 39
            for surface in fixed["locales"]["ja"]["surfaces"].values() for row in surface["records"]
        ))
        with self.assertRaises(FixedWidthLabelError):
            build_fixed_width_label_metrics(
                ROOT, localized_payloads={"ja": {target: "一" * 4 for target in labels}},
                aliases_path=aliases_path, font_metrics=metrics,
            )
        for defect, invalid in (
            ("count", source.replace("gLocalizedFontJaSystemGlyphCount = 1", "gLocalizedFontJaSystemGlyphCount = 2")),
            ("width", source.replace("{PRIVATE_SYSTEM_WIDTH}", "{0}")),
            ("scalar", source.replace("{0, 78, 0, 0}", "{0, 216, 0, 0}")),
            ("bitmap", source.replace("[LOCALIZED_FONT_BITMAP_STRIDE]", "[LOCALIZED_FONT_BITMAP_STRIDE - 1]", 1)),
            ("missing-symbol", source.replace("const u32 gLocalizedFontJaSystemGlyphCount = 1;\n", "")),
        ):
            with self.subTest(native_defect=defect):
                regional.write_text(invalid, encoding="ascii")
                rebuilt = subprocess.run(
                    [*command, str(objects[2].relative_to(ROOT))],
                    cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
                with self.assertRaises(CompiledFontError):
                    measure_selected_fonts(context, output_dir=namespace / defect)
        regional.write_text(source, encoding="ascii")
        preprocessor = self.project / "regional-preprocessor"
        preprocessor.write_text(
            f"#!{sys.executable}\n"
            "import subprocess, sys\n"
            f"result = subprocess.run([{str(ROOT / 'tools/preproc/preproc')!r}, *sys.argv[2:]], "
            "capture_output=True, text=True)\n"
            "sys.stderr.write(result.stderr)\n"
            "if result.returncode:\n"
            "    raise SystemExit(result.returncode)\n"
            'sys.stdout.write(result.stdout.replace("PRIVATE_SYSTEM_WIDTH", sys.argv[1]))\n',
            encoding="utf-8",
        )
        preprocessor.chmod(0o755)
        preprocessed = (namespace / "src/data/localized_font_data.pre.c").relative_to(ROOT)
        configured = (
            makefile.read_text(encoding="ascii")
            + f"MODERN_PREPROC := {preprocessor}\n"
            + f"{preprocessed}: private MODERN_PREPROC_FLAGS := 13 $(MODERN_PREPROC_FLAGS)\n"
        )
        makefile.write_text(configured, encoding="utf-8")
        rebuild = [*command, "expansion-modern-text-inputs",
                   *(str(path.relative_to(ROOT)) for path in objects)]
        for width in (13, 14):
            with self.subTest(private_preprocessor_width=width):
                makefile.write_text(
                    configured.replace("MODERN_PREPROC_FLAGS := 13 ", f"MODERN_PREPROC_FLAGS := {width} "),
                    encoding="utf-8",
                )
                rebuilt = subprocess.run(
                    rebuild, cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
                measured = measure_selected_fonts(context, output_dir=namespace / f"preprocessor-{width}")
                self.assertEqual(measured["localized"]["ja"]["system"], {0x4E00: width})
        inherited = "\n".join(
            line for line in configured.splitlines() if not line.startswith(f"{preprocessed}:")
        ) + "\n"
        relative_objects = {str(path.relative_to(ROOT)) for path in objects}
        inherited_command = [argument for argument in rebuild if argument not in relative_objects]
        inherited_command.append("native-preprocessor-parent")
        for width in (15, 16):
            with self.subTest(inherited_preprocessor_width=width):
                makefile.write_text(
                    inherited
                    + ".PHONY: native-preprocessor-parent\n"
                    + f"native-preprocessor-parent: MODERN_PREPROC_FLAGS := {width} $(MODERN_PREPROC_FLAGS)\n"
                    + f"native-preprocessor-parent: {' '.join(sorted(relative_objects))}\n",
                    encoding="utf-8",
                )
                rebuilt = subprocess.run(
                    inherited_command, cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
                measured = measure_selected_fonts(context, output_dir=namespace / f"inherited-preprocessor-{width}")
                self.assertEqual(measured["localized"]["ja"]["system"], {0x4E00: width})
        self.assertEqual(list(output.rglob("game_localization_catalog.c")), [])
        self.assertEqual(list(output.rglob("localized_game_text_data.h")), [])

    def test_project_default_profile_supplies_its_effective_settings(self):
        identity = ec.load_identity(
            ROOT / "config.mk",
            config_preset="release",
            abi="aapcs",
            rom_size=None,
            repo_root=ROOT,
            project=self.project.name,
        )
        self.assertEqual(identity.rom_title, "SELECTEDGAME")
        self.assertEqual(identity.rom_size_bytes, 32 * 1024 * 1024)
        self.assertEqual(identity.project_profile, "primary")

    def test_english_only_standalone_translation_targets_do_not_forward_empty_locales(self):
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        command = [
            "make", "--no-print-directory", "-s", "NODEP=1",
            f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release", "MODERN_ABI=aapcs",
            "PYTHON=" + sys.executable, "PYTHON3=" + sys.executable,
        ]
        goals = [
            "game-localization-validate", "game-localization-generate", "game-localization-check",
            "game-localization-width-check", "game-localization-leakage-audit",
            "game-localization-leakage-check", "game-localization-budget",
        ]
        result = subprocess.run(
            [*command, *goals], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((output / "primary/release/aapcs/generated/game-localization").exists())
        for locale, expected in (("ja", 0), ("unknown", 2)):
            with self.subTest(explicit_locale=locale):
                result = subprocess.run(
                    [*command, "GAME_LOCALIZATION_ENABLED_LOCALES=" + locale,
                     "game-localization-validate"],
                    cwd=ROOT, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REQUIRED_SETTINGS += EXPANSION_ENABLED_LOCALES\n"
                "FE8_PROJECT_primary_EXPANSION_ENABLED_LOCALES := en,ja\n"
            )
        result = subprocess.run(
            [*command, "GAME_LOCALIZATION_ENABLED_LOCALES=", "game-localization-validate"],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_explicit_overrides_cannot_contradict_the_selected_profile(self):
        for overrides in ({"rom_title": "OTHER"}, {"rom_size": "16M"}):
            with self.subTest(overrides=overrides):
                settings = {
                    "config_preset": "release",
                    "abi": "aapcs",
                    "rom_size": None,
                    "repo_root": ROOT,
                    "project": self.project.name,
                    **overrides,
                }
                with self.assertRaises(ec.ConfigError):
                    ec.load_identity(ROOT / "config.mk", **settings)

    def test_project_profile_supplies_locale_and_save_contract_settings(self):
        declaration = self.project / "project.mk"
        with declaration.open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REQUIRED_SETTINGS += EXPANSION_ENABLED_LOCALES "
                "EXPANSION_DEFAULT_LOCALE EXPANSION_SAVE_COMPAT_EPOCH\n"
                "FE8_PROJECT_primary_EXPANSION_ENABLED_LOCALES := ja,en\n"
                "FE8_PROJECT_primary_EXPANSION_DEFAULT_LOCALE := ja\n"
                "FE8_PROJECT_primary_EXPANSION_SAVE_COMPAT_EPOCH := 256\n"
            )
        identity = ec.load_identity(
            ROOT / "config.mk",
            config_preset="release",
            abi="aapcs",
            rom_size=None,
            repo_root=ROOT,
            project=self.project.name,
        )
        self.assertEqual(identity.enabled_locales, ("en", "ja"))
        self.assertEqual(identity.default_locale, "ja")
        self.assertEqual(identity.save_compat_epoch, 256)
        equivalent = ec.load_identity(
            ROOT / "config.mk", config_preset="release", abi="aapcs",
            rom_size="32M", repo_root=ROOT, project=self.project.name,
            enabled_locales="en,ja", default_locale="ja", save_compat_epoch="0x100",
        )
        self.assertEqual(identity.config_fingerprint, equivalent.config_fingerprint)

    def test_unsupported_profile_settings_fail_in_the_declaration_frontend(self):
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REQUIRED_SETTINGS += FE8_ITEM_ID_CAP\n"
                "FE8_PROJECT_primary_FE8_ITEM_ID_CAP := 0xCE\n"
            )
        rejected = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "-f", "project_selection.mk",
                f"FE8_PROJECT={self.project.name}", "project-selection-config",
            ],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unsupported project setting", rejected.stderr)

    def test_standalone_producers_enforce_normalized_profile_requirements(self):
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REQUIRED_SETTINGS += EXPANSION_ENABLED_LOCALES "
                "EXPANSION_DEFAULT_LOCALE EXPANSION_SAVE_COMPAT_EPOCH\n"
                "FE8_PROJECT_primary_EXPANSION_ENABLED_LOCALES := en,fr\n"
                "FE8_PROJECT_primary_EXPANSION_DEFAULT_LOCALE := en\n"
                "FE8_PROJECT_primary_EXPANSION_SAVE_COMPAT_EPOCH := 256\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        base = [
            "make", "--no-print-directory", "-s", "NODEP=1",
            f"FE8_PROJECT={self.project.name}",
        ]
        for goal in (
            "generated-data-manifest", "localization-generate",
            "assets-validate", "game-localization-generate",
        ):
            with self.subTest(goal=goal):
                rejected = subprocess.run(
                    [*base, "EXPANSION_ENABLED_LOCALES=en,de", goal],
                    cwd=ROOT, text=True, capture_output=True,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("requires EXPANSION_ENABLED_LOCALES", rejected.stderr)
        equivalent = subprocess.run(
            [
                *base, "EXPANSION_ENABLED_LOCALES=fr,en",
                "EXPANSION_SAVE_COMPAT_EPOCH=0x100", "localization-generate",
            ],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(equivalent.returncode, 0, equivalent.stderr)

    def test_selected_project_is_recorded_by_the_real_metadata_cli(self):
        output = self.project / "metadata"
        result = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts/modernize/expansion_config.py"),
                "generate", "--config-mk", str(ROOT / "config.mk"),
                "--config", "release", "--abi", "aapcs", "--rom-size", "32M",
                "--repo-root", str(ROOT),
                "--project", self.project.name,
                "--output-dir", str(output),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(
            (output / "expansion_build_metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["project"], self.project.name)
        self.assertEqual(metadata["project_profile"], "primary")
        self.assertEqual(metadata["rom_title"], "SELECTEDGAME")

    def test_make_generates_the_selected_profile_metadata_in_its_own_namespace(self):
        project_output = ROOT / "build/projects" / self.project.name
        self.assertFalse(project_output.exists())
        self.addCleanup(
            lambda: shutil.rmtree(project_output) if project_output.exists() else None
        )
        metadata_path = (
            project_output / "primary/release/aapcs/generated/expansion_build_metadata.json"
        )
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str(metadata_path.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"], self.project.name)
        self.assertEqual(metadata["project_profile"], "primary")
        self.assertEqual(metadata["rom_title"], "SELECTEDGAME")
        self.assertEqual(metadata["rom_size_bytes"], 32 * 1024 * 1024)

    def test_configure_persists_selection_not_derived_preset_values(self):
        from scripts.modernize.tests.test_build_default_lane import parse_make_assignments

        build = self.project / "configured"
        build.mkdir()
        configured = subprocess.run(
            [
                str(ROOT / "configure"),
                f"--with-project={self.project.name}",
                "--with-project-profile=alternate",
            ],
            cwd=build, text=True, capture_output=True, check=False,
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        values = parse_make_assignments(
            (build / "config.autotools.mk").read_text(encoding="utf-8")
        )
        self.assertEqual(
            values,
            {"FE8_PROJECT": self.project.name, "FE8_PROJECT_PROFILE": "alternate"},
        )
        project_output = ROOT / "build/projects" / self.project.name
        self.assertFalse(project_output.exists())
        self.addCleanup(
            lambda: shutil.rmtree(project_output) if project_output.exists() else None
        )
        metadata_path = (
            project_output / "alternate/release/aapcs/generated/expansion_build_metadata.json"
        )
        generated = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1", "MODERN_CONFIG=release",
                str(metadata_path.relative_to(ROOT)),
            ],
            cwd=build, text=True, capture_output=True, check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["rom_title"], "ALTERNATE")
        self.assertEqual(metadata["rom_size_bytes"], 16 * 1024 * 1024)
        self.assertEqual(metadata["project_profile"], "alternate")
        default_metadata = (
            ROOT / "build/expansion-modern/release/aapcs/generated/expansion_build_metadata.json"
        )
        switched = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                "FE8_PROJECT=default", "MODERN_CONFIG=release",
                str(default_metadata.relative_to(ROOT)),
            ],
            cwd=build, text=True, capture_output=True, check=False,
        )
        self.assertEqual(switched.returncode, 0, switched.stderr)
        default = json.loads(default_metadata.read_text(encoding="utf-8"))
        self.assertNotIn("project", default)
        self.assertEqual(default["rom_title"], "FIREEMBLEM2E")
        self.assertEqual(default["rom_size_bytes"], 16 * 1024 * 1024)

    def test_project_asset_selection_is_used_by_the_public_identity_resolver(self):
        manifest = self.project / "assets.json"
        manifest.write_text(
            json.dumps({"assets": [{"kind": "custom-spell-effect"}]}),
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := assets\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_assets := {manifest.relative_to(ROOT)}\n"
            )
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", config_preset="release", abi="aapcs",
                rom_size=None, repo_root=ROOT, project=self.project.name,
            )

    def test_selected_data_replaces_the_actual_generated_table_without_source_drift(self):
        source = self.project / "characters.json"
        data = json.loads((ROOT / "src/data/characters.json").read_text(encoding="utf-8"))
        data["characters"][0]["base"]["hp"] = 23
        source.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := characters\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_characters := {source.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        tracked = subprocess.run(
            ["git", "diff", "--", "src", "include", "reports"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
        from scripts.generated_data.character_refs import read_character_designators
        from scripts.generated_data.characters.parser import parse_hand_written

        configured = self.project / "configured"
        configured.mkdir()
        configuration = subprocess.run(
            [str(ROOT / "configure"), f"--with-project={self.project.name}",
             "--with-project-profile=alternate"],
            cwd=configured, text=True, capture_output=True, check=False,
        )
        self.assertEqual(configuration.returncode, 0, configuration.stderr)
        default = ROOT / "build/generated/data/data_characters.c"
        for cwd, profile in ((ROOT, "primary"), (configured, "alternate")):
            baseline = None
            for selection, expected_hp in (("default", 0), (self.project.name, 23), ("default", 0)):
                with self.subTest(workflow=profile, selection=selection):
                    generated = (
                        default if selection == "default"
                        else output / f"{profile}/release/aapcs/generated/data/data_characters.c"
                    )
                    result = subprocess.run(
                        [
                            "make", "--no-print-directory", "-s", "NODEP=1",
                            f"FE8_PROJECT={selection}", "MODERN_CONFIG=release",
                            str(generated.relative_to(ROOT)),
                        ],
                        cwd=cwd, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    record = parse_hand_written(
                        str(generated), read_character_designators(), {1})[1]
                    self.assertEqual(record["tuple"][8][0], expected_hp)
                    if selection == "default":
                        if baseline is None:
                            baseline = generated.read_bytes()
                        self.assertEqual(generated.read_bytes(), baseline)
        self.assertEqual(
            subprocess.run(
                ["git", "diff", "--", "src", "include", "reports"],
                cwd=ROOT, capture_output=True, check=True,
            ).stdout,
            tracked,
        )

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "ARM compiler required")
    def test_selected_code_is_compiled_in_the_original_logical_object_slot(self):
        source = self.project / "time.c"
        source.write_text(
            '#include "global.h"\nu32 GetGameClock(void) { return 73; }\n',
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := code\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_CODE_REPLACEMENTS := src/time.c={source.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        for config in ("debug", "release"):
            with self.subTest(config=config):
                obj = output / f"primary/{config}/aapcs/src/time.o"
                result = subprocess.run(
                    [
                        "make", "--no-print-directory", "-s", "NODEP=1",
                        f"FE8_PROJECT={self.project.name}", f"MODERN_CONFIG={config}",
                        str(obj.relative_to(ROOT)),
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                linked = self.project / f"selected-code-{config}.o"
                subprocess.run(
                    ["arm-none-eabi-ld", "-r", str(obj), "-o", str(linked)],
                    text=True, capture_output=True, check=True,
                )
                symbols = subprocess.run(
                    ["arm-none-eabi-nm", "--defined-only", str(linked)],
                    text=True, capture_output=True, check=True,
                )
                functions = {
                    line.split()[-1]
                    for line in symbols.stdout.splitlines()
                    if len(line.split()) == 3 and line.split()[1] in ("T", "t")
                }
                self.assertEqual(functions, {"GetGameClock"})

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "ARM compiler required")
    def test_selected_english_source_generates_and_compiles_its_own_message_header(self):
        source = self.project / "texts.txt"
        source.write_text(
            '#include "../../texts/texts.txt"\n'
            "## MSG_PROJECT_SELECTION_PROBE\nSelected project.[X]\n",
            encoding="utf-8",
        )
        code = self.project / "message.c"
        code.write_text(
            '#include "global.h"\n#include "constants/msg.h"\n'
            "const u32 gProjectMessageId = MSG_PROJECT_SELECTION_PROBE;\n",
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := game-text code\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_TEXT_MAIN := {source.relative_to(ROOT)}\n"
                "FE8_PROJECT_TEXT_DEFS := texts/textdefs.txt\n"
                f"FE8_PROJECT_CODE_REPLACEMENTS := src/time.c={code.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        profile = output / "primary/release/aapcs"
        generated = profile / "generated/text/msg_data.c"
        obj = profile / "src/time.o"
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str(generated.relative_to(ROOT)), str(obj.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        from scripts.generated_data.validators import extract_define_constants

        header = profile / "generated/text/include/constants/msg.h"
        constants = extract_define_constants(str(header))
        self.assertEqual(constants["MSG_PROJECT_SELECTION_PROBE"][0], 3414)
        self.assertEqual(constants["MSG_COUNT"][0], 3415)
        binary = self.project / "message.bin"
        subprocess.run(
            ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".rodata", str(obj), str(binary)],
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(binary.read_bytes(), (3414).to_bytes(4, "little"))

    def test_custom_build_roots_do_not_bypass_the_complete_project_namespace(self):
        variables = (
            "MODERN_OUTPUT_DIR", "GENERATED_DATA_OUT_DIR", "LOCALIZATION_OUT_DIR",
            "GAME_LOCALIZATION_OUT_DIR", "MODERN_LOCALIZATION_GENERATED_DIR",
            "MODERN_GAME_LOCALIZATION_GENERATED_DIR", "ASSET_OUTPUT_DIR",
            "ASSET_DISCOVERY_ROOT", "BANIM_OBJECT", "MODERN_ELF_BANIM_SYM",
        )
        output = ROOT / "build/selection-root/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "-", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                "MODERN_BUILD_ROOT=build/selection-root", "project-output-probe",
            ],
            input="project-output-probe:\n\t@printf '%s\\n' "
            + " ".join(f"'{variable}=$({variable})'" for variable in variables) + "\n",
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = dict(
            line.split("=", 1) for line in result.stdout.splitlines()
            if line.split("=", 1)[0] in variables
        )
        prefix = (
            f"build/selection-root/projects/{self.project.name}/primary/release/aapcs/"
        )
        for variable in variables:
            with self.subTest(variable=variable):
                self.assertTrue(Path(paths[variable]).is_relative_to(Path(prefix)), paths[variable])
        for variable, value in (
            ("FE8_PROJECT_OUTPUT_DIR", "build/outside"),
            ("MODERN_OUTPUT_DIR", "build/outside"),
            ("GENERATED_DATA_OUT_DIR", "build/outside"),
            ("ASSET_OUTPUT_DIR", "build/outside"),
            ("ASSET_PROFILE_ROOT", "build/outside"),
            ("ASSET_DISCOVERY_ROOT", "build/outside"),
            ("MODERN_LOCALIZATION_ROOT", "build/outside"),
            ("MODERN_LOCALIZATION_GENERATED_DIR", "build/outside"),
            ("MODERN_LOCALIZATION_LEGACY_GENERATED_DIR", "build/outside"),
            ("MODERN_GAME_LOCALIZATION_ROOT", "build/outside"),
            ("MODERN_GAME_LOCALIZATION_GENERATED_DIR", "build/outside"),
            ("MODERN_PROJECT_TEXT_DIR", "build/outside"),
            ("MODERN_GDB_SMOKE_OUTPUT_DIR", "build/outside"),
            ("MODERN_AUTOPLAY_PLANNER_ROOT", "build/outside"),
            ("MODERN_PATCH_RELEASE_ARTIFACT_DIR", "build/outside"),
            ("MODERN_BUILD_ROOT", ""),
        ):
            with self.subTest(override=variable):
                rejected = subprocess.run(
                    [
                        "make", "--no-print-directory", "-s", "-f", "project_selection.mk",
                        f"FE8_PROJECT={self.project.name}", f"{variable}={value}",
                        "project-selection-config",
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(rejected.returncode, 0)

        for variable in (
            "MODERN_TOOLCHAIN_ROOT", "MODERN_BINUTILS_DIR",
            "MODERN_LIBGCC_DIR", "MODERN_LIBC_DIR",
        ):
            accepted = subprocess.run(
                [
                    "make", "--no-print-directory", "-s", "-f", "project_selection.mk",
                    f"FE8_PROJECT={self.project.name}", f"{variable}=build/tool-input",
                    "project-selection-config",
                ],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_recursive_runtime_consumers_use_the_selected_producer_namespace(self):
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "-n", "NODEP=1", "MAKE=echo",
                "MODERN_CC=/missing-arm-compiler",
                "MODERN_SCANINC=" + str(self.project / "uninstalled-scaninc"),
                "-o", "expansion-modern-rom", "-o", "expansion-modern-all",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=debug",
                "expansion-modern-gdb-smoke",
                "expansion-modern-banim-presentation-check",
                "expansion-modern-localization-runtime-multi-check",
                "expansion-modern-autoplay-planner-check",
                "expansion-modern-aoe-check",
                "expansion-modern-hq-mixer-check",
                "expansion-modern-autoplay-strategy-runtime-check",
                "expansion-modern-blue-phase-delegate-check",
                "expansion-modern-debugtools-phase-control-check",
                "expansion-modern-chapter-objectives-check",
                "expansion-modern-autoplay-accelerated-fidelity-check",
                "expansion-modern-debugtools-diagnostics-check",
                "expansion-modern-map-menu-presentation-check",
                "expansion-modern-portrait-package-runtime-check",
                "expansion-modern-banim-package-runtime-check",
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(output.rglob("*.headers.d")), [])
        self.assertEqual(list(output.rglob("*.assets.d")), [])
        artifacts = []
        portrait_roots = []
        for line in result.stdout.replace("\\\n", " ").splitlines():
            for argument in shlex.split(line):
                if argument.startswith("FE8_PORTRAIT_PACKAGE_RUNTIME_BUILD_ROOT="):
                    portrait_roots.append(argument.split("=", 1)[1])
                path = argument.split("=", 1)[-1]
                if not any(char.isspace() for char in path) and Path(path).name in (
                    "fireemblem8.elf", "fireemblem8.gba", "fireemblem8.map",
                    "linker-budget.json", "planner-linked-budget.json",
                ):
                    artifacts.append(path)
        self.assertGreater(len(artifacts), 4, result.stdout)
        namespace = f"/projects/{self.project.name}/primary/"
        invalid = [
            path for path in artifacts
            if not path.startswith("build/")
            or not any(namespace + config + "/aapcs/" in path for config in ("debug", "release"))
        ]
        self.assertEqual(invalid, [])
        self.assertTrue(portrait_roots, result.stdout)
        self.assertTrue(all(path.endswith(namespace.rstrip("/")) for path in portrait_roots))

    def test_packed_animation_selectors_retain_the_real_base_pin_mismatch_gate(self):
        makefile = self.project / "pin.mk"
        script = self.project / "pin.ld"
        empty = self.project / "empty.txt"
        empty.write_text("", encoding="utf-8")
        command = [
            sys.executable, str(ROOT / "scripts/shiftcheck/scan_build_addrs.py"),
            "--makefile", str(makefile), "--ldscript", str(script),
            "--banim-ldscript", str(empty),
        ]
        for selectors in (
            ("banim/data_banim.o",),
            ("[b]anim/data_banim.o",),
            ("*banim/project_data_banim.o",),
            ("[b]anim/data_banim.o", "*banim/project_data_banim.o"),
        ):
            script.write_text(
                "ROM __text_start : {\n. = 0xC02000;\n"
                + "".join(f"{selector}(.data)\n" for selector in selectors)
                + "}\n",
                encoding="utf-8",
            )
            for base in (0x08C02000, 0x08C02004):
                with self.subTest(selectors=selectors, base=base):
                    makefile.write_text(f"packed:\n\tpacker -b {base:#x}\n", encoding="utf-8")
                    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0 if base == 0x08C02000 else 1, result.stdout)
        makefile.write_text("packed:\n\tpacker -b 0x08c02000\n", encoding="utf-8")
        script.write_text(
            "ROM __text_start : {\n. = 0xC02000;\n[b]anim/data_banim.o(.data)\n"
            ". = 0xC03000;\n*banim/project_data_banim.o(.data)\n}\n",
            encoding="utf-8",
        )
        mismatch = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(mismatch.returncode, 1, mismatch.stdout)

    @unittest.skipUnless(
        shutil.which("arm-none-eabi-ld") and shutil.which("arm-none-eabi-objcopy"),
        "ARM binutils required",
    )
    def test_private_packed_output_creates_its_own_parent_and_preserves_input_bytes(self):
        source = self.project / "packed.bin"
        source.write_bytes(bytes(range(16)))
        script = self.project / "packed.txt"
        script.write_text(str(source) + "\n", encoding="utf-8")
        output = self.project / "owned/nested/project_data_banim.o"
        self.assertFalse(output.parent.exists())
        result = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts/arm_compressing_linker.py"),
                "-o", str(output), "-t", str(script), "-b", "0x8c02000",
                "-l", "arm-none-eabi-ld", "--objcopy", "arm-none-eabi-objcopy",
            ],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = self.project / "observed.bin"
        subprocess.run(
            [
                "arm-none-eabi-objcopy", "-O", "binary", "-j", ".data",
                str(output), str(observed),
            ],
            check=True, capture_output=True,
        )
        self.assertEqual(observed.read_bytes(), source.read_bytes())
        self.assertTrue(Path(str(output) + ".sym.o").is_file())

    @unittest.skipUnless(
        shutil.which("arm-none-eabi-ld") and shutil.which("arm-none-eabi-objcopy"),
        "ARM binutils required",
    )
    def test_actual_selected_packing_command_waits_on_the_common_stock_input_lock(self):
        from scripts import arm_compressing_linker as linker

        source = self.project / "packed.bin"
        source.write_bytes(bytes(range(16)))
        script = self.project / "packed.txt"
        script.write_text(str(source) + "\n", encoding="utf-8")
        root = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(root) if root.exists() else None)
        output = root / "primary/debug/aapcs/banim/project_data_banim.o"
        resolved = subprocess.run(
            [
                "make", "--no-print-directory", "-sn", "NODEP=1", "MAKE=echo",
                f"FE8_PROJECT={self.project.name}",
                f"ASSET_BANIM_COMBINED_LINKER_SCRIPT={script}",
                str(output.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        commands = [
            shlex.split(line)
            for line in resolved.stdout.replace("\\\n", " ").splitlines()
            if " -o " + str(output.relative_to(ROOT)) + " " in line
        ]
        self.assertEqual(len(commands), 1, resolved.stdout)
        command = commands[0]
        self.assertEqual(command[command.index("--lock-output") + 1], "banim/data_banim.o")
        control = command[command.index("--") + 1:]
        command.insert(command.index("--lock-output"), "-d")
        with linker.output_lock(ROOT / "banim/data_banim.o"):
            bypass = subprocess.run(control, cwd=ROOT, text=True, capture_output=True, timeout=10)
            self.assertEqual(bypass.returncode, 0, bypass.stderr)
            self.assertTrue(output.is_file())
            output.unlink()
            Path(str(output) + ".sym.o").unlink()
            child = subprocess.Popen(
                command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertTrue(select.select([child.stdout], [], [], 10)[0])
            self.assertTrue(child.stdout.readline().startswith("Waiting for output lock:"))
            self.assertFalse(select.select([child.stdout], [], [], 0.1)[0])
            self.assertIsNone(child.poll())
            self.assertFalse(output.exists())
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, stdout + stderr)
        observed = self.project / "locked.bin"
        subprocess.run(
            ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".data", str(output), str(observed)],
            capture_output=True, check=True,
        )
        self.assertEqual(observed.read_bytes(), source.read_bytes())

    def test_selected_ui_catalog_reaches_the_linked_catalog_descriptor(self):
        catalog = self.project / "catalog.en.json"
        data = json.loads(
            (ROOT / "texts/expansion/catalog.en.json").read_text(encoding="utf-8")
        )
        data["strings"]["framework.title"] = "Selected Project"
        data["strings"]["project.title"] = "Selected Project"
        catalog.write_text(json.dumps(data), encoding="utf-8")
        registry = self.project / "registry.json"
        registry_data = json.loads(
            (ROOT / "texts/expansion/registry.json").read_text(encoding="utf-8")
        )
        entry = dict(registry_data["messages"][0])
        entry.update(
            id=max(message["id"] for message in registry_data["messages"]) + 1,
            key="project.title",
        )
        registry_data["messages"].append(entry)
        registry.write_text(json.dumps(registry_data), encoding="utf-8")
        presentation = self.project / "presentation.json"
        presentation_data = json.loads(
            (ROOT / "src/data/ui_presentation.json").read_text(encoding="utf-8")
        )
        presentation_data["contexts"][0]["title_key"] = "project.title"
        presentation.write_text(json.dumps(presentation_data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := ui-text ui_presentation\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_UI_REGISTRY := {registry.relative_to(ROOT)}\n"
                f"FE8_PROJECT_UI_CATALOGS := en={catalog.relative_to(ROOT)}\n"
                f"FE8_PROJECT_SOURCE_ui_presentation := {presentation.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        generated = output / "primary/release/aapcs/expansion-localization/generated"
        manifest_c = (
            output / "primary/release/aapcs/generated/data/expansion_ui_presentation_manifest.c"
        )
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str((generated / "expansion_locale_catalog.c").relative_to(ROOT)),
                str(manifest_c.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        driver = self.project / "catalog_driver.c"
        driver.write_text(
            '#include "global.h"\n#include "expansion_locale.h"\n'
            '#include "expansion_msg_ids.h"\n#include "expansion_ui_presentation.h"\n'
            "#include <stdio.h>\n"
            "int main(void) {\n"
            "    unsigned i;\n"
            "    const struct ExpansionLocaleCatalogDescriptor *catalog = &gExpansionLocaleCatalogs[0];\n"
            "    if (gExpansionUiPresentationManifest[0].titleMsgId != EXP_MSG_PROJECT_TITLE)\n"
            "        return 2;\n"
            "    for (i = 0; i < catalog->count; ++i)\n"
            "        if (catalog->ids[i] == EXP_MSG_PROJECT_TITLE) {\n"
            "            puts(catalog->strings[i]);\n"
            "            return 0;\n"
            "        }\n"
            "    return 1;\n"
            "}\n",
            encoding="utf-8",
        )
        binary = self.project / "catalog_driver"
        compiled = subprocess.run(
            [
                "cc", "-std=gnu89", "-DMODERN=1", "-I", str(ROOT / "include"),
                "-I", str(generated), str(driver),
                str(generated / "expansion_locale_catalog.c"), str(manifest_c),
                "-o", str(binary),
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        observed = subprocess.run(
            [str(binary)], text=True, capture_output=True, check=True,
        )
        self.assertEqual(observed.stdout, "Selected Project\n")

    def test_selected_asset_manifest_drives_the_actual_tmx_derivative(self):
        tmx = self.project / "map.tmx"
        tmx.write_text(
            (ROOT / "assets/tmx/Ch2Map.tmx").read_text(encoding="utf-8").replace(
                "521,521,521,", "2,521,521,", 1
            ),
            encoding="utf-8",
        )
        manifest = self.project / "assets.json"
        data = json.loads((ROOT / "assets/manifest.json").read_text(encoding="utf-8"))
        data["assets"] = [data["assets"][0]]
        data["assets"][0]["sources"] = [str(tmx.relative_to(ROOT))]
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := assets\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_assets := {manifest.relative_to(ROOT)}\n"
            )
        environment = {**os.environ, "GIT_INDEX_FILE": str(self.project / "asset.index")}
        subprocess.run(["git", "read-tree", "HEAD"], cwd=ROOT, env=environment, check=True)
        subprocess.run(
            ["git", "add", "--", str(tmx.relative_to(ROOT))],
            cwd=ROOT, env=environment, check=True,
        )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                "assets-generate", "print-ASSET_OUTPUT_DIR",
            ],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        asset_output = ROOT / result.stdout.splitlines()[-1]
        payload = (asset_output / "tmx/CH2_MAIN_MAP.mar").read_bytes()
        self.assertEqual(len(payload), 450)
        self.assertEqual(int.from_bytes(payload[:2], "little"), 8)

    def test_selected_map_resize_revalidates_objective_bounds_before_publishing(self):
        tmx = self.project / "map.tmx"
        shutil.copyfile(ROOT / "assets/tmx/Ch2Map.tmx", tmx)
        manifest = self.project / "assets.json"
        data = json.loads((ROOT / "assets/manifest.json").read_text(encoding="utf-8"))
        data["assets"] = [data["assets"][0]]
        data["assets"][0]["sources"] = [str(tmx.relative_to(ROOT))]
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := assets chapterobjectives chapterbundle\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_assets := {manifest.relative_to(ROOT)}\n"
                "FE8_PROJECT_SOURCE_chapterobjectives := scripts/generated_data/tests/fixtures/chapterobjectives/valid.json\n"
                "FE8_PROJECT_SOURCE_chapterbundle := scripts/generated_data/tests/fixtures/chapterobjectives/ch2_bundle.json\n"
            )
        environment = {**os.environ, "GIT_INDEX_FILE": str(self.project / "asset.index")}
        subprocess.run(["git", "read-tree", "HEAD"], cwd=ROOT, env=environment, check=True)
        subprocess.run(
            ["git", "add", "--", str(tmx.relative_to(ROOT))],
            cwd=ROOT, env=environment, check=True,
        )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        generated = output / "primary/debug/aapcs/generated/data/data_chapter_objectives.c"
        command = [
            "make", "--no-print-directory", "-s", "NODEP=1",
            f"FE8_PROJECT={self.project.name}", str(generated.relative_to(ROOT)),
        ]
        valid = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        previous = generated.read_bytes()
        tree = ET.parse(tmx)
        tree.getroot().set("width", "1")
        tree.getroot().set("height", "1")
        layer = tree.find("layer")
        layer.set("width", "1")
        layer.set("height", "1")
        layer.find("data").text = "521"
        tree.write(tmx, encoding="utf-8", xml_declaration=True)
        data["assets"][0]["resources"].update(mapWidth=1, mapHeight=1)
        manifest.write_text(json.dumps(data), encoding="utf-8")
        rejected = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("area", rejected.stderr)
        self.assertEqual(generated.read_bytes(), previous)

    def test_incomplete_duplicate_unknown_and_inherited_replacement_inputs_fail(self):
        declaration = self.project / "project.mk"
        original = declaration.read_text(encoding="utf-8")
        for invalid in (
            "FE8_PROJECT_INHERIT := code\n",
            "FE8_PROJECT_REPLACE := characters\n",
            "FE8_PROJECT_INHERIT += code\n",
            "FE8_PROJECT_INHERIT += unknown\n",
            "FE8_PROJECT_SOURCE_characters := src/data/characters.json\n",
            "FE8_PROJECT_SOURCE_unknown := src/data/characters.json\n",
            "FE8_PROJECT_REPLACE := characters\n"
            "FE8_PROJECT_INHERIT := $(filter-out characters,$(FE8_PROJECT_MODULES))\n"
            "FE8_PROJECT_SOURCE_characters := examples/no-such-project/characters.json\n",
        ):
            with self.subTest(declaration=invalid):
                declaration.write_text(original + invalid, encoding="utf-8")
                result = subprocess.run(
                    [
                        "make", "--no-print-directory", "-s", "-f", "project_selection.mk",
                        f"FE8_PROJECT={self.project.name}", "project-selection-config",
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse((ROOT / "build/projects" / self.project.name).exists())
        declaration.write_text(original, encoding="utf-8")
        for project, profile in (
            ("../outside", None), ("bad project", None),
            ("no-such-project", None), (self.project.name, "missing"),
        ):
            with self.subTest(project=project, profile=profile):
                with self.assertRaises(ec.ConfigError):
                    ec.load_identity(
                        ROOT / "config.mk", config_preset="release", abi="aapcs",
                        rom_size=None, repo_root=ROOT, project=project, project_profile=profile,
                    )

    def test_selected_projects_reject_archival_mixed_and_non_aapcs_requests(self):
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        legacy_generated = (
            f"build/projects/{self.project.name}/primary/debug/aapcs/generated/data/data_items.o"
        )
        for goals in (
            ["legacy"], ["all", "fireemblem8.gba"], ["src/time.o"],
            [legacy_generated], ["generated-data-link-check"], ["asm/fe6sio.o"],
        ):
            with self.subTest(goals=goals):
                result = subprocess.run(
                    [
                        "make", "--no-print-directory", "-sn", "NODEP=1",
                        f"FE8_PROJECT={self.project.name}", *goals,
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FE8_PROJECT", result.stderr)
                self.assertIn("archival", result.stderr.lower())
                self.assertNotIn("tools/agbcc", result.stdout)
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", config_preset="release", abi="apcs-gnu",
                rom_size=None, repo_root=ROOT, project=self.project.name,
            )

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "ARM compiler required")
    def test_selected_font_binary_is_preprocessed_and_compiled_in_its_existing_slot(self):
        widths = self.project / "widths.u8"
        widths.write_bytes(b"\x09")
        source = self.project / "font.c"
        source.write_text(
            '#include "global.h"\n'
            "const u8 gLocalizedFontJaSystemWidths[] = "
            f'INCBIN_U8("{widths.relative_to(ROOT)}");\n',
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := fonts\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                "FE8_PROJECT_FONT_REPLACEMENTS := "
                f"src/data/localized_font_data.c={source.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        obj = output / "primary/release/aapcs/src/data/localized_font_data.o"
        depfile = obj.with_suffix(".assets.d")
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str(depfile.relative_to(ROOT)), str(obj.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(widths.relative_to(ROOT)), depfile.read_text(encoding="utf-8").split())
        binary = self.project / "font.bin"
        subprocess.run(
            ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".rodata", str(obj), str(binary)],
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(binary.read_bytes(), b"\x09")

    def test_selected_item_symbols_resolve_and_validate_against_the_generated_message_header(self):
        text = self.project / "texts.txt"
        text.write_text(
            '#include "../../texts/texts.txt"\n'
            "## MSG_PROJECT_SELECTION_PROBE\nSelected project.[X]\n",
            encoding="utf-8",
        )
        items = self.project / "items.json"
        data = json.loads((ROOT / "src/data/items.json").read_text(encoding="utf-8"))
        data["items"][0]["nameTextId"] = "MSG_PROJECT_SELECTION_PROBE"
        items.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := game-text items\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_TEXT_MAIN := {text.relative_to(ROOT)}\n"
                "FE8_PROJECT_TEXT_DEFS := texts/textdefs.txt\n"
                f"FE8_PROJECT_SOURCE_items := {items.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        generated = output / "primary/release/aapcs/generated/data/data_items.c"
        command = [
            "make", "--no-print-directory", "-s", "NODEP=1",
            f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
            str(generated.relative_to(ROOT)),
        ]
        result = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        from scripts.generated_data.items.parser import parse_hand_written

        record = parse_hand_written(str(generated), ["ITEM_NONE"])["ITEM_NONE"]
        self.assertEqual(record["tuple"][0], 3414)
        previous = generated.read_bytes()
        data["items"][0]["nameTextId"] = "MSG_COUNT"
        items.write_text(json.dumps(data), encoding="utf-8")
        rejected = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(generated.read_bytes(), previous)

    def test_transitive_non_txt_text_changes_refresh_the_actual_message_outputs(self):
        source = self.project / "texts.txt"
        included = self.project / "story.inc"
        source.write_text('#0000\nBase.[X]\n#include "story.inc"\n', encoding="utf-8")
        included.write_text("## MSG_PROJECT_INCLUDE\nFirst.[X]\n", encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := game-text\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_TEXT_MAIN := {source.relative_to(ROOT)}\n"
                "FE8_PROJECT_TEXT_DEFS := texts/textdefs.txt\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        generated = output / "primary/release/aapcs/generated/text"
        header = generated / "include/constants/msg.h"
        command = [
            "make", "--no-print-directory", "-s", "NODEP=1",
            f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
            str(header.relative_to(ROOT)),
        ]
        from scripts.generated_data.validators import extract_define_constants

        first = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(extract_define_constants(header)["MSG_COUNT"][0], 2)
        with included.open("a", encoding="utf-8") as stream:
            stream.write("## MSG_PROJECT_INCLUDE_NEW\nSecond.[X]\n")
        updated = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(updated.returncode, 0, updated.stderr)
        constants = extract_define_constants(header)
        self.assertEqual(constants["MSG_COUNT"][0], 3)
        self.assertEqual(constants["MSG_PROJECT_INCLUDE_NEW"][0], 2)
        paths = (header, generated / "msg_data.c")
        previous = tuple((path.read_bytes(), path.stat().st_mtime_ns) for path in paths)
        unchanged = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertEqual(
            tuple((path.read_bytes(), path.stat().st_mtime_ns) for path in paths), previous
        )

    def test_standalone_data_goals_build_and_refresh_the_selected_header(self):
        text = self.project / "texts.txt"
        text.write_text(
            '#include "../../texts/texts.txt"\n'
            "## MSG_PROJECT_DATA_FIRST\nFirst selected text.[X]\n",
            encoding="utf-8",
        )
        items = self.project / "items.json"
        data = json.loads((ROOT / "src/data/items.json").read_text(encoding="utf-8"))
        data["items"][0]["nameTextId"] = "MSG_PROJECT_DATA_FIRST"
        items.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := game-text items\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_TEXT_MAIN := {text.relative_to(ROOT)}\n"
                "FE8_PROJECT_TEXT_DEFS := texts/textdefs.txt\n"
                f"FE8_PROJECT_SOURCE_items := {items.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        header = output / "primary/release/aapcs/generated/text/include/constants/msg.h"
        from scripts.generated_data.validators import extract_define_constants

        for goal, symbol, expected in (
            ("generated-data-validate", "MSG_PROJECT_DATA_FIRST", 3414),
            ("generated-data-manifest", "MSG_PROJECT_DATA_SECOND", 3415),
        ):
            with self.subTest(goal=goal):
                if expected == 3415:
                    with text.open("a", encoding="utf-8") as stream:
                        stream.write("## MSG_PROJECT_DATA_SECOND\nSecond selected text.[X]\n")
                    data["items"][0]["nameTextId"] = symbol
                    items.write_text(json.dumps(data), encoding="utf-8")
                result = subprocess.run(
                    [
                        "make", "--no-print-directory", "-s", "NODEP=1",
                        f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release", goal,
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(extract_define_constants(str(header))[symbol][0], expected)

    @unittest.skipUnless(
        shutil.which("arm-none-eabi-as") and (ROOT / "tools/scaninc/scaninc").is_file(),
        "ARM assembler and built scaninc required",
    )
    def test_modern_project_link_preparation_builds_real_shared_assembly(self):
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        query = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "-", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "project-shared-input",
            ],
            input="project-shared-input:\n"
            "\t@printf '%s\\n' '$(firstword $(filter sound/songs/%.o,$(MODERN_ELF_LEGACY_ASM)))'\n",
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(query.returncode, 0, query.stderr)
        shared = query.stdout.splitlines()[-1]
        self.assertTrue(shared.startswith("sound/") and shared.endswith(".o"), shared)
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "expansion-modern-legacy-ready",
                f"MODERN_ELF_LEGACY_ASM={shared}", "MODERN_ELF_LEGACY_MIDI=",
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        elf = subprocess.run(
            ["arm-none-eabi-readelf", "-h", str(ROOT / shared)],
            text=True, capture_output=True, check=True,
        )
        self.assertRegex(elf.stdout, r"Machine:\s+ARM\b")

    def test_eu_override_does_not_disable_real_inherited_cjk_catalog_drift_validation(self):
        stage = self.project / "catalog-root"
        stage.mkdir()
        for directory in ("texts", "scripts", "src", "include"):
            shutil.copytree(
                ROOT / directory, stage / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for directory in ("graphics", "assets", "data"):
            (stage / directory).symlink_to(ROOT / directory, target_is_directory=True)
        command = [
            sys.executable, "-m", "scripts.localization.game_catalog", "validate",
            "--enabled-locales", "ja,fr",
        ]
        for locale in ("fr", "de", "es", "it"):
            command.extend(
                ["--eu-authored", f"{locale}={stage / f'texts/locales/eu/authored.{locale}.json'}"]
            )
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONPATH": str(stage), "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_WORK_TREE": str(stage), "GIT_INDEX_FILE": str(self.project / "catalog.index"),
            "GIT_DIR": subprocess.check_output(
                ["git", "rev-parse", "--absolute-git-dir"], cwd=ROOT, text=True,
            ).strip(),
        }
        subprocess.run(["git", "read-tree", "HEAD"], cwd=stage, env=environment, check=True)
        valid = subprocess.run(
            command, cwd=stage, env=environment, text=True, capture_output=True, timeout=120,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        catalog = stage / "texts/locales/authored/catalog.ja.json"
        data = json.loads(catalog.read_text(encoding="utf-8"))
        original = data["strings"]["game.chapter_event.msg_6a5"]
        data["strings"]["game.chapter_event.msg_6a5"] = original.replace("\uff12", "\uff13")
        self.assertNotEqual(data["strings"]["game.chapter_event.msg_6a5"], original)
        catalog.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rejected = subprocess.run(
            command, cwd=stage, env=environment, text=True, capture_output=True, timeout=120,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("authored catalogs differ from deterministic shard merge", rejected.stderr)
        custom_ja = stage / "selected-ja.json"
        shutil.copyfile(ROOT / "texts/locales/authored/catalog.ja.json", custom_ja)
        selected = [
            *command, "--authored", f"ja={custom_ja}",
            "--authored", f"zh-Hans={stage / 'texts/locales/authored/catalog.zh-Hans.json'}",
        ]
        replacement = subprocess.run(
            selected, cwd=stage, env=environment, text=True, capture_output=True, timeout=120,
        )
        self.assertEqual(replacement.returncode, 0, replacement.stderr)
        inherited_zh = stage / "texts/locales/authored/catalog.zh-Hans.json"
        zh = json.loads(inherited_zh.read_text(encoding="utf-8"))
        zh["strings"]["game.chapter_event.msg_6a5"] = (
            "\u4e2d" + zh["strings"]["game.chapter_event.msg_6a5"]
        )
        inherited_zh.write_text(
            json.dumps(zh, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rejected_zh = subprocess.run(
            selected, cwd=stage, env=environment, text=True, capture_output=True, timeout=120,
        )
        self.assertNotEqual(rejected_zh.returncode, 0)
        self.assertIn("authored catalogs differ from deterministic shard merge", rejected_zh.stderr)

    def test_default_leakage_checks_keep_the_full_committed_locale_set(self):
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-sn", "NODEP=1",
                "FE8_PROJECT=default", "GAME_LOCALIZATION_ENABLED_LOCALES=ja",
                "game-localization-check", "game-localization-leakage-audit",
                "game-localization-leakage-check",
            ],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        routes = {}
        for line in result.stdout.replace("\\\n", " ").splitlines():
            arguments = shlex.split(line)
            if "scripts.localization.game_catalog" not in arguments:
                continue
            action = arguments[arguments.index("scripts.localization.game_catalog") + 1]
            locales = arguments[arguments.index("--enabled-locales") + 1]
            routes.setdefault(action, set()).add(locales)
        self.assertEqual(routes["generate"], {"ja"})
        self.assertEqual(routes["check-width"], {"ja"})
        self.assertEqual(routes["check-leakage"], {"ja,zh-Hans"})
        self.assertEqual(routes["audit-leakage"], {"ja,zh-Hans"})

    def test_selected_leakage_check_validates_and_creates_its_build_local_report(self):
        from scripts.localization.game_catalog.leakage import DEFAULT_REPORT_PATH

        stock = ROOT / DEFAULT_REPORT_PATH
        previous = stock.read_bytes(), stock.stat().st_mtime_ns
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        report = output / (
            "primary/debug/aapcs/generated/game-localization/"
            "game_localization_latin_span_audit.json"
        )
        self.assertFalse(report.exists())
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "EXPANSION_ENABLED_LOCALES=en,ja",
                "game-localization-leakage-check",
            ],
            cwd=ROOT, text=True, capture_output=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        audit = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(audit["summary"]["unapproved_span_count"], 0)
        self.assertGreater(audit["summary"]["game_payload_count"], 0)
        self.assertEqual((stock.read_bytes(), stock.stat().st_mtime_ns), previous)

    @unittest.skipIf(host_only_enabled(), "native catalog generation belongs to the ARM lane")
    @unittest.skipUnless(
        shutil.which(os.environ.get("MODERN_CC", "arm-none-eabi-gcc")),
        "selected ARM compiler required",
    )
    def test_selected_translation_and_mapping_use_the_real_engine_root(self):
        authored = self.project / "authored.fr.json"
        data = json.loads(
            (ROOT / "texts/locales/eu/authored.fr.json").read_text(encoding="utf-8")
        )
        data["strings"]["0x040C"] = "Project proof.[CTRL:001F]"
        authored.write_text(json.dumps(data), encoding="utf-8")
        mapping = self.project / "mapping.json"
        shutil.copyfile(ROOT / "texts/locales/mapping/fe8u_target_map.json", mapping)
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REQUIRED_SETTINGS += EXPANSION_ENABLED_LOCALES\n"
                "FE8_PROJECT_primary_EXPANSION_ENABLED_LOCALES := en,fr\n"
                "FE8_PROJECT_REPLACE := game-authored-fr game-mapping\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_game-authored-fr := {authored.relative_to(ROOT)}\n"
                f"FE8_PROJECT_SOURCE_game-mapping := {mapping.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        generated = output / "primary/release/aapcs/game-localization/generated"
        catalog = generated / "game_localization_catalog.c"
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str(catalog.relative_to(ROOT)),
                "game-localization-generate", "game-localization-leakage-check",
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        standalone_budget = json.loads(
            (output / "primary/release/aapcs/generated/game-localization/game_localization_budget.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(standalone_budget["enabled_locales"], ["fr"])
        driver = self.project / "game_catalog_driver.c"
        driver.write_text(
            '#include "global.h"\n#include "game_localization_catalog.h"\n'
            '#include "localized_text_codec.h"\n#include <stdio.h>\n'
            "int main(void) {\n"
            "    const struct GameLocalizationLocaleCatalog *catalog = "
            "gGameLocalizationCatalogs[GAME_LOCALIZATION_LOCALE_FR];\n"
            "    const struct GameLocalizationCatalogEntry *entry = &catalog->entries[0x040C];\n"
            "    unsigned char buffer[128];\n"
            "    u32 length;\n"
            "    if (LocalizedTextCodec_Decode(catalog->nodes, catalog->nodeCount,\n"
            "            catalog->rootIndex, entry->data, entry->compressedSize,\n"
            "            entry->bitLength, buffer, sizeof(buffer), &length) != 0)\n"
            "        return 1;\n"
            "    puts((const char *) buffer);\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        binary = self.project / "game_catalog_driver"
        compiled = subprocess.run(
            [
                "cc", "-std=gnu89", "-DMODERN=1",
                "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=0x09u",
                "-I", str(ROOT / "include"), "-I", str(generated),
                str(driver), str(catalog), str(ROOT / "src/localized_text_codec.c"),
                "-o", str(binary),
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        observed = subprocess.run([str(binary)], capture_output=True, check=True)
        self.assertEqual(observed.stdout, b"Project proof.\x1f\n")


@unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "modern ROM toolchain required")
class ProjectSelectionRomTests(unittest.TestCase):
    def test_selected_project_rom_links_and_boots(self):
        projects = ROOT / "examples"
        projects.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="selection-rom-", dir=projects) as directory:
            project = Path(directory)
            (project / "project.mk").write_text(
                "FE8_PROJECT_PROFILES := probe\n"
                "FE8_PROJECT_DEFAULT_PROFILE := probe\n"
                "FE8_PROJECT_REQUIRED_SETTINGS := MODERN_ROM_SIZE\n"
                "FE8_PROJECT_probe_MODERN_ROM_SIZE := 32M\n"
                "FE8_PROJECT_INHERIT := $(FE8_PROJECT_MODULES)\n",
                encoding="utf-8",
            )
            output = ROOT / "build/projects" / project.name
            self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
            result = subprocess.run(
                [
                    "make", "--no-print-directory", "-s", "-j2",
                    f"FE8_PROJECT={project.name}", "MODERN_CONFIG=debug", "MODERN_ABI=aapcs",
                    "expansion-modern-boot-check",
                ],
                cwd=ROOT, text=True, capture_output=True, check=False, timeout=1800,
            )
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-20000:])
            profile = output / "probe/debug/aapcs"
            self.assertEqual((profile / "fireemblem8.gba").stat().st_size, 32 * 1024 * 1024)
            metadata = json.loads(
                (profile / "generated/expansion_build_metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["project"], project.name)
            self.assertEqual(metadata["project_profile"], "probe")
            pinned = []
            floating_banim_data = []
            for line in (profile / "fireemblem8.map").read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if (
                    len(fields) == 4 and fields[0] == ".data"
                    and fields[1].startswith("0x") and fields[2].startswith("0x")
                ):
                    address, size = int(fields[1], 16), int(fields[2], 16)
                    if address == 0x08C02000 and size > 0:
                        pinned.append((ROOT / fields[3]).resolve())
                    if fields[3] == "src/data/banim/data_banim.o":
                        floating_banim_data.append((address, size))
            self.assertEqual(pinned, [(profile / "banim/project_data_banim.o").resolve()])
            self.assertEqual(len(floating_banim_data), 1)
            address, size = floating_banim_data[0]
            self.assertGreater(size, 0)
            self.assertLessEqual(address + size, 0x08C00000)


class ValidateTitleTests(unittest.TestCase):
    def test_valid_title_passes_through(self):
        self.assertEqual(ec.validate_title("FIREEMBLEM2E"), "FIREEMBLEM2E")

    def test_exactly_12_bytes_is_ok(self):
        self.assertEqual(ec.validate_title("ABCDEFGHIJKL"), "ABCDEFGHIJKL")

    def test_empty_title_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_title("")

    def test_too_long_title_rejected(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_title("THISISTOOLONG")
        self.assertIn("EXPANSION_ROM_TITLE", str(ctx.exception))

    def test_non_ascii_title_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_title("FE8\u00e9")


class ValidateGameCodeTests(unittest.TestCase):
    def test_valid_game_code_passes_through(self):
        self.assertEqual(ec.validate_game_code("BE8E"), "BE8E")

    def test_wrong_length_rejected(self):
        assert_rejects_wrong_length(self, ec.validate_game_code, ("ABC", "ABCDE"))

    def test_non_ascii_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_game_code("B\u00e98E")


class ValidateMakerCodeTests(unittest.TestCase):
    def test_valid_maker_code_passes_through(self):
        self.assertEqual(ec.validate_maker_code("01"), "01")

    def test_wrong_length_rejected(self):
        assert_rejects_wrong_length(self, ec.validate_maker_code, ("0", "012"))


class ValidateRevisionTests(unittest.TestCase):
    def test_boundary_values_ok(self):
        self.assertEqual(ec.validate_revision(0), 0)
        self.assertEqual(ec.validate_revision(255), 255)
        self.assertEqual(ec.validate_revision("42"), 42)

    def test_out_of_range_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_revision(256)
        with self.assertRaises(ec.ConfigError):
            ec.validate_revision(-1)

    def test_non_integer_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_revision("not-a-number")


class ValidateRomSizeTests(unittest.TestCase):
    def test_named_sizes_case_insensitive(self):
        self.assertEqual(ec.validate_rom_size("16M"), 16 * 1024 * 1024)
        self.assertEqual(ec.validate_rom_size("16m"), 16 * 1024 * 1024)
        self.assertEqual(ec.validate_rom_size("32M"), 32 * 1024 * 1024)

    def test_exact_byte_counts_accepted(self):
        self.assertEqual(ec.validate_rom_size(str(32 * 1024 * 1024)), 32 * 1024 * 1024)

    def test_unsupported_size_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_rom_size("8M")
        with self.assertRaises(ec.ConfigError):
            ec.validate_rom_size("12345")

    def test_garbage_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_rom_size("banana")


class ValidateVersionTests(unittest.TestCase):
    def test_valid_version_components(self):
        self.assertEqual(ec.validate_version(1, 2, 3), (1, 2, 3))
        self.assertEqual(ec.validate_version("0", "1", "0"), (0, 1, 0))

    def test_boundary_values_ok(self):
        self.assertEqual(ec.validate_version(0, 0, 0), (0, 0, 0))
        self.assertEqual(ec.validate_version(255, 255, 255), (255, 255, 255))

    def test_out_of_range_component_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_version(256, 0, 0)
        with self.assertRaises(ec.ConfigError):
            ec.validate_version(0, -1, 0)

    def test_non_integer_component_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_version("x", 0, 0)


class ValidatePresetAbiTests(unittest.TestCase):
    def test_supported_presets_ok(self):
        self.assertEqual(ec.validate_preset("debug"), "debug")
        self.assertEqual(ec.validate_preset("release"), "release")

    def test_unsupported_preset_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_preset("profiling")

    def test_supported_abis_ok(self):
        self.assertEqual(ec.validate_abi("aapcs"), "aapcs")
        self.assertEqual(ec.validate_abi("apcs-gnu"), "apcs-gnu")

    def test_unsupported_abi_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_abi("eabi")


class ValidateTextShiftTests(unittest.TestCase):
    def test_aligned_value_ok(self):
        self.assertEqual(ec.validate_text_shift("0"), 0)
        self.assertEqual(ec.validate_text_shift("1024"), 1024)

    def test_unaligned_value_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_text_shift("3")

    def test_non_numeric_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_text_shift("nope")


class ValidateBuildIdOverrideTests(unittest.TestCase):
    def test_none_and_empty_are_ok_and_mean_unset(self):
        self.assertIsNone(ec.validate_build_id_override(None))
        self.assertIsNone(ec.validate_build_id_override(""))

    def test_valid_hex_sha_is_lowercased(self):
        self.assertEqual(ec.validate_build_id_override("ABCDEF12"), "abcdef12")

    def test_short_prefix_ok(self):
        self.assertEqual(ec.validate_build_id_override("abcd"), "abcd")

    def test_timestamp_like_value_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_build_id_override("2024-01-01T00:00:00Z")

    def test_branch_name_like_value_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_build_id_override("feature/my-branch")

    def test_too_short_or_too_long_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_build_id_override("abc")
        with self.assertRaises(ec.ConfigError):
            ec.validate_build_id_override("a" * 41)


class ParseConfigMkTests(unittest.TestCase):
    def test_parses_all_required_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config_mk(Path(tmp))
            values = ec.parse_config_mk(path)
        for key in ec.CONFIG_MK_KEYS:
            self.assertIn(key, values)
        self.assertEqual(values["EXPANSION_ROM_TITLE"], "FIREEMBLEM2E")

    def test_ignores_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.mk"
            path.write_text(
                "\n".join(
                    [
                        "# a leading comment",
                        "",
                        "EXPANSION_VERSION_MAJOR := 0  # inline comment",
                        "EXPANSION_VERSION_MINOR := 1",
                        "EXPANSION_VERSION_PATCH := 0",
                        "EXPANSION_ROM_TITLE := FIREEMBLEM2E",
                        "EXPANSION_ROM_GAME_CODE := BE8E",
                        "EXPANSION_ROM_MAKER_CODE := 01",
                        "EXPANSION_ROM_REVISION := 0",
                        "EXPANSION_BUILD_ID :=",
                        "EXPANSION_SAVE_COMPAT_EPOCH := 1",
                        "EXPANSION_ENABLED_LOCALES := en",
                        "EXPANSION_DEFAULT_LOCALE := en",
                        "EXPANSION_PSEUDO_LOCALE := 0",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            values = ec.parse_config_mk(path)
        self.assertEqual(values["EXPANSION_VERSION_MAJOR"], "0")

    def test_missing_file_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.parse_config_mk(Path("/nonexistent/config.mk"))

    def test_missing_required_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.mk"
            path.write_text("EXPANSION_VERSION_MAJOR := 0\n", encoding="utf-8")
            with self.assertRaises(ec.ConfigError) as ctx:
                ec.parse_config_mk(path)
        self.assertIn("EXPANSION_ROM_TITLE", str(ctx.exception))


class ResolveBuildCommitTests(unittest.TestCase):
    def test_explicit_override_wins(self):
        commit = ec.resolve_build_commit("abcd1234", Path("."))
        self.assertEqual(commit, "abcd1234")

    def test_falls_back_to_git_rev_parse_head(self):
        # This repository itself is a git checkout, so HEAD must resolve to
        # a real 40-hex-character commit SHA.
        commit = ec.resolve_build_commit(None, ROOT)
        self.assertRegex(commit, r"^[0-9a-f]{40}$")

    def test_falls_back_to_unknown_sentinel_outside_a_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            commit = ec.resolve_build_commit(None, Path(tmp))
        self.assertEqual(commit, "unknown")

    def test_nested_non_git_tree_never_adopts_outer_repo_head(self):
        """issue #9 remediation regression: a non-git candidate tree
        nested *inside* an unrelated outer Git repository (with its own,
        different HEAD) must resolve to the fixed "unknown" sentinel --
        never git's own upward-directory-discovery-found outer HEAD."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "outer"
            outer.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=str(outer), check=True)
            subprocess.run(
                ["git", "config", "user.email", "outer@example.invalid"], cwd=str(outer), check=True
            )
            subprocess.run(["git", "config", "user.name", "outer"], cwd=str(outer), check=True)
            (outer / "outer-file.txt").write_text("unrelated outer repository content\n")
            subprocess.run(["git", "add", "outer-file.txt"], cwd=str(outer), check=True)
            subprocess.run(["git", "commit", "-q", "-m", "outer commit"], cwd=str(outer), check=True)
            outer_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(outer), capture_output=True, text=True, check=True,
            ).stdout.strip()

            candidate = outer / "nested" / "candidate"
            candidate.mkdir(parents=True)
            self.assertFalse((candidate / ".git").exists())

            commit = ec.resolve_build_commit(None, candidate)
            self.assertEqual(commit, "unknown")
            self.assertNotEqual(commit, outer_head)

    def test_nested_non_git_tree_makes_no_git_subprocess_call_at_all(self):
        """The fix must never even *invoke* git for a candidate root with
        no `.git` of its own -- not merely "invoke it, but discard/ignore
        an unwanted result". A patched `subprocess.run` that raises on
        any call proves this empirically."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "outer"
            outer.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=str(outer), check=True)
            candidate = outer / "nested" / "candidate"
            candidate.mkdir(parents=True)

            def _forbidden_run(*args, **kwargs):
                raise AssertionError(f"unexpected subprocess.run call: {args!r} {kwargs!r}")

            with mock.patch.object(ec.subprocess, "run", _forbidden_run):
                commit = ec.resolve_build_commit(None, candidate)
            self.assertEqual(commit, "unknown")


class FingerprintDeterminismTests(unittest.TestCase):
    def test_same_inputs_produce_same_fingerprint(self):
        fields = {"version": [0, 1, 0], "abi": "aapcs"}
        self.assertEqual(ec.compute_fingerprint(fields), ec.compute_fingerprint(dict(fields)))

    def test_key_order_does_not_affect_fingerprint(self):
        a = {"abi": "aapcs", "version": [0, 1, 0]}
        b = {"version": [0, 1, 0], "abi": "aapcs"}
        self.assertEqual(ec.compute_fingerprint(a), ec.compute_fingerprint(b))

    def test_different_inputs_produce_different_fingerprints(self):
        a = {"abi": "aapcs"}
        b = {"abi": "apcs-gnu"}
        self.assertNotEqual(ec.compute_fingerprint(a), ec.compute_fingerprint(b))

    def test_debug_and_release_presets_produce_different_fingerprints(self):
        base = {
            "version": [0, 1, 0], "abi": "aapcs", "rom_size_bytes": 16 * 1024 * 1024,
            "text_shift": 0, "rom_title": "FIREEMBLEM2E", "rom_game_code": "BE8E",
            "rom_maker_code": "01", "rom_revision": 0,
        }
        debug = ec.compute_fingerprint(dict(base, config_preset="debug"))
        release = ec.compute_fingerprint(dict(base, config_preset="release"))
        self.assertNotEqual(debug, release)


class LoadIdentityTests(unittest.TestCase):
    def test_defaults_come_from_config_mk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk,
                config_preset="debug",
                abi="aapcs",
                rom_size="16M",
                repo_root=Path(tmp),
            )
        self.assertEqual(identity.rom_title, "FIREEMBLEM2E")
        self.assertEqual(identity.rom_game_code, "BE8E")
        self.assertEqual(identity.version_string, "0.1.0")
        self.assertEqual(identity.build_commit, "unknown")

    def test_override_wins_over_config_mk_value(self):
        """The fix for the two-sources-of-truth bug: an explicit override
        parameter must take precedence over config.mk's parsed value."""
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk,
                config_preset="debug",
                abi="aapcs",
                rom_size="16M",
                repo_root=Path(tmp),
                rom_title="FE8CUSTOMTST",
                rom_game_code="ZZZZ",
                rom_maker_code="9X",
                rom_revision=7,
                version_major=2,
            )
        self.assertEqual(identity.rom_title, "FE8CUSTOMTST")
        self.assertEqual(identity.rom_game_code, "ZZZZ")
        self.assertEqual(identity.rom_maker_code, "9X")
        self.assertEqual(identity.rom_revision, 7)
        self.assertEqual(identity.version_major, 2)

    def test_empty_string_override_falls_back_to_config_mk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk,
                config_preset="debug",
                abi="aapcs",
                rom_size="16M",
                repo_root=Path(tmp),
                rom_title="",
            )
        self.assertEqual(identity.rom_title, "FIREEMBLEM2E")

    def test_invalid_override_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            with self.assertRaises(ec.ConfigError):
                ec.load_identity(
                    config_mk_path=config_mk,
                    config_preset="debug",
                    abi="aapcs",
                    rom_size="16M",
                    repo_root=Path(tmp),
                    rom_game_code="ABC",
                )

    def test_fingerprint_is_populated_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            first = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
            second = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertEqual(first.config_fingerprint, second.config_fingerprint)
        self.assertTrue(first.config_fingerprint)

    def test_debug_vs_release_fingerprints_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            debug = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
            release = ec.load_identity(
                config_mk_path=config_mk, config_preset="release", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertNotEqual(debug.config_fingerprint, release.config_fingerprint)

    def test_item_cap_is_recorded_and_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            default = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
            expanded = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp), item_id_cap="0xCE",
            )
        self.assertEqual(default.item_id_cap, 0xCD)
        self.assertEqual(expanded.item_id_cap, 0xCE)
        self.assertEqual(expanded.to_dict()["item_id_cap"], 0xCE)
        self.assertNotEqual(default.config_fingerprint, expanded.config_fingerprint)

    def test_build_id_override_from_config_mk_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), build_id="deadbeef")
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertEqual(identity.build_commit, "deadbeef")

    def test_explicit_build_id_override_wins_over_config_mk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), build_id="deadbeef")
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp), build_id_override="cafef00d",
            )
        self.assertEqual(identity.build_commit, "cafef00d")


class ValidateEnabledLocalesTests(unittest.TestCase):
    def test_single_en_ok(self):
        self.assertEqual(ec.validate_enabled_locales("en"), ("en",))

    def test_en_and_qps_ok_and_normalized_to_stable_order(self):
        # Input order is reversed from the stable id order; the validator
        # must normalize to the fixed stable-id order regardless.
        self.assertEqual(
            ec.validate_enabled_locales("qps-ploc,en"), ("en", "qps-ploc")
        )

    def test_list_input_accepted(self):
        self.assertEqual(ec.validate_enabled_locales(["en", "qps-ploc"]), ("en", "qps-ploc"))

    def test_real_cjk_locales_are_configurable_and_stably_ordered(self):
        expected = {
            "en,ja": ("en", "ja"),
            "en,zh-Hans": ("en", "zh-Hans"),
            "zh-Hans,en,ja": ("en", "ja", "zh-Hans"),
        }
        for configured, normalized in expected.items():
            with self.subTest(configured=configured):
                self.assertEqual(
                    ec.validate_enabled_locales(configured),
                    normalized,
                )

    def test_real_eu_locales_are_configurable_and_stably_ordered(self):
        self.assertEqual(
            ec.validate_enabled_locales("it,es,de,fr,en"),
            ("en", "fr", "de", "es", "it"),
        )

    def test_empty_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_enabled_locales("")

    def test_missing_en_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_enabled_locales("qps-ploc")

    def test_unknown_locale_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_enabled_locales("en,klingon")

    def test_duplicate_locale_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_enabled_locales("en,en")

class ValidateDefaultLocaleTests(unittest.TestCase):
    def test_default_within_enabled_set_ok(self):
        self.assertEqual(ec.validate_default_locale("en", ("en", "qps-ploc")), "en")

    def test_default_not_in_enabled_set_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_default_locale("qps-ploc", ("en",))

    def test_unknown_default_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_default_locale("klingon", ("en",))


class ValidatePseudoLocaleTests(unittest.TestCase):
    def test_zero_without_qps_ok(self):
        self.assertEqual(ec.validate_pseudo_locale("0", ("en",)), 0)

    def test_one_with_qps_ok(self):
        self.assertEqual(ec.validate_pseudo_locale("1", ("en", "qps-ploc")), 1)

    def test_one_without_qps_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_pseudo_locale("1", ("en",))

    def test_zero_with_qps_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_pseudo_locale("0", ("en", "qps-ploc"))

    def test_non_zero_one_value_rejected(self):
        for bogus in ("2", "true", "yes", "-1", ""):
            with self.assertRaises(ec.ConfigError):
                ec.validate_pseudo_locale(bogus, ("en",))


class ValidateLocaleRomSizeTests(unittest.TestCase):
    def test_english_and_pseudo_profiles_allow_16m(self):
        ec.validate_locale_rom_size(("en",), 16 * 1024 * 1024)
        ec.validate_locale_rom_size(("en", "qps-ploc"), 16 * 1024 * 1024)

    def test_real_cjk_profiles_require_32m(self):
        for locales in (
            ("en", "ja"),
            ("en", "zh-Hans"),
            ("en", "ja", "zh-Hans"),
        ):
            with self.subTest(locales=locales):
                with self.assertRaises(ec.ConfigError) as ctx:
                    ec.validate_locale_rom_size(locales, 16 * 1024 * 1024)
                self.assertIn("MODERN_ROM_SIZE=32M", str(ctx.exception))

    def test_real_cjk_profiles_allow_32m(self):
        for locales in (
            ("en", "ja"),
            ("en", "zh-Hans"),
            ("en", "ja", "zh-Hans"),
        ):
            with self.subTest(locales=locales):
                ec.validate_locale_rom_size(locales, 32 * 1024 * 1024)

    def test_real_eu_profiles_require_and_allow_32m(self):
        locales = ("en", "fr", "de", "es", "it")
        with self.assertRaises(ec.ConfigError):
            ec.validate_locale_rom_size(locales, 16 * 1024 * 1024)
        ec.validate_locale_rom_size(locales, 32 * 1024 * 1024)


class ComputeLocaleMaskTests(unittest.TestCase):
    def test_en_only_mask_is_bit_zero(self):
        self.assertEqual(ec.compute_locale_mask(("en",)), 0x1)

    def test_en_and_qps_mask_matches_bit_positions(self):
        # en=0, qps-ploc=7 -- see scripts/localization/schema.py LOCALE_IDS.
        self.assertEqual(ec.compute_locale_mask(("en", "qps-ploc")), 0x81)

    def test_en_ja_zh_mask_matches_stable_bit_positions(self):
        self.assertEqual(ec.compute_locale_mask(("en", "ja", "zh-Hans")), 0x7)

    def test_en_eu_mask_matches_stable_bit_positions(self):
        self.assertEqual(
            ec.compute_locale_mask(("en", "fr", "de", "es", "it")),
            0x79,
        )


class LoadIdentityLocaleTests(unittest.TestCase):
    """load_identity() end-to-end locale config resolution -- defaults,
    overrides, invalid combinations, and the fingerprint/save-epoch
    independence guarantee (issue #18 sprint 1 WHAT item 5)."""

    def test_defaults_are_en_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertEqual(identity.enabled_locales, ("en",))
        self.assertEqual(identity.default_locale, "en")
        self.assertEqual(identity.pseudo_locale_enabled, 0)
        self.assertEqual(identity.enabled_locale_mask, 0x1)
        self.assertEqual(identity.default_locale_id, 0)

    def test_config_mk_qps_enabled_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(
                Path(tmp), enabled_locales="en,qps-ploc", pseudo_locale="1"
            )
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertEqual(identity.enabled_locales, ("en", "qps-ploc"))
        self.assertEqual(identity.pseudo_locale_enabled, 1)
        self.assertEqual(identity.enabled_locale_mask, 0x81)

    def test_override_wins_over_config_mk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
                enabled_locales="en,qps-ploc", default_locale="qps-ploc", pseudo_locale="1",
            )
        self.assertEqual(identity.enabled_locales, ("en", "qps-ploc"))
        self.assertEqual(identity.default_locale, "qps-ploc")
        self.assertEqual(identity.pseudo_locale_enabled, 1)

    def test_invalid_locale_combination_from_config_mk_fails_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), pseudo_locale="1")  # qps not enabled
            with self.assertRaises(ec.ConfigError):
                ec.load_identity(
                    config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                    rom_size="16M", repo_root=Path(tmp),
                )

    def test_cjk_profiles_reject_16m_and_resolve_at_32m(self):
        for configured in ("en,ja", "en,zh-Hans", "zh-Hans,en,ja"):
            with self.subTest(configured=configured):
                with tempfile.TemporaryDirectory() as tmp:
                    config_mk = write_config_mk(
                        Path(tmp), enabled_locales=configured
                    )
                    with self.assertRaises(ec.ConfigError) as ctx:
                        ec.load_identity(
                            config_mk_path=config_mk,
                            config_preset="debug",
                            abi="aapcs",
                            rom_size="16M",
                            repo_root=Path(tmp),
                        )
                    identity = ec.load_identity(
                        config_mk_path=config_mk,
                        config_preset="debug",
                        abi="aapcs",
                        rom_size="32M",
                        repo_root=Path(tmp),
                    )
                self.assertIn("MODERN_ROM_SIZE=32M", str(ctx.exception))
                expected = tuple(
                    locale
                    for locale in ("en", "ja", "zh-Hans")
                    if locale in configured.split(",")
                )
                self.assertEqual(identity.enabled_locales, expected)

    def test_cjk_default_locale_ja_and_zh_hans_resolve(self):
        for default_locale, expected_id in (("ja", 1), ("zh-Hans", 2)):
            with self.subTest(default_locale=default_locale):
                with tempfile.TemporaryDirectory() as tmp:
                    config_mk = write_config_mk(
                        Path(tmp),
                        enabled_locales="en,ja,zh-Hans",
                        default_locale=default_locale,
                    )
                    identity = ec.load_identity(
                        config_mk_path=config_mk,
                        config_preset="release",
                        abi="aapcs",
                        rom_size="32M",
                        repo_root=Path(tmp),
                    )
                self.assertEqual(identity.default_locale, default_locale)
                self.assertEqual(identity.default_locale_id, expected_id)
                self.assertEqual(identity.enabled_locale_mask, 0x7)

    def test_locale_config_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            config_a = write_config_mk(Path(tmp_a))
            config_b = write_config_mk(
                Path(tmp_b), enabled_locales="en,qps-ploc", pseudo_locale="1"
            )
            identity_a = ec.load_identity(
                config_mk_path=config_a, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp_a),
            )
            identity_b = ec.load_identity(
                config_mk_path=config_b, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp_b),
            )
        self.assertNotEqual(identity_a.config_fingerprint, identity_b.config_fingerprint)

    def test_locale_config_change_does_not_move_save_compat_epoch(self):
        """The exact property WHAT item 5 requires: changing the locale
        config changes the fingerprint but never the save-compat epoch."""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            config_a = write_config_mk(Path(tmp_a), save_compat_epoch="42")
            config_b = write_config_mk(
                Path(tmp_b), save_compat_epoch="42",
                enabled_locales="en,qps-ploc", pseudo_locale="1",
            )
            identity_a = ec.load_identity(
                config_mk_path=config_a, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp_a),
            )
            identity_b = ec.load_identity(
                config_mk_path=config_b, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp_b),
            )
        self.assertNotEqual(identity_a.config_fingerprint, identity_b.config_fingerprint)
        self.assertEqual(identity_a.save_compat_epoch, identity_b.save_compat_epoch)
        self.assertEqual(identity_a.save_compat_epoch, 42)


class GenerateMetadataFilesTests(unittest.TestCase):
    def _identity(self, tmp):
        config_mk = write_config_mk(Path(tmp))
        return ec.load_identity(
            config_mk_path=config_mk, config_preset="debug", abi="aapcs",
            rom_size="16M", repo_root=Path(tmp),
        )

    def test_writes_json_and_mk_with_expected_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(tmp)
            out_dir = Path(tmp) / "generated"
            paths = ec.generate_metadata_files(out_dir, identity)
            data = json.loads(paths["json"].read_text(encoding="utf-8"))
            mk_text = paths["mk"].read_text(encoding="utf-8")
        self.assertEqual(data["rom_title"], "FIREEMBLEM2E")
        self.assertEqual(data["config_fingerprint"], identity.config_fingerprint)
        self.assertIn(f"MODERN_BUILD_COMMIT := {identity.build_commit}", mk_text)
        self.assertIn(f"MODERN_CONFIG_FINGERPRINT := {identity.config_fingerprint}", mk_text)

    def test_does_not_rewrite_file_when_content_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(tmp)
            out_dir = Path(tmp) / "generated"
            paths = ec.generate_metadata_files(out_dir, identity)
            first_mtime = paths["json"].stat().st_mtime_ns
            # Regenerate with the exact same identity -- content is
            # byte-identical, so the file must not be rewritten.
            ec.generate_metadata_files(out_dir, identity)
            second_mtime = paths["json"].stat().st_mtime_ns
        self.assertEqual(first_mtime, second_mtime)

    def test_rewrites_file_when_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(tmp)
            out_dir = Path(tmp) / "generated"
            paths = ec.generate_metadata_files(out_dir, identity)
            original = paths["json"].read_text(encoding="utf-8")

            identity.rom_revision = 9
            ec.generate_metadata_files(out_dir, identity)
            updated = paths["json"].read_text(encoding="utf-8")
        self.assertNotEqual(original, updated)


class CliTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "modernize" / "expansion_config.py"

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_validate_succeeds_silently_for_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_resolve_prints_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MODERN_BUILD_COMMIT=", result.stdout)
        self.assertIn("MODERN_CONFIG_FINGERPRINT=", result.stdout)
        self.assertIn("MODERN_VERSION_STRING=0.1.0", result.stdout)
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=1", result.stdout)

    def test_resolve_save_compat_epoch_override_changes_token_not_fingerprint(
        self,
    ):
        """Regression test for issue #2 slice 1 review finding #4 (HIGH):
        modern.mk threads --save-compat-epoch through this CLI's resolve
        command to get the MODERN_SAVE_COMPAT_EPOCH token it embeds in
        MODERN_CFLAGS's -D define and in the compile-settings stamp. This
        proves the override changes that token while leaving
        MODERN_CONFIG_FINGERPRINT untouched -- the save-compat epoch must
        remain a distinct compatibility gate, never folded into the #8
        config fingerprint (see ExpansionIdentity.fingerprint_fields)."""
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            baseline = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
            overridden = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--save-compat-epoch", "2",
            )
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=1", baseline.stdout)
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=2", overridden.stdout)

        def fingerprint_of(stdout: str) -> str:
            for token in stdout.split():
                if token.startswith("MODERN_CONFIG_FINGERPRINT="):
                    return token
            raise AssertionError(f"no fingerprint token in: {stdout}")

        self.assertEqual(
            fingerprint_of(baseline.stdout), fingerprint_of(overridden.stdout)
        )

    def test_resolve_prints_locale_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MODERN_EXPANSION_ENABLED_LOCALE_MASK=1", result.stdout)
        self.assertIn("MODERN_EXPANSION_DEFAULT_LOCALE_ID=0", result.stdout)
        self.assertIn("MODERN_EXPANSION_PSEUDO_LOCALE_ENABLED=0", result.stdout)

    def test_resolve_locale_overrides_change_tokens_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            baseline = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
            overridden = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--enabled-locales", "en,qps-ploc",
                "--pseudo-locale", "1",
            )
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        self.assertIn("MODERN_EXPANSION_ENABLED_LOCALE_MASK=1 ", baseline.stdout)
        self.assertIn("MODERN_EXPANSION_ENABLED_LOCALE_MASK=129", overridden.stdout)

        def fingerprint_of(stdout: str) -> str:
            for token in stdout.split():
                if token.startswith("MODERN_CONFIG_FINGERPRINT="):
                    return token
            raise AssertionError(f"no fingerprint token in: {stdout}")

        self.assertNotEqual(fingerprint_of(baseline.stdout), fingerprint_of(overridden.stdout))

    def test_resolve_fails_early_on_inconsistent_locale_combination(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--pseudo-locale", "1",  # qps-ploc not enabled -- must fail
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_resolve_cjk_profiles_require_32m_and_emit_real_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            rejected = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--enabled-locales", "en,ja",
            )
            accepted = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "32M",
                "--repo-root", tmp,
                "--enabled-locales", "zh-Hans,en,ja",
                "--default-locale", "zh-Hans",
            )
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("MODERN_ROM_SIZE=32M", rejected.stderr)
        self.assertEqual(rejected.stdout, "")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("MODERN_EXPANSION_ENABLED_LOCALE_MASK=7", accepted.stdout)
        self.assertIn("MODERN_EXPANSION_DEFAULT_LOCALE_ID=2", accepted.stdout)

    def test_generate_writes_metadata_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            out_dir = Path(tmp) / "generated"
            result = self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "release",
                "--abi", "apcs-gnu",
                "--rom-size", "32M",
                "--repo-root", tmp,
                "--output-dir", str(out_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads((out_dir / "expansion_build_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(data["config_preset"], "release")
            self.assertEqual(data["abi"], "apcs-gnu")
            self.assertEqual(data["rom_size_bytes"], 32 * 1024 * 1024)

    def test_resolve_honors_identity_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--title", "FE8CUSTOMTST",
                "--game-code", "ZZZZ",
                "--maker-code", "9X",
                "--revision", "7",
            )
            out_dir = Path(tmp) / "generated"
            self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--output-dir", str(out_dir),
                "--title", "FE8CUSTOMTST",
                "--game-code", "ZZZZ",
                "--maker-code", "9X",
                "--revision", "7",
            )
            data = json.loads(
                (out_dir / "expansion_build_metadata.json").read_text(encoding="utf-8")
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["rom_title"], "FE8CUSTOMTST")
        self.assertEqual(data["rom_game_code"], "ZZZZ")
        self.assertEqual(data["rom_maker_code"], "9X")
        self.assertEqual(data["rom_revision"], 7)

    # -- invalid inputs / incompatible combinations fail before any write ---

    def test_invalid_title_fails_before_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), rom_title="TOOLONGTITLE!!")
            out_dir = Path(tmp) / "generated"
            result = self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--output-dir", str(out_dir),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_ROM_TITLE", result.stderr)
        self.assertFalse(out_dir.exists())

    def test_invalid_game_code_fails_before_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), rom_game_code="ABC")
            out_dir = Path(tmp) / "generated"
            result = self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--output-dir", str(out_dir),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_ROM_GAME_CODE", result.stderr)
        self.assertFalse(out_dir.exists())

    def test_unsupported_preset_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "profiling",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", result.stderr)

    def test_unsupported_abi_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "eabi",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertNotEqual(result.returncode, 0)
        # argparse enforces the choices= constraint itself (SUPPORTED_ABIS),
        # so an unsupported --abi is rejected before validate_abi() even
        # runs -- still an actionable, pre-compilation diagnostic.
        self.assertIn("--abi", result.stderr)
        self.assertIn("invalid choice", result.stderr)

    def test_unsupported_rom_size_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "8M",
                "--repo-root", tmp,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MODERN_ROM_SIZE", result.stderr)

    def test_invalid_build_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--build-id", "not-a-sha-branch-name",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_BUILD_ID", result.stderr)

    def test_missing_config_mk_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "validate",
                "--config-mk", str(Path(tmp) / "does-not-exist.mk"),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config.mk", result.stderr)


class ValidateFeatureFlagTests(unittest.TestCase):
    """Issue #6: each starter-feature flag is a strict 0/1 build switch."""

    def test_zero_and_one_pass_through(self):
        self.assertEqual(ec.validate_feature_flag("EXPANSION_MECHANICS_HOOKS", "0"), 0)
        self.assertEqual(ec.validate_feature_flag("EXPANSION_MECHANICS_HOOKS", "1"), 1)
        self.assertEqual(ec.validate_feature_flag("EXPANSION_MECHANICS_HOOKS", 1), 1)

    def test_negative_one_rejected(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_feature_flag("EXPANSION_MECHANICS_HOOKS", "-1")
        self.assertIn("EXPANSION_MECHANICS_HOOKS", str(ctx.exception))

    def test_two_rejected(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_feature_flag("EXPANSION_DANGER_OVERLAY_MENU", "2")
        self.assertIn("out of range", str(ctx.exception))

    def test_text_rejected(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_feature_flag("EXPANSION_MECHANICS_SAMPLE", "yes")
        self.assertIn("not an integer", str(ctx.exception))


class ValidateFeatureFlagRelationshipTests(unittest.TestCase):
    """Issue #6: the sample mechanic can only exist with the hook registry."""

    def test_sample_requires_hooks(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_feature_flags("0", "1", "0")
        message = str(ctx.exception)
        self.assertIn("EXPANSION_MECHANICS_SAMPLE=1", message)
        self.assertIn("EXPANSION_MECHANICS_HOOKS=1", message)

    def test_sample_with_hooks_is_ok(self):
        self.assertEqual(ec.validate_feature_flags("1", "1", "0"), (1, 1, 0, 0))

    def test_all_off_is_ok(self):
        self.assertEqual(ec.validate_feature_flags("0", "0", "0"), (0, 0, 0, 0))

    def test_hooks_without_sample_is_ok(self):
        self.assertEqual(ec.validate_feature_flags("1", "0", "1"), (1, 0, 1, 0))

    def test_content_defaults_off_for_legacy_three_argument_callers(self):
        """The issue #6 Sprint 2 content flag is an OPTIONAL fourth switch:
        an existing three-argument call keeps its exact previous meaning and
        simply resolves the content flag to 0."""
        self.assertEqual(ec.validate_feature_flags("1", "1", "1"), (1, 1, 1, 0))


class LoadIdentityFeatureFlagTests(unittest.TestCase):
    """Issue #6: flags flow into the identity/JSON and the fingerprint, but
    never into the independent save-compatibility epoch/key."""

    def _identity(self, tmp, **kwargs):
        config_mk = write_config_mk(Path(tmp))
        return ec.load_identity(
            config_mk_path=config_mk,
            config_preset="debug",
            abi="aapcs",
            rom_size="16M",
            repo_root=Path(tmp),
            **kwargs,
        )

    def test_absent_flags_default_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(tmp)
        self.assertEqual(identity.mechanics_hooks, 0)
        self.assertEqual(identity.mechanics_sample, 0)
        self.assertEqual(identity.danger_overlay_menu, 0)
        self.assertEqual(identity.blue_phase_delegate, 0)

    def test_flags_appear_in_json_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(
                tmp,
                mechanics_hooks="1",
                danger_overlay_menu="1",
                blue_phase_delegate="1",
            )
        data = identity.to_dict()
        self.assertEqual(data["mechanics_hooks"], 1)
        self.assertEqual(data["mechanics_sample"], 0)
        self.assertEqual(data["danger_overlay_menu"], 1)
        self.assertEqual(data["blue_phase_delegate"], 1)

    def test_autoplay_profile_flag_changes_identity_without_save_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._identity(tmp)
            enabled = self._identity(tmp, autoplay_strategies="1")
            configured = ec.load_identity(
                config_mk_path=write_config_mk(Path(tmp), autoplay_strategies="1"),
                config_preset="debug",
                abi="aapcs",
                rom_size="16M",
                repo_root=Path(tmp),
            )
        self.assertEqual(base.autoplay_strategies, 0)
        self.assertEqual(enabled.autoplay_strategies, 1)
        self.assertEqual(configured.autoplay_strategies, 1)
        self.assertNotEqual(base.config_fingerprint, enabled.config_fingerprint)
        self.assertEqual(base.save_compat_epoch, enabled.save_compat_epoch)
        with self.assertRaises(ec.ConfigError):
            ec.validate_feature_flag("EXPANSION_AUTOPLAY_STRATEGIES", "2")

    def test_flag_change_changes_fingerprint_but_not_epoch_or_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._identity(tmp)
            hooks = self._identity(tmp, mechanics_hooks="1")
            sample = self._identity(tmp, mechanics_hooks="1", mechanics_sample="1")
            danger = self._identity(tmp, danger_overlay_menu="1")
            delegate = self._identity(tmp, blue_phase_delegate="1")
        # Every flag toggle changes the config fingerprint...
        self.assertNotEqual(base.config_fingerprint, hooks.config_fingerprint)
        self.assertNotEqual(hooks.config_fingerprint, sample.config_fingerprint)
        self.assertNotEqual(base.config_fingerprint, danger.config_fingerprint)
        self.assertNotEqual(base.config_fingerprint, delegate.config_fingerprint)
        # ...while the save-compatibility epoch/key is untouched by all of them.
        for identity in (base, hooks, sample, danger, delegate):
            self.assertEqual(identity.save_compat_epoch, 1)

    def test_feature_flags_are_not_in_save_compat_key(self):
        """The save-compatibility gate is the epoch alone; flags live only in
        the diagnostic fingerprint, so they can never make an existing save
        look incompatible."""
        with tempfile.TemporaryDirectory() as tmp:
            base = self._identity(tmp)
            hooks = self._identity(tmp, mechanics_hooks="1")
        self.assertEqual(base.save_compat_epoch, hooks.save_compat_epoch)
        self.assertIn("features", base.fingerprint_fields())
        self.assertNotIn("save_compat_epoch", base.fingerprint_fields())

    def test_fingerprint_features_cover_all_parsed_feature_config_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = self._identity(tmp)
            custom_spell = self._identity(
                tmp,
                custom_spell_effects="1",
                asset_manifest=(
                    ROOT / "assets" / "manifests"
                    / "custom-spell-reference.json"
                ),
            )

        expected = {
            key.removeprefix("EXPANSION_").lower()
            for key in ec.CONFIG_MK_FEATURE_KEYS
        }
        actual = (
            set(default.fingerprint_fields()["features"])
            | set(custom_spell.fingerprint_fields()["features"])
            | {"bgm_continuation_policy"}
        )
        self.assertEqual(actual, expected)
        self.assertIn("bgm_continuation_policy", default.fingerprint_fields())

    def test_custom_spell_default_preserves_pre_feature_fingerprint_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            implicit_default = self._identity(tmp)
            explicit_default = self._identity(tmp, custom_spell_effects="0")
            enabled = self._identity(
                tmp,
                custom_spell_effects="1",
                asset_manifest=(
                    ROOT / "assets" / "manifests"
                    / "custom-spell-reference.json"
                ),
            )

        self.assertEqual(
            implicit_default.fingerprint_fields(),
            explicit_default.fingerprint_fields(),
        )
        self.assertEqual(
            implicit_default.config_fingerprint,
            explicit_default.config_fingerprint,
        )
        self.assertNotIn(
            "custom_spell_effects",
            implicit_default.fingerprint_fields()["features"],
        )
        self.assertNotIn(
            "custom_spell_effect_contract",
            implicit_default.fingerprint_fields(),
        )
        self.assertEqual(
            enabled.fingerprint_fields()["features"]["custom_spell_effects"],
            1,
        )
        self.assertIn("custom_spell_effect_contract", enabled.fingerprint_fields())
        self.assertNotEqual(
            implicit_default.config_fingerprint,
            enabled.config_fingerprint,
        )

    def test_custom_spell_inventory_digest_changes_fingerprint(self):
        original = {
            "inventory_digest": "1" * 64,
            "resource_digest": "2" * 64,
        }
        moved_sprite = {
            "inventory_digest": "3" * 64,
            "resource_digest": original["resource_digest"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                ec,
                "resolve_custom_spell_contract",
                side_effect=(original, moved_sprite),
            ):
                original_identity = self._identity(
                    tmp,
                    custom_spell_effects="1",
                    asset_manifest="unused.json",
                )
                moved_identity = self._identity(
                    tmp,
                    custom_spell_effects="1",
                    asset_manifest="unused.json",
                )

        self.assertNotEqual(
            original_identity.custom_spell_effect_inventory_digest,
            moved_identity.custom_spell_effect_inventory_digest,
        )
        self.assertEqual(
            original_identity.custom_spell_effect_resource_budget_digest,
            moved_identity.custom_spell_effect_resource_budget_digest,
        )
        self.assertNotEqual(
            original_identity.config_fingerprint, moved_identity.config_fingerprint
        )
        self.assertEqual(
            original_identity.save_compat_epoch, moved_identity.save_compat_epoch
        )

    def test_custom_spell_uses_resolved_item_cap_not_environment(self):
        contract = {
            "inventory_digest": "1" * 64,
            "resource_digest": "2" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(
                    "os.environ", {"FE8_ITEM_ID_CAP": "0xCD"}, clear=False
                ),
                mock.patch.object(
                    ec,
                    "resolve_custom_spell_contract",
                    return_value=contract,
                ) as resolver,
            ):
                identity = self._identity(
                    tmp,
                    custom_spell_effects="1",
                    asset_manifest="unused.json",
                    item_id_cap="0xCE",
                )
        self.assertEqual(identity.item_id_cap, 0xCE)
        resolver.assert_called_once_with(
            Path(tmp), "unused.json", 1, 0xCE
        )

    def test_sample_without_hooks_rejected_in_load_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ec.ConfigError):
                self._identity(tmp, mechanics_sample="1")


class FeatureFlagCliTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "modernize" / "expansion_config.py"

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _resolve(self, tmp, *extra):
        config_mk = write_config_mk(Path(tmp))
        return self.run_cli(
            "resolve",
            "--config-mk", str(config_mk),
            "--config", "debug",
            "--abi", "aapcs",
            "--rom-size", "16M",
            "--repo-root", tmp,
            *extra,
        )

    @staticmethod
    def _fingerprint_of(stdout):
        for token in stdout.split():
            if token.startswith("MODERN_CONFIG_FINGERPRINT="):
                return token
        raise AssertionError(f"no fingerprint token in: {stdout}")

    def test_hooks_flag_changes_fingerprint_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = self._resolve(tmp)
            hooks = self._resolve(tmp, "--mechanics-hooks", "1")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(hooks.returncode, 0, hooks.stderr)
        self.assertNotEqual(
            self._fingerprint_of(baseline.stdout), self._fingerprint_of(hooks.stdout)
        )
        # The epoch token is unchanged by a feature-flag toggle.
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=1", baseline.stdout)
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=1", hooks.stdout)

    def test_sample_without_hooks_fails_at_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._resolve(tmp, "--mechanics-sample", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_MECHANICS_SAMPLE=1", result.stderr)

    def test_invalid_flag_value_fails_at_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._resolve(tmp, "--blue-phase-delegate", "2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("out of range", result.stderr)
        self.assertIn("EXPANSION_BLUE_PHASE_DELEGATE", result.stderr)

    def test_generate_json_contains_feature_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            out_dir = Path(tmp) / "generated"
            result = self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--mechanics-hooks", "1",
                "--danger-overlay-menu", "1",
                "--blue-phase-delegate", "1",
                "--output-dir", str(out_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(
                (out_dir / "expansion_build_metadata.json").read_text(encoding="utf-8")
            )
        self.assertEqual(data["mechanics_hooks"], 1)
        self.assertEqual(data["mechanics_sample"], 0)
        self.assertEqual(data["danger_overlay_menu"], 1)
        self.assertEqual(data["blue_phase_delegate"], 1)


class BluePhaseDelegateFlagTests(unittest.TestCase):
    def _identity(self, **kwargs):
        params = dict(
            config_mk_path=ROOT / "config.mk",
            config_preset="release",
            abi="aapcs",
            rom_size="16M",
            build_id_override="abcdef12",
        )
        params.update(kwargs)
        return ec.load_identity(**params)

    def test_default_is_off_and_strict(self):
        identity = self._identity()
        self.assertEqual(identity.blue_phase_delegate, 0)
        self.assertEqual(
            identity.fingerprint_fields()["features"]["blue_phase_delegate"], 0
        )
        for bad in ("-1", "2", "yes"):
            with self.subTest(value=bad):
                with self.assertRaises(ec.ConfigError) as ctx:
                    self._identity(blue_phase_delegate=bad)
                self.assertIn("EXPANSION_BLUE_PHASE_DELEGATE", str(ctx.exception))

    def test_toggle_changes_identity_but_not_save_compatibility(self):
        disabled = self._identity(blue_phase_delegate=0)
        enabled = self._identity(blue_phase_delegate=1)
        self.assertNotEqual(disabled.config_fingerprint, enabled.config_fingerprint)
        self.assertEqual(disabled.save_compat_epoch, enabled.save_compat_epoch)

    def test_c_macro_defaults_and_validates_profiles(self):
        macro = "FE8_EXPANSION_BLUE_PHASE_DELEGATE"
        result = preprocess_expansion_header("expansion_config.h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(preprocessor_macros(result.stdout).get(macro), "0")

        for value in ("0", "1"):
            with self.subTest(value=value):
                result = preprocess_expansion_header(
                    "expansion_config.h",
                    "FE8_EXPANSION_MODERN_BUILD=1",
                    f"{macro}={value}",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(preprocessor_macros(result.stdout).get(macro), value)

        for value in ("-1", "2"):
            with self.subTest(value=value):
                result = preprocess_expansion_header(
                    "expansion_config.h",
                    "FE8_EXPANSION_MODERN_BUILD=1",
                    f"{macro}={value}",
                )
                self.assertNotEqual(result.returncode, 0)

        result = preprocess_expansion_header(
            "expansion_config.h",
            "FE8_EXPANSION_MODERN_BUILD=0",
            f"{macro}=1",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_debug_linker_check_selects_arm_configuration_cases(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="blue-route-", dir=ROOT / "build") as temporary:
            build_root = Path(temporary).relative_to(ROOT) / "modern"
            environment = {
                "HOME": temporary,
                "LC_ALL": "C",
                "PATH": os.environ.get("PATH", os.defpath),
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": temporary,
            }
            result = subprocess.run(
                [
                    "make", "--no-print-directory", "-rR", "-n", "-p",
                    "--eval=__blue_route_probe__:; @:", "__blue_route_probe__",
                    "NODEP=1", "MODERN_DRY_RUN=1", "MODERN_CONFIG=debug",
                    "FE8_PROJECT=default", f"MODERN_BUILD_ROOT={build_root}",
                    f"PYTHON={sys.executable}",
                ],
                cwd=ROOT, env=environment, text=True, capture_output=True,
                check=False, timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        rule = make_database_rule(result.stdout, "expansion-modern-linker-check")
        self.assertIsNotNone(rule)
        commands = (
            shlex.split(line)
            for line in re.sub(r"\\\n[ \t]*", " ", rule).splitlines()
            if line.startswith("\t")
        )
        module = "scripts.modernize.tests.test_expansion_config"
        selectors = []
        for command in commands:
            for index in range(len(command) - 1):
                if command[index:index + 2] == ["-m", "unittest"]:
                    selectors.extend(
                        arg for arg in command[index + 2:]
                        if arg == module or arg.startswith(f"{module}.")
                    )

        def case_ids(suite):
            for case in suite:
                if isinstance(case, unittest.TestSuite):
                    yield from case_ids(case)
                else:
                    yield case.id()

        loaded = unittest.TestLoader().loadTestsFromNames(selectors)
        required = {
            f"{module}.{self.__class__.__name__}.{method.__name__}"
            for method in (
                self.test_c_macro_defaults_and_validates_profiles,
                self.test_make_choice_reaches_compiler_metadata_and_stamp,
            )
        }
        self.assertEqual(required - set(case_ids(loaded)), set())

    def test_make_choice_reaches_compiler_metadata_and_stamp(self):
        compiler = find_modern_compiler()
        if compiler is None:
            self.skipTest("arm-none-eabi-gcc is not available")

        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="blue-config-", dir=ROOT / "build") as temporary:
            build_root = Path(temporary) / "modern"
            generated = build_root / "debug" / "aapcs" / "generated"
            make_root = build_root.relative_to(ROOT)
            make_generated = make_root / "debug" / "aapcs" / "generated"
            environment = {
                "HOME": temporary,
                "LC_ALL": "C",
                "PATH": os.environ.get("PATH", os.defpath),
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": temporary,
            }
            snapshots = []
            for value in (0, 1):
                result = subprocess.run(
                    [
                        "make",
                        "--no-print-directory",
                        "NODEP=1",
                        "print-MODERN_CFLAGS",
                        str(make_generated / "compile_settings.txt"),
                        str(make_generated / "expansion_build_metadata.json"),
                        f"MODERN_BUILD_ROOT={make_root}",
                        "MODERN_CONFIG=debug",
                        "MODERN_ABI=aapcs",
                        f"MODERN_CC={compiler}",
                        "FE8_PROJECT=default",
                        f"EXPANSION_BLUE_PHASE_DELEGATE={value}",
                        "MODERN_CONFIG_RESOLVE_GOALS=print-MODERN_CFLAGS",
                    ],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                match = re.search(
                    r"^MODERN_CFLAGS is a \w+ variable set to \[(.*)\]$",
                    result.stdout,
                    re.MULTILINE,
                )
                self.assertIsNotNone(match, result.stdout)
                compiled = subprocess.run(
                    [
                        compiler,
                        "-E",
                        "-dM",
                        "-x",
                        "c",
                        *shlex.split(match.group(1)),
                        "-",
                    ],
                    cwd=ROOT,
                    env=environment,
                    input='#include "expansion_config.h"\n',
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                self.assertEqual(
                    preprocessor_macros(compiled.stdout).get(
                        "FE8_EXPANSION_BLUE_PHASE_DELEGATE"
                    ),
                    str(value),
                )

                settings = dict(
                    line.split("=", 1)
                    for line in (generated / "compile_settings.txt")
                    .read_text(encoding="utf-8").splitlines()
                )
                metadata = json.loads(
                    (generated / "expansion_build_metadata.json")
                    .read_text(encoding="utf-8")
                )
                self.assertEqual(settings["blue_phase_delegate"], str(value))
                self.assertEqual(metadata["blue_phase_delegate"], value)
                self.assertEqual(
                    settings["config_fingerprint"], metadata["config_fingerprint"]
                )
                self.assertEqual(
                    int(settings["save_compat_epoch"]), metadata["save_compat_epoch"]
                )
                snapshots.append(metadata)

        self.assertNotEqual(
            snapshots[0]["config_fingerprint"], snapshots[1]["config_fingerprint"]
        )
        self.assertEqual(
            snapshots[0]["save_compat_epoch"], snapshots[1]["save_compat_epoch"]
        )


class StarterContentFlagTests(unittest.TestCase):
    """Issue #6 Sprint 2: the EXPANSION_STARTER_CONTENT opt-in flag.

    An independent 0/1 identity flag with two hard dependencies -- the
    mechanics hook registry, and an item ID cap that actually reaches
    ITEM_EXPANSION_CE -- folded into the config fingerprint and the
    generated metadata, and deliberately NOT part of the save format.
    """

    def _identity(self, **kwargs):
        params = dict(
            config_mk_path=ROOT / "config.mk",
            config_preset="release",
            abi="aapcs",
            rom_size="16M",
            build_id_override="abcdef12",
        )
        params.update(kwargs)
        return ec.load_identity(**params)

    def test_default_is_off(self):
        identity = self._identity()
        self.assertEqual(identity.starter_content, 0)
        self.assertEqual(
            identity.fingerprint_fields()["features"]["starter_content"], 0)

    def test_config_mk_default_is_zero(self):
        cfg = ec.parse_config_mk(ROOT / "config.mk")
        self.assertEqual(cfg["EXPANSION_STARTER_CONTENT"], "0")

    def test_enabled_requires_mechanics_hooks(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            self._identity(starter_content=1, mechanics_hooks=0,
                           item_id_cap="0xCE")
        message = str(ctx.exception)
        self.assertIn("EXPANSION_STARTER_CONTENT=1", message)
        self.assertIn("EXPANSION_MECHANICS_HOOKS=1", message)

    def test_enabled_requires_expanded_item_cap(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            self._identity(starter_content=1, mechanics_hooks=1)
        message = str(ctx.exception)
        self.assertIn("EXPANSION_STARTER_CONTENT=1", message)
        self.assertIn("FE8_ITEM_ID_CAP", message)
        self.assertIn("ITEM_EXPANSION_CE", message)

    def test_enabled_with_both_dependencies_resolves(self):
        identity = self._identity(starter_content=1, mechanics_hooks=1,
                                  item_id_cap="0xCE")
        self.assertEqual(identity.starter_content, 1)

    def test_invalid_values_rejected_actionably(self):
        for bad in ("2", "-1", "yes", ""):
            if bad == "":
                continue
            with self.assertRaises(ec.ConfigError) as ctx:
                self._identity(starter_content=bad, mechanics_hooks=1,
                               item_id_cap="0xCE")
            self.assertIn("EXPANSION_STARTER_CONTENT", str(ctx.exception))

    def test_platform_stays_testable_at_any_cap_with_content_off(self):
        """The dependency is one-way: raising the cap alone must never
        require the content flag, so the issue #10 ID-space platform stays
        independently testable."""
        for cap in (None, "0xCD", "0xCE", "0xFF"):
            identity = self._identity(starter_content=0, item_id_cap=cap)
            self.assertEqual(identity.starter_content, 0)

    def test_flag_changes_the_config_fingerprint(self):
        off = self._identity(starter_content=0, mechanics_hooks=1,
                             item_id_cap="0xCE")
        on = self._identity(starter_content=1, mechanics_hooks=1,
                            item_id_cap="0xCE")
        self.assertNotEqual(off.config_fingerprint, on.config_fingerprint)

    def test_flag_never_changes_the_save_compat_epoch(self):
        off = self._identity(starter_content=0, mechanics_hooks=1,
                             item_id_cap="0xCE")
        on = self._identity(starter_content=1, mechanics_hooks=1,
                            item_id_cap="0xCE")
        self.assertEqual(off.save_compat_epoch, on.save_compat_epoch)
        # Compared against the real, committed config.mk's current epoch
        # (issue #18 sprint 2 bumped it 1 -> 2) rather than a hardcoded
        # literal, so this test never silently drifts from that file again.
        cfg = ec.parse_config_mk(ROOT / "config.mk")
        self.assertEqual(
            on.save_compat_epoch, int(cfg["EXPANSION_SAVE_COMPAT_EPOCH"])
        )

    def test_flag_appears_in_generated_metadata_json(self):
        identity = self._identity(starter_content=1, mechanics_hooks=1,
                                  item_id_cap="0xCE")
        with tempfile.TemporaryDirectory() as tmp:
            paths = ec.generate_metadata_files(Path(tmp), identity)
            data = json.loads(paths["json"].read_text(encoding="utf-8"))
        self.assertEqual(data["starter_content"], 1)

    def test_item_cap_constants_match_the_idspace_source_of_truth(self):
        """expansion_config.py restates the item cap boundary because it runs
        as a bare script; it must never drift from idspace.py, which owns it."""
        sys.path.insert(0, str(ROOT))
        from scripts.generated_data import idspace

        self.assertEqual(ec.ITEM_ID_EXPANSION_FIRST, idspace.ITEM_EXPANSION_FIRST)
        self.assertEqual(ec.ITEM_ID_DEFAULT_CAP, idspace.ITEM_DEFAULT_CAP)
        self.assertEqual(ec.ITEM_ID_DEFAULT_CAP,
                         idspace.domain_by_key("item").configured_cap)

    def test_invalid_item_cap_rejected(self):
        with self.assertRaises(ec.ConfigError):
            self._identity(item_id_cap="not-a-number")
        with self.assertRaises(ec.ConfigError):
            self._identity(item_id_cap="0x100")


class StarterContentCompileTimeContractTests(unittest.TestCase):
    """The same two dependencies must also be hard C compile errors."""

    preprocess = staticmethod(preprocess_expansion_header)
    macros = staticmethod(preprocessor_macros)

    @mock.patch.dict(os.environ, {"MODERN_CC": ""})
    @mock.patch("subprocess.run")
    @mock.patch("shutil.which")
    def test_preprocess_falls_back_to_gcc_driver(self, which, run):
        which.side_effect = lambda name: (
            "/toolchain/arm-none-eabi-gcc"
            if name == "arm-none-eabi-gcc"
            else None
        )

        self.preprocess("expansion_config.h")

        self.assertEqual(
            which.call_args_list,
            [
                mock.call("arm-none-eabi-cpp"),
                mock.call("arm-none-eabi-gcc"),
            ],
        )
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/toolchain/arm-none-eabi-gcc")
        self.assertIn("-E", command)
        self.assertIn("-dM", command)

    @mock.patch.dict(os.environ, {"MODERN_CC": "/custom/arm-gcc"})
    @mock.patch("subprocess.run")
    @mock.patch("shutil.which", return_value="/custom/arm-gcc")
    def test_preprocess_uses_selected_compiler(self, which, run):
        self.preprocess("expansion_config.h")

        which.assert_called_once_with("/custom/arm-gcc")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/custom/arm-gcc")
        self.assertIn("-E", command)
        self.assertIn("-dM", command)

    @mock.patch.dict(os.environ, {"MODERN_CC": "/missing/arm-gcc"})
    @mock.patch("shutil.which", return_value=None)
    def test_missing_selected_compiler_is_an_error(self, which):
        with self.assertRaisesRegex(FileNotFoundError, "MODERN_CC"):
            self.preprocess("expansion_config.h")
        which.assert_called_once_with("/missing/arm-gcc")

    def test_macros_accepts_valued_and_value_less_definitions(self):
        fixture = "\n".join(
            (
                "#define FE8_EXPANSION_STARTER_CONTENT 0",
                "#define CPP_IMPLEMENTATION",
            )
        )
        values = self.macros(fixture)
        self.assertEqual(values["FE8_EXPANSION_STARTER_CONTENT"], "0")
        self.assertEqual(values["CPP_IMPLEMENTATION"], "")

    def test_macros_treats_a_value_less_mutation_as_empty(self):
        fixture = "#define FE8_EXPANSION_STARTER_CONTENT 1"
        mutated = fixture.replace(" 1", "")
        self.assertEqual(
            self.macros(fixture)["FE8_EXPANSION_STARTER_CONTENT"],
            "1",
        )
        self.assertEqual(
            self.macros(mutated)["FE8_EXPANSION_STARTER_CONTENT"],
            "",
        )

    def test_config_header_defaults_the_flag_off(self):
        result = self.preprocess("expansion_config.h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.macros(result.stdout)["FE8_EXPANSION_STARTER_CONTENT"],
            "0",
        )

    def test_config_header_errors_without_hooks(self):
        result = self.preprocess(
            "expansion_config.h",
            "FE8_EXPANSION_STARTER_CONTENT=1",
            "FE8_EXPANSION_MECHANICS_HOOKS=0",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires FE8_EXPANSION_MECHANICS_HOOKS=1", result.stderr)

    def test_content_header_errors_below_the_expansion_cap(self):
        result = self.preprocess(
            "expansion_starter_content.h",
            "FE8_EXPANSION_STARTER_CONTENT=1",
            "FE8_EXPANSION_MECHANICS_HOOKS=1",
            "FE8_ITEM_ID_CAP=0xCD",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires an expanded item cap", result.stderr)

    def test_modern_mk_flows_the_flag_and_cap(self):
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-rR",
                "-n",
                "-p",
                "__issue102_config_probe__",
                "MODERN_CONFIG_RESOLVE_GOALS=__issue102_config_probe__",
                "EXPANSION_STARTER_CONTENT=1",
                "EXPANSION_MECHANICS_HOOKS=1",
                "FE8_ITEM_ID_CAP=0xCE",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        cflags = make_database_variable(result.stdout, "MODERN_CFLAGS")
        self.assertIsNotNone(cflags, result.stdout[-4000:])
        self.assertIn("-DFE8_EXPANSION_STARTER_CONTENT=1", cflags)
        self.assertIn("-DFE8_ITEM_ID_CAP=0xCE", cflags)

    def test_make_database_variable_collects_cflags_continuations(self):
        fixture = "\n".join(
            (
                "MODERN_CFLAGS := -DFE8_EXPANSION_STARTER_CONTENT=1 \\",
                "    -DFE8_ITEM_ID_CAP=0xCE",
                "    recipe-must-not-be-part-of-the-variable",
                "# automatic",
            )
        )
        cflags = make_database_variable(fixture, "MODERN_CFLAGS")
        self.assertIn("-DFE8_EXPANSION_STARTER_CONTENT=1", cflags)
        self.assertIn("-DFE8_ITEM_ID_CAP=0xCE", cflags)


if __name__ == "__main__":
    unittest.main()
