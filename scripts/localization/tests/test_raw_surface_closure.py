import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.raw_closure import (
    RawClosureError,
    build_raw_surface_closure,
    canonical_json_bytes,
)


class RawSurfaceClosureTests(unittest.TestCase):
    MAPPING_DIR = ROOT / "texts/locales/mapping"

    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(
            (ROOT / "texts/locales/zh-Hans/raw.json").read_text(encoding="utf-8")
        )
        cls.mapping = json.loads(
            (cls.MAPPING_DIR / "fe8u_target_map.json").read_text(encoding="utf-8")
        )
        cls.ja_raw = json.loads(
            (ROOT / "texts/locales/ja/raw.json").read_text(encoding="utf-8")
        )
        cls.decisions = json.loads(
            (cls.MAPPING_DIR / "raw_surface_decisions.json").read_text(
                encoding="utf-8"
            )
        )
        cls.registry = json.loads(
            (ROOT / "texts/expansion/registry.json").read_text(encoding="utf-8")
        )
        cls.catalogs = {
            locale: json.loads(
                (ROOT / f"texts/expansion/catalog.{locale}.json").read_text(
                    encoding="utf-8"
                )
            )
            for locale in ("en", "ja", "zh-Hans")
        }
        cls.closure = build_raw_surface_closure(
            raw_data=cls.raw,
            mapping_data=cls.mapping,
            decisions_data=cls.decisions,
            ja_raw_provider_data=cls.ja_raw,
            registry_data=cls.registry,
            catalog_data=cls.catalogs,
            repo_root=ROOT,
        )

    def test_all_143_records_have_one_honest_decision(self):
        summary = self.closure["summary"]
        self.assertEqual(summary["total_count"], 143)
        self.assertEqual(summary["baseline_game_message_count"], 114)
        self.assertEqual(summary["deferred_decision_count"], 29)
        self.assertEqual(summary["game_message_count"], 137)
        self.assertEqual(summary["expansion_message_count"], 6)
        self.assertEqual(summary["provider_count"], 143)
        self.assertEqual(summary["ja_materialized_count"], 143)
        self.assertEqual(summary["zh_hans_materialized_count"], 143)
        self.assertEqual(summary["non_user_facing_exclusion_count"], 0)
        self.assertEqual(summary["diagnostic_exclusion_count"], 0)
        self.assertEqual(summary["english_fallback_count"], 0)
        self.assertEqual(summary["unresolved_count"], 0)
        self.assertEqual(summary["user_facing_deferred_localized_count"], 25)
        self.assertEqual(
            len({row["import_id"] for row in self.closure["rows"]}), 143
        )

    def test_committed_manifest_matches_deterministic_rebuild(self):
        path = self.MAPPING_DIR / "raw_surface_closure.json"
        self.assertEqual(path.read_bytes(), canonical_json_bytes(self.closure))

    def test_every_record_has_materialized_japanese_and_chinese_providers(self):
        for row in self.closure["rows"]:
            with self.subTest(import_id=row["import_id"]):
                self.assertRegex(row["providers"]["ja"]["text_sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(
                    row["providers"]["zh-Hans"]["text_sha256"],
                    r"^[0-9a-f]{64}$",
                )

    def test_expansion_adapters_use_exact_imported_chinese_payloads(self):
        raw_text = {
            row["import_id"]: row["text"] for row in self.raw["records"]
        }
        zh = self.catalogs["zh-Hans"]["strings"]
        for decision in self.decisions["decisions"]:
            if decision["classification"] != "expansion_message":
                continue
            self.assertEqual(
                zh[decision["expansion_key"]],
                raw_text[decision["import_id"]],
            )

        source = (ROOT / "src/menu_def.c").read_text(encoding="utf-8")
        self.assertIn("LocalizedRawUnitActionMenuDraw", source)
        self.assertIn(
            "Text_DrawString(&item->text, ExpansionLocale_ResolveCurrent(msgId));",
            source,
        )
        self.assertNotIn(
            "Text_DrawStringASCII(&item->text, ExpansionLocale_ResolveCurrent(msgId));",
            source,
        )
        self.assertIn("#define LOCALIZED_RAW_UNIT_ACTION_DRAW 0", source)

    def test_class_choice_initializers_are_locale_safe_in_modern(self):
        source = (ROOT / "src/classchg-menuselect.c").read_text(encoding="utf-8")
        self.assertEqual(source.count("ClassChgMenuItem_OnTextDraw,"), 3)
        self.assertIn('#define PROMO_OPTION_1_NAME ""', source)
        self.assertIn('#define PROMO_OPTION_2_NAME ""', source)
        self.assertIn('#define PROMO_OPTION_3_NAME ""', source)
        self.assertIn('#define PROMO_OPTION_1_NAME "　第１兵種"', source)
        self.assertIn('#define PROMO_OPTION_2_NAME "　第２兵種"', source)
        self.assertIn('#define PROMO_OPTION_3_NAME "　第３兵種"', source)
        self.assertIn(
            "GetStringFromIndex(GetClassData(gparent->jid[pmitem->itemNumber])->nameTextId)",
            source,
        )

    def test_build_timestamp_has_locale_provider_and_legacy_path(self):
        decision = next(
            row
            for row in self.decisions["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0142"
        )
        self.assertEqual(decision["classification"], "expansion_message")
        self.assertEqual(
            decision["expansion_key"],
            "raw_surface.diagnostic.build_timestamp",
        )
        self.assertFalse(decision["user_facing"])
        source = (ROOT / "src/fe3_dummy.c").read_text(encoding="utf-8")
        self.assertIn("ExpansionLocale_ResolveCurrent", source)
        self.assertIn("PrintDebugStringToBG(bg, gBuildDateTime);", source)

    def test_disappearing_call_site_anchor_fails_the_closure(self):
        broken = deepcopy(self.decisions)
        broken["decisions"][0]["call_sites"][0]["anchors"] = [
            "__missing_raw_surface_anchor__"
        ]
        with self.assertRaisesRegex(RawClosureError, "no surviving anchor"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_tampered_literal_context_fails_the_closure(self):
        broken = deepcopy(self.mapping)
        row = next(
            row
            for row in broken["rows"]
            if row.get("source", {})
            .get("regional_sources", {})
            .get("ja", {})
            .get("kind")
            == "literal"
        )
        row["source"]["regional_sources"]["ja"]["provenance"][
            "context_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(
            RawClosureError,
            "literal evidence failed.*context_sha256",
        ):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=broken,
                decisions_data=self.decisions,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_missing_japanese_symbol_payload_fails_strict_gate(self):
        broken = deepcopy(self.ja_raw)
        del broken["providers"]["0x01C1"]
        broken["provider_count"] -= 1
        with self.assertRaisesRegex(
            RawClosureError,
            "Japanese raw symbol provider is missing",
        ):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=self.decisions,
                ja_raw_provider_data=broken,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_goal_labels_use_raw_symbols_not_same_id_indexed_messages(self):
        expected = {
            "0x01C1": ("GoalString_UnitsLeft", "残り"),
            "0x01C2": ("GoalString_Turn", "ターン"),
            "0x01C3": ("GoalString_LastTurn", "最終ターン"),
        }
        mapping = {row["target_id"]: row["source"] for row in self.mapping["rows"]}
        for target_id, (symbol, text) in expected.items():
            with self.subTest(target_id=target_id):
                self.assertEqual(mapping[target_id]["kind"], "raw")
                self.assertEqual(
                    mapping[target_id]["regional_sources"]["ja"],
                    {"kind": "symbol", "symbol": symbol},
                )
                self.assertEqual(
                    self.ja_raw["providers"][target_id],
                    {"symbol": symbol, "text": text},
                )


if __name__ == "__main__":
    unittest.main()
