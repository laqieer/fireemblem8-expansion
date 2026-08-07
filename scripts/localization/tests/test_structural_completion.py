import hashlib
import json
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.crosswalk import canonical_json_bytes
from scripts.localization.game_locales.structural_completion import (
    EventMessageNode,
    StructuralCompletionError,
    align_event_subgroups,
    validate_structural_completion_evidence,
)


class StructuralCompletionTests(unittest.TestCase):
    EVIDENCE_PATH = (
        ROOT
        / "texts/locales/mapping/structural_completion_evidence.json"
    )

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(cls.EVIDENCE_PATH.read_text(encoding="utf-8"))
        cls.proposals = {
            int(row["target_id"], 16): row for row in cls.data["proposals"]
        }
        cls.collisions = {
            int(row["target_id"], 16): row for row in cls.data["collisions"]
        }

    def test_opcode_subgroups_align_despite_symbol_count_mismatch(self):
        target_nodes = (
            EventMessageNode(0x100, ("LOAD", "MESSAGE", "END"), "u", "u.c", 10),
            EventMessageNode(0x101, ("MOVE", "MESSAGE", "END"), "u", "u.c", 20),
        )
        source_nodes = (
            EventMessageNode(0x080, ("LOAD", "MESSAGE", "END"), "j", "j.c", 10),
            EventMessageNode(0x090, ("EXTRA", "MESSAGE", "END"), "j", "j.c", 15),
            EventMessageNode(0x081, ("MOVE", "MESSAGE", "END"), "j", "j.c", 20),
        )
        aligned = align_event_subgroups(
            target_nodes,
            source_nodes,
            {(0x100, 0x080), (0x101, 0x081)},
        )
        self.assertEqual(set(aligned), {(0x100, 0x080), (0x101, 0x081)})

    def test_committed_artifact_is_canonical_and_schema_valid(self):
        validate_structural_completion_evidence(
            self.data,
            repo_root=ROOT,
            target_count=3414,
        )
        self.assertEqual(
            self.EVIDENCE_PATH.read_bytes(),
            canonical_json_bytes(self.data),
        )

    def test_artifact_materially_covers_requested_structural_families(self):
        summary = self.data["summary"]
        self.assertGreaterEqual(summary["proposed_target_count"], 1300)
        self.assertEqual(summary["fallback_target_count"], 1797)
        self.assertEqual(
            summary["proposed_target_count"] + summary["residual_target_count"],
            summary["fallback_target_count"],
        )
        self.assertTrue(
            {
                "chapter-event",
                "chapter-title",
                "dungeon-timeline",
                "entity-row",
                "help-tutorial",
                "menu-definition",
                "shop-arena",
                "trainee-prep",
            }
            <= set(summary["family_counts"])
        )

    def test_ch14b_pins_use_event_table_opcode_slots(self):
        expected = {
            **{
                target: 0x0ABB + (target - 0x0AFA)
                for target in range(0x0AFA, 0x0B00)
            },
            0x0B00: 0x0AC1,
            **{
                target: 0x0AC6 + (target - 0x0B05)
                for target in range(0x0B05, 0x0B11)
            },
        }
        for target_id, source_id in expected.items():
            row = self.proposals[target_id]
            self.assertEqual(row["source_id"], f"0x{source_id:04X}")
            self.assertEqual(
                row["evidence"]["basis"],
                "pinned-event-table-opcode-path",
            )
            self.assertIn("chapter=Ch14B", row["semantic_slot"]["key"])

    def test_trainee_corrections_and_prep_collision_are_explicit(self):
        for target_id in range(0x0C44, 0x0C52):
            row = self.proposals[target_id]
            self.assertEqual(
                row["source_id"],
                f"0x{0x0C04 + target_id - 0x0C44:04X}",
            )
            self.assertEqual(
                row["evidence"]["basis"],
                "trainee-function-message-array",
            )
        collision = self.collisions[0x0C52]
        self.assertEqual(
            {
                option["source_id"] for option in collision["source_options"]
            },
            {"0x06F4", "0x0700"},
        )
        self.assertTrue(collision["relation"]["context_required"])

    def test_no_proposal_uses_numeric_interpolation_as_evidence(self):
        forbidden = ("interp", "proximity", "shifted", "extrap")
        for row in self.data["proposals"]:
            basis = row["evidence"]["basis"]
            self.assertFalse(any(word in basis for word in forbidden), row)
            self.assertTrue(row["validation"]["source_in_bounds"])
            self.assertTrue(row["validation"]["source_payload_nonempty"])
            self.assertTrue(row["validation"]["reference_site_evidence"])

    def test_protected_final_map_and_existing_evidence_are_hash_pinned(self):
        for record in self.data["inputs"]["protected_artifacts"]:
            path = ROOT / record["path"]
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                record["sha256"],
            )

    def test_residuals_and_context_collisions_are_not_silently_promoted(self):
        residual_ids = {
            int(row["target_id"], 16)
            for row in self.data["residual_targets"]
        }
        proposal_ids = set(self.proposals)
        collision_ids = set(self.collisions)
        self.assertFalse(proposal_ids & residual_ids)
        self.assertTrue(collision_ids <= residual_ids)
        self.assertTrue(
            all(row["reason"] for row in self.data["residual_targets"])
        )

    def test_validator_rejects_interpolation_basis(self):
        broken = deepcopy(self.data)
        broken["proposals"][0]["evidence"]["basis"] = "numeric-interpolation"
        with self.assertRaisesRegex(
            StructuralCompletionError, "numeric interpolation"
        ):
            validate_structural_completion_evidence(
                broken,
                repo_root=ROOT,
                target_count=3414,
            )

    def test_check_command_passes_without_external_reference_trees(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.localization.game_locales",
                "check-structural-completion",
            ],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("valid structural completion evidence", result.stdout)


if __name__ == "__main__":
    unittest.main()
