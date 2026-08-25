"""Shape checks for the bounded real-CpDecide strategy runner."""

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "gba-playtest"))

import gba_playtest  # noqa: E402
import run_autoplay_strategy_checks as strategy_checks  # noqa: E402


class AutoplayStrategyRuntimeScenarioTests(unittest.TestCase):
    def test_scenario_is_bounded_semantic(self):
        scenario = gba_playtest.parse_scenario_data(
            strategy_checks._scenario("strategy-shape")
        )
        self.assertEqual(scenario.schema_version, 2)
        self.assertEqual(scenario.run_until.max_frames, 18001)
        self.assertEqual(scenario.run_until.terminal_conditions[0].reason, "success")
        self.assertEqual(
            [
                probe.binding
                for probe in scenario.checkpoints[-1].probes
                if probe.binding.startswith(strategy_checks.STRATEGY_PROBE_SYMBOL)
            ],
            list(strategy_checks.STRATEGY_PROBE_BINDINGS),
        )


if __name__ == "__main__":
    unittest.main()
