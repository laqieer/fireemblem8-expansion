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
                "c-febuilder": 1305,
                "c-febuilder-raw": 3,
                "d-contextual-resolution": 21,
                "d-existing-authored": 3,
                "d-semantic-correction": 48,
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
            "0x000D": "game.semantic_correction.msg_00d",
            "0x000E": "game.semantic_correction.msg_00e",
            "0x0679": "game.semantic_correction.msg_679",
            "0x06A2": "game.semantic_correction.msg_6a2",
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
