"""Focused contracts for the typed BGM router, registry, and continuation policy."""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.modernize.tests.test_save_format_meta_bytes_native import (
    _extract_c_function,
)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modernize"))

import bgm_registry  # noqa: E402


class BgmRegistryTests(unittest.TestCase):
    def test_default_registry_is_inert_and_generated_source_is_current(self):
        registry = json.loads(
            (ROOT / "src/data/bgm_registry.json").read_text(encoding="utf-8")
        )
        normalized = bgm_registry.validate(registry)
        self.assertEqual(normalized["variants"], [])
        self.assertEqual(normalized["selectors"], [])

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/modernize/bgm_registry.py"),
                "check",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_generated_source_fails_the_registry_check(self):
        artifact_dir = ROOT / "build" / "test-artifacts" / "bgm-registry"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        output = artifact_dir / "expansion_bgm_data.c"
        try:
            generated = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/modernize/bgm_registry.py"),
                    "generate",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            output.write_text(output.read_text(encoding="utf-8") + "/* stale */\n", encoding="utf-8")
            checked = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/modernize/bgm_registry.py"),
                    "check",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("stale", checked.stderr)
        finally:
            shutil.rmtree(artifact_dir, ignore_errors=True)

    def test_modern_make_wires_registry_inputs_before_compilation(self):
        makefile = (ROOT / "modern.mk").read_text(encoding="utf-8")
        for path in (
            "src/data/bgm_registry.json",
            "scripts/modernize/bgm_registry.py",
            "include/expansion_bgm.h",
            "include/constants/songs.h",
            "include/id_space.h",
            "src/eventinfo.c",
        ):
            self.assertIn(path, makefile)
        self.assertIn("expansion-modern-bgm-registry-check", makefile)
        planned = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "expansion-modern-bgm-registry-check",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertIn("bgm_registry.py check", planned.stdout)

    def test_registry_rejects_unknown_song_context_and_staff_kind(self):
        base = {"$schema": bgm_registry.SCHEMA, "variants": [], "selectors": []}

        invalid_song = dict(base, variants=[{"context": "map_phase", "song": "0x7F"}])
        with self.assertRaises(bgm_registry.RegistryError):
            bgm_registry.validate(invalid_song)

        invalid_context = dict(base, variants=[{"context": "unknown", "song": 1}])
        with self.assertRaises(bgm_registry.RegistryError):
            bgm_registry.validate(invalid_context)

        invalid_staff_kind = dict(
            base,
            selectors=[{"action": "dance", "staffKind": "heal", "song": 1}],
        )
        with self.assertRaises(bgm_registry.RegistryError):
            bgm_registry.validate(invalid_staff_kind)

    def test_registry_enforces_runtime_flag_and_id_boundaries(self):
        base = {"$schema": bgm_registry.SCHEMA, "variants": [], "selectors": []}
        legal = dict(
            base,
            variants=[
                {
                    "context": "map_phase",
                    "chapter": bgm_registry.CHAPTER_ID_MAX,
                    "flag": bgm_registry.PERMANENT_FLAG_MAX,
                    "song": 1,
                },
                {
                    "context": "battle",
                    "flag": bgm_registry.CHAPTER_FLAG_MAX,
                    "song": 1,
                },
            ],
            selectors=[
                {
                    "action": "staff",
                    "character": bgm_registry.CHARACTER_ID_MAX,
                    "class": bgm_registry.CLASS_ID_MAX,
                    "song": 1,
                }
            ],
        )
        normalized = bgm_registry.validate(legal)
        self.assertEqual(normalized["variants"][0][0], bgm_registry.CHAPTER_ID_MAX)
        self.assertEqual(normalized["variants"][0][1], bgm_registry.PERMANENT_FLAG_MAX)
        self.assertEqual(normalized["variants"][1][1], bgm_registry.CHAPTER_FLAG_MAX)
        self.assertEqual(normalized["selectors"][0][3], bgm_registry.CHARACTER_ID_MAX)
        self.assertEqual(normalized["selectors"][0][4], bgm_registry.CLASS_ID_MAX)

        for invalid in (
            dict(base, variants=[{"context": "map_phase", "flag": 0, "song": 1}]),
            dict(base, variants=[{"context": "map_phase", "flag": 41, "song": 1}]),
            dict(base, variants=[{"context": "map_phase", "flag": 100, "song": 1}]),
            dict(base, variants=[{"context": "map_phase", "flag": 301, "song": 1}]),
            dict(base, variants=[{"context": "map_phase", "chapter": 0x80, "song": 1}]),
            dict(base, selectors=[{"action": "staff", "character": 0x100, "song": 1}]),
            dict(base, selectors=[{"action": "staff", "class": 0x80, "song": 1}]),
            dict(base, selectors=[{"action": "staff", "class": 0xFF, "song": 1}]),
        ):
            with self.assertRaises(bgm_registry.RegistryError):
                bgm_registry.validate(invalid)

    def test_wildcards_use_explicit_masks_and_allow_character_ff(self):
        data = {
            "$schema": bgm_registry.SCHEMA,
            "variants": [
                {"context": "map_phase", "song": 1},
                {"context": "map_phase", "chapter": 0, "song": 2},
            ],
            "selectors": [
                {"action": "staff", "song": 1},
                {"action": "dance", "character": 0, "song": 3},
                {"action": "staff", "character": 0xFF, "song": 2},
            ],
        }
        output = bgm_registry.generate_c(bgm_registry.validate(data))
        self.assertIn("{ 0, 0, 1, 0, 1, 0, 0, { 0, 0 } }", output)
        self.assertIn("{ 0, 0, 2, 0, 1, 0, 1, { 0, 0 } }", output)
        self.assertIn("{ 1, 0, 0, 0, 0, 0, 0, 1 }", output)
        self.assertIn("{ 0, 0, 0, 0, 0, 2, 0, 3 }", output)
        self.assertIn("{ 1, 0, 0, 255, 0, 2, 0, 2 }", output)

    def test_registry_priority_and_selector_specificity_are_emitted(self):
        data = {
            "$schema": bgm_registry.SCHEMA,
            "variants": [
                {"context": "map_phase", "song": 1, "priority": 2},
                {"context": "map_phase", "song": 2, "priority": 9},
            ],
            "selectors": [
                {"action": "staff", "staffKind": "heal", "song": 1},
                {
                    "action": "staff",
                    "staffKind": "heal",
                    "class": 3,
                    "item": 4,
                    "song": 2,
                },
            ],
        }
        output = bgm_registry.generate_c(bgm_registry.validate(data))
        self.assertIn("{ 1, 0, 1, 0, 0, 1, 0, 1 }", output)
        self.assertIn("{ 1, 0, 1, 0, 3, 13, 4, 2 }", output)


class BgmRoutingContractTests(unittest.TestCase):
    def test_native_fixture_resolves_variants_explicit_requests_and_selectors(self):
        source = (ROOT / "src/expansion_bgm.c").read_text(encoding="utf-8")
        header = (ROOT / "include/expansion_bgm.h").read_text(encoding="utf-8")
        request = re.search(
            r"struct ExpansionBgmContextRequest\s*\{.*?\n\};", header, re.DOTALL
        )
        variant = re.search(
            r"struct ExpansionBgmVariant\s*\{.*?\n\};", header, re.DOTALL
        )
        selector = re.search(
            r"struct ExpansionBgmActionSelector\s*\{.*?\n\};", header, re.DOTALL
        )
        self.assertIsNotNone(request)
        self.assertIsNotNone(variant)
        self.assertIsNotNone(selector)

        functions = "\n".join(
            _extract_c_function(source, name)
            for name in (
                "ExpansionBgm_ContextIsValid",
                "ExpansionBgm_ActionIsValid",
                "ExpansionBgm_VariantMatches",
                "ExpansionBgm_Resolve",
                "ExpansionBgm_SelectorMatches",
                "ExpansionBgm_SelectActionSong",
            )
        )
        probe = f"""
#include <stdint.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define NULL ((void *)0)
#define TRUE 1
#define FALSE 0
#define SONG_NONE 0
#define ITEM_INDEX(item) ((item) & 0xFF)
#define UNIT_CHAR_ID(unit) ((unit)->characterId)
#define UNIT_CLASS_ID(unit) ((unit)->classId)
#define EXPANSION_BGM_CONTEXT_MAP_PHASE 0
#define EXPANSION_BGM_CONTEXT_COUNT 13
#define EXPANSION_BGM_ACTION_DANCE 0
#define EXPANSION_BGM_ACTION_STAFF 1
#define EXPANSION_BGM_ACTION_COUNT 2
#define EXPANSION_BGM_STAFF_NONE 0
#define EXPANSION_BGM_STAFF_HEAL 1
#define EXPANSION_BGM_VARIANT_MATCH_CHAPTER (1 << 0)
#define EXPANSION_BGM_VARIANT_MATCH_FLAG (1 << 1)
#define EXPANSION_BGM_SELECTOR_MATCH_STAFF_KIND (1 << 0)
#define EXPANSION_BGM_SELECTOR_MATCH_CHARACTER (1 << 1)
#define EXPANSION_BGM_SELECTOR_MATCH_CLASS (1 << 2)
#define EXPANSION_BGM_SELECTOR_MATCH_ITEM (1 << 3)
enum ExpansionBgmContext {{ EXPANSION_BGM_CONTEXT_ENUM_MAP_PHASE = 0 }};
enum ExpansionBgmAction {{ EXPANSION_BGM_ACTION_ENUM_DANCE = 0 }};
enum ExpansionBgmStaffKind {{ EXPANSION_BGM_STAFF_ENUM_NONE = 0 }};
struct Unit {{ u8 characterId; u8 classId; }};
{request.group(0)}
{variant.group(0)}
{selector.group(0)}

static u8 sFlag5;
int CheckFlag(u16 flagId) {{ return flagId == 5 ? sFlag5 : 0; }}
const struct ExpansionBgmVariant gExpansionBgmVariants[] = {{
    {{ 7, 0, 11, EXPANSION_BGM_CONTEXT_MAP_PHASE, 0, 1,
        EXPANSION_BGM_VARIANT_MATCH_CHAPTER, {{ 0, 0 }} }},
    {{ 7, 5, 12, EXPANSION_BGM_CONTEXT_MAP_PHASE, 1, 5,
        EXPANSION_BGM_VARIANT_MATCH_CHAPTER | EXPANSION_BGM_VARIANT_MATCH_FLAG,
        {{ 0, 0 }} }},
    {{ 7, 5, 13, EXPANSION_BGM_CONTEXT_MAP_PHASE, 1, 5,
        EXPANSION_BGM_VARIANT_MATCH_CHAPTER | EXPANSION_BGM_VARIANT_MATCH_FLAG,
        {{ 0, 0 }} }},
}};
const u32 gExpansionBgmVariantCount = 3;
const struct ExpansionBgmActionSelector gExpansionBgmActionSelectors[] = {{
    {{ EXPANSION_BGM_ACTION_DANCE, 1, 0, 0, 0, 0, 0, 21 }},
    {{ EXPANSION_BGM_ACTION_DANCE, 1, 0, 10, 3,
        EXPANSION_BGM_SELECTOR_MATCH_CHARACTER | EXPANSION_BGM_SELECTOR_MATCH_CLASS,
        0, 22 }},
    {{ EXPANSION_BGM_ACTION_DANCE, 1, 0, 10, 3,
        EXPANSION_BGM_SELECTOR_MATCH_CHARACTER | EXPANSION_BGM_SELECTOR_MATCH_CLASS,
        0, 23 }},
    {{ EXPANSION_BGM_ACTION_STAFF, 2, EXPANSION_BGM_STAFF_HEAL, 0, 0,
        EXPANSION_BGM_SELECTOR_MATCH_STAFF_KIND | EXPANSION_BGM_SELECTOR_MATCH_ITEM,
        4, 24 }},
}};
const u32 gExpansionBgmActionSelectorCount = 4;
{functions}

int main(void)
{{
    struct ExpansionBgmContextRequest request =
        {{ EXPANSION_BGM_CONTEXT_MAP_PHASE, 7, 0, 0, 10, SONG_NONE }};
    struct Unit unit = {{ 10, 3 }};

    if (ExpansionBgm_Resolve(&request) != 11)
        return 1;
    sFlag5 = 1;
    if (ExpansionBgm_Resolve(&request) != 12)
        return 2;
    request.hasExplicitSong = 1;
    request.explicitSong = 99;
    if (ExpansionBgm_Resolve(&request) != 99)
        return 3;
    request.hasExplicitSong = 0;
    request.fallbackSong = SONG_NONE;
    if (ExpansionBgm_Resolve(&request) != SONG_NONE)
        return 4;
    if (ExpansionBgm_SelectActionSong(
            EXPANSION_BGM_ACTION_DANCE, &unit, 0, EXPANSION_BGM_STAFF_NONE, 20) != 22)
        return 5;
    if (ExpansionBgm_SelectActionSong(
            EXPANSION_BGM_ACTION_STAFF, &unit, 4, EXPANSION_BGM_STAFF_HEAL, 20) != 24)
        return 6;
    if (ExpansionBgm_SelectActionSong(
            EXPANSION_BGM_ACTION_STAFF, &unit, 4, EXPANSION_BGM_STAFF_NONE, 20) != 20)
        return 7;
    return 0;
}}
"""
        artifact_dir = ROOT / "build" / "test-artifacts" / "bgm-routing-native"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        source_path = artifact_dir / "probe.c"
        binary_path = artifact_dir / "probe"
        source_path.write_text(probe, encoding="utf-8")
        try:
            subprocess.run(
                ["cc", "-std=c99", str(source_path), "-o", str(binary_path)],
                cwd=ROOT,
                check=True,
            )
            subprocess.run([str(binary_path)], cwd=ROOT, check=True)
        finally:
            shutil.rmtree(artifact_dir, ignore_errors=True)

    def test_public_surface_and_linker_entries_exist(self):
        header = (ROOT / "include/expansion_bgm.h").read_text(encoding="utf-8")
        source = (ROOT / "src/expansion_bgm.c").read_text(encoding="utf-8")
        linker = (ROOT / "ldscript.txt").read_text(encoding="utf-8")

        for symbol in (
            "enum ExpansionBgmContext",
            "struct ExpansionBgmContextRequest",
            "ExpansionBgm_Resolve",
            "ExpansionBgm_SelectActionSong",
            "ExpansionBgm_StartExplicit",
            "ExpansionBgm_ChangeExplicit",
            "ExpansionBgm_Continue",
        ):
            self.assertIn(symbol, header)
        self.assertIn("bestPriority", source)
        self.assertIn("bestSpecificity", source)
        self.assertIn("matchMask", source)
        self.assertNotIn("EXPANSION_BGM_ANY_MATCH", header)
        self.assertIn("src/expansion_bgm.o(.text);", linker)
        self.assertIn("src/expansion_bgm_data.o(.rodata);", linker)

    def test_explicit_event_override_and_typed_action_fallbacks_are_preserved(self):
        event = (ROOT / "src/eventscr.c").read_text(encoding="utf-8")
        battle = (ROOT / "src/banim-efxsound.c").read_text(encoding="utf-8")

        self.assertIn("ExpansionBgm_Override(EXPANSION_BGM_CONTEXT_EVENT", event)
        self.assertIn("ExpansionBgm_SelectActionSong", battle)
        self.assertIn("SONG_TETHYS", battle)
        self.assertIn("SONG_HEALING", battle)
        self.assertIn("SONG_CURING", battle)

    def test_known_context_callers_use_router(self):
        expected = {
            "src/bm.c": "EXPANSION_BGM_CONTEXT_MAP_PHASE",
            "src/prepscreen.c": "EXPANSION_BGM_CONTEXT_PREPARATION",
            "src/bmshop.c": "EXPANSION_BGM_CONTEXT_SHOP",
            "src/worldmap_main.c": "EXPANSION_BGM_CONTEXT_WORLD_MAP",
            "src/uiconfig.c": "EXPANSION_BGM_CONTEXT_PREPARATION",
            "src/titlescreen.c": "EXPANSION_BGM_CONTEXT_TITLE",
        }
        for relative, context in expected.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("ExpansionBgm_", source, relative)
            self.assertIn(context, source, relative)

    def test_continuation_policy_is_configured_and_identity_participates(self):
        config = (ROOT / "config.mk").read_text(encoding="utf-8")
        configure = (ROOT / "configure.ac").read_text(encoding="utf-8")
        header = (ROOT / "include/expansion_config.h").read_text(encoding="utf-8")
        docs = (ROOT / "docs/config_identity.md").read_text(encoding="utf-8")

        self.assertIn("EXPANSION_BGM_CONTINUATION_POLICY ?= preserve", config)
        self.assertIn("--with-bgm-continuation-policy=preserve|resume|restart", configure)
        self.assertIn("FE8_EXPANSION_BGM_CONTINUATION_POLICY", header)
        self.assertIn(
            "`EXPANSION_BGM_CONTINUATION_POLICY`",
            docs,
        )
        self.assertIn("fingerprint", docs)

        tool = ROOT / "scripts/modernize/expansion_config.py"
        args = [
            sys.executable,
            str(tool),
            "resolve",
            "--config",
            "debug",
            "--abi",
            "aapcs",
            "--rom-size",
            "16M",
        ]
        preserve = subprocess.run(
            args + ["--bgm-continuation-policy", "preserve"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        restart = subprocess.run(
            args + ["--bgm-continuation-policy", "restart"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        invalid = subprocess.run(
            args + ["--bgm-continuation-policy", "invalid"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(preserve.returncode, 0, preserve.stderr)
        self.assertEqual(restart.returncode, 0, restart.stderr)
        self.assertNotEqual(preserve.stdout, restart.stdout)
        self.assertNotEqual(invalid.returncode, 0)

    def test_continuation_resolves_context_and_restart_bypasses_same_song_noop(self):
        source = (ROOT / "src/expansion_bgm.c").read_text(encoding="utf-8")
        self.assertIn("request = ExpansionBgm_MakeRequest(context, songId);", source)
        self.assertIn("resolvedSong = ExpansionBgm_Resolve(&request);", source)
        self.assertIn("if (songId == SONG_NONE)", source)
        self.assertIn("StartBgmFadeIn(resolvedSong, speed, player);", source)
        self.assertIn(
            "== EXPANSION_BGM_CONTINUATION_PRESERVE\n"
            "        && (!IsBgmPlaying() || GetCurrentBgmSong() == resolvedSong)",
            source,
        )
        self.assertIn(
            "if (IsBgmPlaying() && GetCurrentBgmSong() == resolvedSong)",
            source,
        )
        self.assertIn("StartOrChangeBgm(resolvedSong, speed, player);", source)

    def test_continuation_policy_paths_are_distinct(self):
        source = (ROOT / "src/expansion_bgm.c").read_text(encoding="utf-8")
        restart = source.index("== EXPANSION_BGM_CONTINUATION_RESTART")
        preserve = source.index("== EXPANSION_BGM_CONTINUATION_PRESERVE")
        self.assertLess(restart, preserve)
        self.assertIn("StartBgmFadeIn(resolvedSong, speed, player);", source[restart:preserve])
        self.assertIn(
            "&& (!IsBgmPlaying() || GetCurrentBgmSong() == resolvedSong)",
            source[preserve:],
        )
        self.assertIn(
            "if (IsBgmPlaying() && GetCurrentBgmSong() == resolvedSong)",
            source[preserve:],
        )

    def test_level_up_restore_has_independent_volume_regression_path(self):
        source = (ROOT / "src/mapanim_lvup.c").read_text(encoding="utf-8")
        self.assertIn("ExpansionBgm_GetContinuationPolicy()", source)
        self.assertIn("ExpansionBgm_Continue(", source)
        self.assertIn("StartBgmVolumeChange(0x80, 0x100, 0x10, proc);", source)

    def test_song_none_stop_requests_are_not_routed(self):
        source = (ROOT / "src/expansion_bgm.c").read_text(encoding="utf-8")
        for relative in ("src/savemenu.c", "src/prep_menuproc.c", "src/prep_itemuse.c"):
            caller = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("ExpansionBgm_Change(", caller)
            self.assertIn("SONG_NONE", caller)
        self.assertIn("if (fallbackSong == SONG_NONE)", source)
        self.assertIn("if (request->fallbackSong == SONG_NONE)", source)
        self.assertIn("if (legacySong == SONG_NONE", source)
