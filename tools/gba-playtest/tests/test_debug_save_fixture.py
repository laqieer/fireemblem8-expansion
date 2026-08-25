"""Contract tests for issue #128's volatile debug save fixture."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = ROOT / "tools" / "gba-playtest" / "scenarios"
FINGERPRINTS = ROOT / "tools" / "gba-playtest" / "fingerprints"
sys.path.insert(0, str(ROOT / "tools" / "gba-playtest"))
sys.path.insert(0, str(ROOT / "tools" / "gba-playtest" / "tests"))

import gba_playtest  # noqa: E402
import host_mode  # noqa: E402
import run_debug_save_fixture_checks  # noqa: E402
import sram_hash_mirror  # noqa: E402

DEBUG_SCENARIOS = (
    "debug-save-fixture-positive-modern-debug.json",
    "debug-save-fixture-cancel-modern-debug.json",
    "debug-save-fixture-invalid-modern-debug.json",
    "debug-save-fixture-incompatible-modern-debug.json",
    "debug-save-fixture-interruption-modern-debug.json",
)
RELEASE_SCENARIO = "debug-save-fixture-modern-release.json"


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", text)


class DebugSaveFixtureContractTests(unittest.TestCase):
    def test_every_scenario_and_fingerprint_is_well_formed(self):
        for name in (*DEBUG_SCENARIOS, RELEASE_SCENARIO):
            with self.subTest(name=name):
                scenario_path = SCENARIOS / name
                fingerprint_path = FINGERPRINTS / name
                self.assertTrue(scenario_path.is_file())
                self.assertTrue(fingerprint_path.is_file())
                scenario = gba_playtest.load_scenario(scenario_path)
                expected = gba_playtest.validate_fingerprint(
                    json.loads(fingerprint_path.read_text(encoding="utf-8")),
                    str(fingerprint_path),
                    policy="behavior",
                )
                self.assertEqual(scenario.name, expected["scenario"])

    def test_positive_case_uses_one_exact_whole_sram_hash(self):
        data = json.loads(
            (
                FINGERPRINTS
                / "debug-save-fixture-positive-modern-debug.json"
            ).read_text(encoding="utf-8")
        )
        hashes = {
            checkpoint["sram_hash"]
            for checkpoint in data["checkpoints"]
        }
        self.assertEqual(len(hashes), 1)
        self.assertTrue(next(iter(hashes)).startswith("fnv1a64-sram:"))
        scenario = json.loads(
            (
                SCENARIOS
                / "debug-save-fixture-positive-modern-debug.json"
            ).read_text(encoding="utf-8")
        )
        for checkpoint in scenario["checkpoints"]:
            self.assertNotIn("sram_hash_exclude_ranges", checkpoint)


class DebugSaveFixtureLayoutParserTests(unittest.TestCase):
    def test_shared_menu_symbol_size_mutation_is_rejected(self):
        object_symbols = (
            "00000000 00000048 D sSaveStateStableLayout\n"
            "00000048 000000d8 D sDebugToolsMenuItemDefs\n"
        )
        object_table = (
            "00000000 g O ewram_data 00000048 sSaveStateStableLayout\n"
            "00000048 g O ewram_data 000000d8 sDebugToolsMenuItemDefs\n"
        )
        fixture_symbols = (
            "00000000 0000006c d sDebugSaveFixtureState\n"
            "0000006c 00000058 D gDebugSaveFixtureProbe\n"
        )
        fixture_table = (
            "00000000 l O debug_save_fixture_data 0000006c sDebugSaveFixtureState\n"
            "0000006c g O debug_save_fixture_data 00000058 gDebugSaveFixtureProbe\n"
        )
        elf_symbols = (
            "02031818 0000008c B gDebugToolsProbe\n"
            "020318ac 00000048 B sSaveStateStableLayout\n"
            "020318f4 000000d8 B sDebugToolsMenuItemDefs\n"
            "020319d0 00000014 B gExpansionLanguageMenuProbe\n"
            "0203f424 0000006c b sDebugSaveFixtureState\n"
            "0203f490 00000058 B gDebugSaveFixtureProbe\n"
        )
        language_symbols = "                 U sDebugToolsMenuItemDefs\n"
        map_text = (
            "ewram_data      0x0202018c    0x1f35c\n"
            "debug_save_fixture_data 0x0203f424 0xc4\n"
        )

        run_debug_save_fixture_checks._check_layout_evidence(
            object_symbols,
            object_table,
            fixture_symbols,
            fixture_table,
            elf_symbols,
            language_symbols,
            map_text,
        )
        with self.assertRaisesRegex(RuntimeError, "shared debug menu"):
            run_debug_save_fixture_checks._check_layout_evidence(
                object_symbols.replace("000000d8", "000000d4"),
                object_table,
                fixture_symbols,
                fixture_table,
                elf_symbols,
                language_symbols,
                map_text,
            )
    def test_positive_case_drives_manual_suspend_through_map_menu_input(self):
        scenario = json.loads(
            (
                SCENARIOS
                / "debug-save-fixture-positive-modern-debug.json"
            ).read_text(encoding="utf-8")
        )
        actual = [
            (frame["start"], frame["keys"])
            for frame in scenario["frames"]
            if 2100 <= frame["start"] <= 2460
        ]
        self.assertEqual(
            actual,
            [
                (2100, ["A"]),
                (2200, ["DOWN"]),
                (2260, ["DOWN"]),
                (2320, ["DOWN"]),
                (2380, ["DOWN"]),
                (2460, ["A"]),
            ],
        )
        blocked = next(
            checkpoint
            for checkpoint in scenario["checkpoints"]
            if checkpoint["name"] == "volatile-write-blocked"
        )
        expected_probes = {
            probe["address"]: probe["expected"]
            for probe in blocked["probes"]
        }
        self.assertEqual(
            expected_probes["gDebugSaveFixtureProbe+0x20"],
            "0x00000005",
        )
        self.assertEqual(
            expected_probes["gDebugSaveFixtureProbe+0x24"],
            "0x00000002",
        )

    def test_generated_suspend_has_minimal_interactive_roster(self):
        fixture = (
            run_debug_save_fixture_checks.sram_fixture
            .build_debug_save_fixture_source_image(ROOT)
        )
        suspend_start = (
            run_debug_save_fixture_checks.sram_fixture
            .DEBUG_SAVE_SUSPEND_ALT_OFFSET
        )
        blue = (
            suspend_start
            + run_debug_save_fixture_checks.sram_fixture
            .DEBUG_SAVE_SUSPEND_BLUE_UNITS_OFFSET
        )
        red = (
            suspend_start
            + run_debug_save_fixture_checks.sram_fixture
            .DEBUG_SAVE_SUSPEND_RED_UNITS_OFFSET
        )
        self.assertEqual(fixture[blue:blue + 2], b"\x01\x02")
        self.assertEqual(fixture[blue + 0x0E:blue + 0x10], b"\x10\x10")
        self.assertEqual(fixture[red:red + 2], b"\x47\x41")
        self.assertEqual(fixture[red + 0x0E:red + 0x10], b"\x14\x14")

    def test_release_negative_allows_only_normal_release_boot_writes(self):
        source = (
            run_debug_save_fixture_checks.sram_fixture
            .build_debug_save_fixture_source_image(
                ROOT,
                include_runtime_roster=False,
            )
        )
        fixture_tools = run_debug_save_fixture_checks.sram_fixture.sft
        self.assertEqual(
            sram_hash_mirror.compute_sram_hash(source),
            "fnv1a64-sram:4c0f364a34fd1659",
        )

        after_probe = bytearray(source)
        after_probe[
            fixture_tools.SRAM_PROBE_OFFSET:
            fixture_tools.SRAM_PROBE_OFFSET + fixture_tools.SRAM_PROBE_SIZE
        ] = (0x12345678).to_bytes(4, "little")
        self.assertEqual(
            sram_hash_mirror.compute_sram_hash(bytes(after_probe)),
            "fnv1a64-sram:e81bac1ff9c4a929",
        )

        after_title_music = bytearray(after_probe)
        after_title_music[fixture_tools.SOUND_ROOM_OFFSET] = 0x02
        after_title_music[
            fixture_tools.SOUND_ROOM_CHECKSUM_OFFSET:
            fixture_tools.SOUND_ROOM_CHECKSUM_OFFSET + 2
        ] = (4).to_bytes(2, "little")
        after_title_music[
            fixture_tools.SOUND_ROOM_FORMAT_OFFSET:
            fixture_tools.SOUND_ROOM_FORMAT_OFFSET + 2
        ] = fixture_tools.SOUND_ROOM_FORMAT_CURRENT.to_bytes(2, "little")
        self.assertEqual(
            sram_hash_mirror.compute_sram_hash(bytes(after_title_music)),
            "fnv1a64-sram:941cd9f46edd338a",
        )

        fingerprint = json.loads(
            (FINGERPRINTS / RELEASE_SCENARIO).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [
                checkpoint["sram_hash"]
                for checkpoint in fingerprint["checkpoints"]
            ],
            [
                "fnv1a64-sram:e81bac1ff9c4a929",
                "fnv1a64-sram:941cd9f46edd338a",
                "fnv1a64-sram:941cd9f46edd338a",
            ],
            "only SramInit's hardware probe and title music's ordinary "
            "Sound Room write may change the release source image",
        )

    def test_cancel_invalid_incompatible_and_interruption_are_exact(self):
        for name in DEBUG_SCENARIOS[1:]:
            with self.subTest(name=name):
                data = json.loads((FINGERPRINTS / name).read_text(encoding="utf-8"))
                hashes = [
                    checkpoint["sram_hash"]
                    for checkpoint in data["checkpoints"]
                ]
                self.assertTrue(hashes)
                self.assertTrue(
                    all(value.startswith("fnv1a64-sram:") for value in hashes)
                )
                self.assertEqual(len(set(hashes)), 1)

    def test_fixture_core_never_calls_a_persistent_writer(self):
        source = _strip_comments(
            (ROOT / "src" / "debug_save_fixture.c").read_text(encoding="utf-8")
        )
        for banned in (
            "WriteSramFast(",
            "WriteAndVerifySramFast(",
            "WipeSram(",
            "InitGlobalSaveInfodata(",
            "RestartGameAndGoto",
            "SoftReset(",
            "DEBUGONLY_Startup",
            "DebugContinueMenu_",
            "DebugChuudanMenu_",
        ):
            self.assertNotIn(banned, source)
        for banned_name in ("WriteGameSave", "WriteSuspendSave"):
            self.assertNotRegex(
                source,
                rf"\b{re.escape(banned_name)}\s*\(",
            )
        self.assertNotRegex(source, r"\bgSram\s*=")
        self.assertIn("BuildCurrentExpansionSaveMeta(", source)
        self.assertIn("ClassifySaveCompatRaw(", source)

    def test_low_level_writer_guards_precede_every_store(self):
        source = _strip_comments(
            (ROOT / "src" / "agb_sram.c").read_text(encoding="utf-8")
        )
        write_body = source.split(
            "void WriteSramFast(const u8 *src, u8 *dest, u32 size)", 1
        )[1].split("u32 VerifySramFast_Core", 1)[0]
        verify_body = source.split(
            "u32 WriteAndVerifySramFast(void const * src, void * dest, u32 size)",
            1,
        )[1]
        self.assertLess(
            write_body.index("DebugSaveFixture_ShouldBlockSramWrite"),
            write_body.index("*dest++ = *src++"),
        )
        self.assertIn("WriteSramFast(src, dest, size)", verify_body)
        self.assertNotIn("*dest++ = *src++", verify_body)

    def test_gamecontrol_owns_continue_and_debug_menu_owns_no_reset(self):
        tools = _strip_comments(
            (ROOT / "src" / "debugtools_tools.c").read_text(encoding="utf-8")
        )
        gamecontrol = _strip_comments(
            (ROOT / "src" / "gamecontrol.c").read_text(encoding="utf-8")
        )
        title = _strip_comments(
            (ROOT / "src" / "titlescreen.c").read_text(encoding="utf-8")
        )
        self.assertIn("DebugSaveFixture_RequestContinue(", tools)
        self.assertIn("DebugTools_EndSessionAfterMenuEnd(", tools)
        self.assertNotIn("DebugSaveFixture_ConsumePendingContinue(", tools)
        self.assertNotIn("SoftReset(", tools)
        self.assertNotIn("RestartGameAndGoto", tools)
        self.assertIn("DebugSaveFixture_ConsumePendingContinue(", gamecontrol)
        self.assertIn("DebugSaveFixture_IsContinuePending(", title)
        self.assertLess(
            tools.index('sSaveFixtureMenuItemDefs[0].name = "Back"'),
            tools.index('sSaveFixtureMenuItemDefs[1].name = "Run RAM"'),
        )
        self.assertIn(
            "gKeyStatusPtr->heldKeys & requiredModifiers",
            tools,
        )
        self.assertIn("gKeyStatusPtr->newKeys & A_BUTTON", tools)

    def test_save_format_and_archival_boundaries_are_explicit(self):
        header = (
            ROOT / "include" / "expansion_debug_save_fixture.h"
        ).read_text(encoding="utf-8")
        source = (
            ROOT / "src" / "debug_save_fixture.c"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "FE8_EXPANSION_DEBUGTOOLS_ENABLED && !defined(FE8_ARCHIVAL_BUILD)",
            header,
        )
        self.assertIn(
            "FE8_EXPANSION_DEBUGTOOLS_ENABLED && !defined(FE8_ARCHIVAL_BUILD)",
            source,
        )
        self.assertNotIn("SAVE_FORMAT_VERSION_CURRENT =", source)
        self.assertNotIn("FE8_EXPANSION_SAVE_COMPAT_EPOCH =", source)


@host_mode.live_artifact_testcase("debug save-fixture layout coverage")
class DebugSaveFixtureLayoutEmissionTests(unittest.TestCase):
    def test_retained_anchor_and_fixture_budget_layout(self):
        elf = (
            ROOT
            / "build"
            / "expansion-modern"
            / "debug"
            / "aapcs"
            / "fireemblem8.elf"
        )
        self.assertTrue(elf.is_file(), f"focused debug ELF is missing: {elf}")
        run_debug_save_fixture_checks.check_layout_anchor(elf)


if __name__ == "__main__":
    unittest.main()
