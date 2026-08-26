"""Parallel profile generation must not mutate committed strategy inventories."""

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TRACKED_INVENTORY = ROOT / "reports" / "generated_data_autoplaystrategies_inventory.md"
FIXTURES = ROOT / "scripts" / "generated_data" / "tests" / "fixtures"


class AutoplayStrategiesProfileIsolationTests(unittest.TestCase):
    def test_parallel_profiles_use_build_local_inventories(self):
        before = TRACKED_INVENTORY.read_bytes()
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            root = Path(temporary)
            enabled_out = root / "enabled" / "generated"
            disabled_out = root / "disabled" / "generated"
            enabled_inventory = root / "enabled" / "inventory.md"
            disabled_inventory = root / "disabled" / "inventory.md"

            def command(out_dir, inventory, enabled):
                return [
                    "make",
                    "--no-print-directory",
                    str(out_dir / "data_autoplay_strategies.c"),
                    "GENERATED_DATA_OUT_DIR={}".format(out_dir),
                    "GENERATED_DATA_AUTOPLAYSTRATEGIES_INVENTORY={}".format(inventory),
                    "GENERATED_DATA_AUTOPLAYSTRATEGIES_SOURCE={}".format(
                        FIXTURES / "autoplaystrategies" / "runtime_valid.json"
                    ),
                    "GENERATED_DATA_AUTOPLAYSTRATEGIES_CHAPTEROBJECTIVES_SOURCE={}".format(
                        FIXTURES / "chapterobjectives" / "strategy_runtime_valid.json"
                    ),
                    "GENERATED_DATA_AUTOPLAYSTRATEGIES_CHAPTERBUNDLE_SOURCE={}".format(
                        FIXTURES / "chapterobjectives" / "strategy_runtime_bundle.json"
                    ),
                    "GENERATED_DATA_AUTOPLAYSTRATEGIES_REFERENCE_PROFILES={}".format(enabled),
                ]

            enabled = subprocess.Popen(command(enabled_out, enabled_inventory, 1), cwd=ROOT)
            disabled = subprocess.Popen(command(disabled_out, disabled_inventory, 0), cwd=ROOT)
            self.assertEqual(enabled.wait(), 0)
            self.assertEqual(disabled.wait(), 0)
            self.assertTrue(enabled_inventory.is_file())
            self.assertTrue(disabled_inventory.is_file())
            enabled_source = (enabled_out / "data_autoplay_strategies.c").read_text(
                encoding="utf-8"
            )
            disabled_source = (disabled_out / "data_autoplay_strategies.c").read_text(
                encoding="utf-8"
            )

        self.assertEqual(TRACKED_INVENTORY.read_bytes(), before)
        self.assertIn("ExpansionAutoplayStrategy_Aggressive", enabled_source)
        self.assertNotIn("ExpansionAutoplayStrategy_Aggressive", disabled_source)


if __name__ == "__main__":
    unittest.main()
