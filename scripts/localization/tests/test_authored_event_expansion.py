import hashlib
import json
import re
import unittest
from collections import Counter
from pathlib import Path

from scripts.localization.game_locales.crosswalk import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[3]
QUEUE_PATH = ROOT / "texts/locales/mapping/authored_translation_queue.json"
SHARD_DIR = ROOT / "texts/locales/authored/shards"
LOCALES = ("ja", "zh-Hans")
EXPECTED_QUEUE_SHA256 = (
    "ffdff913a552076928d5cb06634ed75d8983b65c05bb4ecec9aa2b610ffe6ad6"
)
EXPECTED_TARGET_IDS = (
    "0x0000",
    "0x0537",
    "0x0539",
    "0x05AE",
    "0x05BB",
    "0x05BC",
    "0x05BD",
    "0x05BE",
    "0x06A5",
    "0x06A6",
    "0x06A7",
    "0x06A8",
    "0x06A9",
    "0x06B1",
    "0x06B3",
    "0x06B4",
    "0x06B5",
    "0x06B6",
    "0x06B7",
    "0x077E",
    "0x0889",
    "0x088A",
    "0x088B",
    "0x0944",
    "0x0945",
    "0x094A",
    "0x094D",
    "0x0950",
    "0x096B",
    "0x096C",
    "0x096E",
    "0x0979",
    "0x0983",
    "0x0984",
    "0x0986",
    "0x0987",
    "0x0AAD",
    "0x0B2A",
    "0x0D4C",
    "0x0D4D",
    "0x0D4E",
    "0x0D4F",
    "0x0D50",
    "0x0D51",
    "0x0D52",
    "0x0D55",
)
EXPECTED_SUBSYSTEM_COUNTS = {
    "chapter-event": 28,
    "chapter-title": 1,
    "expansion": 8,
    "system": 1,
    "trainee-prep": 8,
}
EXPECTED_EXPANSION_TEXT = {
    "ja": {
        "0x0D4C": (
            "このセーブデータは[.][LF]\n"
            "拡張セーブ形式より古く[.][LF]\n"
            "ここでは開けません。[X]\n"
        ),
        "0x0D4D": "セーブデータが破損しており[.][LF]\n読み込めません。[X]\n",
        "0x0D4E": (
            "セーブデータの拡張情報が[.][LF]\n"
            "破損しており[.][LF]\n"
            "読み込めません。[X]\n"
        ),
        "0x0D4F": (
            "このセーブデータは[.][LF]\n"
            "古い拡張版のものです。[.][LF]\n"
            "外部ツールを使って[.][LF]\n"
            "移行できます。[X]\n"
        ),
        "0x0D50": (
            "このセーブデータは[.][LF]\n"
            "新しい拡張版のもので[.][LF]\n"
            "この環境では[.][LF]\n"
            "使用できません。[X]\n"
        ),
        "0x0D51": (
            "このセーブデータは[.][LF]\n"
            "互換性のない拡張設定で[.][LF]\n"
            "作成されており使用できません。[X]\n"
        ),
        "0x0D52": "このセーブデータは[.][LF]\nここでは使用できません。[X]\n",
        "0x0D55": "全セーブデータ消去を実行しますか？[.][LF]\n元に戻せません。[X]",
    },
    "zh-Hans": {
        "0x0D4C": "此存档早于[.][LF]\n扩展存档格式，[.][LF]\n无法在此打开。[X]\n",
        "0x0D4D": "存档似乎已损坏，[.][LF]\n无法读取。[X]\n",
        "0x0D4E": "存档的扩展信息[.][LF]\n已经损坏，[.][LF]\n无法读取。[X]\n",
        "0x0D4F": (
            "此存档来自较旧的[.][LF]\n"
            "扩展版本，[.][LF]\n"
            "可使用外部工具[.][LF]\n"
            "进行迁移。[X]\n"
        ),
        "0x0D50": (
            "此存档来自较新的[.][LF]\n"
            "扩展版本，[.][LF]\n"
            "当前版本[.][LF]\n"
            "不支持该存档。[X]\n"
        ),
        "0x0D51": (
            "此存档使用了[.][LF]\n"
            "不兼容的扩展设置，[.][LF]\n"
            "因此无法使用。[X]\n"
        ),
        "0x0D52": "此存档[.][LF]\n无法在此使用。[X]\n",
        "0x0D55": "清除全部存档？[.][LF]\n此操作无法撤销。[X]",
    },
}


def structural_sequence(text):
    return re.findall(r"\[[^\]\n]+\]|\n", text)


class AuthoredEventExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue_bytes = QUEUE_PATH.read_bytes()
        cls.queue = json.loads(cls.queue_bytes.decode("utf-8"))
        cls.queue_rows = {
            row["target_id"]: row for row in cls.queue["targets"]
        }
        cls.shard_paths = {
            locale: SHARD_DIR / f"event_expansion.{locale}.json"
            for locale in LOCALES
        }
        cls.shards = {
            locale: json.loads(path.read_text(encoding="utf-8"))
            for locale, path in cls.shard_paths.items()
        }
        cls.entries = {
            locale: {
                row["target_id"]: row
                for row in cls.shards[locale]["translations"]
            }
            for locale in LOCALES
        }

    def test_exact_target_scope_count_and_order(self):
        selected = [
            row
            for row in self.queue["targets"]
            if row["subsystem"] in EXPECTED_SUBSYSTEM_COUNTS
        ]
        self.assertEqual(
            tuple(row["target_id"] for row in selected),
            EXPECTED_TARGET_IDS,
        )
        self.assertEqual(
            Counter(row["subsystem"] for row in selected),
            Counter(EXPECTED_SUBSYSTEM_COUNTS),
        )
        for locale in LOCALES:
            shard = self.shards[locale]
            self.assertEqual(shard["target_count"], 46)
            self.assertEqual(
                tuple(row["target_id"] for row in shard["translations"]),
                EXPECTED_TARGET_IDS,
            )
            self.assertEqual(
                len(set(row["target_id"] for row in shard["translations"])),
                46,
            )
            self.assertEqual(
                shard["subsystem_counts"], EXPECTED_SUBSYSTEM_COUNTS
            )

    def test_locale_parity_and_source_hash_order_are_pinned(self):
        self.assertEqual(
            hashlib.sha256(self.queue_bytes).hexdigest(),
            EXPECTED_QUEUE_SHA256,
        )
        ja_rows = self.shards["ja"]["translations"]
        zh_rows = self.shards["zh-Hans"]["translations"]
        for ja_row, zh_row in zip(ja_rows, zh_rows):
            for field in (
                "target_id",
                "key",
                "subsystem",
                "english_payload_sha256",
            ):
                self.assertEqual(ja_row[field], zh_row[field])
            source = self.queue_rows[ja_row["target_id"]]
            self.assertEqual(ja_row["key"], source["suggested_key"])
            self.assertEqual(ja_row["subsystem"], source["subsystem"])
            self.assertEqual(
                ja_row["english_payload_sha256"],
                source["english_payload_sha256"],
            )
        for locale in LOCALES:
            shard = self.shards[locale]
            self.assertEqual(shard["locale"], locale)
            self.assertEqual(shard["shard"], "event_expansion")
            self.assertEqual(
                shard["source_queue_sha256"], EXPECTED_QUEUE_SHA256
            )
            self.assertEqual(
                shard["source_map_sha256"],
                self.queue["authoritative_target_map_sha256"],
            )

    def test_controls_newlines_and_placeholders_match_source(self):
        for locale in LOCALES:
            for target_id in EXPECTED_TARGET_IDS:
                source = self.queue_rows[target_id]
                text = self.entries[locale][target_id]["text"]
                self.assertEqual(
                    structural_sequence(text),
                    structural_sequence(source["source_text"]),
                    (locale, target_id),
                )
                for placeholder in source["placeholders"]:
                    token = placeholder["token"]
                    self.assertEqual(
                        text.count(token),
                        source["source_text"].count(token),
                        (locale, target_id, token),
                    )
        for locale in LOCALES:
            self.assertEqual(self.entries[locale]["0x0000"]["text"], "[X]\n")
            self.assertEqual(
                self.entries[locale]["0x0537"]["text"],
                "[DashedLine][DashedLine][DashedLine][DashedLine][X]\n",
            )

    def test_files_are_canonical_deterministic_utf8(self):
        for locale, path in self.shard_paths.items():
            raw = path.read_bytes()
            self.assertEqual(raw.decode("utf-8").encode("utf-8"), raw)
            self.assertTrue(any(byte >= 0x80 for byte in raw))
            self.assertEqual(raw, canonical_json_bytes(self.shards[locale]))

    def test_no_untranslated_english_prose(self):
        for locale in LOCALES:
            for target_id, entry in self.entries[locale].items():
                prose = re.sub(r"\[[^\]\n]+\]", "", entry["text"])
                self.assertIsNone(
                    re.search(r"[A-Za-z]{4,}", prose),
                    (locale, target_id, prose),
                )

    def test_established_terminology_is_used(self):
        expected = {
            "ja": {
                "0x0945": ("ミュラン", "グラド", "ブレゲ", "エイリーク", "制圧"),
                "0x094A": ("十字ボタン", "きずぐすり"),
                "0x0950": ("ゼト",),
                "0x096B": ("傭兵", "とっこうやく"),
                "0x096E": (
                    "ロス",
                    "ガルシア",
                    "かけだし戦士",
                    "英雄の証",
                    "騎士の勲章",
                ),
                "0x0AAD": (
                    "エフラム",
                    "リオン",
                    "デュッセル",
                    "ヴィガルド",
                    "ルネス",
                    "グラド",
                ),
                "0x0B2A": (
                    "ラーチェル",
                    "フレリア",
                    "ジャハナ",
                    "【魔石】",
                    "【聖石】",
                ),
            },
            "zh-Hans": {
                "0x0945": ("缪兰", "古拉德", "普利肯", "艾瑞珂", "占领"),
                "0x094A": ("十字键", "伤药"),
                "0x0950": ("塞思",),
                "0x096B": ("佣兵", "特效药"),
                "0x096E": (
                    "罗斯",
                    "加西亚",
                    "年轻的战士",
                    "英雄之证",
                    "骑士勋章",
                ),
                "0x0AAD": (
                    "伊弗列姆",
                    "利昂",
                    "杜塞尔",
                    "彼加尔德",
                    "鲁内斯",
                    "古拉德",
                ),
                "0x0B2A": (
                    "拉切尔",
                    "弗雷利亚",
                    "贾哈那",
                    "【魔石】",
                    "【圣石】",
                ),
            },
        }
        for locale, targets in expected.items():
            for target_id, terms in targets.items():
                text = self.entries[locale][target_id]["text"]
                for term in terms:
                    self.assertIn(term, text, (locale, target_id, term))

    def test_expansion_save_messages_are_materialized_and_exact(self):
        for locale in LOCALES:
            actual = {
                target_id: self.entries[locale][target_id]["text"]
                for target_id in EXPECTED_EXPANSION_TEXT[locale]
            }
            self.assertEqual(actual, EXPECTED_EXPANSION_TEXT[locale])
            catalog = json.loads(
                (
                    ROOT / f"texts/expansion/catalog.{locale}.json"
                ).read_text(encoding="utf-8")
            )
            erase_label = catalog["strings"]["save_compat.menu_erase_all"]
            self.assertIn(erase_label, actual["0x0D55"])


if __name__ == "__main__":
    unittest.main()
