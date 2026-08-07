import json
import re
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
    ROOT = ROOT
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

    @classmethod
    def localized_text(cls, target_id, locale):
        source = cls.rows[target_id]["source"]
        if source["kind"] == "indexed":
            return cls.indexed[locale][int(source["id"], 16)]
        if source["kind"] == "authored":
            catalog = json.loads(
                (
                    cls.ROOT
                    / f"texts/locales/authored/catalog.{locale}.json"
                ).read_text(encoding="utf-8")
            )
            return catalog["strings"][source["translation_key"]]
        raise AssertionError((target_id, source))

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
                "c-febuilder": 1318,
                "c-febuilder-raw": 3,
                "d-contextual-resolution": 21,
                "d-existing-authored": 3,
                "d-semantic-correction": 35,
                "d-structural-reference-second-check": 6,
                "e-exact-english": 135,
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
            "0x000E": "d-semantic-correction",
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
        self.assertEqual(len(dedup_rows), 136)
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

    def test_remaining_semantic_targets_use_exact_meanings(self):
        expected_indexed_sources = {
            "0x000B": "0x0809",
            "0x000C": "0x080A",
            "0x01B8": "0x013E",
            "0x05B4": "0x0545",
            "0x058B": "0x0049",
            "0x0771": "0x0048",
            "0x0773": "0x0048",
            "0x0775": "0x005F",
            "0x0778": "0x004A",
            "0x0779": "0x0048",
            "0x077A": "0x0049",
        }
        expected_authored_keys = {
            "0x0005": "game.semantic_correction.msg_005",
            "0x0006": "game.semantic_correction.msg_006",
            "0x000D": "game.semantic_correction.msg_00d",
            "0x000E": "game.semantic_correction.msg_00e",
            "0x0679": "game.semantic_correction.msg_679",
            "0x06A2": "game.semantic_correction.msg_6a2",
        }
        for target_id, source_id in expected_indexed_sources.items():
            self.assertEqual(
                self.rows[target_id]["source"],
                {"id": source_id, "kind": "indexed", "layout": "FE8J"},
            )
            self.assertEqual(
                self.rows[target_id]["verification"]["promotion"]["precedence"],
                "d-semantic-correction",
            )
        for target_id, translation_key in expected_authored_keys.items():
            self.assertEqual(
                self.rows[target_id]["source"],
                {"kind": "authored", "translation_key": translation_key},
            )
            self.assertEqual(
                self.rows[target_id]["verification"]["promotion"]["precedence"],
                "d-semantic-correction",
            )

        exact_terms = {
            "0x01B8": {
                "ja": ("リオン", "撃破"),
                "zh-Hans": ("利昂", "击破"),
            },
            "0x05B4": {
                "ja": ("売り", "大事なアイテム", "売れません"),
                "zh-Hans": ("卖掉", "重要的物品", "不能卖"),
            },
            "0x058B": {"ja": ("サバイバル",), "zh-Hans": ("生存",)},
            "0x0679": {
                "ja": ("記録中", "再開", "記録から始める"),
                "zh-Hans": ("正在保存", "继续游戏", "从记录开始"),
            },
            "0x06A2": {"ja": ("デバッグ",), "zh-Hans": ("调试",)},
        }
        for target_id, terms_by_locale in exact_terms.items():
            for locale, terms in terms_by_locale.items():
                payload = self.localized_text(target_id, locale)
                for term in terms:
                    self.assertIn(term, payload, (target_id, locale, term))

        prep_items = (self.ROOT / "src/prep_itemscreen.c").read_text(
            encoding="utf-8"
        )
        world_map = (self.ROOT / "src/worldmap_path.c").read_text(
            encoding="utf-8"
        )
        fortune = (self.ROOT / "src/prep_80A0760.c").read_text(
            encoding="utf-8"
        )
        self.assertIn('MSG_5B4, // "Sell your unneeded items.', prep_items)
        self.assertIn(".helpMsgId = 0x0679", world_map)
        self.assertIn('0x58B, // TODO: msgid "Survival"', fortune)

    def test_dummy_labels_are_placeholders_not_rice(self):
        expected_sources = {
            "0x0025": "0x038A",
            "0x0403": "0x038A",
            "0x0449": "0x03D1",
            "0x04AB": "0x0433",
            "0x04E4": "0x046C",
        }
        for target_id, source_id in expected_sources.items():
            self.assertEqual(self.rows[target_id]["source"]["id"], source_id)
            self.assertIn("ダミー", self.localized_text(target_id, "ja"))
            zh = self.localized_text(target_id, "zh-Hans")
            self.assertIn("占位", zh)
            self.assertNotIn("大米", zh)

    def test_popup_fragments_concatenate_in_runtime_order(self):
        popup = (self.ROOT / "src/popup.c").read_text(encoding="utf-8")
        battle_popup = (self.ROOT / "src/banim-ekrpopup.c").read_text(
            encoding="utf-8"
        )
        self.assertIsNotNone(
            re.search(
                r"PopupScr_GotGold.*?POPUP_MSG\(0x005\).*?"
                r"POPUP_NUM.*?POPUP_MSG\(0x006\)",
                popup,
                re.DOTALL,
            )
        )
        self.assertIsNotNone(
            re.search(
                r"PopupScr_ItemStolen.*?POPUP_ITEM_STR_CAP.*?"
                r"POPUP_MSG\(0x00B\)",
                popup,
                re.DOTALL,
            )
        )
        self.assertIn("GetStringFromIndex(0x0D)", battle_popup)
        self.assertIsNotNone(
            re.search(
                r"PopupScr_NewAlly.*?POPUP_MSG\(0x00E\).*?"
                r"POPUP_UNIT_NAME",
                popup,
                re.DOTALL,
            )
        )

        def visible(text):
            return re.sub(r"\[[^\]]+\]", "", text).strip()

        def compact(text):
            return re.sub(r"\s+", "", text)

        expected = {
            "ja": {
                "gold": "入手：1000ゴールド。",
                "stolen": "鉄の剣をぬすまれた",
                "support": "支援レベルがアップした",
                "weapon": "使用可能：剣",
                "unit": "使用可能ユニット：エイリーク",
            },
            "zh-Hans": {
                "gold": "获得：1000金币。",
                "stolen": "铁剑被盗走了",
                "support": "支援等级上升",
                "weapon": "可使用：剑",
                "unit": "可使用单位：艾瑞珂",
            },
        }
        samples = {
            "ja": ("鉄の剣", "剣", "エイリーク"),
            "zh-Hans": ("铁剑", "剑", "艾瑞珂"),
        }
        for locale, (item, weapon, unit) in samples.items():
            composed = {
                "gold": (
                    visible(self.localized_text("0x0005", locale))
                    + "1000"
                    + visible(self.localized_text("0x0006", locale))
                ),
                "stolen": item
                + visible(self.localized_text("0x000B", locale)),
                "support": visible(self.localized_text("0x000C", locale)),
                "weapon": visible(self.localized_text("0x000D", locale))
                + weapon,
                "unit": visible(self.localized_text("0x000E", locale)) + unit,
            }
            self.assertEqual(
                {key: compact(value) for key, value in composed.items()},
                expected[locale],
            )

    def test_link_arena_table_order_and_labels_are_not_shifted(self):
        postbattle = (self.ROOT / "src/sio_postbattle.c").read_text(
            encoding="utf-8"
        )
        points = (self.ROOT / "src/sio_points.c").read_text(encoding="utf-8")
        result = (self.ROOT / "src/sio_result.c").read_text(encoding="utf-8")
        self.assertIsNotNone(
            re.search(
                r"gLinkArenaRuleData\[\].*?"
                r"\{\s*0x776,\s*14,\s*17,\s*0x77B,\s*0x77C\s*\},.*?"
                r"\{\s*0x777,\s*16,\s*22,\s*0x779,\s*0x77A\s*\},.*?"
                r"\{\s*0x778,\s*14,\s*17,\s*0x77B,\s*0x77C\s*\}",
                postbattle,
                re.DOTALL,
            )
        )
        self.assertIn("GetStringFromIndex(0x771)", points)
        self.assertIn("GetStringFromIndex(MSG_773)", result)
        self.assertIn("GetStringFromIndex(MSG_775)", result)

        expected = {
            "0x0771": {"ja": "ポイント", "zh-Hans": "得分"},
            "0x0773": {"ja": "ポイント", "zh-Hans": "得分"},
            "0x0775": {"ja": "人数", "zh-Hans": "人数"},
            "0x0778": {"ja": "自動武器選択", "zh-Hans": "自动选择武器"},
            "0x0779": {"ja": "ポイント", "zh-Hans": "得分"},
            "0x077A": {"ja": "サバイバル", "zh-Hans": "生存"},
        }
        for target_id, payloads in expected.items():
            for locale, payload in payloads.items():
                self.assertEqual(
                    self.localized_text(target_id, locale),
                    payload,
                    (target_id, locale),
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
