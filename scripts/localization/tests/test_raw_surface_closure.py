import json
import hashlib
import re
import shutil
import subprocess
import sys
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.catalog import load_catalog
from scripts.localization.game_locales.raw_closure import (
    RawClosureError,
    build_raw_surface_closure,
    canonical_json_bytes,
)
from scripts.localization.game_locales.raw_providers import (
    GitSourceBlob,
    RawProviderError,
    _extract_source_anchor_values,
    load_ja_raw_providers,
    verify_ja_raw_provider_git_source,
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
        cls.loaded_catalog = load_catalog()
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
        self.assertEqual(summary["runtime_consumer_verified_count"], 6)
        self.assertEqual(summary["ja_materialized_count"], 143)
        self.assertEqual(summary["ja_provenance_count"], 143)
        self.assertEqual(summary["zh_hans_materialized_count"], 143)
        self.assertEqual(summary["non_user_facing_exclusion_count"], 0)
        self.assertEqual(summary["diagnostic_exclusion_count"], 0)
        self.assertEqual(summary["english_fallback_count"], 0)
        self.assertEqual(summary["unresolved_count"], 0)
        self.assertEqual(summary["user_facing_deferred_localized_count"], 28)
        self.assertEqual(
            len({row["import_id"] for row in self.closure["rows"]}), 143
        )

    def test_committed_manifest_matches_deterministic_rebuild(self):
        path = self.MAPPING_DIR / "raw_surface_closure.json"
        self.assertEqual(path.read_bytes(), canonical_json_bytes(self.closure))

    def test_one_raw_provider_can_cover_multiple_exact_targets(self):
        rows = {row["import_id"]: row for row in self.closure["rows"]}
        self.assertEqual(
            rows["fe8cn.raw.import-0061"]["target_ids"],
            ["0x0024", "0x0142", "0x075C"],
        )
        self.assertEqual(
            rows["fe8cn.raw.import-0088"]["target_ids"],
            ["0x01D2"],
        )

    def test_raw_snapshots_remain_pinned_behind_authored_corrections(self):
        raw_text = {
            row["import_id"]: row["text"] for row in self.raw["records"]
        }
        self.assertEqual(raw_text["fe8cn.raw.import-0000"], "你确定要将存档")
        self.assertEqual(raw_text["fe8cn.raw.import-0078"], "已关闭")
        self.assertEqual(raw_text["fe8cn.raw.import-0116"], "荒地")
        self.assertEqual(raw_text["fe8cn.raw.import-0117"], "破屋")

        rows = {row["import_id"]: row for row in self.closure["rows"]}
        for import_id in (
            "fe8cn.raw.import-0000",
            "fe8cn.raw.import-0001",
            "fe8cn.raw.import-0002",
            "fe8cn.raw.import-0003",
            "fe8cn.raw.import-0014",
            "fe8cn.raw.import-0015",
            "fe8cn.raw.import-0078",
            "fe8cn.raw.import-0116",
            "fe8cn.raw.import-0117",
        ):
            self.assertEqual(
                rows[import_id]["providers"]["ja"]["kind"],
                "authored_semantic_correction",
            )
            self.assertEqual(
                rows[import_id]["providers"]["zh-Hans"]["kind"],
                "authored_semantic_correction",
            )

    def test_every_record_has_materialized_japanese_and_chinese_providers(self):
        provenance_kinds = Counter()
        for row in self.closure["rows"]:
            with self.subTest(import_id=row["import_id"]):
                self.assertRegex(row["providers"]["ja"]["text_sha256"], r"^[0-9a-f]{64}$")
                self.assertIsInstance(row["providers"]["ja"]["provenance"], dict)
                provenance_kinds[
                    row["providers"]["ja"]["provenance"]["kind"]
                ] += 1
                provenance = row["providers"]["ja"]["provenance"]
                if provenance["kind"] == "pinned_git_source_artifact":
                    self.assertRegex(
                        provenance["source_revision"],
                        r"^(?!0{40})[0-9a-f]{40}$",
                    )
                    self.assertRegex(
                        provenance["source_blob_oid"],
                        r"^(?!0{40})[0-9a-f]{40}$",
                    )
                    self.assertTrue(provenance["source_repository"])
                    self.assertTrue(provenance["source_path"])
                    self.assertTrue(provenance["source_anchor"])
                    self.assertRegex(
                        provenance["provider_values_artifact"]["sha256"],
                        r"^[0-9a-f]{64}$",
                    )
                self.assertRegex(
                    row["providers"]["zh-Hans"]["text_sha256"],
                    r"^[0-9a-f]{64}$",
                )
        self.assertEqual(
            provenance_kinds,
            Counter(
                {
                    "pinned_git_source_artifact": 110,
                    "pinned_baserom_slice": 3,
                    "tracked_source_literal": 13,
                    "reviewed_authored_translation": 11,
                    "authored_expansion_catalog": 6,
                }
            ),
        )

    def test_expansion_adapters_use_expected_chinese_payloads(self):
        raw_text = {
            row["import_id"]: row["text"] for row in self.raw["records"]
        }
        zh = self.catalogs["zh-Hans"]["strings"]
        for decision in self.decisions["decisions"]:
            if decision["classification"] != "expansion_message":
                continue
            if "runtime_payload_source" in decision:
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
        self.assertIn("ClassChgMenu_GetDisplayLabel", source)
        self.assertIn("classId <= CLASS_ID_CONFIGURED_CAP", source)
        self.assertIn("classData->nameTextId < MSG_COUNT", source)
        for suffix in ("OPTION_1", "OPTION_2", "OPTION_3"):
            self.assertIn(
                f"EXP_MSG_RAW_SURFACE_CLASS_CHANGE_{suffix}",
                source,
            )

    def test_build_timestamp_matches_executable_in_every_runtime_locale(self):
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
        self.assertEqual(
            decision["runtime_payload_source"],
            {
                "kind": "c_string_symbol",
                "path": "src/main.c",
                "symbol": "gBuildDateTime",
            },
        )
        main_source = (ROOT / "src/main.c").read_text(encoding="utf-8")
        match = re.search(
            r'const char gBuildDateTime\[\]\s*=\s*"([^"]+)";',
            main_source,
        )
        self.assertIsNotNone(match)
        build_timestamp = match.group(1)
        registry_entry = next(
            row
            for row in self.registry["messages"]
            if row["key"] == "raw_surface.diagnostic.build_timestamp"
        )
        self.assertEqual(registry_entry["pseudo_policy"], "preserve")
        for locale in ("en", "ja", "zh-Hans", "qps-ploc"):
            self.assertEqual(
                self.loaded_catalog.strings_for(locale)[
                    "raw_surface.diagnostic.build_timestamp"
                ],
                build_timestamp,
            )
        raw_text = next(
            row["text"]
            for row in self.raw["records"]
            if row["import_id"] == "fe8cn.raw.import-0142"
        )
        self.assertEqual(raw_text, "2004/09/09(THU) 13:12:56")
        self.assertNotEqual(raw_text, build_timestamp)
        source = (ROOT / "src/fe3_dummy.c").read_text(encoding="utf-8")
        self.assertIn("ExpansionLocale_ResolveCurrent", source)
        self.assertIn("PrintDebugStringToBG(bg, gBuildDateTime);", source)

    def test_every_expansion_provider_has_a_verified_runtime_consumer(self):
        rows = [
            row
            for row in self.closure["rows"]
            if row["classification"] == "expansion_message"
        ]
        self.assertEqual(len(rows), 6)
        for row in rows:
            with self.subTest(import_id=row["import_id"]):
                self.assertTrue(row["runtime_consumers"])
                for consumer in row["runtime_consumers"]:
                    self.assertRegex(
                        consumer["symbol"],
                        r"^[A-Za-z_][A-Za-z0-9_]*$",
                    )
                    self.assertTrue((ROOT / consumer["path"]).is_file())

    def test_catalog_only_expansion_provider_fails_without_runtime_consumer(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["classification"] == "expansion_message"
        )
        del decision["runtime_consumers"]
        with self.assertRaisesRegex(RawClosureError, "runtime_consumers"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_runtime_consumer_anchor_must_be_inside_named_function(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0062"
        )
        decision["runtime_consumers"][0]["anchors"] = ["PROMO_OPTION_1_NAME"]
        with self.assertRaisesRegex(RawClosureError, "missing ordered anchors"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_disappearing_call_site_anchor_fails_the_closure(self):
        broken = deepcopy(self.decisions)
        broken["decisions"][0]["call_sites"][0]["anchors"] = [
            "__missing_raw_surface_anchor__"
        ]
        with self.assertRaisesRegex(RawClosureError, "missing ordered anchors"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_one_surviving_call_site_anchor_cannot_hide_a_missing_anchor(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0062"
        )
        decision["call_sites"][0]["anchors"].append(
            "__missing_relationship_anchor__"
        )
        with self.assertRaisesRegex(RawClosureError, "missing ordered anchors"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_stale_scoped_call_site_symbol_fails_closed(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0062"
        )
        decision["call_sites"][0]["symbol"] = "gMenuItem_PromoSel_Stale"
        with self.assertRaisesRegex(RawClosureError, "array initializer"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_unrelated_surviving_provider_anchor_fails_closed(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0062"
        )
        decision["call_sites"][0]["anchors"][0] = "PROMO_OPTION_2_NAME"
        decision["call_sites"][0]["provider_anchor"] = "PROMO_OPTION_2_NAME"
        with self.assertRaisesRegex(RawClosureError, "provider anchors do not match"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_symbol_backed_decision_requires_provider_anchor(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0062"
        )
        del decision["call_sites"][0]["provider_anchor"]
        with self.assertRaisesRegex(
            RawClosureError,
            "must declare provider_anchor",
        ):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_symbol_backed_decision_rejects_empty_provider_anchor(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0139"
        )
        decision["call_sites"][0]["provider_anchor"] = ""
        with self.assertRaisesRegex(RawClosureError, "non-empty string"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_goal_target_anchor_must_remain_inside_goal_display_init(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0139"
        )
        decision["call_sites"][0]["anchors"].append("BmMapFill")
        with self.assertRaisesRegex(RawClosureError, "missing ordered anchors"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_goal_provider_anchor_must_remain_inside_pinned_goal_function(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0139"
        )
        decision["call_sites"][0]["provider_scope"]["anchors"] = [
            "GoalString_UnitsLeft",
            "ClassChgMenuItem_OnTextDraw",
        ]
        with self.assertRaisesRegex(RawClosureError, "missing ordered anchors"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=broken,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_stale_terrain_provider_symbol_fails_closed(self):
        broken = deepcopy(self.mapping)
        row = next(row for row in broken["rows"] if row["target_id"] == "0x01C4")
        row["verification"]["source_symbol"] = "gTerrainNames"
        with self.assertRaisesRegex(
            RawClosureError,
            "gTerrainNames is not a scoped function or array provider",
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

    def test_reversed_call_site_anchor_relationship_fails_closed(self):
        broken = deepcopy(self.decisions)
        decision = next(
            row
            for row in broken["decisions"]
            if row["import_id"] == "fe8cn.raw.import-0142"
        )
        decision["call_sites"][1]["anchors"] = list(
            reversed(decision["call_sites"][1]["anchors"])
        )
        with self.assertRaisesRegex(RawClosureError, "missing ordered anchors"):
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
            "source snapshot provider_count does not match providers",
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

    def test_tampered_japanese_provider_symbol_fails_source_verification(self):
        broken = deepcopy(self.ja_raw)
        broken["providers"]["0x01C1"]["symbol"] = "GoalString_UnitsLeft_Stale"
        with self.assertRaisesRegex(RawClosureError, "source symbol mismatch"):
            build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=self.decisions,
                ja_raw_provider_data=broken,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )

    def test_tampered_japanese_provider_value_fails_source_verification(self):
        broken = deepcopy(self.ja_raw)
        broken["providers"]["0x01C1"]["text"] = "残"
        with self.assertRaisesRegex(
            RawClosureError,
            "source value does not match catalog text",
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

    def test_tampered_japanese_source_snapshot_hash_fails_closed(self):
        broken = deepcopy(self.ja_raw)
        broken["source_snapshot"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(RawClosureError, "snapshot SHA-256 mismatch"):
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
        decisions = {
            row["target_id"]: row
            for row in self.decisions["decisions"]
            if row.get("target_id") in expected
        }
        for target_id, (symbol, _) in expected.items():
            site = decisions[target_id]["call_sites"][0]
            self.assertEqual(site["scope_kind"], "function")
            self.assertEqual(site["symbol"], "GoalDisplay_Init")
            self.assertEqual(
                site["anchors"],
                [f"GetStringFromIndex(MSG_{int(target_id, 16):X})"],
            )
            self.assertEqual(site["provider_anchor"], symbol)
            self.assertEqual(
                site["provider_scope"],
                {
                    "anchors": [
                        symbol,
                        {
                            "0x01C1": "081F5528",
                            "0x01C2": "081F553C",
                            "0x01C3": "081F5530",
                        }[target_id],
                        "data",
                        "GoalDisplay_Init",
                    ],
                    "path": (
                        "texts/locales/source/fe8j/upstream/"
                        "layout/baseline_syms.d/"
                        "GoalDisplay_Init-134e6b42.tsv"
                    ),
                    "scope_kind": "line",
                    "symbol": symbol,
                },
            )
        providers = load_ja_raw_providers(
            self.ja_raw,
            source_root=ROOT / "texts/locales/ja",
        )
        for target_id, (symbol, text) in expected.items():
            provider = providers[int(target_id, 16)]
            with self.subTest(source_target_id=target_id):
                self.assertEqual(provider.symbol, symbol)
                self.assertEqual(provider.text, text)
                self.assertEqual(
                    provider.source_repository,
                    "https://github.com/laqieer/fireemblem8j",
                )
                self.assertEqual(
                    provider.source_revision,
                    "bf424414d075789d757e2f4cd0cea823bfb2862e",
                )
                self.assertEqual(
                    provider.source_path,
                    (
                        "layout/baseline_syms.d/"
                        "GoalDisplay_Init-134e6b42.tsv"
                    ),
                )
                self.assertEqual(provider.source_anchor, symbol)
                self.assertEqual(provider.provenance_kind, "pinned_baserom_slice")
                self.assertEqual(
                    provider.rom_sha256,
                    "44fd343625ab9e6b90f63a80758c15066d526e6873fae91474006314a5ead464",
                )
                self.assertEqual(provider.decoded_value, text)

    def test_comment_only_extern_and_wrong_definition_are_not_source_data(self):
        fixtures = {
            "comment-only.c": (
                b'extern const char GoalString_UnitsLeft[]; '
                b'/* 081F5528 "residual" */\n'
            ),
            "wrong-definition.c": (
                b'extern const char GoalString_UnitsLeft[];\n'
                b'const char DifferentGoalString[] = "residual";\n'
            ),
            "assignment-only.c": (
                b'GoalString_UnitsLeft = "residual";\n'
            ),
            "line-comment-only.c": (
                b'extern const char GoalString_UnitsLeft[]; '
                b'// "residual"\n'
            ),
            "asm-comment-only.s": (
                b'GoalString_UnitsLeft:\n'
                b'    @ .asciz "residual"\n'
            ),
        }
        for source_path, raw in fixtures.items():
            with self.subTest(source_path=source_path):
                source_blob = GitSourceBlob(
                    path=source_path,
                    oid="1" * 40,
                    vendored_path=source_path,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    raw=raw,
                )
                with self.assertRaisesRegex(
                    RawProviderError,
                    "cannot be materialized",
                ):
                    _extract_source_anchor_values(
                        source_blob,
                        source_anchor="GoalString_UnitsLeft",
                    )

    def test_normal_raw_closure_checks_vendored_git_objects_offline(self):
        with mock.patch(
            "scripts.localization.game_locales.raw_providers._git_output",
            side_effect=AssertionError(
                "normal raw closure must use vendored objects offline"
            ),
        ):
            providers = load_ja_raw_providers(
                self.ja_raw,
                source_root=ROOT / "texts/locales/ja",
            )
            closure = build_raw_surface_closure(
                raw_data=self.raw,
                mapping_data=self.mapping,
                decisions_data=self.decisions,
                ja_raw_provider_data=self.ja_raw,
                registry_data=self.registry,
                catalog_data=self.catalogs,
                repo_root=ROOT,
            )
        self.assertEqual(len(providers), 119)
        self.assertEqual(closure["summary"]["provider_count"], 143)

    def test_japanese_raw_provider_snapshot_is_accessible_and_exact(self):
        specification = self.ja_raw["source_snapshot"]
        path = ROOT / "texts/locales/ja" / specification["path"]
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["source_revision"], self.ja_raw["source_revision"])
        self.assertRegex(snapshot["source_revision"], r"^[0-9a-f]{40}$")
        self.assertNotEqual(snapshot["source_revision"], "0" * 40)
        self.assertIn(snapshot["source_revision"], snapshot["source_url"])
        self.assertEqual(snapshot["provider_count"], 119)
        commit_path = path.parent / snapshot["source_commit"]["path"]
        commit = commit_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(commit).hexdigest(),
            snapshot["source_commit"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha1(
                f"commit {len(commit)}\0".encode("ascii") + commit
            ).hexdigest(),
            snapshot["source_revision"],
        )
        self.assertEqual(snapshot["schema_version"], 6)
        self.assertTrue(snapshot["source_trees"])
        pinned_blobs = {}
        for source_blob in snapshot["source_blobs"]:
            source_path = path.parent / source_blob["vendored_path"]
            raw = source_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(),
                source_blob["sha256"],
            )
            self.assertEqual(
                hashlib.sha1(
                    f"blob {len(raw)}\0".encode("ascii") + raw
                ).hexdigest(),
                source_blob["oid"],
            )
            pinned_blobs[source_blob["path"]] = raw
        artifact = snapshot["provider_values_artifact"]
        self.assertEqual(
            set(artifact["generated_from_paths"]),
            set(pinned_blobs)
            - {
                (
                    "layout/baseline_syms.d/"
                    "GoalDisplay_Init-134e6b42.tsv"
                )
            },
        )
        blob_path = path.parent / artifact["path"]
        blob = blob_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(blob).hexdigest(),
            artifact["sha256"],
        )
        self.assertEqual(artifact["encoding"], "cp932-nul-terminated")
        baserom_source = snapshot["baserom_source"]
        self.assertEqual(
            baserom_source["rom"],
            {
                "sha256": (
                    "44fd343625ab9e6b90f63a80758c15066d526e6873fae91474006314a5ead464"
                ),
                "size": 0x1000000,
            },
        )
        self.assertEqual(
            baserom_source["offset_source"]["path"],
            (
                "layout/baseline_syms.d/"
                "GoalDisplay_Init-134e6b42.tsv"
            ),
        )
        self.assertEqual(
            baserom_source["offset_source"]["blob_oid"],
            "4325b593a941ce95e3821e3746564b2311fe8142",
        )
        goal_artifact = (
            path.parent / baserom_source["artifact"]["path"]
        ).read_bytes()
        self.assertEqual(
            hashlib.sha256(goal_artifact).hexdigest(),
            baserom_source["artifact"]["sha256"],
        )
        for target, source in snapshot["providers"].items():
            with self.subTest(target=target):
                provider = self.ja_raw["providers"][target]
                self.assertEqual(source["symbol"], provider["symbol"])
                self.assertIn(source["source_path"], pinned_blobs)
                self.assertIn(
                    source["source_anchor"].encode("utf-8"),
                    pinned_blobs[source["source_path"]],
                )
                source_artifact = (
                    goal_artifact
                    if source.get("source_format") == "baserom-slice"
                    else blob
                )
                raw_value = source_artifact[
                    source["offset"] : source["offset"] + source["byte_length"]
                ]
                self.assertEqual(
                    hashlib.sha256(raw_value).hexdigest(),
                    source["value_sha256"],
                )
                self.assertTrue(raw_value.endswith(b"\0"))
                self.assertEqual(
                    raw_value[:-1].decode("cp932"),
                    provider["text"],
                )
                self.assertIsInstance(source["source_value_index"], int)
                self.assertGreaterEqual(source["source_value_index"], 0)
        for target in ("0x01C1", "0x01C2", "0x01C3"):
            source = snapshot["providers"][target]
            record = baserom_source["records"][target]
            self.assertEqual(source["source_format"], "baserom-slice")
            self.assertEqual(source["source_anchor"], source["symbol"])
            self.assertEqual(source["source_value_index"], 0)
            self.assertEqual(source["offset"], record["artifact_offset"])
            self.assertEqual(source["byte_length"], record["length"])
            self.assertEqual(source["value_sha256"], record["bytes_sha256"])
            value = goal_artifact[
                record["artifact_offset"] :
                record["artifact_offset"] + record["length"]
            ]
            self.assertEqual(value[:-1].decode("cp932"), record["decoded_value"])
        self.assertTrue(
                all(
                    not provider["symbol"].startswith("gTerrainNames[")
                    for provider in self.ja_raw["providers"].values()
                )
        )
        self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                specification["sha256"],
        )
        self.assertEqual(
                self.ja_raw["providers"]["0x01C4"]["symbol"],
                "gTerrains_0[TERRAIN_NONE]",
        )

    def test_exact_raw_provider_slots_reject_reassignment_and_fake_git_identity(self):
        fixture = ROOT / "build/tests/raw-provider-binding"
        if fixture.exists():
            shutil.rmtree(fixture)
        shutil.copytree(ROOT / "texts/locales/source/fe8j", fixture)
        catalog_path = fixture / "raw.json"
        snapshot_path = fixture / "raw_symbols.json"

        def write_catalog(catalog, snapshot):
            snapshot_bytes = (
                json.dumps(
                    snapshot,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            snapshot_path.write_bytes(snapshot_bytes)
            catalog["source_snapshot"] = {
                "path": "raw_symbols.json",
                "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            }
            catalog_path.write_text(
                json.dumps(
                    catalog,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

        original_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        try:
            wrong_provider_extension_catalog = deepcopy(self.ja_raw)
            wrong_provider_extension_snapshot = deepcopy(original_snapshot)
            wrong_provider_extension_snapshot["provider_values_artifact"][
                "path"
            ] = "raw_provider_values.cp932" + ".bin"
            write_catalog(
                wrong_provider_extension_catalog,
                wrong_provider_extension_snapshot,
            )
            with self.assertRaisesRegex(
                RawProviderError,
                "must use the typed .cp932 extension",
            ):
                load_ja_raw_providers(
                    wrong_provider_extension_catalog,
                    source_root=fixture,
                )

            wrong_goal_extension_catalog = deepcopy(self.ja_raw)
            wrong_goal_extension_snapshot = deepcopy(original_snapshot)
            wrong_goal_extension_snapshot["baserom_source"]["artifact"][
                "path"
            ] = "goal_strings.cp932" + ".bin"
            write_catalog(
                wrong_goal_extension_catalog,
                wrong_goal_extension_snapshot,
            )
            with self.assertRaisesRegex(
                RawProviderError,
                "must use the typed .cp932 extension",
            ):
                load_ja_raw_providers(
                    wrong_goal_extension_catalog,
                    source_root=fixture,
                )

            swapped_catalog = deepcopy(self.ja_raw)
            swapped_snapshot = deepcopy(original_snapshot)
            first = "0x01C4"
            second = "0x01C5"
            swapped_catalog["providers"][first]["text"], swapped_catalog[
                "providers"
            ][second]["text"] = (
                swapped_catalog["providers"][second]["text"],
                swapped_catalog["providers"][first]["text"],
            )
            for field in ("byte_length", "offset", "value_sha256"):
                swapped_snapshot["providers"][first][field], swapped_snapshot[
                    "providers"
                ][second][field] = (
                    swapped_snapshot["providers"][second][field],
                    swapped_snapshot["providers"][first][field],
                )
            write_catalog(swapped_catalog, swapped_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "differs from exact",
            ):
                load_ja_raw_providers(
                    swapped_catalog,
                    source_root=fixture,
                )

            wrong_index_catalog = deepcopy(self.ja_raw)
            wrong_index_snapshot = deepcopy(original_snapshot)
            wrong_index_snapshot["providers"][first]["source_value_index"] = (
                wrong_index_snapshot["providers"][second][
                    "source_value_index"
                ]
            )
            write_catalog(wrong_index_catalog, wrong_index_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "differs from exact",
            ):
                load_ja_raw_providers(
                    wrong_index_catalog,
                    source_root=fixture,
                )

            wrong_goal_index_catalog = deepcopy(self.ja_raw)
            wrong_goal_index_snapshot = deepcopy(original_snapshot)
            wrong_goal_index_snapshot["providers"]["0x01C1"][
                "source_value_index"
            ] = 1
            write_catalog(wrong_goal_index_catalog, wrong_goal_index_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "baserom metadata differs",
            ):
                load_ja_raw_providers(
                    wrong_goal_index_catalog,
                    source_root=fixture,
                )

            goal_artifact_path = (
                fixture
                / original_snapshot["baserom_source"]["artifact"]["path"]
            )
            goal_artifact = goal_artifact_path.read_bytes()
            goal_artifact_path.write_bytes(
                bytes([goal_artifact[0] ^ 1]) + goal_artifact[1:]
            )
            altered_byte_catalog = deepcopy(self.ja_raw)
            write_catalog(altered_byte_catalog, deepcopy(original_snapshot))
            with self.assertRaisesRegex(
                RawProviderError,
                "baserom artifact SHA-256 mismatch",
            ):
                load_ja_raw_providers(
                    altered_byte_catalog,
                    source_root=fixture,
                )
            goal_artifact_path.write_bytes(goal_artifact)

            rebound_catalog = deepcopy(self.ja_raw)
            rebound_snapshot = deepcopy(original_snapshot)
            rebound_goal_artifact = (
                "偽り\0".encode("cp932") + goal_artifact[5:]
            )
            rebound_goal_sha256 = hashlib.sha256(
                rebound_goal_artifact
            ).hexdigest()
            rebound_value_sha256 = hashlib.sha256(
                rebound_goal_artifact[:5]
            ).hexdigest()
            goal_artifact_path.write_bytes(rebound_goal_artifact)
            rebound_snapshot["baserom_source"]["artifact"][
                "sha256"
            ] = rebound_goal_sha256
            rebound_snapshot["baserom_source"]["records"]["0x01C1"].update(
                {
                    "bytes_sha256": rebound_value_sha256,
                    "decoded_value": "偽り",
                }
            )
            rebound_snapshot["providers"]["0x01C1"][
                "value_sha256"
            ] = rebound_value_sha256
            rebound_catalog["providers"]["0x01C1"]["text"] = "偽り"
            write_catalog(rebound_catalog, rebound_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "origin proof.*ranges differ",
            ):
                load_ja_raw_providers(
                    rebound_catalog,
                    source_root=fixture,
                )
            goal_artifact_path.write_bytes(goal_artifact)

            altered_offset_catalog = deepcopy(self.ja_raw)
            altered_offset_snapshot = deepcopy(original_snapshot)
            altered_offset_snapshot["baserom_source"]["records"]["0x01C1"][
                "rom_offset"
            ] += 1
            write_catalog(altered_offset_catalog, altered_offset_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "ROM offset differs from the pinned baseline map",
            ):
                load_ja_raw_providers(
                    altered_offset_catalog,
                    source_root=fixture,
                )

            altered_rom_catalog = deepcopy(self.ja_raw)
            altered_rom_snapshot = deepcopy(original_snapshot)
            altered_rom_snapshot["baserom_source"]["rom"]["sha256"] = "0" * 64
            write_catalog(altered_rom_catalog, altered_rom_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "independently pinned FE8J ROM",
            ):
                load_ja_raw_providers(
                    altered_rom_catalog,
                    source_root=fixture,
                )

            nested_manifest_catalog = deepcopy(self.ja_raw)
            nested_manifest_snapshot = deepcopy(original_snapshot)
            nested_manifest_snapshot["additional_git_sources"] = {
                "fake-generated-manifest": {}
            }
            write_catalog(nested_manifest_catalog, nested_manifest_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "nested generated manifests are not accepted",
            ):
                load_ja_raw_providers(
                    nested_manifest_catalog,
                    source_root=fixture,
                )

            fake_commit_catalog = deepcopy(self.ja_raw)
            fake_commit_snapshot = deepcopy(original_snapshot)
            fake_commit = (
                fixture / fake_commit_snapshot["source_commit"]["path"]
            ).read_bytes() + b"\nfabricated\n"
            fake_revision = hashlib.sha1(
                f"commit {len(fake_commit)}\0".encode("ascii") + fake_commit
            ).hexdigest()
            (
                fixture / fake_commit_snapshot["source_commit"]["path"]
            ).write_bytes(fake_commit)
            fake_commit_snapshot["source_commit"]["sha256"] = hashlib.sha256(
                fake_commit
            ).hexdigest()
            fake_commit_snapshot["source_revision"] = fake_revision
            fake_commit_snapshot["source_url"] = (
                "https://github.com/laqieer/fireemblem8j/tree/"
                + fake_revision
            )
            fake_commit_catalog["source_revision"] = fake_revision
            write_catalog(fake_commit_catalog, fake_commit_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "independently pinned FE8J commit",
            ):
                load_ja_raw_providers(
                    fake_commit_catalog,
                    source_root=fixture,
                )

            original_commit_path = original_snapshot["source_commit"]["path"]
            (fixture / original_commit_path).write_bytes(
                (
                    ROOT
                    / "texts/locales/source/fe8j"
                    / original_commit_path
                ).read_bytes()
            )
            fake_repository_catalog = deepcopy(self.ja_raw)
            fake_repository_snapshot = deepcopy(original_snapshot)
            fake_repository_snapshot["source_repository"] = (
                "https://github.com/example/fabricated"
            )
            fake_repository_snapshot["source_url"] = (
                "https://github.com/example/fabricated/tree/"
                + fake_repository_snapshot["source_revision"]
            )
            write_catalog(
                fake_repository_catalog,
                fake_repository_snapshot,
            )
            with self.assertRaisesRegex(
                RawProviderError,
                "independently pinned FE8J repository",
            ):
                load_ja_raw_providers(
                    fake_repository_catalog,
                    source_root=fixture,
                )

            fake_blob_catalog = deepcopy(self.ja_raw)
            fake_blob_snapshot = deepcopy(original_snapshot)
            source_blob = fake_blob_snapshot["source_blobs"][0]
            source_path = fixture / source_blob["vendored_path"]
            fake_blob = source_path.read_bytes() + b"\n/* fabricated */\n"
            source_path.write_bytes(fake_blob)
            source_blob["oid"] = hashlib.sha1(
                f"blob {len(fake_blob)}\0".encode("ascii") + fake_blob
            ).hexdigest()
            source_blob["sha256"] = hashlib.sha256(fake_blob).hexdigest()
            write_catalog(fake_blob_catalog, fake_blob_snapshot)
            with self.assertRaisesRegex(
                RawProviderError,
                "pinned commit path/blob mismatch",
            ):
                load_ja_raw_providers(
                    fake_blob_catalog,
                    source_root=fixture,
                )
        finally:
            shutil.rmtree(fixture)

    def test_git_origin_fixture_rejects_zero_missing_commit_and_arbitrary_blob(self):
        fixture = ROOT / "build/tests/raw-provider-origin"
        if fixture.exists():
                shutil.rmtree(fixture)
        fixture.mkdir(parents=True)
        repository = fixture / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Fixture"],
                check=True,
        )
        subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "config",
                    "user.email",
                    "fixture@example.com",
                ],
                check=True,
        )
        source_path = repository / "src/provider.c"
        source_path.parent.mkdir()
        source_path.write_text(
                'const char FixtureProvider[] = "fixture";\n',
                encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(
                ["git", "-C", str(repository), "commit", "-q", "-m", "fixture"],
                check=True,
        )
        revision = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                text=True,
        ).strip()
        source_oid = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", f"{revision}:src/provider.c"],
                text=True,
        ).strip()
        root_tree_oid = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", f"{revision}^{{tree}}"],
                text=True,
        ).strip()
        src_tree_oid = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", f"{revision}:src"],
                text=True,
        ).strip()
        root_tree_raw = subprocess.check_output(
                ["git", "-C", str(repository), "cat-file", "tree", root_tree_oid]
        )
        src_tree_raw = subprocess.check_output(
                ["git", "-C", str(repository), "cat-file", "tree", src_tree_oid]
        )
        source_raw = source_path.read_bytes()
        commit_raw = subprocess.check_output(
                ["git", "-C", str(repository), "cat-file", "commit", revision]
        )

        catalog_root = fixture / "catalog"
        catalog_root.mkdir()
        (catalog_root / "source.c").write_bytes(source_raw)
        (catalog_root / "commit.txt").write_bytes(commit_raw)
        (catalog_root / "root.tree").write_bytes(root_tree_raw)
        (catalog_root / "src.tree").write_bytes(src_tree_raw)
        value_raw = b"fixture\0"
        (catalog_root / "values.cp932").write_bytes(value_raw)
        snapshot = {
                "kind": "fe8j-raw-symbol-source-snapshot",
                "provider_count": 1,
                "provider_values_artifact": {
                    "encoding": "cp932-nul-terminated",
                    "generated_from_paths": ["src/provider.c"],
                    "path": "values.cp932",
                    "sha256": hashlib.sha256(value_raw).hexdigest(),
                },
                "providers": {
                    "0x0001": {
                        "byte_length": len(value_raw),
                        "offset": 0,
                        "source_anchor": "FixtureProvider",
                        "source_path": "src/provider.c",
                        "source_value_index": 0,
                        "symbol": "FixtureProvider",
                        "value_sha256": hashlib.sha256(value_raw).hexdigest(),
                    }
                },
                "schema_version": 6,
                "source_blobs": [
                    {
                        "oid": source_oid,
                        "path": "src/provider.c",
                        "sha256": hashlib.sha256(source_raw).hexdigest(),
                        "vendored_path": "source.c",
                    }
                ],
                "source_commit": {
                    "path": "commit.txt",
                    "sha256": hashlib.sha256(commit_raw).hexdigest(),
                },
                "source_trees": [
                    {
                        "oid": root_tree_oid,
                        "path": "",
                        "sha256": hashlib.sha256(root_tree_raw).hexdigest(),
                        "vendored_path": "root.tree",
                    },
                    {
                        "oid": src_tree_oid,
                        "path": "src",
                        "sha256": hashlib.sha256(src_tree_raw).hexdigest(),
                        "vendored_path": "src.tree",
                    },
                ],
                "source_repository": "https://github.com/example/fixture",
                "source_revision": revision,
                "source_url": f"https://github.com/example/fixture/tree/{revision}",
        }

        def catalog_for(source_snapshot):
                snapshot_bytes = (
                    json.dumps(
                        source_snapshot,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
                (catalog_root / "snapshot.json").write_bytes(snapshot_bytes)
                return {
                    "kind": "fe8j-raw-provider-catalog",
                    "locale_id": "ja",
                    "provider_count": 1,
                    "providers": {
                        "0x0001": {
                            "symbol": "FixtureProvider",
                            "text": "fixture",
                        }
                    },
                    "schema_version": 6,
                    "source_layout": "FE8J-raw-symbol",
                    "source_revision": source_snapshot["source_revision"],
                    "source_snapshot": {
                        "path": "snapshot.json",
                        "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                    },
                }

        try:
                catalog = catalog_for(snapshot)
                load_ja_raw_providers(
                    catalog,
                    source_root=catalog_root,
                    expected_repository=None,
                    expected_revision=None,
                )
                verify_ja_raw_provider_git_source(
                    catalog,
                    source_root=catalog_root,
                    repository=repository,
                )

                mismatch = deepcopy(snapshot)
                mismatch_value = "値".encode("cp932") + b"\0"
                (catalog_root / "values.cp932").write_bytes(mismatch_value)
                mismatch["provider_values_artifact"]["sha256"] = hashlib.sha256(
                    mismatch_value
                ).hexdigest()
                mismatch["providers"]["0x0001"]["byte_length"] = len(
                    mismatch_value
                )
                mismatch["providers"]["0x0001"]["value_sha256"] = hashlib.sha256(
                    mismatch_value
                ).hexdigest()
                mismatch_catalog = catalog_for(mismatch)
                mismatch_catalog["providers"]["0x0001"]["text"] = "値"
                with self.assertRaisesRegex(
                    RawProviderError,
                    "differs from exact",
                ):
                    load_ja_raw_providers(
                        mismatch_catalog,
                        source_root=catalog_root,
                        expected_repository=None,
                        expected_revision=None,
                    )
                (catalog_root / "values.cp932").write_bytes(value_raw)

                zero = deepcopy(snapshot)
                zero["source_revision"] = "0" * 40
                zero_catalog = catalog_for(zero)
                with self.assertRaisesRegex(RawProviderError, "nonzero full Git OID"):
                    load_ja_raw_providers(
                        zero_catalog,
                        source_root=catalog_root,
                        expected_repository=None,
                        expected_revision=None,
                    )

                missing = deepcopy(snapshot)
                fake_commit = commit_raw + b"\nmissing\n"
                fake_revision = hashlib.sha1(
                    f"commit {len(fake_commit)}\0".encode("ascii") + fake_commit
                ).hexdigest()
                (catalog_root / "commit.txt").write_bytes(fake_commit)
                missing["source_revision"] = fake_revision
                missing["source_url"] = (
                    f"https://github.com/example/fixture/tree/{fake_revision}"
                )
                missing["source_commit"]["sha256"] = hashlib.sha256(
                    fake_commit
                ).hexdigest()
                missing_catalog = catalog_for(missing)
                load_ja_raw_providers(
                    missing_catalog,
                    source_root=catalog_root,
                    expected_repository=None,
                    expected_revision=None,
                )
                with self.assertRaisesRegex(
                    RawProviderError,
                    "git source verification failed",
                ):
                    verify_ja_raw_provider_git_source(
                        missing_catalog,
                        source_root=catalog_root,
                        repository=repository,
                    )

                (catalog_root / "commit.txt").write_bytes(commit_raw)
                arbitrary = deepcopy(snapshot)
                arbitrary_raw = source_raw + b"/* arbitrary FixtureProvider */\n"
                (catalog_root / "source.c").write_bytes(arbitrary_raw)
                arbitrary["source_blobs"][0]["oid"] = hashlib.sha1(
                    f"blob {len(arbitrary_raw)}\0".encode("ascii") + arbitrary_raw
                ).hexdigest()
                arbitrary["source_blobs"][0]["sha256"] = hashlib.sha256(
                    arbitrary_raw
                ).hexdigest()
                arbitrary_catalog = catalog_for(arbitrary)
                with self.assertRaisesRegex(
                    RawProviderError,
                    "pinned commit path/blob mismatch",
                ):
                    load_ja_raw_providers(
                        arbitrary_catalog,
                        source_root=catalog_root,
                        expected_repository=None,
                        expected_revision=None,
                    )
        finally:
                shutil.rmtree(fixture)


if __name__ == "__main__":
    unittest.main()
