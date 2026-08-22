"""Schema and production-exclusion checks for issue #58 runtime evidence."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "tools" / "gba-playtest" / "run_banim_presentation_checks.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("banim_presentation_runtime", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load runtime runner: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
            "IWRAM_DATA static u8 sSelectedPolicyState",
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

    def test_hit_transition_uses_captured_checkpoints_not_constants(self):
        runner = load_runner()

        def checkpoint(name, values):
            return {
                "name": name,
                "probes": [{"value": f"0x{value:08x}"} for value in values],
            }

        before = (21, 21, 0, 0, 1, 1, 0, 1, 1, 1, 1)
        hit = (21, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1)
        values = runner.extract_hit_transition(
            [checkpoint("before-hit", before), checkpoint("lethal-hit", hit)],
            "synthetic",
        )

        self.assertEqual(values["enemyMaxHpBefore"], 21)
        self.assertEqual(values["enemyCurHpBefore"], 21)
        self.assertEqual(values["enemyMaxHpAtHit"], 21)
        self.assertEqual(values["enemyCurHpAtHit"], 0)
        self.assertEqual(values["enemyCurHpAfter"], 0)

        fabricated = dict(values)
        fabricated.update(
            enemyMaxHpBefore=15,
            enemyCurHpBefore=15,
            enemyCurHpAfter=0,
        )
        failures = runner.check(fabricated, "synthetic", {})
        self.assertTrue(
            any("actual hit checkpoint HP was 21/0" in failure for failure in failures),
            failures,
        )

    def test_runner_captures_the_combat_scenarios_exact_hit_checkpoint(self):
        runner = load_runner()
        combat_path = REPO_ROOT / "tools" / "gba-playtest" / "scenarios" / "combat.json"
        combat = json.loads(combat_path.read_text(encoding="utf-8"))
        actual_hit = next(
            checkpoint["frame"]
            for checkpoint in combat["checkpoints"]
            if checkpoint["name"] == "artur-scripted-fight-lethal-hit"
        )

        self.assertIn(actual_hit, runner.observation_frames(combat))


if __name__ == "__main__":
    unittest.main()
