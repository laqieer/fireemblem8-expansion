"""Issues #11/#125: bounded tools and cursor-selected unit editing live.

Scenario:    tools/gba-playtest/scenarios/debugtools-tools-modern-debug.json
Fingerprint: tools/gba-playtest/fingerprints/debugtools-tools-modern-debug.json
Release neg: tools/gba-playtest/scenarios/debugtools-tools-modern-release.json

Boots the debug-only Fast Boot: Chapter 2 map hub, then triggers each of the
five bounded tools from its real hub row and proves an asserted semantic state
effect plus a safe return to the hub. Issue #125 then reselects the unit under
the cursor, previews and confirms an actual 17 -> 16 HP edit, heals 16 -> 17,
rejects an empty tile, proves SRAM equality apart from documented
build-variable metadata fields, and returns to an interactive map. Evidence is
semantic probes and normalized SRAM hashes, never a framebuffer or
relocated-pointer oracle.

  5 Unit Inspect/Edit -- inspect resolves live cursor slot 1, character 6,
    class 0x48 at 17/17 HP. A separate editor preview leaves HP unchanged,
    confirmation changes 17 -> 16, and confirmed heal restores 16 -> 17.
  6 Convoy Inspect/Edit -- inspect samples count 0; confirm adds an item
    (convoyAddTransactionCount 0 -> 1); a re-inspect shows the count rose 0 -> 1.
  7 Flag/Chapter -- inspect samples chapterIndex 2 and flag 0; confirm toggles
    the flag 0 -> 1 (debugFlagToggleCount 0 -> 1).
  8 RNG Inspect/Control -- inspect samples seed 0x0000ee77; confirm reseeds
    (rngReseedTransactionCount 0 -> 1); a re-inspect shows the seed changed.
  9 Save Compatibility/State Inspect -- read-only inspect classifies SRAM
    (SAVE_COMPAT_CURRENT, count 0 -> 1); its Back item never mutates (count
    stays 1).

The cursor then moves to an empty tile; Unit Inspect records a typed rejection
without changing HP or transaction count. The release sibling replays the same
input, proves the established probe stays zero, and pairs with the disabled
object test proving issue #125's editor probe/code are absent.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
SCENARIOS_DIR = PLAYTEST_DIR / "scenarios"
FINGERPRINTS_DIR = PLAYTEST_DIR / "fingerprints"
DEBUG_NAME = "debugtools-tools-modern-debug"
RELEASE_NAME = "debugtools-tools-modern-release"
DEBUG_SCENARIO = SCENARIOS_DIR / f"{DEBUG_NAME}.json"
DEBUG_FINGERPRINT = FINGERPRINTS_DIR / f"{DEBUG_NAME}.json"
RELEASE_SCENARIO = SCENARIOS_DIR / f"{RELEASE_NAME}.json"
RELEASE_FINGERPRINT = FINGERPRINTS_DIR / f"{RELEASE_NAME}.json"

# debug gDebugToolsProbe (0x02031818) fields + registry sHubActive + gBmSt cursor
HUB_OPEN = 0x02031818
REG_COUNT = 0x0203181C
U_FOUND = 0x02031868
U_CURHP = 0x0203186C
U_MAXHP = 0x02031870
U_HEALTX = 0x02031874
CV_COUNT = 0x02031878
CV_ADDTX = 0x0203187C
CH_INDEX = 0x02031880
FLG_TX = 0x02031884
FLG_VAL = 0x02031888
RNG_SEED = 0x0203188C
RNG_TX = 0x02031890
SV_STATE = 0x02031894
SV_COUNT = 0x02031898
S_HUB = 0x02031614
CURSOR_X = 0x02021104
U_FOUND_BINDING = "gDebugToolsProbe+0x50"
CURSOR_X_BINDING = "gBmSt+0x14"
UNIT_TARGET_SLOT = "gDebugToolsUnitEditorProbe+0x00"
UNIT_CHARACTER = "gDebugToolsUnitEditorProbe+0x04"
UNIT_CLASS = "gDebugToolsUnitEditorProbe+0x08"
UNIT_PREVIEW_COUNT = "gDebugToolsUnitEditorProbe+0x1c"
UNIT_EDIT_TX = "gDebugToolsUnitEditorProbe+0x20"
UNIT_REJECT_COUNT = "gDebugToolsUnitEditorProbe+0x28"
UNIT_LAST_FIELD = "gDebugToolsUnitEditorProbe+0x34"
UNIT_OLD = "gDebugToolsUnitEditorProbe+0x38"
UNIT_NEW = "gDebugToolsUnitEditorProbe+0x3c"
UNIT_OUTCOME = "gDebugToolsUnitEditorProbe+0x40"
UNIT_REFRESH_COUNT = "gDebugToolsUnitEditorProbe+0x44"
CURSOR_UNIT_HP = "gUnitArrayBlue+0x13"
SRAM_METADATA_NORMALIZATION_RANGES = (
    (29620, 17),
    (29640, 9),
    (29650, 2),
)

_POINTER_RANGES = (
    (0x02000000, 0x0203FFFF),
    (0x03000000, 0x03007FFF),
    (0x08000000, 0x0DFFFFFF),
    (0x0E000000, 0x0E00FFFF),
)

sys.path.insert(0, str(PLAYTEST_DIR))
sys.path.insert(0, str(PLAYTEST_DIR / "tests"))
import gba_playtest  # noqa: E402
import host_mode  # noqa: E402

DEBUG_ROM = host_mode.modern_rom("debug")
RELEASE_ROM = host_mode.modern_rom("release")


class ToolsScenarioFilesTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DEBUG_SCENARIO.exists(), f"missing scenario: {DEBUG_SCENARIO}")
        self.scenario = gba_playtest.load_scenario(DEBUG_SCENARIO)

    def _cp(self, name):
        by_name = {c.name: c for c in self.scenario.checkpoints}
        self.assertIn(name, by_name, f"missing checkpoint {name}")
        return {
            p.address if p.address is not None else p.binding: p.expected
            for p in by_name[name].probes
        }

    def test_scenario_parses_enabled_probe_only(self):
        self.assertEqual(self.scenario.name, DEBUG_NAME)
        self.assertFalse(self.scenario.disabled)
        for c in self.scenario.checkpoints:
            self.assertFalse(c.framebuffer, f"{c.name} must be probe-only (no framebuffer)")
            self.assertTrue(c.probes, f"{c.name} must carry probes")
            if c.name not in {
                "hub-closed-map-interactive",
                "unit-editor-final-map-interactive",
            }:
                self.assertFalse(
                    c.sram_hash,
                    f"{c.name} must use SRAM only at before/after boundaries",
                )

    def test_checkpoint_names_and_order(self):
        self.assertEqual(
            [c.name for c in self.scenario.checkpoints],
            [
                "hub-open",
                "unit-inspected",
                "unit-heal-confirmed",
                "convoy-inspected",
                "convoy-add-confirmed",
                "convoy-reinspected-count-rose",
                "flag-inspected",
                "flag-toggle-confirmed",
                "rng-inspected",
                "rng-reseed-confirmed",
                "rng-reinspected-seed-changed",
                "save-inspected",
                "save-back-readonly-unchanged",
                "hub-closed-map-interactive",
                "unit-editor-cursor-target-inspected",
                "unit-current-hp-previewed",
                "unit-current-hp-confirmed",
                "unit-heal-restored-full-hp",
                "unit-empty-tile-rejected",
                "unit-editor-final-map-interactive",
            ],
        )

    def test_hub_open_registers_all_nine_tools(self):
        c = self._cp("hub-open")
        self.assertEqual(c[HUB_OPEN], "0x00000002")
        self.assertEqual(c[S_HUB], "0x01")
        self.assertEqual(c[REG_COUNT], "0x0000000a")
        # every mutating transaction counter starts at zero
        for addr in (U_HEALTX, CV_ADDTX, FLG_TX, RNG_TX, SV_COUNT):
            self.assertEqual(c[addr], "0x00000000")

    def test_unit_inspect_then_confirm(self):
        insp = self._cp("unit-inspected")
        self.assertEqual(insp[U_FOUND], "0x00000001")
        self.assertEqual(insp[U_CURHP], "0x00000011")
        self.assertEqual(insp[U_MAXHP], "0x00000011")
        self.assertEqual(insp[UNIT_TARGET_SLOT], "0x00000001")
        self.assertEqual(insp[UNIT_CHARACTER], "0x00000006")
        self.assertEqual(insp[UNIT_CLASS], "0x00000048")
        self.assertEqual(insp[U_HEALTX], "0x00000000")
        self.assertEqual(insp[S_HUB], "0x00", "inspect opens the confirm submenu (hub closed)")
        conf = self._cp("unit-heal-confirmed")
        self.assertEqual(conf[U_HEALTX], "0x00000001", "heal only after explicit confirm")
        self.assertEqual(conf[HUB_OPEN], "0x00000003", "submenu returns safely to the hub")

    def test_convoy_inspect_confirm_then_count_rose(self):
        self.assertEqual(self._cp("convoy-inspected")[CV_COUNT], "0x00000000")
        self.assertEqual(self._cp("convoy-add-confirmed")[CV_ADDTX], "0x00000001")
        rose = self._cp("convoy-reinspected-count-rose")
        self.assertEqual(rose[CV_COUNT], "0x00000001", "re-inspect proves the add's effect")

    def test_flag_inspect_then_toggle(self):
        insp = self._cp("flag-inspected")
        self.assertEqual(insp[CH_INDEX], "0x00000002")
        self.assertEqual(insp[FLG_VAL], "0x00000000")
        conf = self._cp("flag-toggle-confirmed")
        self.assertEqual(conf[FLG_TX], "0x00000001")
        self.assertEqual(conf[FLG_VAL], "0x00000001", "flag value flips 0 -> 1")

    def test_rng_inspect_reseed_then_seed_changed(self):
        seed_in = self._cp("rng-inspected")[RNG_SEED]
        self.assertEqual(self._cp("rng-reseed-confirmed")[RNG_TX], "0x00000001")
        seed_after = self._cp("rng-reinspected-seed-changed")[RNG_SEED]
        self.assertNotEqual(seed_after, seed_in, "reseed must change the sampled seed")

    def test_save_inspect_is_read_only(self):
        insp = self._cp("save-inspected")
        self.assertEqual(insp[SV_COUNT], "0x00000001")
        back = self._cp("save-back-readonly-unchanged")
        self.assertEqual(back[SV_COUNT], "0x00000001", "read-only: Back must not mutate")
        self.assertEqual(back[HUB_OPEN], "0x00000009")

    def test_map_interactive_after_hub_closes(self):
        c = self._cp("hub-closed-map-interactive")
        self.assertEqual(c[S_HUB], "0x00", "hub fully closed")
        self.assertEqual(c[CURSOR_X], "0x07", "player cursor responds to input (0x06 -> 0x07)")

    def test_cursor_unit_hp_preview_confirm_heal_and_empty_reject(self):
        inspected = self._cp("unit-editor-cursor-target-inspected")
        self.assertEqual(inspected[CURSOR_UNIT_HP], "0x11")
        self.assertEqual(inspected[UNIT_TARGET_SLOT], "0x00000001")
        self.assertEqual(inspected[UNIT_CHARACTER], "0x00000006")
        self.assertEqual(inspected[UNIT_CLASS], "0x00000048")
        self.assertEqual(inspected[UNIT_OUTCOME], "0x00000001")

        preview = self._cp("unit-current-hp-previewed")
        self.assertEqual(preview[CURSOR_UNIT_HP], "0x11", "preview is read-only")
        self.assertEqual(preview[UNIT_PREVIEW_COUNT], "0x00000002")
        self.assertEqual(preview[UNIT_EDIT_TX], "0x00000000")
        self.assertEqual(preview[UNIT_OLD], "0x00000011")
        self.assertEqual(preview[UNIT_NEW], "0x00000010")
        self.assertEqual(preview[UNIT_OUTCOME], "0x00000002")

        confirmed = self._cp("unit-current-hp-confirmed")
        self.assertEqual(confirmed[CURSOR_UNIT_HP], "0x10")
        self.assertEqual(confirmed[UNIT_EDIT_TX], "0x00000001")
        self.assertEqual(confirmed[UNIT_LAST_FIELD], "0x00000001")
        self.assertEqual(confirmed[UNIT_OLD], "0x00000011")
        self.assertEqual(confirmed[UNIT_NEW], "0x00000010")
        self.assertEqual(confirmed[UNIT_OUTCOME], "0x00000003")
        self.assertEqual(confirmed[UNIT_REFRESH_COUNT], "0x00000001")

        healed = self._cp("unit-heal-restored-full-hp")
        self.assertEqual(healed[CURSOR_UNIT_HP], "0x11")
        self.assertEqual(healed[UNIT_EDIT_TX], "0x00000002")
        self.assertEqual(healed[UNIT_OLD], "0x00000010")
        self.assertEqual(healed[UNIT_NEW], "0x00000011")
        self.assertEqual(healed[UNIT_OUTCOME], "0x00000003")
        self.assertEqual(healed[UNIT_REFRESH_COUNT], "0x00000002")

        rejected = self._cp("unit-empty-tile-rejected")
        self.assertEqual(rejected[U_FOUND_BINDING], "0x00000000")
        self.assertEqual(rejected[CURSOR_UNIT_HP], "0x11")
        self.assertEqual(rejected[UNIT_EDIT_TX], "0x00000002")
        self.assertEqual(rejected[UNIT_REJECT_COUNT], "0x00000001")
        self.assertEqual(rejected[UNIT_OUTCOME], "0x00000007")

    def test_unit_editor_preserves_sram_and_final_map_interactivity(self):
        by_name = {c.name: c for c in self.scenario.checkpoints}
        before = by_name["hub-closed-map-interactive"]
        after = by_name["unit-editor-final-map-interactive"]
        self.assertTrue(before.sram_hash)
        self.assertTrue(after.sram_hash)
        self.assertEqual(before.expected_sram_hash, after.expected_sram_hash)
        self.assertEqual(
            before.sram_hash_exclude_ranges,
            SRAM_METADATA_NORMALIZATION_RANGES,
        )
        self.assertEqual(
            after.sram_hash_exclude_ranges,
            SRAM_METADATA_NORMALIZATION_RANGES,
        )
        self.assertTrue(
            before.expected_sram_hash.startswith("fnv1a64-sram-normalized:"),
        )

        final = self._cp("unit-editor-final-map-interactive")
        self.assertEqual(final[S_HUB], "0x00")
        self.assertEqual(
            final[CURSOR_X_BINDING],
            "0x06",
            "LEFT moves the cursor after cleanup",
        )
        self.assertEqual(final[CURSOR_UNIT_HP], "0x11")
        self.assertEqual(final[UNIT_EDIT_TX], "0x00000002")
        self.assertEqual(final[UNIT_REJECT_COUNT], "0x00000001")

    def test_no_probe_asserts_a_relocated_pointer_value(self):
        for c in self.scenario.checkpoints:
            for p in c.probes:
                if p.expected is None or p.size < 4:
                    continue
                value = int(p.expected, 16)
                self.assertFalse(
                    any(lo <= value <= hi for lo, hi in _POINTER_RANGES),
                    f"{c.name} probe {p.binding} asserts pointer-range value {p.expected}",
                )

    def test_committed_fingerprint_matches(self):
        fp = gba_playtest.validate_fingerprint(
            json.loads(DEBUG_FINGERPRINT.read_text(encoding="utf-8")),
            str(DEBUG_FINGERPRINT),
            policy="behavior",
        )
        self.assertEqual(fp["scenario"], DEBUG_NAME)
        self.assertEqual(len(fp["checkpoints"]), 20)


class ToolsReleaseNegativeFilesTests(unittest.TestCase):
    def test_release_sibling_asserts_all_zero(self):
        scenario = gba_playtest.load_scenario(RELEASE_SCENARIO)
        self.assertEqual(scenario.name, RELEASE_NAME)
        any_probe = False
        for c in scenario.checkpoints:
            self.assertFalse(c.framebuffer)
            for p in c.probes:
                any_probe = True
                self.assertEqual(
                    p.expected, "0x00000000",
                    f"release {c.name} probe {p.binding} must be zero (tools compiled out)",
                )
        self.assertTrue(any_probe, "release sibling must carry all-zero probes")

    def test_release_sram_hash_is_unchanged_across_identical_input_tail(self):
        scenario = gba_playtest.load_scenario(RELEASE_SCENARIO)
        by_name = {c.name: c for c in scenario.checkpoints}
        before = by_name["hub-closed-map-interactive-release-zero"]
        after = by_name["unit-editor-release-final"]
        self.assertTrue(before.sram_hash)
        self.assertTrue(after.sram_hash)
        self.assertEqual(before.expected_sram_hash, after.expected_sram_hash)
        self.assertEqual(
            before.sram_hash_exclude_ranges,
            SRAM_METADATA_NORMALIZATION_RANGES,
        )
        self.assertEqual(
            after.sram_hash_exclude_ranges,
            SRAM_METADATA_NORMALIZATION_RANGES,
        )
        self.assertTrue(
            before.expected_sram_hash.startswith("fnv1a64-sram-normalized:"),
        )


@host_mode.live_artifact_testcase("debugtools-tools runtime coverage")
class ToolsRuntimeTests(unittest.TestCase):
    """Category B (tests/host_mode.py): host-only mode skips this class
    before either ROM is touched; normal mode is unchanged."""

    def _run(self, rom, scenario_path, fingerprint_path, name):
        host_mode.require_built_rom(rom, f"modern ROM for {name}")
        config = "release" if "release" in name else "debug"
        elf = host_mode.modern_elf(config)
        self.assertTrue(elf.is_file(), f"modern ELF for {name} not built: {elf}")
        scenario = gba_playtest.load_scenario(
            scenario_path,
            gba_playtest.ElfSymbolResolver(elf),
        )
        expected = gba_playtest.validate_fingerprint(
            json.loads(fingerprint_path.read_text(encoding="utf-8")),
            str(fingerprint_path),
            policy="behavior",
        )
        actual = host_mode.capture_live_or_skip(  # blank SRAM
            rom, scenario, label=f"debugtools-tools runtime coverage ({name})"
        )
        differences = gba_playtest.compare_fingerprints(expected, actual, policy="behavior")
        self.assertEqual(differences, [], f"{name}: {differences}")

    def test_debug_rom_matches_committed_fingerprint(self):
        self._run(DEBUG_ROM, DEBUG_SCENARIO, DEBUG_FINGERPRINT, DEBUG_NAME)

    def test_release_rom_stays_all_zero(self):
        self._run(RELEASE_ROM, RELEASE_SCENARIO, RELEASE_FINGERPRINT, RELEASE_NAME)


if __name__ == "__main__":
    unittest.main()
