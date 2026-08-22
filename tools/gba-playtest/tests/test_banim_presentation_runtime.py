"""Schema and production-exclusion checks for issue #58 runtime evidence."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class BanimPresentationRuntimeProbeTests(unittest.TestCase):
    def test_probe_is_test_profile_only(self):
        header = (REPO_ROOT / "include/banim_presentation.h").read_text(encoding="utf-8")
        source = (REPO_ROOT / "src/banim_presentation.c").read_text(encoding="utf-8")
        event_battle = (REPO_ROOT / "src/eventscr3.c").read_text(encoding="utf-8")
        gamecontrol = (REPO_ROOT / "src/gamecontrol.c").read_text(encoding="utf-8")
        makefile = (REPO_ROOT / "modern.mk").read_text(encoding="utf-8")

        self.assertIn("#ifdef BANIM_PRESENTATION_RUNTIME_PROBE_POLICY", header)
        self.assertIn("#ifdef BANIM_PRESENTATION_RUNTIME_PROBE_POLICY", source)
        self.assertIn(
            "EWRAM_DATA static u8 sSelectedPolicy",
            source,
        )
        self.assertIn(
            "MODERN_BANIM_PRESENTATION_RUNTIME_PROBE_POLICY is debug-test-only",
            makefile,
        )
        self.assertIn("MODERN_BANIM_PRESENTATION_RUNTIME_STANDARD_ROOT", makefile)
        self.assertIn("MODERN_BANIM_PRESENTATION_RUNTIME_OFF_ROOT", makefile)
        self.assertIn("#ifdef BANIM_PRESENTATION_RUNTIME_PROBE_POLICY", gamecontrol)
        self.assertIn("DebugTools_RequestChapter4PrepLaunch();", gamecontrol)
        self.assertIn("autoLaunchArmed = TRUE", gamecontrol)
        self.assertIn("#ifdef BANIM_PRESENTATION_RUNTIME_PROBE_POLICY", event_battle)
        self.assertIn("BanimPresentationPolicy_RuntimeProbeRecordHit(policy);", event_battle)

    def test_runtime_runner_reuses_the_real_combat_route(self):
        runner = (
            REPO_ROOT / "tools/gba-playtest/run_banim_presentation_checks.py"
        ).read_text(encoding="utf-8")

        self.assertIn('COMBAT_SCENARIO = REPO_ROOT / "tools" / "gba-playtest" / "scenarios" / "combat.json"', runner)
        self.assertIn("FIGHT_OBSERVATION_START = 3000", runner)
        self.assertIn("FIGHT_OBSERVATION_END = 8000", runner)
        self.assertIn('"realHitPathObserved": 1', runner)
        self.assertIn('"autoLaunchArmed": 1', runner)
        self.assertIn('"paletteFlashEnabled": 1', runner)
        self.assertEqual(runner.count('"paletteFlashStarted": 0'), 2)
        self.assertIn('"hitNumbersVisible": 0', runner)


if __name__ == "__main__":
    unittest.main()
