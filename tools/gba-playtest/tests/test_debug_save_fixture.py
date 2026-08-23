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

import gba_playtest  # noqa: E402

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
            "WriteGameSave(",
            "WriteSuspendSave(",
            "RestartGameAndGoto",
            "SoftReset(",
            "DEBUGONLY_Startup",
            "DebugContinueMenu_",
            "DebugChuudanMenu_",
        ):
            self.assertNotIn(banned, source)
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
            tools.index('sSaveFixtureFinalMenuItemDefs[0].name = "Back"'),
            tools.index('sSaveFixtureFinalMenuItemDefs[1].name = "Run RAM"'),
        )
        self.assertIn(
            "gKeyStatusPtr->heldKeys & required",
            tools,
        )

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


if __name__ == "__main__":
    unittest.main()
