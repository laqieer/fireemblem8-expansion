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
from scripts.localization.game_locales.parsers import parse_hash_indexed


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
        cls.indexed = {
            locale: {
                message.id: message.text
                for message in parse_hash_indexed(
                    (
                        ROOT / f"texts/locales/{locale}/indexed.txt"
                    ).read_text(encoding="utf-8"),
                    source_name=locale,
                )
            }
            for locale in ("ja", "zh-Hans")
        }
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
            runtime_authored_catalogs={
                locale: json.loads(
                    (
                        ROOT
                        / f"texts/locales/authored/catalog.{locale}.json"
                    ).read_text(encoding="utf-8")
                )
                for locale in ("ja", "zh-Hans")
            },
            authored_queue_data=cls.queue,
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
                "c-febuilder": 1329,
                "c-febuilder-raw": 3,
                "d-contextual-resolution": 21,
                "d-existing-authored": 3,
                "d-semantic-correction": 18,
                "d-structural-reference-second-check": 6,
                "e-exact-english": 139,
                "e-exact-english-context": 1,
                "f-authored-queue": 259,
            },
        )
        samples = {
            "0x0C46": "b-structural-high",
            "0x0004": "c-febuilder",
            "0x0032": "c-febuilder-raw",
            "0x0C52": "d-contextual-resolution",
            "0x002A": "d-semantic-correction",
            "0x0505": "d-structural-reference-second-check",
            "0x0693": "d-existing-authored",
            "0x000E": "e-exact-english",
            "0x0579": "e-exact-english-context",
            "0x0011": "f-authored-queue",
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

    def test_semantic_corrections_use_target_correct_official_payloads(self):
        expected_sources = {
            "0x002A": ("0x0005", "0x0564"),
            "0x093F": ("0x0907", "0x08E4"),
            "0x0946": ("0x090B", "0x0906"),
            "0x0947": ("0x090E", "0x0907"),
            "0x0948": ("0x090F", "0x0908"),
            "0x0949": ("0x0931", "0x0909"),
            "0x094B": ("0x0932", "0x090B"),
            "0x094C": ("0x0934", "0x090C"),
            "0x094F": ("0x0939", "0x090F"),
            "0x0952": ("0x090C", "0x0912"),
            "0x0971": ("0x093A", "0x0931"),
            "0x0972": ("0x093C", "0x0932"),
            "0x0975": ("0x090A", "0x0935"),
            "0x0976": ("0x0941", "0x0936"),
            "0x097A": ("0x0942", "0x093A"),
            "0x097B": ("0x0912", "0x093B"),
            "0x097C": ("0x0943", "0x093C"),
            "0x0982": ("0x0947", "0x0942"),
        }
        english_terms = {
            "0x093F": ("Eirika", "A Button"),
            "0x0946": ("trade", "Gilliam", "A Button"),
            "0x0947": ("Gilliam", "A Button"),
            "0x0948": ("Trade",),
            "0x0949": ("Gilliam", "Franz", "right"),
            "0x094B": ("vulnerary", "Franz", "A Button"),
            "0x094C": ("traded", "B Button", "finish"),
            "0x094F": ("Seth", "A Button"),
            "0x0952": ("axe", "sword", "Attack", "A Button"),
            "0x0971": ("Ross", "A Button"),
            "0x0972": ("Rescue", "A Button"),
            "0x0975": ("Vanessa", "Ross", "Moulder", "A Button"),
            "0x0976": ("drop", "A Button"),
            "0x097A": ("Staff", "A Button"),
            "0x097B": ("Ross", "Moulder", "Vanessa", "A Button"),
            "0x097C": ("Vanessa", "flashing", "A Button"),
            "0x0982": ("Eirika", "village", "flashing", "A Button"),
        }
        localized_terms = {
            "0x093F": {
                "ja": ("エイリーク", "Ａボタン"),
                "zh-Hans": ("艾瑞珂", "A键"),
            },
            "0x0946": {
                "ja": ("アイテム交換", "ギリアム", "Ａボタン"),
                "zh-Hans": ("物品交换", "吉利安姆", "A键"),
            },
            "0x0947": {
                "ja": ("ギリアム", "Ａボタン"),
                "zh-Hans": ("吉利安姆", "A键"),
            },
            "0x0948": {
                "ja": ("交換",),
                "zh-Hans": ("交换",),
            },
            "0x0949": {
                "ja": ("ギリアム", "フランツ", "右ボタン"),
                "zh-Hans": ("吉利安姆", "弗朗茨", "右键"),
            },
            "0x094B": {
                "ja": ("フランツ", "きずぐすり", "Ａ"),
                "zh-Hans": ("弗朗茨", "伤药", "A键"),
            },
            "0x094C": {
                "ja": ("交換", "Ｂボタン", "終了"),
                "zh-Hans": ("交换", "B键", "结束"),
            },
            "0x094F": {
                "ja": ("ゼト", "Ａボタン"),
                "zh-Hans": ("塞思", "A键"),
            },
            "0x0952": {
                "ja": ("斧", "剣", "攻撃", "Ａボタン"),
                "zh-Hans": ("斧", "剑", "攻击", "A键"),
            },
            "0x0971": {
                "ja": ("ロス", "隣", "Ａ"),
                "zh-Hans": ("罗斯", "旁边", "A键"),
            },
            "0x0972": {
                "ja": ("救出", "Ａボタン"),
                "zh-Hans": ("救出", "A键"),
            },
            "0x0975": {
                "ja": ("ヴァネッサ", "ロス", "降ろ", "Ａボタン"),
                "zh-Hans": ("瓦内萨", "罗斯", "放下来", "A键"),
            },
            "0x0976": {
                "ja": ("降ろす", "Ａボタン"),
                "zh-Hans": ("放下", "A键"),
            },
            "0x097A": {
                "ja": ("杖", "Ａボタン"),
                "zh-Hans": ("杖", "A键"),
            },
            "0x097B": {
                "ja": ("ロス", "ヴァネッサ", "モルダ", "Ａボタン"),
                "zh-Hans": ("罗斯", "瓦内萨", "摩达", "A键"),
            },
            "0x097C": {
                "ja": ("ヴァネッサ", "点滅", "Ａボタン"),
                "zh-Hans": ("瓦内萨", "闪烁", "A键"),
            },
            "0x0982": {
                "ja": ("エイリーク", "村", "点滅", "Ａボタン"),
                "zh-Hans": ("艾瑞珂", "村庄", "闪烁", "A键"),
            },
        }

        for target_id, (incorrect_source_id, source_id) in expected_sources.items():
            row = self.rows[target_id]
            self.assertEqual(row["source"], {
                "id": source_id,
                "kind": "indexed",
                "layout": "FE8J",
            })
            promotion = row["verification"]["promotion"]
            self.assertEqual(promotion["precedence"], "d-semantic-correction")
            self.assertEqual(
                promotion["details"]["incorrect_source"]["id"],
                incorrect_source_id,
            )

        self.assertEqual(self.indexed["ja"][0x0564], "ワールドマップ")
        self.assertEqual(self.indexed["zh-Hans"][0x0564], "世界地图")

        for target_id, terms in english_terms.items():
            source = self.english[int(target_id, 16)].source_text.casefold()
            for term in terms:
                self.assertIn(term.casefold(), source, (target_id, term))
            source_id = int(expected_sources[target_id][1], 16)
            for locale in ("ja", "zh-Hans"):
                payload = self.indexed[locale][source_id]
                for term in localized_terms[target_id][locale]:
                    self.assertIn(term, payload, (target_id, locale, term))

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

    def test_historical_queue_is_complete_fulfilled_and_canonical(self):
        fallback_ids = [
            row["target_id"]
            for row in self.mapping["rows"]
            if row["source"]["kind"] == "english_fallback"
        ]
        queue_ids = [row["target_id"] for row in self.queue["targets"]]
        self.assertEqual(fallback_ids, [])
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
            mapped = self.rows[row["target_id"]]["source"]
            self.assertEqual(mapped["kind"], "authored")
            self.assertEqual(
                mapped["translation_key"], row["suggested_key"]
            )
        self.assertEqual(
            (self.MAPPING_DIR / "authored_translation_queue.json").read_bytes(),
            canonical_json_bytes(self.queue),
        )
        report = self.rebuilt["report"]
        self.assertTrue(
            report["policy"]["historical_queue_map_hash_is_immutable_provenance"]
        )
        self.assertFalse(
            report["policy"]["historical_queue_map_hash_matches_current"]
        )
        self.assertEqual(
            self.queue["authoritative_target_map_sha256"],
            report["inputs"]["historical_pre_authored_target_map_sha256"],
        )
        self.assertNotEqual(
            report["inputs"]["historical_pre_authored_target_map_sha256"],
            report["inputs"]["current_pre_authored_target_map_sha256"],
        )

    def test_final_delivery_gate_accepts_fulfilled_historical_queue(self):
        require_no_fallback(self.queue, mapping=self.mapping)
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
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(
            "translated=3414 fallback=0 queue=259",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
