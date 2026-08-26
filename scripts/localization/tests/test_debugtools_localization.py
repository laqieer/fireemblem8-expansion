import json
import re
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from scripts.localization.catalog import load_catalog
from scripts.localization.game_catalog.build import build_game_catalog
from scripts.localization.game_locales.ending_metrics import (
    _ascii_widths,
    _cjk_widths,
    _line_width,
)
from scripts.localization.legacy_spacing import (
    LEGACY_SJIS_SPACE_BYTES,
    LEGACY_SJIS_SPACE_WIDTH,
)
from scripts.localization import schema as localization_schema


ROOT = Path(__file__).resolve().parents[3]
BUILD = ROOT / "build"
CC = shutil.which("gcc") or shutil.which("cc")

FLAG_STATUS_RENDER_DRIVER = r'''
#include "global.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "uimenu.h"
#include "bmunit.h"
#include "expansion_debugtools.h"
#include "expansion_locale.h"
#include "expansion_msg_ids.h"

extern struct MenuDef CONST_DATA gDebugToolsFlagMenuDef;
extern char gDebugToolsToolsHostStubStatusLines[3][64];
extern int gDebugToolsToolsHostStubStatusLineCount;
extern int gDebugToolsToolsHostStubPutDrawTextCallCount;
extern int gDebugToolsToolsHostStubStatusLineTileWidths[3];
extern void DebugToolsHostStub_ResetStatusLines(void);
extern void DebugToolsHostStub_SetPhaseBoundaryIdle(void);

static ExpansionLocaleId sLocale;
static const char *sTurnText;
static const char *sComputerText;
static const char *sBlockedText;

ExpansionLocaleId ExpansionLocale_GetCurrent(void)
{
    return sLocale;
}

const char *ExpansionLocale_ResolveCurrent(ExpansionMsgId message)
{
    switch (message)
    {
    case EXP_MSG_DEBUG_STATUS_TURN:
        return sTurnText;

    case EXP_MSG_DEBUG_MODE_COMPUTER:
        return sComputerText;

    case EXP_MSG_DEBUG_MODE_BLOCKED:
        return sBlockedText;

    default:
        return "";
    }
}

int main(int argc, char **argv)
{
    struct MenuProc menu;
    int index;

    if (argc != 7)
        return 2;

    sLocale = (ExpansionLocaleId)strtoul(argv[1], NULL, 0);
    sTurnText = argv[2];
    sComputerText = argv[3];
    sBlockedText = argv[4];
    memset(&menu, 0, sizeof(menu));
    memset(&gPlaySt, 0, sizeof(gPlaySt));
    memset(&gDebugToolsProbe, 0, sizeof(gDebugToolsProbe));
    gPlaySt.faction = FACTION_BLUE;
    gPlaySt.chapterTurnNumber = 999;
    gDebugToolsProbe.chapterIndexSample = 255;
    gDebugToolsProbe.debugFlagLastValue = 1;
    DebugToolsHostStub_SetPhaseBoundaryIdle();
    DebugToolsPhaseControl_Reset();

    if (DebugToolsPhaseControl_RequestFactionMode(
            (int)strtol(argv[5], NULL, 0),
            (enum DebugToolsPhaseControlMode)strtol(argv[6], NULL, 0))
        != DEBUGTOOLS_PHASE_CONTROL_OK)
        return 3;

    if (gDebugToolsFlagMenuDef.onInit == NULL)
        return 4;

    menu.def = &gDebugToolsFlagMenuDef;
    DebugToolsHostStub_ResetStatusLines();
    gDebugToolsFlagMenuDef.onInit(&menu);
    if (gDebugToolsToolsHostStubStatusLineCount != 3)
        return 5;

    printf("RENDERER\t%d\n", gDebugToolsToolsHostStubPutDrawTextCallCount != 0 ? 2 : 1);
    for (index = 0; index < gDebugToolsToolsHostStubStatusLineCount; index++)
        printf("LINE\t%d\t%d\t%s\n",
            index,
            gDebugToolsToolsHostStubStatusLineTileWidths[index],
            gDebugToolsToolsHostStubStatusLines[index]);

    return 0;
}
'''


class DebugToolsLocalizationTests(unittest.TestCase):
    ACTION_LABELS = {
        "Fast Boot: Chapter 2": "debug.action.fastboot_ch2",
        "Weather": "debug.action.weather",
        "Fog": "debug.action.fog",
        "Chapter/Skirmish": "debug.action.chapter_skirmish",
        "Unit Inspect": "debug.action.unit_inspect",
        "Convoy Inspect": "debug.action.convoy_inspect",
        "Flag/Chapter": "debug.action.flag_chapter",
        "RNG Inspect": "debug.action.rng_inspect",
        "Save State": "debug.action.save_state",
        "Music Preview": "debug.action.music_preview",
    }
    DIRECT_MENU_LABELS = Counter(
        {
            "Back": 14,
            "Refresh": 1,
            "Confirm Heal to Full": 1,
            "Confirm Add Item": 1,
            "Confirm Toggle Flag": 1,
            "Confirm Reseed": 1,
            "Apply Turn +1": 1,
            "Apply Turn -1": 1,
            "Apply R CPU": 1,
            "Apply R Block": 1,
            "Apply G CPU": 1,
            "Apply G Block": 1,
            "Weather": 1,
            "Fog": 1,
            "Game 0": 1,
            "Game 1": 1,
            "Game 2": 1,
            "Suspend": 1,
            "Clears:": 1,
            "Name:": 1,
            "Arm RAM": 1,
            "Run RAM": 1,
            "Music": 1,
            "Edit HP": 1,
            "Edit Stats": 1,
            "Edit AI": 1,
            "Confirm Clear Status": 1,
            "Unit/Class": 1,
            "State": 1,
            "Current HP": 1,
            "Max HP": 1,
            "Power": 1,
            "Skill": 1,
            "Speed": 1,
            "Defense": 1,
            "Resistance": 1,
            "Luck": 1,
            "AI A": 1,
            "AI B": 1,
        }
    )
    EXPANSION_ADAPTERS = {
        "Back": "framework.back",
        "Confirm Heal to Full": "debug.confirm.heal_full",
        "Confirm Add Item": "debug.confirm.add_item",
        "Confirm Toggle Flag": "debug.confirm.toggle_flag",
        "Confirm Reseed": "debug.confirm.reseed",
        "Apply Turn +1": "debug.confirm.turn_increment",
        "Apply Turn -1": "debug.confirm.turn_decrement",
        "Apply R CPU": "debug.confirm.red_computer",
        "Apply R Block": "debug.confirm.red_blocked",
        "Apply G CPU": "debug.confirm.green_computer",
        "Apply G Block": "debug.confirm.green_blocked",
        "DBGTOOLS": "debug.status.hub",
        "DBGTOOLS ERR": "debug.status.hub_error",
        "UNIT HP": "debug.status.unit_hp",
        "UNIT N/A": "debug.status.unit_unavailable",
        "CONVOY": "debug.status.convoy",
        "RNG SEED": "debug.status.rng_seed",
        "SAVE STATE": "debug.status.save_state",
        "Game 0": "debug.save_fixture.game0",
        "Game 1": "debug.save_fixture.game1",
        "Game 2": "debug.save_fixture.game2",
        "Suspend": "debug.save_fixture.latest_suspend",
        "Clears:": "debug.save_fixture.completion",
        "Name:": "debug.save_fixture.tactician",
        "Keep": "debug.save_fixture.keep",
        "Fixture": "debug.save_fixture.marker",
        "Arm RAM": "debug.save_fixture.arm",
        "Run RAM": "debug.save_fixture.continue",
        "RAM Fixture": "debug.save_fixture.preview",
        "Title Only": "debug.save_fixture.title_only",
        "Invalid": "debug.save_fixture.invalid",
        "Save Blocked": "debug.save_fixture.blocked",
        "Chapter": "debug.selector.chapter",
        "Skirmish": "debug.selector.skirmish",
        "Eirika": "debug.selector.eirika",
        "Ephraim": "debug.selector.ephraim",
        "Target unavailable": "debug.selector.unavailable",
        "Edit HP": "debug.unit.edit_hp",
        "Edit Stats": "debug.unit.edit_stats",
        "Edit AI": "debug.unit.edit_ai",
        "Confirm Clear Status": "debug.unit.clear_status",
        "Unit/Class": "debug.unit.identity",
        "State": "debug.unit.state",
        "Current HP": "debug.unit.current_hp",
        "Max HP": "debug.unit.max_hp",
        "Power": "debug.unit.power",
        "Skill": "debug.unit.skill",
        "Speed": "debug.unit.speed",
        "Defense": "debug.unit.defense",
        "Resistance": "debug.unit.resistance",
        "Luck": "debug.unit.luck",
        "AI A": "debug.unit.ai_a",
        "AI B": "debug.unit.ai_b",
    }

    @classmethod
    def setUpClass(cls):
        cls.sources = {
            name: (ROOT / f"src/{name}").read_text(encoding="utf-8")
            for name in (
                "debugtools_registry.c",
                "debugtools_actions.c",
                "debugtools_launcher.c",
                "debugtools_selector.c",
                "debugtools_tools.c",
                "debugtools_music.c",
            )
        }
        cls.registry = json.loads(
            (ROOT / "texts/expansion/registry.json").read_text(encoding="utf-8")
        )
        cls.catalogs = {
            locale: json.loads(
                (ROOT / f"texts/expansion/catalog.{locale}.json").read_text(
                    encoding="utf-8"
                )
            )["strings"]
            for locale in ("en", "ja", "zh-Hans")
        }
        cls.loaded_catalog = load_catalog()
        cls.game_catalog = build_game_catalog(
            enabled_locales=("ja", "zh-Hans")
        )
        cls.header = (ROOT / "include/expansion_debugtools.h").read_text(
            encoding="utf-8"
        )
        cls.uimenu = (ROOT / "src/uimenu.c").read_text(encoding="utf-8")
        cls.ascii_widths = _ascii_widths(ROOT)
        cls.cjk_widths = {
            locale: _cjk_widths(ROOT, locale)[0]
            for locale in ("ja", "zh-Hans")
        }

    @classmethod
    def _pixel_width(cls, text, locale):
        return _line_width(
            text,
            locale=locale,
            ascii_widths=cls.ascii_widths,
            cjk_widths=cls.cjk_widths.get(locale, {}),
        )

    @classmethod
    def _catalog_entry_width(cls, catalog, message_id, locale):
        payload = catalog.decode_entry(message_id)[:-1]
        legacy_spaces = payload.count(LEGACY_SJIS_SPACE_BYTES)
        text = payload.replace(LEGACY_SJIS_SPACE_BYTES, b"").decode("utf-8")
        return (
            cls._pixel_width(text, locale)
            + legacy_spaces * LEGACY_SJIS_SPACE_WIDTH
        )

    @classmethod
    def _constant(cls, name):
        match = re.search(rf"\b{name}\s*=\s*(\d+)", cls.header)
        if match is None:
            raise AssertionError(f"{name} is missing from expansion_debugtools.h")
        return int(match.group(1))

    @classmethod
    def _write_message_header(cls, directory):
        lines = [
            "#ifndef TEST_EXPANSION_MSG_IDS_H",
            "#define TEST_EXPANSION_MSG_IDS_H",
        ]
        for message in cls.registry["messages"]:
            macro = "EXP_MSG_" + re.sub(r"[^A-Za-z0-9]+", "_", message["key"]).upper()
            lines.append(f"#define {macro} {message['id']}u")
        lines += ["#endif", ""]
        (directory / "expansion_msg_ids.h").write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

    @classmethod
    def _compile_flag_status_driver(cls, directory):
        source = directory / "debugtools_flag_status_host.c"
        executable = directory / "debugtools_flag_status_host"
        source.write_text(
            FLAG_STATUS_RENDER_DRIVER,
            encoding="utf-8",
        )
        common = [
            "-std=gnu89",
            "-w",
            "-I",
            str(directory),
            "-I",
            str(ROOT / "include"),
            "-I",
            str(ROOT / "include" / "generated"),
            "-DFE8_EXPANSION_MODERN_BUILD=1",
            "-DFE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
            "-DMODERN=1",
        ]
        sources = (
            (ROOT / "src" / "debugtools_registry.c", "registry.o", ()),
            (ROOT / "src" / "debugtools_tools.c", "tools.o", ()),
            (ROOT / "src" / "debugtools_diag.c", "diagnostics.o", ()),
            (
                ROOT / "tools" / "gba-playtest" / "tests" / "c"
                / "debugtools_tools_host_stubs.c",
                "tools_stubs.o",
                (
                    "-DExpansionLocale_GetCurrent=DebugToolsHostStub_GetCurrent",
                    "-DExpansionLocale_ResolveCurrent=DebugToolsHostStub_ResolveCurrent",
                ),
            ),
            (
                ROOT / "tools" / "gba-playtest" / "tests" / "c"
                / "debugtools_registry_support_stubs.c",
                "registry_support.o",
                (),
            ),
            (source, "driver.o", ()),
        )
        objects = []
        for source_path, object_name, defines in sources:
            output = directory / object_name
            completed = subprocess.run(
                [
                    CC,
                    *common,
                    *defines,
                    "-c",
                    str(source_path),
                    "-o",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise AssertionError(completed.stdout + completed.stderr)
            objects.append(output)
        completed = subprocess.run(
            [CC, *map(str, objects), "-o", str(executable)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stdout + completed.stderr)
        return executable

    def _run_flag_status_driver(
        self,
        executable,
        locale,
        strings,
        faction,
        mode,
    ):
        completed = subprocess.run(
            [
                str(executable),
                str(localization_schema.LOCALE_INDEX[locale]),
                strings["debug.status.turn"],
                strings["debug.mode.computer"],
                strings["debug.mode.blocked"],
                hex(faction),
                str(mode),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

        renderer = None
        lines = []
        for output in completed.stdout.splitlines():
            fields = output.split("\t", 3)
            if fields[0] == "RENDERER":
                renderer = int(fields[1])
            elif fields[0] == "LINE":
                lines.append((int(fields[1]), int(fields[2]), fields[3]))
        self.assertIsNotNone(renderer, completed.stdout)
        self.assertEqual(len(lines), 3, completed.stdout)
        return renderer, lines

    def test_direct_debug_ui_literal_inventory_is_exact_and_closed(self):
        action_labels = []
        for name in (
            "debugtools_launcher.c",
            "debugtools_actions.c",
            "debugtools_selector.c",
            "debugtools_tools.c",
            "debugtools_music.c",
        ):
            action_labels.extend(
                re.findall(
                    r"DebugToolsAction\s+\w+\s*=\s*\{\s*\d+,\s*\"([^\"]+)\"",
                    self.sources[name],
                )
            )
        self.assertEqual(
            Counter(action_labels),
            Counter(self.ACTION_LABELS.keys()),
        )

        menu_labels = []
        for name in (
            "debugtools_registry.c",
            "debugtools_actions.c",
            "debugtools_tools.c",
            "debugtools_music.c",
        ):
            menu_labels.extend(
                re.findall(r"\.name\s*=\s*\"([^\"]+)\"", self.sources[name])
            )
            menu_labels.extend(
                re.findall(
                    r"DEBUGTOOLS_ROM_MENU_ITEM\s*\(\s*\"([^\"]+)\"",
                    self.sources[name],
                )
            )
        self.assertEqual(Counter(menu_labels), self.DIRECT_MENU_LABELS)

        registry = {
            row["key"]: row
            for row in self.registry["messages"]
            if row["status"] == "active"
        }
        for literal, key in {
            **self.ACTION_LABELS,
            **self.EXPANSION_ADAPTERS,
        }.items():
            with self.subTest(literal=literal, key=key):
                self.assertIn(key, registry)
                self.assertEqual(self.catalogs["en"][key], literal)
                self.assertIn(key, self.catalogs["ja"])
                self.assertIn(key, self.catalogs["zh-Hans"])

    def test_every_direct_debug_literal_has_the_expected_runtime_adapter(self):
        registry = self.sources["debugtools_registry.c"]
        tools = self.sources["debugtools_tools.c"]

        for key_suffix in (
            "FASTBOOT_CH2",
            "WEATHER",
            "FOG",
            "CHAPTER_SKIRMISH",
            "UNIT_INSPECT",
            "CONVOY_INSPECT",
            "FLAG_CHAPTER",
            "RNG_INSPECT",
            "SAVE_STATE",
            "MUSIC_PREVIEW",
        ):
            self.assertIn(f"EXP_MSG_DEBUG_ACTION_{key_suffix}", registry)

        self.assertIn("EXP_MSG_FRAMEWORK_BACK", registry)
        self.assertIn("DebugToolsHub_BuiltinActionRowDraw", registry)
        self.assertIn("EXP_MSG_DEBUG_STATUS_HUB", registry)
        self.assertIn("EXP_MSG_DEBUG_STATUS_HUB_ERROR", registry)

        for key_suffix in (
            "CONFIRM_HEAL_FULL",
            "CONFIRM_ADD_ITEM",
            "CONFIRM_TOGGLE_FLAG",
            "CONFIRM_RESEED",
            "CONFIRM_TURN_INCREMENT",
            "CONFIRM_TURN_DECREMENT",
            "CONFIRM_RED_COMPUTER",
            "CONFIRM_RED_BLOCKED",
            "CONFIRM_GREEN_COMPUTER",
            "CONFIRM_GREEN_BLOCKED",
            "STATUS_UNIT_HP",
            "STATUS_UNIT_UNAVAILABLE",
            "STATUS_CONVOY",
            "STATUS_RNG_SEED",
            "STATUS_SAVE_STATE",
            "STATUS_TURN",
            "MODE_COMPUTER",
            "MODE_BLOCKED",
        ):
            self.assertIn(f"EXP_MSG_DEBUG_{key_suffix}", tools)

        selector = self.sources["debugtools_selector.c"]
        for key_suffix in (
            "SELECTOR_CHAPTER",
            "SELECTOR_SKIRMISH",
            "SELECTOR_EIRIKA",
            "SELECTOR_EPHRAIM",
            "SELECTOR_UNAVAILABLE",
        ):
            self.assertIn(f"EXP_MSG_DEBUG_{key_suffix}", selector)

        for key_suffix in (
            "UNIT_EDIT_HP",
            "UNIT_EDIT_STATS",
            "UNIT_EDIT_AI",
            "UNIT_CLEAR_STATUS",
            "UNIT_IDENTITY",
            "UNIT_STATE",
            "UNIT_CURRENT_HP",
            "UNIT_MAX_HP",
            "UNIT_POWER",
            "UNIT_SKILL",
            "UNIT_SPEED",
            "UNIT_DEFENSE",
            "UNIT_RESISTANCE",
            "UNIT_LUCK",
            "UNIT_AI_A",
            "UNIT_AI_B",
            "SAVE_FIXTURE_GAME0",
            "SAVE_FIXTURE_GAME1",
            "SAVE_FIXTURE_GAME2",
            "SAVE_FIXTURE_LATEST_SUSPEND",
            "SAVE_FIXTURE_COMPLETION",
            "SAVE_FIXTURE_TACTICIAN",
            "SAVE_FIXTURE_KEEP",
            "SAVE_FIXTURE_MARKER",
            "SAVE_FIXTURE_ARM",
            "SAVE_FIXTURE_CONTINUE",
            "SAVE_FIXTURE_PREVIEW",
            "SAVE_FIXTURE_TITLE_ONLY",
            "SAVE_FIXTURE_INVALID",
            "SAVE_FIXTURE_BLOCKED",
        ):
            self.assertIn(f"EXP_MSG_DEBUG_{key_suffix}", tools)

        self.assertEqual(tools.count("EXP_MSG_FRAMEWORK_BACK"), 11)
        self.assertIn("DebugToolsTools_LocalizedMenuItemDraw", tools)
        self.assertIn("ExpansionLocale_ResolveCurrent", tools)
        self.assertIn("PutDrawText(", tools)

        self.assertEqual(
            re.findall(
                r"PrintDebugStringToBG\([^;]*ExpansionLocale_ResolveCurrent",
                registry + tools,
                flags=re.DOTALL,
            ),
            [],
        )

    def test_japanese_and_chinese_debug_adapters_do_not_fall_back_to_english(self):
        for key in self.EXPANSION_ADAPTERS.values():
            with self.subTest(key=key):
                self.assertNotEqual(self.catalogs["ja"][key], self.catalogs["en"][key])
                self.assertNotEqual(
                    self.catalogs["zh-Hans"][key],
                    self.catalogs["en"][key],
                )

    def test_every_generated_debug_menu_label_fits_actual_text_allocation(self):
        menu_width_tiles = self._constant("DEBUGTOOLS_MENU_WIDTH_TILES")
        allocation_pixels = (menu_width_tiles - 1) * 8
        self.assertIn("InitText(&item->text, rect.w - 1);", self.uimenu)

        menu_sources = "\n".join(
            self.sources[name]
            for name in (
                "debugtools_registry.c",
                "debugtools_actions.c",
                "debugtools_selector.c",
                "debugtools_tools.c",
                "debugtools_music.c",
            )
        )
        menu_width_tokens = re.findall(
            r"CONST_DATA struct MenuDef gDebugTools\w+MenuDef\s*=\s*\{\s*"
            r"\{\s*\d+\s*,\s*\d+\s*,\s*([^,\s]+)\s*,\s*0\s*\}",
            menu_sources,
            flags=re.DOTALL,
        )
        self.assertEqual(len(menu_width_tokens), 16)
        self.assertEqual(
            set(menu_width_tokens),
            {"DEBUGTOOLS_MENU_WIDTH_TILES"},
        )
        self.assertLessEqual(1 + menu_width_tiles, 30)

        menu_keys = {
            "framework.back",
            *self.ACTION_LABELS.values(),
            *(
                key
                for key in self.EXPANSION_ADAPTERS.values()
                if key.startswith(
                    ("debug.confirm.", "debug.save_fixture.", "debug.unit.")
                )
            ),
        }
        for locale in ("en", "ja", "zh-Hans", "qps-ploc"):
            strings = self.loaded_catalog.strings_for(locale)
            for key in sorted(menu_keys):
                with self.subTest(locale=locale, key=key):
                    self.assertLessEqual(
                        self._pixel_width(strings[key], locale),
                        allocation_pixels,
                    )

            value_x = {
                "debug.unit.identity": 96,
                "debug.unit.state": 72,
                "debug.unit.current_hp": 112,
                "debug.unit.max_hp": 112,
                "debug.unit.power": 112,
                "debug.unit.skill": 112,
                "debug.unit.speed": 112,
                "debug.unit.defense": 112,
                "debug.unit.resistance": 112,
                "debug.unit.luck": 112,
                "debug.unit.ai_a": 112,
                "debug.unit.ai_b": 112,
            }
            for key, x in value_x.items():
                with self.subTest(locale=locale, key=f"{key}+value"):
                    self.assertLessEqual(self._pixel_width(strings[key], locale), x)

    def test_maximum_hub_rows_and_qps_labels_fit_allocator_budget(self):
        action_max = self._constant("DEBUGTOOLS_ACTION_MAX")
        contributor_max = self._constant("DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX")
        page_action_max = self._constant("DEBUGTOOLS_HUB_PAGE_ACTION_MAX")
        page_max = self._constant("DEBUGTOOLS_HUB_PAGE_MAX")
        menu_width = self._constant("DEBUGTOOLS_MENU_WIDTH_TILES")
        status_width = self._constant("DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES")
        capacity = self._constant("DEBUGTOOLS_TEXT_ALLOC_CAPACITY")
        configured_budget = self._constant(
            "DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET"
        )

        expected_budget = (page_action_max + 1) * (menu_width - 1) + status_width
        self.assertEqual(action_max, 19)
        self.assertEqual(contributor_max, 9)
        self.assertEqual(page_action_max, 9)
        self.assertEqual(page_max, 3)
        self.assertEqual(expected_budget, 187)
        self.assertEqual(configured_budget, expected_budget)
        self.assertLessEqual(expected_budget, capacity)

        qps = self.loaded_catalog.strings_for("qps-ploc")
        allocation_pixels = (menu_width - 1) * 8
        for key in sorted({"framework.back", *self.ACTION_LABELS.values()}):
            with self.subTest(key=key):
                self.assertLessEqual(
                    self._pixel_width(qps[key], "qps-ploc"),
                    allocation_pixels,
                )

    def test_every_generated_debug_status_row_fits_actual_surface_geometry(self):
        status_width_tiles = self._constant(
            "DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES"
        )
        suffixes = {
            "debug.status.hub": " 19/19 3/3",
            "debug.status.hub_error": " -99",
            "debug.status.unit_hp": " 255/255",
            "debug.status.unit_unavailable": "",
            "debug.status.convoy": " 100/100",
            "debug.status.rng_seed": " FFFF",
            "debug.status.save_state": " -99",
        }
        for locale in ("en", "ja", "zh-Hans", "qps-ploc"):
            strings = self.loaded_catalog.strings_for(locale)
            allocation_pixels = (
                status_width_tiles * 8
                if locale in ("ja", "zh-Hans")
                else 29 * 8
            )
            for key, suffix in suffixes.items():
                text = strings[key] + suffix
                width = (
                    self._pixel_width(text, locale)
                    if locale in ("ja", "zh-Hans")
                    else len(text) * 8
                )
                with self.subTest(locale=locale, key=key):
                    self.assertLessEqual(width, allocation_pixels)

            combined = (
                strings["debug.status.chapter"]
                + " 255 "
                + strings["debug.status.flag"]
                + " 1"
            )
            combined_width = (
                self._pixel_width(combined, locale)
                if locale in ("ja", "zh-Hans")
                else len(combined) * 8
            )
            with self.subTest(locale=locale, key="chapter+flag"):
                self.assertLessEqual(combined_width, allocation_pixels)

    def test_flag_status_formatter_and_configured_renderer_cover_authored_locales(self):
        if CC is None:
            self.skipTest("no host C compiler")

        cjk_width = self._constant(
            "DEBUGTOOLS_FLAG_STATUS_CJK_WIDTH_TILES"
        ) * 8
        non_cjk_width = self._constant(
            "DEBUGTOOLS_FLAG_STATUS_NON_CJK_WIDTH_TILES"
        ) * 8

        BUILD.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            work = Path(temporary)
            self._write_message_header(work)
            executable = self._compile_flag_status_driver(work)

            for locale in localization_schema.AUTHORED_CATALOG_LOCALES:
                strings = self.loaded_catalog.strings_for(locale)
                for faction_name, faction in (
                    ("red", 0x80),
                    ("green", 0x40),
                ):
                    for mode_name, mode in (
                        ("computer", 0),
                        ("blocked", 2),
                    ):
                        with self.subTest(
                            locale=locale,
                            faction=faction_name,
                            mode=mode_name,
                        ):
                            renderer, rendered = self._run_flag_status_driver(
                                executable,
                                locale,
                                strings,
                                faction,
                                mode,
                            )
                            expected_red_mode = (
                                strings["debug.mode.blocked"]
                                if faction_name == "red" and mode_name == "blocked"
                                else strings["debug.mode.computer"]
                            )
                            expected_green_mode = (
                                strings["debug.mode.blocked"]
                                if faction_name == "green" and mode_name == "blocked"
                                else strings["debug.mode.computer"]
                            )
                            expected_lines = [
                                f"{strings['debug.status.turn']} 999 C:255 F:1",
                                f"R:{expected_red_mode}",
                                f"G:{expected_green_mode}",
                            ]
                            is_cjk = locale in ("ja", "zh-Hans")
                            self.assertEqual(renderer, 2 if is_cjk else 1)
                            self.assertEqual(
                                [text for _, _, text in rendered],
                                expected_lines,
                            )
                            self.assertEqual(
                                [width for _, width, _ in rendered],
                                [24, 24, 24] if is_cjk else [0, 0, 0],
                            )
                            width_limit = cjk_width if is_cjk else non_cjk_width
                            for line_index, _, text in rendered:
                                with self.subTest(line=line_index):
                                    self.assertLessEqual(
                                        self._pixel_width(text, locale),
                                        width_limit,
                                    )

    def test_weather_and_fog_rows_fit_the_same_actual_menu_geometry(self):
        allocation_pixels = (
            self._constant("DEBUGTOOLS_MENU_WIDTH_TILES") - 1
        ) * 8
        rows = (
            (0x06AC, range(0x06B1, 0x06B8)),
            (0x06AD, (0x0849, 0x084A)),
        )
        for locale in ("en", "ja", "zh-Hans", "qps-ploc"):
            game_locale = "en" if locale == "qps-ploc" else locale
            catalog = (
                self.game_catalog.english.catalog
                if game_locale == "en"
                else self.game_catalog.locale_bundle(game_locale).catalog
            )
            for label_id, value_ids in rows:
                label_width = self._catalog_entry_width(
                    catalog,
                    label_id,
                    game_locale,
                )
                with self.subTest(
                    locale=locale,
                    label_id=f"0x{label_id:04X}",
                ):
                    self.assertLessEqual(8 + label_width, 64)
                for value_id in value_ids:
                    value_width = self._catalog_entry_width(
                        catalog,
                        value_id,
                        game_locale,
                    )
                    with self.subTest(
                        locale=locale,
                        label_id=f"0x{label_id:04X}",
                        value_id=f"0x{value_id:04X}",
                    ):
                        self.assertLessEqual(64 + value_width, allocation_pixels)


if __name__ == "__main__":
    unittest.main()
