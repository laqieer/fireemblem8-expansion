"""Focused contract/source-audit tests for issues #34 and #35."""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.modernize.tests.test_save_format_meta_bytes_native import (
    _extract_c_function,
)

ROOT = Path(__file__).resolve().parents[3]


class CasualModeContractTests(unittest.TestCase):
    def test_configuration_surface_is_default_off_and_validated(self):
        config = (ROOT / "config.mk").read_text(encoding="utf-8")
        configure_ac = (ROOT / "configure.ac").read_text(encoding="utf-8")
        header = (ROOT / "include/expansion_config.h").read_text(encoding="utf-8")

        self.assertIn("EXPANSION_CASUAL_MODE ?= 0", config)
        self.assertIn("--enable-casual-mode", configure_ac)
        self.assertIn("--casual-mode", configure_ac)
        self.assertIn("FE8_EXPANSION_CASUAL_MODE must be 0 or 1", header)

    def test_casual_mode_participates_in_identity_and_rejects_other_values(self):
        tool = ROOT / "scripts/modernize/expansion_config.py"
        base = [
            "python3",
            str(tool),
            "resolve",
            "--config",
            "debug",
            "--abi",
            "aapcs",
            "--rom-size",
            "16M",
        ]
        disabled = subprocess.run(base + ["--casual-mode", "0"], capture_output=True, text=True)
        enabled = subprocess.run(base + ["--casual-mode", "1"], capture_output=True, text=True)
        invalid = subprocess.run(base + ["--casual-mode", "2"], capture_output=True, text=True)

        self.assertEqual(disabled.returncode, 0)
        self.assertEqual(enabled.returncode, 0)
        self.assertNotEqual(disabled.stdout, enabled.stdout)
        self.assertNotEqual(invalid.returncode, 0)

    def test_only_combat_and_arena_paths_mark_defeats(self):
        battle = (ROOT / "src/bmmind.c").read_text(encoding="utf-8")
        policy = (ROOT / "src/expansion_casual_mode.c").read_text(encoding="utf-8")
        unit = (ROOT / "src/bmunit.c").read_text(encoding="utf-8")
        scripted = (ROOT / "src/eventinfo.c").read_text(encoding="utf-8")
        hazards = (ROOT / "src/bmusailment.c").read_text(encoding="utf-8")

        self.assertEqual(battle.count("ExpansionCasualMode_MarkDefeat"), 2)
        self.assertIn("EXPANSION_CASUAL_DEFEAT_COMBAT", battle)
        self.assertIn("EXPANSION_CASUAL_DEFEAT_ARENA", battle)
        self.assertIn("#if FE8_EXPANSION_CASUAL_MODE", policy)
        self.assertIn("unit->state &= ~US_BIT24", unit)
        self.assertNotIn("ExpansionCasualMode_MarkDefeat", scripted)
        self.assertNotIn("ExpansionCasualMode_MarkDefeat", hazards)

    def test_marker_survives_both_save_paths_and_is_cleared_at_boundary(self):
        unit = (ROOT / "include/bmunit.h").read_text(encoding="utf-8")
        save_header = (ROOT / "include/bmsave.h").read_text(encoding="utf-8")
        save = (ROOT / "src/bmsave.c").read_text(encoding="utf-8")
        cleanup = (ROOT / "src/bmio.c").read_text(encoding="utf-8")
        policy = (ROOT / "src/expansion_casual_mode.c").read_text(encoding="utf-8")

        self.assertIn("US_BIT24        = (1 << 24)", unit)
        self.assertIn("PACKED_US_CASUAL_DEFEAT = 1 << 8", save_header)
        self.assertIn("PACKED_US_CASUAL_DEFEAT", save)
        self.assertIn("ExpansionCasualMode_RestoreAtChapterBoundary", cleanup)
        self.assertIn("US_BIT24 | US_DEAD", policy)

    def test_game_save_marker_round_trips_across_casual_profiles(self):
        save = (ROOT / "src/bmsave.c").read_text(encoding="utf-8")
        cleanup = (ROOT / "src/bmio.c").read_text(encoding="utf-8")

        write_match = re.search(
            r"void WriteGameSavePackedUnit\(.*?\n\}",
            save,
            re.DOTALL,
        )
        load_match = re.search(
            r"void LoadSavedUnit\(.*?\n\}",
            save,
            re.DOTALL,
        )
        self.assertIsNotNone(write_match)
        self.assertIsNotNone(load_match)

        write_body = write_match.group(0)
        load_body = load_match.group(0)
        self.assertIn("PACKED_US_CASUAL_DEFEAT", write_body)
        self.assertIn("PACKED_US_CASUAL_DEFEAT", load_body)
        self.assertNotIn("#if FE8_EXPANSION_CASUAL_MODE", write_body)
        self.assertNotIn("#if FE8_EXPANSION_CASUAL_MODE", load_body)
        self.assertIn("US_BIT24 |", cleanup)

    def test_marker_serialization_is_present_in_both_profile_preprocessed_sources(self):
        cc = shutil.which("gcc") or shutil.which("cc")
        if cc is None:
            self.skipTest("no host C preprocessor available")

        for casual_mode in ("0", "1"):
            with self.subTest(casual_mode=casual_mode):
                result = subprocess.run(
                    [
                        cc,
                        "-E",
                        "-P",
                        "-w",
                        "-I",
                        str(ROOT / "include"),
                        "-I",
                        str(ROOT / "include" / "generated"),
                        "-DMODERN",
                        "-DFE8_EXPANSION_CASUAL_MODE=" + casual_mode,
                        str(ROOT / "src" / "bmsave.c"),
                    ],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("unitp.flag |= PACKED_US_CASUAL_DEFEAT", result.stdout)
                self.assertIn("unit->state |= US_BIT24", result.stdout)


class PortraitResolverContractTests(unittest.TestCase):
    def test_native_fixture_resolves_rule_order_flags_bounds_and_legacy_fallback(self):
        source = (ROOT / "src/expansion_portraits.c").read_text(encoding="utf-8")
        header = (ROOT / "include/expansion_portraits.h").read_text(encoding="utf-8")
        context = re.search(
            r"struct ExpansionPortraitContext\s*\{.*?\n\};", header, re.DOTALL
        )
        rule = re.search(
            r"struct ExpansionPortraitRule\s*\{.*?\n\};", header, re.DOTALL
        )
        self.assertIsNotNone(context)
        self.assertIsNotNone(rule)

        functions = "\n".join(
            _extract_c_function(source, name)
            for name in (
                "IsValidPortraitId",
                "IsValidMinimugId",
                "ExpansionPortrait_ValidateRegistry",
                "RuleMatches",
                "ResolveLegacy",
                "ExpansionPortrait_Resolve",
            )
        )
        probe = f"""
#include <stdint.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define NULL ((void *)0)
#define EXPANSION_PORTRAIT_MATCH_ANY 0xFFFF
#define EXPANSION_PORTRAIT_CHAPTER_ANY 0xFF
#define EXPANSION_PORTRAIT_FULL_ID_MAX 0x00AC
#define EXPANSION_PORTRAIT_MINIMUG_ID_MIN 0x7F00
#define EXPANSION_PORTRAIT_MINIMUG_ID_MAX 0x7F07
enum ExpansionPortraitKind {{
    EXPANSION_PORTRAIT_KIND_FULL = 0,
    EXPANSION_PORTRAIT_KIND_MINIMUG = 1,
}};
struct CharacterData {{ u16 number; u16 portraitId; u8 miniPortrait; }};
struct ClassData {{ u16 number; u16 defaultPortraitId; }};
struct Unit {{ int unused; }};
{context.group(0)}
{rule.group(0)}

struct ExpansionPortraitRule gExpansionPortraitRules[] = {{
    {{ 10, EXPANSION_PORTRAIT_MATCH_ANY, EXPANSION_PORTRAIT_CHAPTER_ANY, 0,
        0, 0, 20, 0x7F01 }},
    {{ 10, 3, 7, 0, 1, 0, 30, 0x7F02 }},
    {{ 11, EXPANSION_PORTRAIT_MATCH_ANY, EXPANSION_PORTRAIT_CHAPTER_ANY, 0,
        1, 2, 40, 0x7F03 }},
}};
unsigned gExpansionPortraitRuleCount = 3;
{functions}

int main(void)
{{
    struct CharacterData character = {{ 10, 0x4A, 2 }};
    struct ClassData classData = {{ 3, 0x66 }};
    struct Unit unit = {{ 0 }};
    struct ExpansionPortraitContext context = {{
        &unit, &character, &classData, 10, 3, 7, 1
    }};

    if (ExpansionPortrait_Resolve(&context, EXPANSION_PORTRAIT_KIND_FULL) != 20)
        return 1;
    if (ExpansionPortrait_Resolve(&context, EXPANSION_PORTRAIT_KIND_MINIMUG) != 0x7F01)
        return 2;

    context.character_id = 11;
    context.flags = 1;
    if (ExpansionPortrait_Resolve(&context, EXPANSION_PORTRAIT_KIND_FULL) != 40)
        return 3;
    context.flags = 3;
    if (ExpansionPortrait_Resolve(&context, EXPANSION_PORTRAIT_KIND_FULL) != 0x4A)
        return 4;

    context.character_id = 10;
    context.flags = 0;
    context.chapter_id = 0x22;
    gExpansionPortraitRuleCount = 0;
    if (ExpansionPortrait_Resolve(&context, EXPANSION_PORTRAIT_KIND_FULL) != 0x46)
        return 5;

    gExpansionPortraitRuleCount = 3;
    context.chapter_id = 7;
    gExpansionPortraitRules[0].full_portrait_id = 0xAD;
    if (ExpansionPortrait_ValidateRegistry() != 0)
        return 6;
    if (ExpansionPortrait_Resolve(&context, EXPANSION_PORTRAIT_KIND_MINIMUG) != 0x7F02)
        return 7;
    return 0;
}}
"""
        artifact_dir = ROOT / "build" / "test-artifacts" / "portrait-resolver-native"
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

    def test_typed_registry_supports_all_selectors_and_validation(self):
        header = (ROOT / "include/expansion_portraits.h").read_text(encoding="utf-8")
        source = (ROOT / "src/expansion_portraits.c").read_text(encoding="utf-8")

        for field in ("character_id", "class_id", "chapter_id", "flags"):
            self.assertIn(field, header)
        self.assertIn("ExpansionPortrait_ValidateRegistry", header)
        self.assertIn("required_flags & rule->forbidden_flags", source)
        self.assertIn("RuleMatches", source)
        self.assertIn("ResolveLegacy", source)

    def test_known_direct_consumers_route_through_resolver(self):
        consumers = (
            "src/banim-ekrlvup.c",
            "src/mapanim_lvup.c",
            "src/classchg-event.c",
            "src/uisupport.c",
            "src/ending_details.c",
        )
        direct = re.compile(r"(?:pCharacterData|gCharacterData)\[[^\n]*\]\.portraitId")

        for relative in consumers:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("ExpansionPortrait_Resolve", source, relative)
            self.assertIsNone(direct.search(source), relative)

    def test_unit_backed_consumers_preserve_class_context(self):
        expected_counts = {
            "src/banim-ekrlvup.c": 2,
            "src/mapanim_lvup.c": 1,
            "src/classchg-event.c": 1,
        }

        for relative, expected_count in expected_counts.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertEqual(
                source.count("ExpansionPortrait_ResolveUnit("),
                expected_count,
                relative,
            )
            self.assertNotIn("ExpansionPortrait_ResolveCharacter(", source, relative)

    def test_no_runtime_consumer_reads_portrait_fields_directly(self):
        direct = re.compile(r"(?:\.portraitId|\.miniPortrait)")
        excluded = {"data_characters.c", "expansion_portraits.c"}

        for path in (ROOT / "src").glob("*.c"):
            if path.name in excluded:
                continue
            self.assertIsNone(direct.search(path.read_text(encoding="utf-8")), path)

    def test_default_wrappers_and_legacy_fallback_remain(self):
        unit = (ROOT / "src/bmunit.c").read_text(encoding="utf-8")
        resolver = (ROOT / "src/expansion_portraits.c").read_text(encoding="utf-8")
        linker = (ROOT / "ldscript.txt").read_text(encoding="utf-8")

        self.assertIn("EXPANSION_PORTRAIT_KIND_FULL", unit)
        self.assertIn("EXPANSION_PORTRAIT_KIND_MINIMUG", unit)
        self.assertIn("character->portraitId", resolver)
        self.assertIn("class_data->defaultPortraitId", resolver)
        self.assertIn("src/expansion_portraits.o(.text);", linker)
