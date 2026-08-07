import json
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.english_source import (
    load_english_source_entries,
)
from scripts.localization.game_locales.crosswalk import canonical_json_bytes
from scripts.localization.game_locales.final_mapping import (
    FinalMappingError,
    build_final_mapping_artifacts,
    canonical_artifacts,
    recover_original_rows,
    require_no_fallback,
)


class FinalMappingTests(unittest.TestCase):
    MAPPING_DIR = ROOT / "texts/locales/mapping"

    @classmethod
    def setUpClass(cls):
        cls.mapping = json.loads(
            (cls.MAPPING_DIR / "fe8u_target_map.json").read_text(encoding="utf-8")
        )
        cls.febuilder = json.loads(
            (cls.MAPPING_DIR / "febuilder_alignment_evidence.json").read_text(
                encoding="utf-8"
            )
        )
        cls.structural = json.loads(
            (cls.MAPPING_DIR / "structural_completion_evidence.json").read_text(
                encoding="utf-8"
            )
        )
        cls.queue = json.loads(
            (cls.MAPPING_DIR / "authored_translation_queue.json").read_text(
                encoding="utf-8"
            )
        )
        cls.report = json.loads(
            (cls.MAPPING_DIR / "final_mapping_report.json").read_text(
                encoding="utf-8"
            )
        )
        cls.rows = {row["target_id"]: row for row in cls.mapping["rows"]}
        cls.original_rows = {
            row["target_id"]: row for row in recover_original_rows(cls.mapping)
        }
        cls.english = load_english_source_entries(
            ROOT / "texts/texts.txt",
            ROOT / "texts/textdefs.txt",
            target_count=3414,
        )
        cls.rebuilt = build_final_mapping_artifacts(
            repo_root=ROOT,
            target_count=3414,
            mapping_data=cls.mapping,
            febuilder_data=cls.febuilder,
            structural_data=cls.structural,
            english_texts_path=ROOT / "texts/texts.txt",
            english_definitions_path=ROOT / "texts/textdefs.txt",
            ja_indexed_text=(ROOT / "texts/locales/ja/indexed.txt").read_text(
                encoding="utf-8"
            ),
            zh_indexed_text=(
                ROOT / "texts/locales/zh-Hans/indexed.txt"
            ).read_text(encoding="utf-8"),
            zh_raw_data=json.loads(
                (ROOT / "texts/locales/zh-Hans/raw.json").read_text(
                    encoding="utf-8"
                )
            ),
            ja_raw_data=json.loads(
                (ROOT / "texts/locales/ja/raw.json").read_text(encoding="utf-8")
            ),
            authored_catalogs={
                locale: json.loads(
                    (ROOT / f"texts/expansion/catalog.{locale}.json").read_text(
                        encoding="utf-8"
                    )
                )
                for locale in ("en", "ja", "zh-Hans")
            },
        )

    def test_committed_artifacts_match_deterministic_rebuild(self):
        encoded = canonical_artifacts(self.rebuilt)
        paths = {
            "coverage": self.MAPPING_DIR / "fe8u_target_map.coverage.json",
            "mapping": self.MAPPING_DIR / "fe8u_target_map.json",
            "queue": self.MAPPING_DIR / "authored_translation_queue.json",
            "report": self.MAPPING_DIR / "final_mapping_report.json",
        }
        for name, path in paths.items():
            self.assertEqual(path.read_bytes(), encoded[name], name)

    def test_precedence_counts_and_representative_rows_are_pinned(self):
        self.assertEqual(
            self.report["promotion_counts"],
            {
                "b-structural-high": 27,
                "c-febuilder": 1347,
                "c-febuilder-raw": 3,
                "d-contextual-resolution": 21,
                "d-existing-authored": 3,
                "d-structural-reference-second-check": 6,
                "e-exact-english": 139,
                "e-exact-english-context": 1,
            },
        )
        samples = {
            "0x0C46": "b-structural-high",
            "0x0004": "c-febuilder",
            "0x0032": "c-febuilder-raw",
            "0x0C52": "d-contextual-resolution",
            "0x0505": "d-structural-reference-second-check",
            "0x0693": "d-existing-authored",
            "0x000E": "e-exact-english",
            "0x0579": "e-exact-english-context",
        }
        for target_id, precedence in samples.items():
            self.assertEqual(
                self.rows[target_id]["verification"]["promotion"]["precedence"],
                precedence,
            )

    def test_conflicts_are_preserved_and_collisions_are_context_resolved(self):
        conflicts = self.febuilder["summary"]["structural_conflict_targets"]
        self.assertEqual(len(conflicts), 12)
        for target_id in conflicts:
            self.assertEqual(
                self.rows[target_id]["source"],
                self.original_rows[target_id]["source"],
                target_id,
            )

        resolved = [
            row
            for row in self.mapping["rows"]
            if row["verification"]
            .get("promotion", {})
            .get("precedence")
            == "d-contextual-resolution"
        ]
        self.assertEqual(len(resolved), 21)
        for row in resolved:
            details = row["verification"]["promotion"]["details"]
            self.assertEqual(row["source"]["id"], details["accepted_source_id"])
            self.assertIn(
                row["source"]["id"], details["reviewed_source_options"]
            )

    def test_exact_english_dedup_includes_control_bytes(self):
        dedup_rows = [
            row
            for row in self.mapping["rows"]
            if row["verification"]
            .get("promotion", {})
            .get("precedence")
            in ("e-exact-english", "e-exact-english-context")
        ]
        self.assertEqual(len(dedup_rows), 140)
        for row in dedup_rows:
            target = int(row["target_id"], 16)
            donor = int(
                row["verification"]["promotion"]["details"]["donor_target_id"],
                16,
            )
            self.assertEqual(
                self.english[target].encoded_bytes,
                self.english[donor].encoded_bytes,
                row["target_id"],
            )
        self.assertEqual(
            self.rows["0x0693"]["source"]["control_suffix"], "[CTRL:001F]"
        )

    def test_one_provider_can_safely_serve_many_targets(self):
        source_counts = Counter(
            json.dumps(row["source"], sort_keys=True)
            for row in self.mapping["rows"]
            if row["source"]["kind"] != "english_fallback"
        )
        provider = json.dumps(
            {"id": "0x04C5", "kind": "indexed", "layout": "FE8J"},
            sort_keys=True,
        )
        self.assertGreaterEqual(source_counts[provider], 100)
        self.assertEqual(self.rows["0x00CD"]["source"], json.loads(provider))
        self.assertEqual(
            self.rows["0x00CD"]["verification"]["promotion"]["details"][
                "donor_target_id"
            ],
            "0x0535",
        )

    def test_raw_pointer_promotions_have_both_locale_providers(self):
        for target_id, import_id, symbol in (
            ("0x0032", "fe8cn.raw.import-0062", "PROMO_OPTION_1_NAME"),
            ("0x0033", "fe8cn.raw.import-0063", "PROMO_OPTION_2_NAME"),
            ("0x0034", "fe8cn.raw.import-0064", "PROMO_OPTION_3_NAME"),
        ):
            source = self.rows[target_id]["source"]
            self.assertEqual(source["kind"], "raw")
            self.assertEqual(source["import_id"], import_id)
            self.assertEqual(
                source["regional_sources"]["ja"]["symbol"], symbol
            )
            self.assertEqual(
                source["regional_sources"]["zh-Hans"]["import_id"], import_id
            )

    def test_queue_is_complete_precise_and_canonical(self):
        fallback_ids = [
            row["target_id"]
            for row in self.mapping["rows"]
            if row["source"]["kind"] == "english_fallback"
        ]
        queue_ids = [row["target_id"] for row in self.queue["targets"]]
        self.assertEqual(queue_ids, fallback_ids)
        self.assertEqual(len(queue_ids), 259)
        for row in self.queue["targets"]:
            self.assertTrue(row["english_canonical_text"] is not None)
            self.assertIsInstance(row["controls"], list)
            self.assertIsInstance(row["placeholders"], list)
            self.assertTrue(row["subsystem"])
            self.assertIsInstance(row["reference_sites"], list)
            self.assertTrue(row["reason_no_source_mapping"])
            self.assertTrue(row["suggested_key"])
            self.assertTrue(row["grouping"]["english_payload_group"])
        self.assertEqual(
            (self.MAPPING_DIR / "authored_translation_queue.json").read_bytes(),
            canonical_json_bytes(self.queue),
        )

    def test_final_delivery_gate_rejects_intermediate_queue(self):
        with self.assertRaisesRegex(
            FinalMappingError, "259 fallback targets remain"
        ):
            require_no_fallback(self.queue)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.localization.game_locales",
                "check-final-mapping",
                "--require-no-fallback",
            ],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("259 fallback targets remain", result.stdout)


if __name__ == "__main__":
    unittest.main()
