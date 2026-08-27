"""Issue #168 optional map-menu presentation regression checks."""

import contextlib
import copy
import io
import json
import re
import runpy
import shutil
import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path

from scripts.localization.generate import _c_string_literal, generate
from scripts.localization.game_locales.ending_metrics import (
    _ascii_widths,
    _cjk_widths,
    _line_width,
)


ROOT = Path(__file__).resolve().parents[3]
BUILD = ROOT / "build"
MENU_DEF = ROOT / "src" / "menu_def.c"
CC = shutil.which("gcc") or shutil.which("cc")
PRODUCTION_LOCALES = ("en", "ja", "zh-Hans", "fr", "de", "es", "it")
LABEL_KEYS = ("danger_overlay.label", "autoplay.charge.label")
HELP_KEYS = ("danger_overlay.help", "autoplay.charge.help")
RUNTIME_RUNNER = ROOT / "tools" / "gba-playtest" / "run_map_menu_presentation_checks.py"
RUNTIME_FINGERPRINT = (
    ROOT
    / "tools"
    / "gba-playtest"
    / "fingerprints"
    / "map-menu-presentation-all-locales-all-features-release.json"
)
HELP_SIZING_DRIVER = (
    ROOT
    / "tools"
    / "gba-playtest"
    / "tests"
    / "c"
    / "map_menu_help_sizing_driver.c"
)
MAP_MENU_DRAW_DRIVER = (
    ROOT
    / "tools"
    / "gba-playtest"
    / "tests"
    / "c"
    / "map_menu_draw_driver.c"
)


def _run(command):
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _write_message_header(directory):
    path = Path(directory) / "expansion_msg_ids.h"
    path.write_text(
        "#define EXP_MSG_RAW_SURFACE_UNIT_ACTION_SUMMON 33u\n"
        "#define EXP_MSG_RAW_SURFACE_UNIT_ACTION_CALL_MONSTER 34u\n"
        "#define EXP_MSG_AUTOPLAY_CHARGE_LABEL 80u\n"
        "#define EXP_MSG_AUTOPLAY_CHARGE_HELP 81u\n"
        "#define EXP_MSG_DANGER_OVERLAY_LABEL 144u\n"
        "#define EXP_MSG_DANGER_OVERLAY_HELP 145u\n",
        encoding="utf-8",
    )


def _menu_entries(directory, danger, charge):
    _write_message_header(directory)
    completed = _run(
        [
            CC,
            "-E",
            "-P",
            "-I",
            str(directory),
            "-I",
            str(ROOT / "include"),
            "-DMODERN=1",
            "-DFE8_EXPANSION_MODERN_BUILD=1",
            f"-DFE8_EXPANSION_DANGER_OVERLAY_MENU={int(danger)}",
            f"-DFE8_EXPANSION_BLUE_PHASE_DELEGATE={int(charge)}",
            str(MENU_DEF),
        ]
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)
    match = re.search(
        r"\bgMapMenuItems\[\].*?=\s*\{(.*?)\n\};",
        completed.stdout,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError("preprocessed gMapMenuItems initializer is missing")
    entries = []
    for body in re.findall(r"\{([^{}]*)\}", match.group(1), flags=re.DOTALL):
        entries.append([field.strip() for field in body.split(",")])
    return entries


def _normalized_latin_width(text, ascii_widths):
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return _line_width(
        normalized,
        locale="en",
        ascii_widths=ascii_widths,
        cjk_widths={},
    )


class MapMenuCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if CC is None:
            raise unittest.SkipTest("no host C compiler")
        BUILD.mkdir(exist_ok=True)

    def test_disabled_and_enabled_compositions_keep_end_last(self):
        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            for danger, charge, expected_prefix in (
                (False, False, ()),
                (True, False, ("144u",)),
                (False, True, ("80u",)),
                (True, True, ("144u", "80u")),
            ):
                with self.subTest(danger=danger, charge=charge):
                    entries = _menu_entries(temporary, danger, charge)
                    visible = entries[:-1]
                    message_ids = [entry[1] for entry in visible]
                    self.assertEqual(
                        tuple(message_ids[: len(expected_prefix)]),
                        expected_prefix,
                    )
                    self.assertEqual(message_ids[-1], "0x6A0")
                    self.assertEqual(len(visible), 8 + int(danger) + int(charge))

    def test_optional_rows_use_stable_ids_and_native_geometry(self):
        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            entries = _menu_entries(temporary, True, True)
            danger, charge = entries[:2]
            self.assertEqual(danger[1:3], ["144u", "145u"])
            self.assertEqual(charge[1:3], ["80u", "81u"])

            binary = Path(temporary) / "map-menu-draw"
            completed = _run(
                [
                    CC,
                    "-std=gnu89",
                    "-O2",
                    "-w",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-I",
                    str(ROOT / "include"),
                    "-DMODERN=1",
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_DANGER_OVERLAY_MENU=1",
                    "-DFE8_EXPANSION_BLUE_PHASE_DELEGATE=0",
                    str(ROOT / "src" / "bmmenu.c"),
                    str(MAP_MENU_DRAW_DRIVER),
                    "-Wl,--gc-sections",
                    "-o",
                    str(binary),
                ]
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = _run([str(binary)])
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn("MAP_MENU_DRAW_CALLBACK: PASS", completed.stdout)


class MapMenuLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(exist_ok=True)
        cls.registry = json.loads(
            (ROOT / "texts" / "expansion" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        cls.catalogs = {
            locale: json.loads(
                (
                    ROOT / "texts" / "expansion" / f"catalog.{locale}.json"
                ).read_text(encoding="utf-8")
            )["strings"]
            for locale in PRODUCTION_LOCALES
        }
        cls.ascii_widths = _ascii_widths(ROOT)
        cls.cjk_widths = {
            locale: _cjk_widths(ROOT, locale)[0]
            for locale in ("ja", "zh-Hans")
        }

    def _width(self, text, locale):
        if locale in self.cjk_widths:
            return _line_width(
                text,
                locale=locale,
                ascii_widths=self.ascii_widths,
                cjk_widths=self.cjk_widths[locale],
            )
        if locale == "en":
            return _line_width(
                text,
                locale=locale,
                ascii_widths=self.ascii_widths,
                cjk_widths={},
            )
        return _normalized_latin_width(text, self.ascii_widths)

    def test_stable_ids_and_all_production_text_bounds(self):
        entries = {entry["key"]: entry for entry in self.registry["messages"]}
        self.assertEqual(entries["autoplay.charge.label"]["id"], 80)
        self.assertEqual(entries["autoplay.charge.help"]["id"], 81)
        self.assertEqual(entries["danger_overlay.label"]["id"], 144)
        self.assertEqual(entries["danger_overlay.help"]["id"], 145)

        for locale, strings in self.catalogs.items():
            for key in LABEL_KEYS:
                with self.subTest(locale=locale, key=key):
                    self.assertTrue(strings[key])
                    self.assertNotIn("\n", strings[key])
                    self.assertLessEqual(
                        8 + self._width(strings[key], locale),
                        48,
                    )
            for key in HELP_KEYS:
                with self.subTest(locale=locale, key=key):
                    lines = strings[key].split("\n")
                    self.assertEqual(len(lines), 2)
                    self.assertTrue(all(lines))
                    self.assertLessEqual(
                        max(self._width(line, locale) for line in lines),
                        208,
                    )

    def test_real_resolver_returns_every_label_and_help(self):
        if CC is None:
            self.skipTest("no host C compiler")
        locale_constants = {
            "en": "EXPANSION_LOCALE_EN",
            "ja": "EXPANSION_LOCALE_JA",
            "zh-Hans": "EXPANSION_LOCALE_ZH_HANS",
            "fr": "EXPANSION_LOCALE_FR",
            "de": "EXPANSION_LOCALE_DE",
            "es": "EXPANSION_LOCALE_ES",
            "it": "EXPANSION_LOCALE_IT",
        }
        id_constants = {
            "danger_overlay.label": "EXP_MSG_DANGER_OVERLAY_LABEL",
            "danger_overlay.help": "EXP_MSG_DANGER_OVERLAY_HELP",
            "autoplay.charge.label": "EXP_MSG_AUTOPLAY_CHARGE_LABEL",
            "autoplay.charge.help": "EXP_MSG_AUTOPLAY_CHARGE_HELP",
        }
        rows = []
        for locale in PRODUCTION_LOCALES:
            for key in (*LABEL_KEYS, *HELP_KEYS):
                runtime_text = self.catalogs[locale][key].replace("\n", "\x01")
                rows.append(
                    "    {%s, %s, %s},"
                    % (
                        locale_constants[locale],
                        id_constants[key],
                        _c_string_literal(runtime_text),
                    )
                )
        driver = """
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef u8 bool8;
#define TRUE 1
#define FALSE 0
#include "expansion_locale.h"
#include "expansion_msg_ids.h"
struct Expected { ExpansionLocaleId locale; ExpansionMsgId id; const char *text; };
static const struct Expected sExpected[] = {
%s
};
void LocalizedGameText_InvalidateCache(void) {}
int main(void)
{
    static const ExpansionLocaleId productionLocales[] = {
        EXPANSION_LOCALE_EN,
        EXPANSION_LOCALE_JA,
        EXPANSION_LOCALE_ZH_HANS,
        EXPANSION_LOCALE_FR,
        EXPANSION_LOCALE_DE,
        EXPANSION_LOCALE_ES,
        EXPANSION_LOCALE_IT,
    };
    unsigned i;
    for (i = 0; i < sizeof(productionLocales) / sizeof(productionLocales[0]); i++)
    {
        if (ExpansionLocale_IsEnabled(productionLocales[i]) != TRUE)
            return 3;
    }
    if (ExpansionLocale_IsEnabled(EXPANSION_LOCALE_QPS_PLOC) != FALSE)
        return 4;
    if (ExpansionLocale_SetCurrent(EXPANSION_LOCALE_FR) != TRUE)
        return 5;
    if (strcmp(
            ExpansionLocale_ResolveCurrent(EXP_MSG_DANGER_OVERLAY_LABEL),
            "Danger") != 0)
        return 6;
    if (ExpansionLocale_SetCurrent(EXPANSION_LOCALE_QPS_PLOC) != FALSE)
        return 7;
    if (ExpansionLocale_GetCurrent() != EXPANSION_LOCALE_FR)
        return 8;

    for (i = 0; i < sizeof(sExpected) / sizeof(sExpected[0]); i++)
    {
        if (strcmp(
                ExpansionLocale_Resolve(sExpected[i].locale, sExpected[i].id),
                sExpected[i].text) != 0)
            return 1;
        if (strcmp(
                ExpansionLocale_ResolvePersistent(
                    sExpected[i].locale, sExpected[i].id),
                sExpected[i].text) != 0)
            return 2;
    }
    puts("MAP_MENU_LOCALIZATION_RESOLVER: PASS");
    return 0;
}
""" % "\n".join(rows)

        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            work = Path(temporary)
            generated = work / "generated"
            generate(output_dir=generated)
            source = work / "resolver_driver.c"
            source.write_text(driver, encoding="utf-8")
            binary = work / "resolver_driver"
            completed = _run(
                [
                    CC,
                    "-std=gnu89",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-I",
                    str(ROOT / "include"),
                    "-I",
                    str(generated),
                    "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=0x7Fu",
                    "-DFE8_EXPANSION_DEFAULT_LOCALE_ID=0u",
                    "-DFE8_EXPANSION_PSEUDO_LOCALE_ENABLED=0",
                    "-DMODERN=1",
                    str(source),
                    str(ROOT / "src" / "expansion_locale.c"),
                    str(generated / "expansion_locale_catalog.c"),
                    "-Wl,--gc-sections",
                    "-o",
                    str(binary),
                ]
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = _run([str(binary)])
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn("MAP_MENU_LOCALIZATION_RESOLVER: PASS", completed.stdout)


class MapMenuHelpSizingTests(unittest.TestCase):
    def _compile_and_run(self, modern):
        if CC is None:
            self.skipTest("no host C compiler")
        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            work = Path(temporary)
            graphics = work / "graphics"
            graphics.mkdir()
            (graphics / "debug_font.4bpp.h").write_text(
                "static const unsigned char debug_font_4bpp[1] = {0};\n",
                encoding="utf-8",
            )
            binary = work / ("help-sizing-modern" if modern else "help-sizing-archival")
            command = [
                CC,
                "-std=gnu89",
                "-O2",
                "-w",
                "-ffunction-sections",
                "-fdata-sections",
                "-DNONMATCHING=1",
                "-I",
                str(work),
                "-I",
                str(ROOT / "include"),
            ]
            if modern:
                command.extend(
                    [
                        "-DMODERN=1",
                        "-DFE8_EXPANSION_MODERN_BUILD=1",
                    ]
                )
            command.extend(
                [
                    str(ROOT / "src" / "fontgrp.c"),
                    str(HELP_SIZING_DRIVER),
                    "-Wl,--gc-sections",
                    "-o",
                    str(binary),
                ]
            )
            completed = _run(command)
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = _run([str(binary)])
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            return completed.stdout

    def test_modern_public_help_sizing_handles_blank_lines_and_stale_buffer(self):
        output = self._compile_and_run(modern=True)
        self.assertIn("MAP_MENU_HELP_SIZING_MODERN: PASS", output)

    def test_archival_public_help_sizing_compiles_links_and_preserves_behavior(self):
        output = self._compile_and_run(modern=False)
        self.assertIn("MAP_MENU_HELP_SIZING_ARCHIVAL: PASS", output)


class MapMenuRuntimeContractTests(unittest.TestCase):
    def _runtime_module_and_capture(self):
        module = runpy.run_path(str(RUNTIME_RUNNER))
        capture = json.loads(RUNTIME_FINGERPRINT.read_text(encoding="utf-8"))
        return module, capture

    def _run_main_with_capture(self, module, capture, fingerprint, output):
        globals_ = module["main"].__globals__
        playtest = globals_["gba_playtest"]
        original_parse = playtest.parse_scenario_data
        original_capture = playtest.capture
        original_resolver = globals_["ElfSymbolResolver"]
        original_fingerprint = globals_["FINGERPRINT"]
        try:
            playtest.parse_scenario_data = lambda *args, **kwargs: object()
            playtest.capture = lambda *args, **kwargs: copy.deepcopy(capture)
            globals_["ElfSymbolResolver"] = lambda path: object()
            globals_["FINGERPRINT"] = fingerprint
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                return module["main"](
                    [
                        "--rom",
                        str(output / "input.gba"),
                        "--elf",
                        str(output / "input.elf"),
                        "--out-dir",
                        str(output),
                    ]
                )
        finally:
            playtest.parse_scenario_data = original_parse
            playtest.capture = original_capture
            globals_["ElfSymbolResolver"] = original_resolver
            globals_["FINGERPRINT"] = original_fingerprint

    def test_missing_stale_and_mutated_fingerprints_fail_closed(self):
        module, capture = self._runtime_module_and_capture()
        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            work = Path(temporary)
            missing = work / "missing.json"
            self.assertEqual(
                self._run_main_with_capture(
                    module,
                    capture,
                    missing,
                    work / "missing-output",
                ),
                1,
            )

            stale_data = copy.deepcopy(capture)
            stale_data["checkpoints"][2]["framebuffer_hash"] = (
                "fnv1a64-rgb24:0000000000000000"
            )
            stale = work / "stale.json"
            stale.write_text(
                module["gba_playtest"].serialize_fingerprint(stale_data),
                encoding="utf-8",
            )
            self.assertEqual(
                self._run_main_with_capture(
                    module,
                    capture,
                    stale,
                    work / "stale-output",
                ),
                1,
            )

            valid = work / "valid.json"
            valid.write_text(
                RUNTIME_FINGERPRINT.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            mutated_capture = copy.deepcopy(capture)
            mutated_capture["checkpoints"][3]["framebuffer_hash"] = (
                "fnv1a64-rgb24:ffffffffffffffff"
            )
            self.assertEqual(
                self._run_main_with_capture(
                    module,
                    mutated_capture,
                    valid,
                    work / "mutated-output",
                ),
                1,
            )

    def test_checked_named_release_scenario_is_semantic_and_framebuffer_pinned(self):
        module, capture = self._runtime_module_and_capture()
        scenario = module["_scenario_data"]()
        self.assertEqual(module["_semantic_failures"](capture, scenario), [])
        self.assertEqual(
            [checkpoint["name"] for checkpoint in scenario["checkpoints"]],
            [
                "prologue-player-before-optional-menu",
                "cursor-moved-map-interactive",
                "danger-first-map-menu",
                "danger-help-complete",
                "danger-overlay-displayed",
                "danger-overlay-cancelled",
                "map-interactive-after-optional-menu",
            ],
        )
        framebuffer_hashes = [
            checkpoint["framebuffer_hash"]
            for checkpoint in capture["checkpoints"]
            if "framebuffer_hash" in checkpoint
        ]
        self.assertEqual(len(framebuffer_hashes), 4)
        self.assertEqual(len(set(framebuffer_hashes)), 4)


if __name__ == "__main__":
    unittest.main()
