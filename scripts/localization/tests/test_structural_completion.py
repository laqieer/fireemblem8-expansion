import hashlib
import json
import os
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.crosswalk import canonical_json_bytes
from scripts.localization.game_locales.structural_completion import (
    StructuralCompletionError,
    _ch14b_alignment,
    _site_index,
    _trainee_alignment,
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

    @classmethod
    def _fe8j_root(cls):
        configured = os.environ.get("FE8J_REFERENCE_ROOT")
        candidates = [
            Path(configured) if configured else None,
            Path.home() / "fireemblem8j",
        ]
        for candidate in candidates:
            if candidate is not None and (candidate / ".git").exists():
                return candidate
        return None

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

    def test_ch14b_pins_parse_real_source_and_target_scripts(self):
        fe8j_root = self._fe8j_root()
        if fe8j_root is None:
            self.skipTest("FE8J reference tree is unavailable")
        event_rows = {
            (target_id, int(row["source_id"], 16))
            for target_id, row in self.proposals.items()
            if row["evidence"]["basis"] == "parsed-event-structure"
        }
        parsed = _ch14b_alignment(
            ROOT,
            fe8j_root,
            accepted_pairs=event_rows,
        )
        self.assertEqual(set(parsed), event_rows)
        for pair, proof in parsed.items():
            self.assertEqual(proof["basis"], "parsed-event-structure")
            source = proof["source_structure"]
            target = proof["target_structure"]
            self.assertEqual(source["chapter"], target["chapter"])
            self.assertEqual(
                source["control_flow_path"],
                target["control_flow_path"],
            )
            self.assertEqual(source["message_ordinal"], target["message_ordinal"])
            self.assertEqual(source["script_key"], target["script_key"])
            self.assertEqual(
                int(target["message_id"], 16),
                pair[0],
            )
            self.assertEqual(
                int(source["message_id"], 16),
                pair[1],
            )

    def test_trainee_pins_parse_real_message_tables(self):
        fe8j_root = self._fe8j_root()
        if fe8j_root is None:
            self.skipTest("FE8J reference tree is unavailable")
        accepted = {
            (target_id, int(row["source_id"], 16))
            for target_id, row in self.proposals.items()
            if 0x0C44 <= target_id <= 0x0C51
        }
        parsed = _trainee_alignment(
            ROOT,
            fe8j_root,
            accepted_pairs=accepted,
        )
        self.assertEqual(
            set(parsed),
            {
                (0x0C46, 0x0C06),
                (0x0C47, 0x0C07),
                (0x0C4A, 0x0C0A),
                (0x0C4B, 0x0C0B),
                (0x0C4F, 0x0C0F),
                (0x0C50, 0x0C10),
            },
        )
        for target_id in range(0x0C44, 0x0C52):
            row = self.proposals[target_id]
            expected_high = any(pair[0] == target_id for pair in parsed)
            self.assertEqual(row["confidence"] == "high", expected_high)
            if expected_high:
                self.assertEqual(
                    row["evidence"]["basis"],
                    "parsed-trainee-message-table",
                )
        collision = self.collisions[0x0C52]
        self.assertEqual(
            {
                option["source_id"] for option in collision["source_options"]
            },
            {"0x06F4", "0x0700"},
        )
        self.assertTrue(collision["relation"]["context_required"])

    def test_no_arbitrary_numeric_constant_is_a_semantic_site(self):
        sites = _site_index(ROOT, {0x008D})
        self.assertEqual(
            [
                (site["kind"], site["path"], site["symbol"])
                for site in sites[0x008D]
            ],
            [
                (
                    "msg-symbol-definition",
                    "include/constants/msg.h",
                    "MSG_08D",
                )
            ],
        )
        serialized = json.dumps(sites[0x008D], sort_keys=True)
        self.assertNotIn("Pal_PrepItemListSpinningArrowCycle", serialized)
        self.assertNotIn("data_banim", serialized)

    def test_every_high_confidence_row_cites_hashed_parsed_structures(self):
        high_rows = [
            row for row in self.data["proposals"] if row["confidence"] == "high"
        ]
        self.assertGreater(len(high_rows), 0)
        for row in high_rows:
            evidence = row["evidence"]
            for side, expected_id in (
                ("source_structure", row["source_id"]),
                ("target_structure", row["target_id"]),
            ):
                structure = evidence[side]
                self.assertTrue(structure["parsed"])
                self.assertEqual(structure["message_id"], expected_id)
                self.assertEqual(
                    hashlib.sha256(
                        structure["context"].encode("utf-8")
                    ).hexdigest(),
                    structure["context_sha256"],
                )
                self.assertEqual(
                    structure["slot_key"],
                    row["semantic_slot"]["key"],
                )
                self.assertTrue(structure["table_symbol"])
                self.assertTrue(structure["path"])

    def test_no_proposal_uses_numeric_interpolation_as_evidence(self):
        forbidden = ("interp", "proximity", "shifted", "extrap")
        for row in self.data["proposals"]:
            basis = row["evidence"]["basis"]
            self.assertFalse(any(word in basis for word in forbidden), row)
            self.assertTrue(row["validation"]["source_in_bounds"])
            self.assertTrue(row["validation"]["source_payload_nonempty"])
            self.assertEqual(
                row["validation"]["parsed_structural_pair"],
                row["confidence"] == "high",
            )

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

    def test_validator_rejects_untyped_numeric_site(self):
        broken = deepcopy(self.data)
        row = next(
            row
            for row in broken["proposals"]
            if row["evidence"]["target_sites"]
        )
        row["evidence"]["target_sites"][0]["kind"] = "generic-hex-literal"
        with self.assertRaisesRegex(
            StructuralCompletionError, "typed message-site extractor"
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
