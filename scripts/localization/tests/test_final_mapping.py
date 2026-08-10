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
from scripts.localization.game_locales.ending_metrics import (
    _ascii_widths,
    _cjk_widths,
    _line_width,
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
        cls.ending_metrics = json.loads(
            (cls.MAPPING_DIR / "ending_layout_metrics.json").read_text(
                encoding="utf-8"
            )
        )
        cls.fixed_width_metrics = json.loads(
            (
                cls.MAPPING_DIR / "fixed_width_label_metrics.json"
            ).read_text(encoding="utf-8")
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
            "ending_metrics": self.MAPPING_DIR / "ending_layout_metrics.json",
            "fixed_width_metrics": (
                self.MAPPING_DIR / "fixed_width_label_metrics.json"
            ),
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
                "b-structural-high": 26,
                "c-febuilder": 1266,
                "c-febuilder-raw": 3,
                "d-contextual-resolution": 20,
                "d-existing-authored": 3,
                "d-semantic-correction": 105,
                "d-structural-reference-second-check": 6,
                "e-exact-english": 134,
                "e-exact-english-context": 1,
                "f-authored-queue": 259,
            },
        )
        samples = {
            "0x0C46": "d-semantic-correction",
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
        self.assertEqual(
            self.rows["0x0C45"]["source"],
            {"id": "0x0C06", "kind": "indexed", "layout": "FE8J"},
        )
        self.assertEqual(
            self.rows["0x0C46"]["source"],
            {"id": "0x0C05", "kind": "indexed", "layout": "FE8J"},
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
        self.assertEqual(len(resolved), 20)
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
        self.assertEqual(len(dedup_rows), 135)
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
            "0x0733": "0x06BC",
            "0x0734": "0x06BD",
            "0x0940": "0x0900",
            "0x0953": "0x0913",
            "0x096F": "0x092F",
            "0x0970": "0x0930",
            "0x0974": "0x0934",
        }
        expected_authored_keys = {
            "0x0005": "game.semantic_correction.msg_005",
            "0x0006": "game.semantic_correction.msg_006",
            "0x0008": "game.semantic_correction.msg_008",
            "0x000A": "game.semantic_correction.msg_00a",
            "0x000D": "game.semantic_correction.msg_00d",
            "0x000E": "game.semantic_correction.msg_00e",
            "0x000F": "game.semantic_correction.msg_00f",
            "0x0010": "game.semantic_correction.msg_010",
            "0x01C8": "game.semantic_correction.msg_1c8",
            "0x01EE": "game.semantic_correction.msg_1ee",
            "0x01EF": "game.semantic_correction.msg_1ef",
            "0x03A8": "game.semantic_correction.msg_3a8",
            "0x0679": "game.semantic_correction.msg_679",
            "0x06A2": "game.semantic_correction.msg_6a2",
            "0x06AE": "game.semantic_correction.msg_6ae",
            "0x06AF": "game.semantic_correction.msg_6af",
            "0x06B2": "game.semantic_correction.msg_6b2",
            "0x06B9": "game.semantic_correction.msg_6b9",
            "0x06BA": "game.semantic_correction.msg_6ba",
            "0x06BB": "game.semantic_correction.msg_6bb",
            "0x06BC": "game.semantic_correction.msg_6bc",
            "0x0593": "game.semantic_correction.msg_593",
            "0x07D1": "game.semantic_correction.msg_7d1",
            "0x07D2": "game.semantic_correction.msg_7d2",
            "0x07D3": "game.semantic_correction.msg_7d3",
            "0x07D4": "game.semantic_correction.msg_7d4",
            "0x0A15": "game.semantic_correction.msg_a15",
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

    def test_blue_team_followup_payloads_match_slots_and_semantics(self):
        expected = {
            "0x0008": ("入手：[X]\n", "获得：[X]\n"),
            "0x000A": ("盗品：[X]\n", "盗取：[X]\n"),
            "0x000F": ("廃棄：[X]\n", "丢弃：[X]\n"),
            "0x0010": ("輸送隊へ送付：[X]\n", "送往运输队：[X]\n"),
            "0x0011": ("。[.][X]\n", "。[.][X]\n"),
            "0x01C8": ("閉じた村[.][X]\n", "关闭村庄[.][X]\n"),
            "0x01EE": ("船板[X]\n", "船板[X]\n"),
            "0x01EF": ("難破船[.][X]\n", "沉船[.][X]\n"),
            "0x0205": ("今回", "本次"),
            "0x03A8": (
                "エンブレムシール[.][X]\n",
                "纹章之印[.][X]\n",
            ),
            "0x04FB": ("相手", "伙伴"),
            "0x0542": (
                "ユニットのレベルです[CTRL:0001]上がると強くなります",
                "人物的等级\n等级越高，能力越强",
            ),
            "0x06AE": ("　周回数[.][X]\n", " 通关次数[.][X]\n"),
            "0x06AF": ("　クリア登録[.][X]\n", " 标记通关[.][X]\n"),
            "0x06B2": ("砂嵐[X]\n", "沙暴[X]\n"),
            "0x06B9": (
                "ファイルをクリア済みに[X]\n",
                "将存档标记为已通关[X]\n",
            ),
            "0x06BA": ("しますか？[X]\n", "确定吗？[X]\n"),
            "0x06BB": (
                "クリア済みファイルは[.][X]\n",
                "标记后，该存档[.][X]\n",
            ),
            "0x06BC": (
                "以後プレイできません[X]\n",
                "将无法继续游玩[X]\n",
            ),
            "0x0867": (
                "持ち物がいっぱいです　　輸送隊へ送るアイテムを選んでください",
                "所持物品已满，请选择要送往运输队的物品",
            ),
            "0x0878": (
                "修復したい武器を持っている相手を選んでください",
                "请选择持有待修复武器的角色",
            ),
            "0x08BC": (
                "それは買い取りできないわ[CTRL:0003]",
                "这件物品无法出售。[CTRL:0003]",
            ),
            "0x08BD": (
                "フフフ・・・[CTRL:0001]それは買い取りできないわ[CTRL:0003]",
                "呵呵……等一下。\n这件物品无法出售！[CTRL:0003]",
            ),
            "0x08CA": (
                "残念だ　輸送隊さえいれば[CTRL:0001]送ることもできたのに[CTRL:0003]",
                "真遗憾，你还没有运输队，\n无法送过去。[CTRL:0003]",
            ),
            "0x08CB": (
                "残念ね　輸送隊さえいれば[CTRL:0001]送ることもできたのに[CTRL:0003]",
                "可惜你还没有运输队，\n不然我就能替你送过去……[CTRL:0003]",
            ),
        }
        for target_id, (ja, zh_hans) in expected.items():
            if self.rows[target_id]["source"]["kind"] == "indexed":
                ja = ja.replace("\n", "[CTRL:0001]")
                zh_hans = zh_hans.replace("\n", "[CTRL:0001]")
            self.assertEqual(self.localized_text(target_id, "ja"), ja)
            self.assertEqual(
                self.localized_text(target_id, "zh-Hans"), zh_hans
            )

        self.assertLessEqual(
            len(self.localized_text("0x00CC", "ja").encode("utf-8")),
            14,
        )
        self.assertEqual(self.localized_text("0x00CC", "ja"), "なし")

        terrain_slots = {
            "0x01C8": "TERRAIN_VILLAGE_CLOSED",
            "0x01EE": "TERRAIN_SHIP_FLAT",
            "0x01EF": "TERRAIN_SHIP_WRECK",
        }
        for target_id, slot in terrain_slots.items():
            self.assertEqual(
                self.rows[target_id]["verification"]["source_key"],
                f"gTerrains_0[{slot}]",
            )

        zh_payloads = "\n".join(
            self.localized_text(target_id, "zh-Hans")
            for target_id in expected
        )
        for leaked in (
            "今回",
            "配合",
            "最高为等级20",
            "删除",
            "买不起",
            "无发",
            "[CTRL:0080]",
            "[CTRL:0020]",
        ):
            self.assertNotIn(leaked, zh_payloads)

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
            "0x0593": {"ja": ("デバッグ",), "zh-Hans": ("调试",)},
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

    def test_full_semantic_audit_exact_targets_and_call_sites(self):
        exact = {
            "0x0805": {
                "ja": "光の女王　ラーチェル",
                "zh-Hans": "光之女王 拉切尔",
            },
            "0x009A": {
                "ja": "ウィンドウカラー",
                "zh-Hans": "窗口颜色",
            },
            "0x00A5": {
                "zh-Hans": "单位移动速度",
            },
            "0x02CD": {"ja": "フォレストナイト"},
            "0x02CF": {"ja": "ドラゴンマスター"},
            "0x02D0": {"ja": "ワイバーンナイト"},
            "0x02E8": {"ja": "ファルコンナイト"},
            "0x02F9": {"ja": "モーサドゥーグ"},
            "0x0300": {"ja": "ゴーゴンエッグ"},
            "0x0302": {"ja": "デスガーゴイル"},
            "0x0303": {"ja": "ドラゴンゾンビ"},
            "0x03AD": {"ja": "サンダーストーム"},
            "0x04EA": {"ja": "ＭＨＰ"},
            "0x0593": {
                "ja": "デバッグ[.][X]\n",
                "zh-Hans": "调试[.][X]\n",
            },
            "0x07A0": {
                "ja": "現れし異形の影Ａ",
                "zh-Hans": "扭曲之影Ａ",
            },
            "0x07A1": {
                "ja": "現れし異形の影Ｂ",
                "zh-Hans": "扭曲之影Ｂ",
            },
            "0x07C8": {
                "ja": "勝利の歌Ａ",
                "zh-Hans": "胜利之歌Ａ",
            },
            "0x07C9": {
                "ja": "勝利の歌Ｂ",
                "zh-Hans": "胜利之歌Ｂ",
            },
        }
        for target_id, payloads in exact.items():
            for locale, payload in payloads.items():
                self.assertEqual(
                    self.localized_text(target_id, locale),
                    payload,
                    (target_id, locale),
                )

        sound_room = (self.ROOT / "src/soundroom_data.c").read_text(
            encoding="utf-8"
        )
        for message_id in ("MSG_7A0", "MSG_7A1", "MSG_7C8", "MSG_7C9"):
            self.assertIn(f".nameTextId = {message_id}", sound_room)

    def test_creature_descriptions_use_verified_adjacent_sources(self):
        expected = {
            "0x0733": {
                "source": "0x06BC",
                "ja": ("鋭い槍", "凶悪な翼魔", "空中から"),
                "zh-Hans": ("锐枪", "凶恶翼魔", "天上袭击"),
            },
            "0x0734": {
                "source": "0x06BD",
                "ja": ("魔の力", "ガーゴイル", "残忍"),
                "zh-Hans": ("更强之力量", "石像鬼", "残忍"),
            },
        }
        for target_id, spec in expected.items():
            self.assertEqual(self.rows[target_id]["source"]["id"], spec["source"])
            for locale in ("ja", "zh-Hans"):
                payload = self.localized_text(target_id, locale)
                for term in spec[locale]:
                    self.assertIn(term, payload)

    def test_tutorial_sources_match_the_target_steps(self):
        expected_sources = {
            "0x0940": "0x0900",
            "0x0953": "0x0913",
            "0x096F": "0x092F",
            "0x0970": "0x0930",
            "0x0974": "0x0934",
        }
        for target_id, source_id in expected_sources.items():
            self.assertEqual(
                self.rows[target_id]["source"],
                {"id": source_id, "kind": "indexed", "layout": "FE8J"},
            )
        expected_terms = {
            "0x0940": {
                "ja": ("エイリーク", "民家", "点滅"),
                "zh-Hans": ("艾瑞珂", "民家", "闪烁"),
            },
            "0x0953": {
                "ja": ("マップメニュー", "辞書", "遊び方"),
                "zh-Hans": ("地图菜单", "辞典", "各个部分"),
            },
            "0x096F": {
                "ja": ("ペガサスナイト", "山", "弓兵"),
                "zh-Hans": ("天马骑士", "山脉", "弓箭手"),
            },
            "0x0970": {
                "ja": ("ヴァネッサ", "ロス", "救出"),
                "zh-Hans": ("瓦内萨", "罗斯", "救出"),
            },
            "0x0974": {
                "ja": ("ヴァネッサ", "Ａボタン"),
                "zh-Hans": ("瓦内萨", "A键"),
            },
        }
        for target_id, by_locale in expected_terms.items():
            for locale, terms in by_locale.items():
                payload = self.localized_text(target_id, locale)
                for term in terms:
                    self.assertIn(term, payload)
        self.assertNotIn("哥哥部分", self.localized_text("0x0953", "zh-Hans"))

    def test_knight_crest_tutorial_and_coin_payload_are_complete(self):
        ja = self.localized_text("0x0A15", "ja")
        zh = self.localized_text("0x0A15", "zh-Hans")
        self.assertEqual(ja.count("騎士勲章"), 2)
        self.assertEqual(zh.count("骑士勋章"), 2)
        self.assertEqual(zh.count("[CTRL:0102]"), 2)
        self.assertIn("请从物品栏中选择『骑士勋章』", zh)
        self.assertIn("再选择『使用』", zh)

        coin = self.localized_text("0x0CF8", "zh-Hans")
        self.assertIn("硬币", coin)
        self.assertNotIn("骰子", coin)

    def test_ending_fragments_compose_with_dynamic_locations(self):
        ending = (self.ROOT / "src/ending_details.c").read_text(
            encoding="utf-8"
        )
        header = (self.ROOT / "include/ending_details.h").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "PrepareUnitDefeatLocationString(MSG_7D1, defeatDetails, MSG_022, str)",
            ending,
        )
        self.assertIn(
            "PrepareUnitDefeatLocationString(MSG_7D3, defeatDetails, MSG_7D4, str)",
            ending,
        )
        self.assertIn(
            "DEFEAT_WOUNDED_PARTEDWAYS   = 1, // unused in FE8",
            header,
        )

        def visible(text):
            text = text.replace("[LF]\n", "\n").replace("[LF]", "\n")
            return re.sub(r"\[[^\]]+\]", "", text).strip()

        expected = {
            "ja": {
                "location": "ルネス",
                "died": "戦死地：\nルネス。",
                "parted": "ルネスで負傷し、\n一行と別れた。",
                "remained": (
                    "負傷地：\nルネス。\n"
                    "しかし最後まで一行と旅を共にした。"
                ),
            },
            "zh-Hans": {
                "location": "雷内斯",
                "died": "战死于\n雷内斯。",
                "parted": "在雷内斯负伤，\n并与队伍分离。",
                "remained": "负伤于\n雷内斯，\n但仍随队奋战至最后。",
            },
        }
        for locale, sample in expected.items():
            location = sample["location"]
            died = (
                visible(self.localized_text("0x07D1", locale))
                + "\n"
                + location
                + visible(self.localized_text("0x0022", locale))
            )
            parted = (
                ("" if locale == "ja" else "在")
                + location
                + visible(self.localized_text("0x07D2", locale))
            )
            remained = (
                visible(self.localized_text("0x07D3", locale))
                + "\n"
                + location
                + visible(self.localized_text("0x07D4", locale))
            )
            self.assertEqual(died, sample["died"])
            self.assertEqual(parted, sample["parted"])
            self.assertEqual(remained, sample["remained"])

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
                r"POPUP_UNIT_NAME.*?POPUP_MSG\(0x022\)",
                popup,
                re.DOTALL,
            )
        )
        for script_name in ("PopupScr_GotItem", "PopupScr_StoleItem"):
            self.assertIsNotNone(
                re.search(
                    rf"{script_name}.*?POPUP_ITEM_STR.*?POPUP_MSG\(0x022\)",
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
            punctuation = visible(self.localized_text("0x0022", locale))
            self.assertEqual(item + punctuation, item + "。")
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

    def test_latest_semantic_audit_payloads_match_verified_call_sites(self):
        call_sites = {
            "0x0028": "gDebugContinueMenuItems.override[0xc].name",
            "0x0169": "L08.chapTitleTextId",
            "0x031D": "CLASS_MAGE.descTextId",
            "0x032A": "CLASS_BERSERKER.descTextId",
            "0x033B": "CLASS_NECROMANCER.descTextId",
            "0x0490": "ITEM_GUIDINGRING.descTextId",
            "0x04DE": "ITEM_MASTERSEAL.useDescTextId",
            "0x06AB": "gDebugMenuItems.override[0x12].name",
            "0x08E5": "EventScrWM_MessedEventscr_0.text[1]",
            "0x08EA": "EventScrWM_MessedEventscr_4.text[1]",
            "0x08F0": "EventScrWM_MessedEventscr_42.text[1]",
            "0x08FB": "EventScrWM_MessedEventscr_49.text[1]",
            "0x0907": "EventScr_Prologue_RenaisThroneCutscene.text[6]",
            "0x095A": "EventScr_Ch2_BeginningScene.text[7]",
            "0x0969": "EventScr_Ch2_Village1.text[1]",
            "0x098F": "EventScr_Ch3_5.text[1]",
            "0x09F2": "EventScr_Ch6_EndingScene.text[2]",
            "0x0ABA": "EventScr_Ch10B_0.text[2]",
            "0x0AD3": "EventScr_Ch11B_1.text[1]",
            "0x0AF9": "EventScr_Ch13B_6.Selena-village",
            "0x0B06": "chapter=Ch14B/script=ending/message-ordinal=2",
            "0x0B29": "EventScr_Ch15A_26.text[3]",
            "0x0CE5": "CHARACTER_ARTUR+CHARACTER_JOSHUA.A",
        }
        for target_id, source_key in call_sites.items():
            self.assertEqual(
                self.rows[target_id]["verification"]["source_key"],
                source_key,
                target_id,
            )

        exact = {
            "0x0012": {
                "ja": "村が破壊された",
                "zh-Hans": "村子被毁了",
            },
            "0x0028": {"zh-Hans": " 手动继续"},
            "0x0169": {"ja": "罠だ！", "zh-Hans": "这是陷阱！"},
            "0x031D": {
                "zh-Hans": "体力较弱，但魔法能力稳定\n装备『理』"
            },
            "0x032A": {
                "zh-Hans": "擅长在山地和海上移动，且容易暴击\n装备『斧』"
            },
            "0x033B": {
                "zh-Hans": "掌握【魔石】之力\n以最高位暗魔法操纵尸骸"
            },
            "0x0490": {
                "ja": (
                    "レベル１０以上の神官・魔道士・トルバドール"
                    "[CTRL:0001]修道士・シャーマンが使います"
                ),
                "zh-Hans": (
                    "供10级以上的僧侣、魔法师、神官骑士、\n"
                    "修道士和巫师使用"
                ),
            },
            "0x04DE": {
                "ja": (
                    "レベル１０以上の未転職[CTRL:0001]"
                    "下位クラスユニットを転職させます"
                ),
                "zh-Hans": "使10级以上尚未转职的\n下位职业单位转职",
            },
            "0x0560": {
                "zh-Hans": "回避敌人攻击的能力\n会降低敌人的命中率"
            },
            "0x0570": {
                "zh-Hans": (
                    "被对手的武器相克\n命中率与威力会下降\n"
                    "但仍会对敌人造成较高伤害"
                )
            },
            "0x057B": {
                "ja": (
                    "ユニットの持ち物を[CTRL:0001]整理することができます。"
                    "[CTRL:0001]名前が灰色のアイテムは[CTRL:0001]"
                    "そのユニットには[CTRL:0001]使用できません。"
                ),
                "zh-Hans": (
                    "可以整理单位的\n所持物品\n名称显示为灰色的物品\n"
                    "该单位无法使用"
                ),
            },
            "0x0612": {
                "ja": (
                    "ユニットを選択した時に表示される[CTRL:0001]"
                    "赤色の部分が攻撃範囲です。[CTRL:0001]"
                    "敵ユニットにカーソルを合わせても[CTRL:0001]"
                    "その敵の攻撃範囲が表示されます。[CTRL:0001]"
                    "接近する前に確認してください。"
                ),
                "zh-Hans": (
                    "选择单位时显示的\n红色区域是攻击范围。\n"
                    "将光标移到敌方单位上时，\n"
                    "也会显示该敌人的攻击范围。\n接近敌人前请先确认。"
                ),
            },
            "0x0640": {
                "ja": (
                    "戦闘や杖の使用で[CTRL:0001]"
                    "ＥＸＰ（経験値）を得られます。[CTRL:0001]"
                    "１００に達するとレベルが１上がります。[CTRL:0001]"
                    "レベルアップの際、【力】【技】などの[CTRL:0001]"
                    "パラメータが上昇することで、[CTRL:0001]"
                    "ユニットは強くなっていきます。[CTRL:0001]"
                    "レベルの上限は２０です。"
                ),
                "zh-Hans": (
                    "进行战斗或使用杖可获得EXP。\n"
                    "EXP达到100时等级提升1。\n"
                    "升级时，『力』、『技』等\n"
                    "能力数值可能会上升，\n"
                    "使单位变得更强。\n等级上限为20。"
                ),
            },
            "0x06AB": {"ja": "　デバッグ情報"},
            "0x0848": {"zh-Hans": "退出"},
        }
        for target_id, by_locale in exact.items():
            for locale, payload in by_locale.items():
                if self.rows[target_id]["source"]["kind"] == "indexed":
                    payload = payload.replace("\n", "[CTRL:0001]")
                self.assertEqual(
                    self.localized_text(target_id, locale),
                    payload,
                    (target_id, locale),
                )

        contains = {
            "0x08E5": {
                "ja": (
                    "塔のマップをひとつクリア",
                    "次の階",
                    "上の階へ進むほど敵は強く",
                ),
                "zh-Hans": (
                    "每通关一张塔内地图",
                    "进入下一层",
                    "塔层越高，敌人就会越强",
                ),
            },
            "0x08EA": {
                "zh-Hans": ("王宫仍遭古拉德", "围攻", "眼看就要陷落")
            },
            "0x08F0": {
                "zh-Hans": ("守护最后的【圣石】", "阻止魔王复活")
            },
            "0x08FB": {
                "zh-Hans": ("守护最后的【圣石】", "阻止魔王复活")
            },
            "0x0907": {
                "zh-Hans": ("单骑行动", "避开古拉德军", "耳目")
            },
            "0x095A": {
                "zh-Hans": ("第一个两难", "尽快行动", "避免", "引人注目")
            },
            "0x0969": {
                "zh-Hans": ("艾莉娜", "隐瞒着什么", "无意干涉")
            },
            "0x097F": {"zh-Hans": ("中立军",)},
            "0x098F": {"zh-Hans": ("祖父", "外出打猎")},
            "0x09F2": {
                "zh-Hans": ("把一切都告诉", "戴着另一只腕轮", "哥哥")
            },
            "0x0A6A": {"zh-Hans": ("克里姆特长老",)},
            "0x0ABA": {"zh-Hans": ("乐趣", "必须尽量延长")},
            "0x0AD3": {"zh-Hans": ("登上敌船", "夺过来")},
            "0x0AF9": {
                "ja": ("セライナさんを", "傷つけないで"),
                "zh-Hans": ("不要", "伤害塞莱娜"),
            },
            "0x0B06": {
                "ja": (
                    "フレリアとルネスの【聖石】はすでに潰え",
                    "ジャハナの【聖石】もまもなく",
                ),
                "zh-Hans": (
                    "弗雷利亚和鲁内斯",
                    "【圣石】已经化为尘埃",
                    "贾哈那的【圣石】也即将",
                ),
            },
            "0x0BA9": {
                "zh-Hans": ("梅尔早已追寻魔王的气息", "离开了这片土地")
            },
            "0x0C10": {
                "zh-Hans": ("到了需要的时候", "我会把事情办妥")
            },
            "0x0C12": {
                "zh-Hans": (
                    "但王子身边还有弗雷利亚正规军",
                    "到了需要的时候",
                    "我会把事情办妥",
                )
            },
            "0x0C00": {
                "zh-Hans": ("不知道", "是否平安", "双胞胎哥哥", "身陷险境")
            },
            "0x0C1C": {
                "zh-Hans": ("不相信", "没有利害关系", "人际关系")
            },
            "0x0CE5": {
                "ja": ("２１戦１１勝１０敗",),
                "zh-Hans": ("21战11胜10败", "但不想再打赌了"),
            },
        }
        for target_id, by_locale in contains.items():
            for locale, terms in by_locale.items():
                payload = self.localized_text(target_id, locale)
                for term in terms:
                    self.assertIn(term, payload, (target_id, locale, term))

        forbidden = {
            ("0x08E5", "ja"): ("章をひとつクリア",),
            ("0x08E5", "zh-Hans"): ("每结束一章", "随着故事的进行"),
            ("0x08EA", "zh-Hans"): ("目前也已经陷落",),
            ("0x0907", "zh-Hans"): ("同时也能立下大功",),
            ("0x0969", "zh-Hans"): ("艾莉斯", "可以放心"),
            ("0x097F", "zh-Hans"): ("友军",),
            ("0x098F", "zh-Hans"): ("叔叔",),
            ("0x09F2", "zh-Hans"): ("您不可了", "拥有相同【圣石】"),
            ("0x0A6A", "zh-Hans"): ("安娜议长",),
            ("0x0ABA", "zh-Hans"): ("不能延长",),
            ("0x0AF9", "ja"): ("救ってあげて",),
            ("0x0AF9", "zh-Hans"): ("救救塞莱娜",),
            ("0x0B06", "ja"): ("グラドとフレリア",),
            ("0x0B06", "zh-Hans"): ("古拉德和弗雷利亚",),
            ("0x0BA9", "zh-Hans"): ("感觉到你的栖息离开了这里",),
            ("0x0BAC", "zh-Hans"): ("能够再见到父亲",),
            ("0x0C10", "zh-Hans"): ("随时的散散步",),
            ("0x0C12", "zh-Hans"): ("不和我们一", "随时的散散步"),
            ("0x0C00", "zh-Hans"): ("双保胎", "现在没有危险"),
            ("0x0C1C", "zh-Hans"): ("以利益为纽带",),
            ("0x0CE5", "ja"): ("２１戦１５勝１６敗",),
            ("0x0CE5", "zh-Hans"): (
                "15胜16败",
                "无论赌博抑或练习我均愿奉陪",
            ),
        }
        for (target_id, locale), fragments in forbidden.items():
            payload = self.localized_text(target_id, locale)
            for fragment in fragments:
                self.assertNotIn(fragment, payload, (target_id, locale, fragment))

    def test_fixed_width_alias_metrics_cover_every_final_name_label(self):
        metrics = self.fixed_width_metrics
        self.assertEqual(metrics["kind"], "fe8u-fixed-width-label-metrics")
        self.assertEqual(
            metrics["summary"],
            {
                "alias_count": 120,
                "label_count": 664,
                "locale_count": 2,
                "overflow_count": 0,
                "surface_count": 3,
            },
        )
        ja = metrics["locales"]["ja"]["surfaces"]
        bolting = next(
            record
            for record in ja["item_name_56"]["records"]
            if record["target_id"] == "0x03AD"
        )
        ranger = next(
            record
            for record in ja["class_name_64"]["records"]
            if record["target_id"] == "0x02CD"
        )
        self.assertEqual(bolting["canonical_text"], "サンダーストーム")
        self.assertEqual(bolting["display_text"], "遠雷")
        self.assertLessEqual(bolting["display_width"], 56)
        self.assertEqual(ranger["canonical_text"], "フォレストナイト")
        self.assertEqual(ranger["display_text"], "森騎士")
        self.assertLessEqual(ranger["display_width"], 64)

    def test_ch15_scene_boundary_does_not_duplicate_0b2a(self):
        expected_end = "[CTRL:0003][CTRL:0015]"
        b29_ja = self.localized_text("0x0B29", "ja")
        b29_zh = self.localized_text("0x0B29", "zh-Hans")
        self.assertTrue(b29_ja.endswith(expected_end))
        self.assertTrue(b29_zh.endswith(expected_end))
        self.assertNotIn("リオンが別人のようになったのは", b29_ja)
        self.assertNotIn("据说，利昂判若两人", b29_zh)

        b2a_ja = self.localized_text("0x0B2A", "ja")
        b2a_zh = self.localized_text("0x0B2A", "zh-Hans")
        self.assertTrue(b2a_ja.startswith("[OpenLeft]リオンが別人のよう"))
        self.assertTrue(b2a_zh.startswith("[OpenLeft]听说利昂是在得到"))
        self.assertEqual(
            self.rows["0x0B29"]["verification"]["source_key"],
            "EventScr_Ch15A_26.text[3]",
        )
        self.assertEqual(
            self.rows["0x0B2A"]["verification"]["source_key"],
            "game.chapter_event.msg_b2a",
        )

    def test_garcia_dozla_support_pair_and_rank_map_to_authored_targets(self):
        expected = {
            "0x0CB6": (
                "C",
                "game.semantic_correction.msg_cb6",
            ),
            "0x0CB7": (
                "B",
                "game.semantic_correction.msg_cb7",
            ),
            "0x0CB8": (
                "A",
                "game.semantic_correction.msg_cb8",
            ),
        }
        for target_id, (rank, translation_key) in expected.items():
            row = self.rows[target_id]
            self.assertEqual(
                row["source"],
                {
                    "kind": "authored",
                    "translation_key": translation_key,
                },
            )
            verification = row["verification"]
            self.assertEqual(
                verification["source_key"],
                f"CHARACTER_DOZLA+CHARACTER_GARCIA.{rank}",
            )
            self.assertEqual(verification["subsystem"], "supports")
            self.assertEqual(
                verification["promotion"]["precedence"],
                "d-semantic-correction",
            )
            self.assertEqual(
                verification["promotion"]["original_source"],
                {
                    "id": f"0x{int(target_id, 16) - 0x41:04X}",
                    "kind": "indexed",
                    "layout": "FE8J",
                },
            )

    def test_all_ending_titles_solo_and_paired_lines_fit_real_text_allocations(self):
        metrics = self.ending_metrics
        self.assertEqual(metrics["kind"], "fe8u-ending-layout-metrics")
        self.assertEqual(
            metrics["allocations"],
            {
                "paired": {
                    "pixel_width": 208,
                    "text_count": 5,
                    "text_index_start": 0,
                    "tile_width": 26,
                },
                "solo": {
                    "pixel_width": 208,
                    "text_count": 5,
                    "text_index_start": 0,
                    "tile_width": 26,
                },
                "title": {
                    "pixel_width": 120,
                    "text_count": 2,
                    "text_index_start": 5,
                    "tile_width": 15,
                },
            },
        )
        ending_source = (self.ROOT / "src/ending_details.c").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "InitText(gpCharacterEndingTexts + 5 + i, 15)",
            ending_source,
        )
        self.assertIn(
            "InitText(gpCharacterEndingTexts + i, 26)",
            ending_source,
        )
        self.assertEqual(
            metrics["summary"],
            {
                "locale_count": 2,
                "overflow_count": 0,
                "paired_target_count": 34,
                "solo_target_count": 33,
                "title_target_count": 33,
            },
        )
        for locale in ("ja", "zh-Hans"):
            locale_metrics = metrics["locales"][locale]
            self.assertEqual(len(locale_metrics["titles"]), 33)
            self.assertEqual(len(locale_metrics["solo"]), 33)
            self.assertEqual(len(locale_metrics["paired"]), 34)
            self.assertEqual(
                [record["target_id"] for record in locale_metrics["solo"]],
                [f"0x{message_id:04X}" for message_id in range(0x07D6, 0x0817, 2)],
            )
            self.assertEqual(
                [record["target_id"] for record in locale_metrics["paired"]],
                [f"0x{message_id:04X}" for message_id in range(0x0817, 0x0839)],
            )
            for record in locale_metrics["titles"]:
                self.assertEqual(record["line_count"], 1)
                self.assertLessEqual(record["max_line_width"], 120)
            for record in locale_metrics["paired"]:
                self.assertEqual(record["line_count"], 5)
                self.assertEqual(len(record["line_widths"]), 5)
                self.assertLessEqual(record["max_line_width"], 208)
            for record in locale_metrics["solo"]:
                self.assertGreaterEqual(record["line_count"], 1)
                self.assertLessEqual(record["line_count"], 5)
                self.assertEqual(
                    len(record["line_widths"]),
                    record["line_count"],
                )
                self.assertLessEqual(record["max_line_width"], 208)

        self.assertEqual(
            self.localized_text("0x0801", "zh-Hans"),
            "智泉·塞勒夫",
        )

    def test_link_arena_labels_fit_their_actual_pixel_allocations(self):
        allocations = {
            "0x0768": 56,
            "0x076A": 56,
            "0x0776": 64,
            "0x0777": 80,
            "0x0778": 64,
        }
        expected_ja = {
            "0x0768": "チーム交換[X]\n",
            "0x076A": "通信画面[.][X]\n",
            "0x0776": "敵を隠す",
        }
        ascii_widths = _ascii_widths(self.ROOT)
        for locale in ("ja", "zh-Hans"):
            cjk_widths, _ = _cjk_widths(self.ROOT, locale)
            for target_id, allocation in allocations.items():
                payload = self.localized_text(target_id, locale)
                visible = re.sub(r"\[[^\[\]\r\n]+\]", "", payload).strip()
                width = _line_width(
                    visible,
                    locale=locale,
                    ascii_widths=ascii_widths,
                    cjk_widths=cjk_widths,
                )
                self.assertLessEqual(
                    width,
                    allocation,
                    f"{locale} {target_id}: {width}px exceeds {allocation}px",
                )
        for target_id, payload in expected_ja.items():
            self.assertEqual(self.localized_text(target_id, "ja"), payload)

        team_list = (self.ROOT / "src/sio_teamlist.c").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "GetStringFromIndex(ptr[i].menuTextId), 7)",
            team_list,
        )
        for path in ("src/sio_rulesettings.c", "src/sio_bat.c"):
            source = (self.ROOT / path).read_text(encoding="utf-8")
            self.assertIn("TILEMAP_LOCATED(gBG0TilemapBuffer, 6,", source)
            self.assertIn("gLinkArenaRuleData[i].labelTextId", source)

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
