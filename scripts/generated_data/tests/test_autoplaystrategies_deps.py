"""Make-dependency discovery coverage for generated strategy data."""

import os
import unittest

from scripts.generated_data.autoplaystrategies import deps


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class AutoplayStrategiesDependencyTests(unittest.TestCase):
    def test_discovery_tracks_strategy_objective_and_bundle_owners(self):
        paths = deps.collect_input_paths(
            os.path.join(ROOT, "src", "data", "autoplay_strategies.json"),
            os.path.join(ROOT, "src", "data", "chapter_objectives.json"),
            os.path.join(ROOT, "src", "data"),
        )
        self.assertIn(
            os.path.join(ROOT, "src", "data", "autoplay_strategies.json"),
            paths,
        )
        self.assertIn(
            os.path.join(ROOT, "src", "data", "chapter_objectives.json"),
            paths,
        )
        self.assertIn(os.path.join(ROOT, "src", "data", "ch2_bundle.json"), paths)


if __name__ == "__main__":
    unittest.main()
