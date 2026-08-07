import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.crosswalk import canonical_json_bytes


class AuthoredMenuShardTests(unittest.TestCase):
    QUEUE_PATH = ROOT / "texts/locales/mapping/authored_translation_queue.json"
    SHARD_DIR = ROOT / "texts/locales/authored/shards"
    EXPECTED_QUEUE_SHA256 = (
        "ffdff913a552076928d5cb06634ed75d8983b65c05bb4ecec9aa2b610ffe6ad6"
    )
    LOCALES = ("ja", "zh-Hans")
    CONTROL_RE = re.compile(r"\[[^\]\n]+\]")
    ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")
    APPROVED_ASCII_TERMS = {"CP", "SRAM"}

    @classmethod
    def setUpClass(cls):
        cls.queue_bytes = cls.QUEUE_PATH.read_bytes()
        cls.queue = json.loads(cls.queue_bytes.decode("utf-8"))
        cls.expected = [
            row
            for row in cls.queue["targets"]
            if row["subsystem"] == "menu-definition"
        ]
        cls.expected_by_id = {row["target_id"]: row for row in cls.expected}
        cls.shard_bytes = {}
        cls.shards = {}
        for locale in cls.LOCALES:
            path = cls.SHARD_DIR / f"menu.{locale}.json"
            raw = path.read_bytes()
            cls.shard_bytes[locale] = raw
            cls.shards[locale] = json.loads(raw.decode("utf-8"))

    @staticmethod
    def _leading_whitespace_by_line(text):
        return [
            re.match(r"[ \t]*", line).group(0)
            for line in text.splitlines(keepends=True)
        ]

    @classmethod
    def _visible_text(cls, text):
        return cls.CONTROL_RE.sub("", text)

    def test_queue_hash_and_exact_menu_target_set_are_pinned(self):
        queue_hash = hashlib.sha256(self.queue_bytes).hexdigest()
        self.assertEqual(queue_hash, self.EXPECTED_QUEUE_SHA256)
        self.assertEqual(len(self.expected), 132)
        self.assertEqual(len(self.expected_by_id), 132)

        expected_ids = [row["target_id"] for row in self.expected]
        for locale, shard in self.shards.items():
            with self.subTest(locale=locale):
                self.assertEqual(shard["kind"], "fe8u-authored-translation-shard")
                self.assertEqual(shard["locale"], locale)
                self.assertEqual(shard["schema_version"], 1)
                self.assertEqual(shard["shard"], "menu")
                self.assertEqual(
                    shard["source_queue"]["sha256"],
                    self.EXPECTED_QUEUE_SHA256,
                )
                self.assertEqual(
                    shard["source_map_sha256"],
                    self.queue["authoritative_target_map_sha256"],
                )
                self.assertEqual(
                    shard["subsystem_counts"], {"menu-definition": 132}
                )
                self.assertEqual(shard["terminology_sources"], [])
                self.assertEqual(shard["target_count"], 132)
                self.assertEqual(
                    [entry["target_id"] for entry in shard["translations"]],
                    expected_ids,
                )
                self.assertEqual(
                    len(
                        {
                            entry["target_id"]
                            for entry in shard["translations"]
                        }
                    ),
                    132,
                )

    def test_entries_have_only_the_deterministic_authored_schema(self):
        for locale, shard in self.shards.items():
            with self.subTest(locale=locale):
                for entry in shard["translations"]:
                    self.assertEqual(
                        set(entry),
                        {
                            "english_payload_sha256",
                            "key",
                            "source_text_sha256",
                            "subsystem",
                            "target_id",
                            "text",
                        },
                    )
                    expected = self.expected_by_id[entry["target_id"]]
                    self.assertEqual(
                        entry["key"], expected["suggested_key"]
                    )
                    self.assertEqual(entry["subsystem"], "menu-definition")
                    self.assertEqual(
                        entry["english_payload_sha256"],
                        expected["english_payload_sha256"],
                    )
                    self.assertEqual(
                        entry["source_text_sha256"],
                        hashlib.sha256(
                            expected["source_text"].encode("utf-8")
                        ).hexdigest(),
                    )

    def test_shards_are_canonical_utf8(self):
        for locale in self.LOCALES:
            with self.subTest(locale=locale):
                self.shard_bytes[locale].decode("utf-8", errors="strict")
                self.assertEqual(
                    self.shard_bytes[locale],
                    canonical_json_bytes(self.shards[locale]),
                )

    def test_controls_placeholders_newlines_and_leading_spaces_match(self):
        for locale, shard in self.shards.items():
            for entry in shard["translations"]:
                expected = self.expected_by_id[entry["target_id"]]
                source = expected["source_text"]
                translated = entry["text"]
                with self.subTest(locale=locale, target_id=entry["target_id"]):
                    self.assertEqual(
                        self.CONTROL_RE.findall(translated),
                        self.CONTROL_RE.findall(source),
                    )
                    self.assertEqual(
                        translated.count("\n"), source.count("\n")
                    )
                    self.assertEqual(
                        translated.endswith("\n"), source.endswith("\n")
                    )
                    self.assertEqual(
                        self._leading_whitespace_by_line(translated),
                        self._leading_whitespace_by_line(source),
                    )
                    for placeholder in expected["placeholders"]:
                        token = (
                            placeholder
                            if isinstance(placeholder, str)
                            else placeholder["token"]
                        )
                        self.assertEqual(
                            translated.count(token), source.count(token)
                        )

                    source_visible = self._visible_text(source).strip()
                    if source_visible and not re.search(
                        r"[A-Za-z0-9]", source_visible
                    ):
                        self.assertEqual(
                            self._visible_text(translated).strip(),
                            source_visible,
                        )

    def test_no_untranslated_english_words_remain(self):
        script_patterns = {
            "ja": re.compile(r"[\u3040-\u30ff\u3400-\u9fff]"),
            "zh-Hans": re.compile(r"[\u3400-\u9fff]"),
        }
        for locale, shard in self.shards.items():
            for entry in shard["translations"]:
                translated_visible = self._visible_text(entry["text"])
                unexpected = set(
                    self.ASCII_WORD_RE.findall(translated_visible)
                ) - self.APPROVED_ASCII_TERMS
                with self.subTest(locale=locale, target_id=entry["target_id"]):
                    self.assertFalse(unexpected)

                    source_words = set(
                        self.ASCII_WORD_RE.findall(
                            self.expected_by_id[entry["target_id"]][
                                "english_canonical_text"
                            ]
                        )
                    )
                    if source_words - self.APPROVED_ASCII_TERMS:
                        self.assertRegex(
                            translated_visible, script_patterns[locale]
                        )


if __name__ == "__main__":
    unittest.main()
