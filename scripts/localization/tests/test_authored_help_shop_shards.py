import hashlib
import json
import re
import unicodedata
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
QUEUE_PATH = ROOT / "texts/locales/mapping/authored_translation_queue.json"
SHARD_PATHS = {
    "ja": ROOT / "texts/locales/authored/shards/help_shop.ja.json",
    "zh-Hans": ROOT / "texts/locales/authored/shards/help_shop.zh-Hans.json",
}
SOURCE_REVISION = "e6435a8e2f444f0e16cab21713fe052b543d2e6e"
SOURCE_QUEUE_SHA256 = "ffdff913a552076928d5cb06634ed75d8983b65c05bb4ecec9aa2b610ffe6ad6"
SUBSYSTEM_COUNTS = {"help-tutorial": 33, "shop-arena": 48}
TOKEN_RE = re.compile(r"\[[^\[\]\r\n]+\]")
ASCII_WORD_RE = re.compile(r"[A-Za-z]+")
CJK_RE = re.compile(r"[\u3040-\u30FF\u3400-\u9FFF]")
ALLOWED_ASCII_LABELS = {"B", "LV", "P", "Pt", "START"}
NON_CJK_LABEL_IDS = {
    "ja": {
        "0x0148",
        "0x077F",
        "0x0781",
        "0x0786",
        "0x0787",
        "0x0788",
        "0x0789",
        "0x078A",
        "0x07D0",
        "0x0884",
    },
    "zh-Hans": {
        "0x0148",
        "0x077F",
        "0x0781",
        "0x0786",
        "0x0787",
        "0x0788",
        "0x0789",
        "0x078A",
        "0x07D0",
        "0x0884",
    },
}
REVIEWED_PAYLOADS = {
    "ja": {
        "0x0738": "十字キー左右で参加チーム数を [.][HASH] に設定します[X]\n",
        "0x0883": "を転送しました[.][X]\n",
    },
    "zh-Hans": {
        "0x0738": "按左右键设置参战小队数（[.][HASH]）[X]\n",
        "0x0756": "每名单位额外获得30点[X]\n",
    },
}
REVIEWED_LINE_EDGE_WHITESPACE = {
    ("ja", "0x0883"): [("", "")],
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(document):
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def load_strict_json(path):
    data = path.read_bytes()
    text = data.decode("utf-8", errors="strict")
    if text.startswith("\ufeff"):
        raise AssertionError(f"{path}: UTF-8 BOM is not allowed")
    return data, json.loads(text)


def visible_text(text):
    return TOKEN_RE.sub("", text)


def line_edge_whitespace(text):
    result = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        leading = body[: len(body) - len(body.lstrip())]
        trailing = body[len(body.rstrip()) :]
        result.append((leading, trailing))
    return result


def display_width(text):
    return sum(
        2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
        for character in text
    )


class AuthoredHelpShopShardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue_bytes, cls.queue = load_strict_json(QUEUE_PATH)
        cls.targets = [
            target
            for target in cls.queue["targets"]
            if target["subsystem"] in SUBSYSTEM_COUNTS
        ]
        cls.target_by_id = {target["target_id"]: target for target in cls.targets}
        cls.shard_bytes = {}
        cls.shards = {}
        cls.records = {}
        for locale, path in SHARD_PATHS.items():
            cls.shard_bytes[locale], cls.shards[locale] = load_strict_json(path)
            cls.records[locale] = {
                record["target_id"]: record
                for record in cls.shards[locale]["translations"]
            }

    def test_source_queue_hash_and_exact_target_partition_are_pinned(self):
        self.assertEqual(sha256(self.queue_bytes), SOURCE_QUEUE_SHA256)
        self.assertEqual(len(self.targets), 81)
        self.assertEqual(
            Counter(target["subsystem"] for target in self.targets),
            Counter(SUBSYSTEM_COUNTS),
        )
        self.assertEqual(len(self.target_by_id), 81)

        for locale, shard in self.shards.items():
            self.assertEqual(shard["kind"], "fe8u-authored-translation-shard")
            self.assertEqual(shard["schema_version"], 1)
            self.assertEqual(shard["locale"], locale)
            self.assertEqual(shard["target_count"], 81)
            self.assertEqual(shard["subsystem_counts"], SUBSYSTEM_COUNTS)
            self.assertEqual(
                shard["source_queue"],
                {
                    "path": "texts/locales/mapping/authored_translation_queue.json",
                    "revision": SOURCE_REVISION,
                    "sha256": SOURCE_QUEUE_SHA256,
                },
            )

    def test_locale_parity_order_and_per_target_hashes_are_exact(self):
        expected_ids = [target["target_id"] for target in self.targets]
        expected_keys = [target["suggested_key"] for target in self.targets]

        for locale, shard in self.shards.items():
            records = shard["translations"]
            self.assertEqual([record["target_id"] for record in records], expected_ids)
            self.assertEqual([record["key"] for record in records], expected_keys)
            self.assertEqual(len(self.records[locale]), 81)

            for record, target in zip(records, self.targets):
                self.assertEqual(record["subsystem"], target["subsystem"])
                self.assertEqual(
                    record["english_payload_sha256"],
                    target["english_payload_sha256"],
                )
                self.assertEqual(
                    record["source_text_sha256"],
                    sha256(target["source_text"].encode("utf-8")),
                )

        self.assertEqual(
            list(self.records["ja"]),
            list(self.records["zh-Hans"]),
        )

    def test_reviewed_payloads_are_exact(self):
        for locale, payloads in REVIEWED_PAYLOADS.items():
            for target_id, payload in payloads.items():
                self.assertEqual(self.records[locale][target_id]["text"], payload)

    def test_json_is_strict_utf8_and_canonical(self):
        for locale, shard in self.shards.items():
            self.assertEqual(self.shard_bytes[locale], canonical_json_bytes(shard))
            self.assertIn(
                locale.encode("utf-8"),
                self.shard_bytes[locale],
            )

            for source in shard["terminology_sources"]:
                path = ROOT / source["path"]
                self.assertEqual(sha256(path.read_bytes()), source["sha256"])

    def test_controls_newlines_placeholders_and_significant_spacing_are_exact(self):
        for locale, records in self.records.items():
            for target_id, record in records.items():
                source = self.target_by_id[target_id]["source_text"]
                translation = record["text"]
                self.assertEqual(
                    TOKEN_RE.findall(translation),
                    TOKEN_RE.findall(source),
                    f"{locale} {target_id}: control or placeholder drift",
                )
                self.assertEqual(
                    translation.splitlines(keepends=True).__len__(),
                    source.splitlines(keepends=True).__len__(),
                    f"{locale} {target_id}: physical line-count drift",
                )
                self.assertEqual(
                    re.findall(r"\r\n|\r|\n", translation),
                    re.findall(r"\r\n|\r|\n", source),
                    f"{locale} {target_id}: newline drift",
                )
                expected_line_edges = REVIEWED_LINE_EDGE_WHITESPACE.get(
                    (locale, target_id),
                    line_edge_whitespace(source),
                )
                self.assertEqual(
                    line_edge_whitespace(translation),
                    expected_line_edges,
                    f"{locale} {target_id}: leading/trailing spacing drift",
                )

                for placeholder in self.target_by_id[target_id]["placeholders"]:
                    token = placeholder["token"]
                    source_after = source[source.index(token) + len(token) :]
                    translation_after = translation[
                        translation.index(token) + len(token) :
                    ]
                    self.assertEqual(
                        source_after[: len(source_after) - len(source_after.lstrip())],
                        translation_after[
                            : len(translation_after) - len(translation_after.lstrip())
                        ],
                        f"{locale} {target_id}: placeholder suffix spacing drift",
                    )

    def test_established_terminology_is_used_without_untranslated_sentences(self):
        required_terms = {
            "ja": {
                "0x0147": "拠点",
                "0x0735": "編成",
                "0x0737": "コンピューター",
                "0x0739": "通信",
                "0x0746": "勝利条件",
                "0x0747": "自動装備",
                "0x0750": "先攻",
                "0x0757": "降伏",
                "0x075D": "通信対戦",
                "0x0763": "輸送隊",
                "0x0765": "武器屋",
                "0x086E": "杖",
                "0x0888": "トライアルマップ",
                "0x0895": "セーブデータ",
            },
            "zh-Hans": {
                "0x0147": "据点",
                "0x0735": "编制",
                "0x0737": "电脑",
                "0x0739": "通信对战",
                "0x0746": "胜利条件",
                "0x0747": "自动装备",
                "0x0750": "先攻",
                "0x0757": "弃权",
                "0x075D": "通信对战",
                "0x0763": "运输队",
                "0x0765": "武器店",
                "0x086E": "杖",
                "0x0888": "试炼地图",
                "0x0895": "已通关",
            },
        }

        for locale, records in self.records.items():
            for target_id, term in required_terms[locale].items():
                self.assertIn(term, records[target_id]["text"])

            for target_id, record in records.items():
                translated_visible = visible_text(record["text"])
                english_visible = visible_text(
                    self.target_by_id[target_id]["source_text"]
                )
                if re.search(r"[A-Za-z]{4}", english_visible):
                    self.assertNotEqual(
                        translated_visible.casefold(),
                        english_visible.casefold(),
                        f"{locale} {target_id}: untranslated English sentence",
                    )
                self.assertLessEqual(
                    set(ASCII_WORD_RE.findall(translated_visible)),
                    ALLOWED_ASCII_LABELS,
                    f"{locale} {target_id}: unexpected English word remains",
                )
                if target_id not in NON_CJK_LABEL_IDS[locale]:
                    self.assertRegex(
                        translated_visible,
                        CJK_RE,
                        f"{locale} {target_id}: translation lacks CJK text",
                    )

    def test_physical_lines_remain_concise(self):
        for locale, records in self.records.items():
            for target_id, record in records.items():
                for line in record["text"].splitlines():
                    width = display_width(visible_text(line))
                    self.assertLessEqual(
                        width,
                        56,
                        f"{locale} {target_id}: line width {width} is not concise",
                    )


if __name__ == "__main__":
    unittest.main()
