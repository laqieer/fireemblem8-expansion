import hashlib
import json
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.febuilder import (
    FEBUILDER_INDEXED_ROW_COUNT,
    FEBUILDER_NUMERIC_PAIR_COUNT,
    FEBUILDER_POINTER_ROW_COUNT,
    FEBUILDER_SOURCE_SHA256,
    PINNED_STRUCTURAL_CONFLICT_TARGETS,
    PINNED_UNRESOLVED_COLLISION_TARGETS,
    FeBuilderEvidenceError,
    build_febuilder_alignment_evidence,
    canonical_json_bytes,
    parse_febuilder_text_id_map,
    validate_febuilder_evidence_document,
)
from scripts.localization.game_locales.parsers import parse_hash_indexed


class FeBuilderAlignmentEvidenceTests(unittest.TestCase):
    SOURCE_PATH = (
        ROOT / "texts/locales/source/febuilder/translate_textid_FE8.txt"
    )
    EVIDENCE_PATH = (
        ROOT / "texts/locales/mapping/febuilder_alignment_evidence.json"
    )

    @classmethod
    def setUpClass(cls):
        cls.source_bytes = cls.SOURCE_PATH.read_bytes()
        cls.rows = parse_febuilder_text_id_map(cls.source_bytes)
        cls.evidence = json.loads(cls.EVIDENCE_PATH.read_text(encoding="utf-8"))
        cls.rebuilt = build_febuilder_alignment_evidence(
            source_path=cls.SOURCE_PATH,
            ja_indexed_path=ROOT / "texts/locales/ja/indexed.txt",
            zh_indexed_path=ROOT / "texts/locales/zh-Hans/indexed.txt",
            raw_path=ROOT / "texts/locales/zh-Hans/raw.json",
            structural_path=(
                ROOT / "texts/locales/mapping/fe8u_structural_evidence.json"
            ),
            target_header_path=ROOT / "include/constants/msg.h",
            repo_root=ROOT,
        )

    def test_source_hash_counts_and_parser_actions_are_pinned(self):
        self.assertEqual(
            hashlib.sha256(self.source_bytes).hexdigest(),
            FEBUILDER_SOURCE_SHA256,
        )
        self.assertEqual(len(self.rows), 3449)
        indexed_rows = [row for row in self.rows if row.row_type == "indexed"]
        pointer_rows = [row for row in self.rows if row.row_type == "pointer"]
        self.assertEqual(len(indexed_rows), FEBUILDER_INDEXED_ROW_COUNT)
        self.assertEqual(len(pointer_rows), FEBUILDER_POINTER_ROW_COUNT)
        self.assertEqual(
            sum(row.target_key > 0 for row in indexed_rows),
            FEBUILDER_NUMERIC_PAIR_COUNT,
        )
        self.assertEqual(
            Counter(row.parser_action for row in self.rows),
            {
                "decode-target": 3105,
                "literal-substitution": 16,
                "notfound": 326,
                "skip-missing-columns": 1,
                "skip-source-zero": 1,
            },
        )
        with self.assertRaisesRegex(FeBuilderEvidenceError, "expected SHA-256"):
            parse_febuilder_text_id_map(self.source_bytes + b"\n")

    def test_actual_febuilder_edge_rows_are_preserved(self):
        by_line = {row.source_line: row for row in self.rows}
        self.assertEqual(by_line[2].parser_action, "skip-source-zero")
        self.assertEqual(by_line[2481].source_token, "09AF")
        self.assertEqual(by_line[2481].parser_action, "skip-missing-columns")
        self.assertEqual(by_line[1158].target_key, 0x04F5)
        self.assertEqual(by_line[1158].replacement_text, "Avid")
        self.assertEqual(by_line[1158].parser_action, "literal-substitution")
        self.assertEqual(by_line[3341].row_type, "pointer")
        self.assertEqual(by_line[3341].source_key, 0x085C4F14)

        duplicate = [
            row for row in self.rows if row.source_key == 0x080D29BC
        ]
        self.assertEqual(
            [(row.source_line, row.parser_action) for row in duplicate],
            [(3446, "decode-target"), (3450, "notfound")],
        )

    def test_conflicts_and_unresolved_collisions_are_exactly_pinned(self):
        summary = self.evidence["summary"]
        self.assertEqual(summary["candidate_row_count"], 3104)
        self.assertEqual(summary["non_candidate_row_count"], 345)
        self.assertEqual(
            summary["candidate_row_count"] + summary["non_candidate_row_count"],
            self.evidence["source_profile"]["source_row_count"],
        )
        self.assertEqual(
            summary["structural_conflict_targets"],
            [f"0x{value:04X}" for value in PINNED_STRUCTURAL_CONFLICT_TARGETS],
        )
        self.assertEqual(summary["structural_conflict_count"], 12)
        self.assertEqual(
            summary["unresolved_differing_payload_collision_targets"],
            [
                f"0x{value:04X}"
                for value in PINNED_UNRESOLVED_COLLISION_TARGETS
            ],
        )
        self.assertEqual(
            summary["unresolved_differing_payload_collision_count"], 17
        )

        target_0647 = next(
            target
            for target in self.evidence["targets"]
            if target["target_id"] == "0x0647"
        )
        self.assertEqual(
            target_0647["marks"],
            ["conflicts", "collision-needs-context"],
        )
        self.assertFalse(target_0647["promotion_eligible"])

    def test_all_candidate_payload_references_exist_and_targets_are_in_bounds(self):
        ja_ids = {
            message.id
            for message in parse_hash_indexed(
                (ROOT / "texts/locales/ja/indexed.txt").read_text(
                    encoding="utf-8"
                )
            )
        }
        zh_ids = {
            message.id
            for message in parse_hash_indexed(
                (ROOT / "texts/locales/zh-Hans/indexed.txt").read_text(
                    encoding="utf-8"
                )
            )
        }
        raw_records = json.loads(
            (ROOT / "texts/locales/zh-Hans/raw.json").read_text(encoding="utf-8")
        )["records"]
        raw_refs = {
            (
                record["import_id"],
                record["provenance"]["address"],
            )
            for record in raw_records
        }

        for target in self.evidence["targets"]:
            self.assertLess(int(target["target_id"], 16), 3414)
            for candidate in target["candidates"]:
                for locale_id, payload in candidate["payloads"].items():
                    if payload["kind"] == "indexed":
                        source_id = int(payload["id"], 16)
                        self.assertIn(source_id, ja_ids if locale_id == "ja" else zh_ids)
                    else:
                        self.assertIn(
                            (payload["import_id"], payload["address"]),
                            raw_refs,
                        )

        missing_raw = [
            row
            for row in self.evidence["non_candidate_rows"]
            if row["exclusion_reason"] == "missing-normalized-raw-address"
        ]
        self.assertEqual(
            [(row["source_key"], row["target_token"]) for row in missing_raw],
            [("0x080D29BC", "1")],
        )

    def test_conflicts_and_collisions_cannot_self_promote(self):
        self.assertFalse(self.evidence["authoritative"])
        self.assertEqual(
            self.evidence["promotion_policy"],
            {
                "auto_promote": False,
                "conflicts_may_promote": False,
                "unresolved_collisions_may_promote": False,
            },
        )

        broken = dict(self.evidence)
        broken_targets = list(self.evidence["targets"])
        broken_index = next(
            index
            for index, target in enumerate(broken_targets)
            if target["target_id"] == "0x0647"
        )
        broken_target = dict(broken_targets[broken_index])
        broken_target["promotion_eligible"] = True
        broken_targets[broken_index] = broken_target
        broken["targets"] = broken_targets
        with self.assertRaisesRegex(
            FeBuilderEvidenceError, "promotion_eligible must be false"
        ):
            validate_febuilder_evidence_document(broken, target_count=3414)

    def test_committed_ledger_matches_deterministic_rebuild(self):
        self.assertEqual(
            self.EVIDENCE_PATH.read_bytes(),
            canonical_json_bytes(self.rebuilt),
        )

    def test_check_command_passes_without_sibling_checkout(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.localization.game_locales",
                "check-febuilder-evidence",
            ],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("conflicts=12 collisions=17", result.stdout)


if __name__ == "__main__":
    unittest.main()
