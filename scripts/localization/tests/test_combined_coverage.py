import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.combined_coverage import (
    build_combined_coverage_report,
)
from scripts.localization.game_locales.coverage import load_fe8u_target_ids
from scripts.localization.game_locales.crosswalk import canonical_json_bytes


class CombinedCoverageTests(unittest.TestCase):
    REPORT_PATH = (
        ROOT / "texts/locales/mapping/combined_fallback_coverage.json"
    )

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(cls.REPORT_PATH.read_text(encoding="utf-8"))
        cls.rebuilt = build_combined_coverage_report(
            repo_root=ROOT,
            target_count=len(
                load_fe8u_target_ids(ROOT / "include/constants/msg.h")
            ),
            mapping_path=(
                ROOT / "texts/locales/mapping/fe8u_target_map.json"
            ),
            coverage_path=(
                ROOT / "texts/locales/mapping/fe8u_target_map.coverage.json"
            ),
            structural_crosswalk_path=(
                ROOT / "texts/locales/mapping/fe8u_structural_evidence.json"
            ),
            febuilder_path=(
                ROOT
                / "texts/locales/mapping/febuilder_alignment_evidence.json"
            ),
            structural_completion_path=(
                ROOT
                / "texts/locales/mapping/structural_completion_evidence.json"
            ),
        )

    def test_committed_report_matches_deterministic_rebuild(self):
        self.assertEqual(
            self.REPORT_PATH.read_bytes(),
            canonical_json_bytes(self.rebuilt),
        )

    def test_summary_accounts_for_zero_fallback_partition(self):
        summary = self.report["summary"]
        self.assertEqual(summary["target_count"], 3414)
        self.assertEqual(summary["translated_target_count"], 3414)
        self.assertEqual(summary["explicit_fallback_target_count"], 0)
        self.assertEqual(
            summary["actionable_not_yet_verified_target_count"], 0
        )
        self.assertEqual(summary["non_actionable_fallback_target_count"], 0)
        self.assertEqual(
            summary["febuilder_unique_uncontested_candidate_count"], 0
        )
        self.assertEqual(summary["structural_high_candidate_count"], 0)
        self.assertEqual(summary["structural_reference_candidate_count"], 0)
        self.assertEqual(summary["combined_candidate_target_count"], 0)
        self.assertEqual(
            summary["combined_unblocked_candidate_target_count"], 0
        )
        self.assertEqual(summary["combined_blocked_target_count"], 0)
        self.assertEqual(summary["residual_target_count"], 0)
        self.assertEqual(
            summary["combined_unblocked_candidate_target_count"]
            + summary["combined_blocked_target_count"]
            + summary["residual_target_count"],
            summary["actionable_not_yet_verified_target_count"],
        )

    def test_conflicts_and_collisions_remain_explicit(self):
        summary = self.report["summary"]
        self.assertEqual(summary["febuilder_global_conflict_count"], 12)
        self.assertEqual(summary["febuilder_actionable_conflict_count"], 0)
        self.assertEqual(summary["febuilder_global_collision_count"], 17)
        self.assertEqual(summary["febuilder_actionable_collision_count"], 0)
        self.assertEqual(summary["structural_context_collision_count"], 0)
        self.assertEqual(
            summary["structural_global_context_collision_count"], 20
        )
        self.assertEqual(
            self.report["intersections"][
                "febuilder_structural_collision_overlap_count"
            ],
            0,
        )
        self.assertEqual(
            self.report["intersections"]["candidate_and_blocked_count"], 0
        )
        self.assertTrue(
            all(row["blockers"] for row in self.report["blocked_targets"])
        )
        self.assertFalse(self.report["policy"]["promotion_permitted"])
        self.assertFalse(self.report["policy"]["updates_authoritative_map"])

    def test_check_command_passes(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.localization.game_locales",
                "check-combined-coverage",
            ],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
