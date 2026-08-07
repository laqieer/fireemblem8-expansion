import hashlib
import json
import re
import unittest
from pathlib import Path

from scripts.localization.game_locales.authored import (
    AUTHORED_CATALOG_KIND,
    AUTHORED_SHARD_KIND,
    build_authored_catalogs,
    canonical_json_bytes,
    normalize_authored_shard,
)


ROOT = Path(__file__).resolve().parents[3]
AUTHORED_DIR = ROOT / "texts/locales/authored"
QUEUE_PATH = ROOT / "texts/locales/mapping/authored_translation_queue.json"
EXPECTED_QUEUE_SHA256 = (
    "ffdff913a552076928d5cb06634ed75d8983b65c05bb4ecec9aa2b610ffe6ad6"
)
LOCALES = ("ja", "zh-Hans")
ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")
TOKEN_RE = re.compile(r"\[[^\]\n]+\]")
ALLOWED_ASCII_TERMS = {"CP", "CPU", "LV", "Pt", "SRAM", "START"}


class AuthoredCatalogMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (AUTHORED_DIR / "manifest.json").read_text(encoding="utf-8")
        )
        cls.queue_bytes = QUEUE_PATH.read_bytes()
        cls.queue = json.loads(cls.queue_bytes.decode("utf-8"))
        cls.queue_by_id = {
            row["target_id"]: row for row in cls.queue["targets"]
        }
        cls.catalogs = {
            locale: json.loads(
                (
                    AUTHORED_DIR / f"catalog.{locale}.json"
                ).read_text(encoding="utf-8")
            )
            for locale in LOCALES
        }

    def test_source_queue_and_every_shard_hash_are_pinned(self):
        self.assertEqual(
            hashlib.sha256(self.queue_bytes).hexdigest(),
            EXPECTED_QUEUE_SHA256,
        )
        self.assertEqual(
            self.manifest["source_queue"]["sha256"],
            EXPECTED_QUEUE_SHA256,
        )
        self.assertEqual(
            self.manifest["source_queue"]["revision"],
            "e6435a8e2f444f0e16cab21713fe052b543d2e6e",
        )
        for locale in LOCALES:
            for spec in self.manifest["locales"][locale]["shards"]:
                path = ROOT / spec["path"]
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    spec["sha256"],
                )
                self.assertEqual(data["kind"], AUTHORED_SHARD_KIND)
                self.assertEqual(data["locale"], locale)
                self.assertEqual(data["source_queue"], self.manifest["source_queue"])
                self.assertEqual(data["target_count"], spec["target_count"])
                self.assertEqual(path.read_bytes(), canonical_json_bytes(data))

    def test_shards_are_disjoint_and_union_is_exact(self):
        existing_ids = {
            row["target_id"]
            for row in self.manifest["existing_authored_targets"]
        }
        expected_ids = set(self.queue_by_id) | existing_ids
        self.assertEqual(len(self.queue_by_id), 259)
        self.assertEqual(len(expected_ids), 262)

        for locale in LOCALES:
            observed = set()
            for spec in self.manifest["locales"][locale]["shards"]:
                shard = json.loads(
                    (ROOT / spec["path"]).read_text(encoding="utf-8")
                )
                shard_ids = {
                    row["target_id"] for row in shard["translations"]
                }
                self.assertTrue(observed.isdisjoint(shard_ids))
                observed.update(shard_ids)
            self.assertEqual(observed, expected_ids)

    def test_catalogs_are_exact_deterministic_merges_with_no_key_drift(self):
        rebuilt = build_authored_catalogs(ROOT)
        expected_keys = {
            row["suggested_key"] for row in self.queue["targets"]
        } | {
            row["translation_key"]
            for row in self.manifest["existing_authored_targets"]
        }
        for locale in LOCALES:
            catalog = self.catalogs[locale]
            self.assertEqual(catalog["kind"], AUTHORED_CATALOG_KIND)
            self.assertEqual(catalog["target_count"], 262)
            self.assertEqual(set(catalog["strings"]), expected_keys)
            self.assertEqual(catalog, rebuilt[locale])
            self.assertEqual(
                (AUTHORED_DIR / f"catalog.{locale}.json").read_bytes(),
                canonical_json_bytes(rebuilt[locale]),
            )

    def test_catalogs_contain_no_unreviewed_english_prose(self):
        for locale in LOCALES:
            for key, text in self.catalogs[locale]["strings"].items():
                visible = TOKEN_RE.sub("", text)
                unexpected = (
                    set(ASCII_WORD_RE.findall(visible)) - ALLOWED_ASCII_TERMS
                )
                self.assertFalse(unexpected, (locale, key, unexpected))

    def test_legacy_menu_shape_normalizes_to_the_common_schema(self):
        queue_row = next(
            row
            for row in self.queue["targets"]
            if row["subsystem"] == "menu-definition"
        )
        legacy = {
            "entries": [
                {
                    "suggested_key": queue_row["suggested_key"],
                    "target_id": queue_row["target_id"],
                    "text": queue_row["source_text"],
                }
            ],
            "kind": "fe8-authored-translation-shard",
            "locale_id": "ja",
            "schema_version": 1,
            "source_queue_sha256": EXPECTED_QUEUE_SHA256,
            "subsystem": "menu-definition",
            "target_count": 1,
        }
        normalized = normalize_authored_shard(
            legacy,
            shard_name="legacy_menu",
            queue_source=self.manifest["source_queue"],
            source_map_sha256=self.queue[
                "authoritative_target_map_sha256"
            ],
            queue_by_id=self.queue_by_id,
        )
        self.assertEqual(normalized["kind"], AUTHORED_SHARD_KIND)
        self.assertEqual(normalized["locale"], "ja")
        self.assertEqual(normalized["target_count"], 1)
        self.assertEqual(
            normalized["translations"][0]["key"],
            queue_row["suggested_key"],
        )


if __name__ == "__main__":
    unittest.main()
