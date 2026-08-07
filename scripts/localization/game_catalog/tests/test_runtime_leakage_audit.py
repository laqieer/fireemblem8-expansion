import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.build import build_game_catalog
from scripts.localization.game_catalog.leakage import (
    DEFAULT_ALLOWLIST_PATH,
    DEFAULT_RAW_CLOSURE_PATH,
    DEFAULT_REPORT_PATH,
    Allowlist,
    _classify_candidate,
    build_leakage_report,
    canonical_json_bytes,
    input_record,
    load_allowlist,
    load_expansion_catalogs,
    load_raw_closure,
)
from scripts.localization.game_catalog.model import GameCatalogError


class RuntimeLeakageAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = build_game_catalog()
        cls.allowlist = load_allowlist(DEFAULT_ALLOWLIST_PATH)
        cls.raw_closure = load_raw_closure(DEFAULT_RAW_CLOSURE_PATH)
        cls.expansion_catalogs = load_expansion_catalogs(
            ROOT / "texts/expansion",
            cls.build.enabled_locales,
        )
        paths = {
            "english_definitions": ROOT / "texts/textdefs.txt",
            "english_texts": ROOT / "texts/texts.txt",
            "en_expansion": ROOT / "texts/expansion/catalog.en.json",
            "ja_authored": ROOT / "texts/locales/authored/catalog.ja.json",
            "ja_expansion": ROOT / "texts/expansion/catalog.ja.json",
            "ja_indexed": ROOT / "texts/locales/ja/indexed.txt",
            "ja_raw": ROOT / "texts/locales/ja/raw.json",
            "mapping": ROOT / "texts/locales/mapping/fe8u_target_map.json",
            "raw_closure": ROOT / DEFAULT_RAW_CLOSURE_PATH,
            "target_header": ROOT / "include/constants/msg.h",
            "zh-Hans_authored": (
                ROOT / "texts/locales/authored/catalog.zh-Hans.json"
            ),
            "zh-Hans_expansion": ROOT / "texts/expansion/catalog.zh-Hans.json",
            "zh_hans_indexed": ROOT / "texts/locales/zh-Hans/indexed.txt",
            "zh_hans_raw": ROOT / "texts/locales/zh-Hans/raw.json",
        }
        cls.report = build_leakage_report(
            cls.build,
            allowlist=cls.allowlist,
            raw_closure=cls.raw_closure,
            expansion_catalogs=cls.expansion_catalogs,
            inputs={
                name: input_record(path, repo_root=ROOT)
                for name, path in sorted(paths.items())
            },
        )

    def test_committed_report_matches_full_materialized_audit(self):
        self.assertEqual(
            (ROOT / DEFAULT_REPORT_PATH).read_bytes(),
            canonical_json_bytes(self.report),
        )
        self.assertEqual(self.report["summary"]["game_payload_count"], 3414 * 2)
        self.assertEqual(self.report["summary"]["raw_surface_payload_count"], 143 * 2)
        self.assertEqual(self.report["summary"]["unapproved_candidate_count"], 0)
        self.assertEqual(self.report["summary"]["stale_approval_count"], 0)
        self.assertEqual(
            self.report["game_catalog"]["mapping_source_counts"],
            {
                "authored": 262,
                "english_fallback": 0,
                "indexed": 3010,
                "raw": 142,
                "unresolved": 0,
            },
        )

    def test_all_remaining_latin_only_payloads_are_explicitly_approved(self):
        for scope in ("game_catalog", "raw_surface"):
            for locale, report in self.report[scope]["locales"].items():
                self.assertEqual(report["unapproved_count"], 0, locale)
                self.assertEqual(report["candidate_count"], report["approved_count"])
                for candidate in report["candidates"]:
                    self.assertTrue(candidate["approved"])
                    self.assertIn("approval", candidate)
                    self.assertTrue(candidate["approval"]["payload_matches"])
                    self.assertTrue(candidate["approval"]["reason"])
                    self.assertTrue(
                        candidate["approval"]["category"].startswith("locale-neutral-")
                    )

    def test_known_and_audit_discovered_titles_are_not_latin_payloads(self):
        expected = {
            "ja": {
                0x00CC: "データなし",
                0x077D: "ロード中",
                0x078D: "序章",
                0x0795: "ついて来い！",
                0x07AB: "癒やし",
                0x07AC: "浄化",
                0x07B2: "憤怒",
                0x07B3: "悲しみの時",
                0x07B4: "おどけたひととき",
                0x07BA: "謎を解く",
                0x07CA: "ゲームオーバー",
                0x07CE: "風とともに",
                0x07CF: "終章",
            },
            "zh-Hans": {
                0x00CC: "无数据",
                0x077D: "正在载入",
                0x078C: "火焰之纹章主题曲",
                0x078D: "序章",
                0x0795: "跟我来！",
                0x07AB: "治疗",
                0x07AC: "净化",
                0x07B2: "愤怒",
                0x07B3: "悲伤时刻",
                0x07B4: "诙谐时刻",
                0x07BA: "解开谜题",
                0x07CA: "游戏结束",
                0x07CE: "随风而行",
                0x07CF: "尾声",
            },
        }
        for locale, entries in expected.items():
            actual = {
                entry.target_id: entry.source_text
                for entry in self.build.locale_bundle(locale).entries
            }
            for target_id, payload in entries.items():
                self.assertEqual(actual[target_id], payload)
                classifications, _ = _classify_candidate(
                    payload,
                    self.build.english.entries[target_id].source_text,
                )
                self.assertEqual(classifications, ())

    def test_missing_exact_approval_fails_closed(self):
        approvals = dict(self.allowlist.approvals)
        approvals.pop("game/ja/0x0086")
        incomplete = Allowlist(
            path=self.allowlist.path,
            sha256=self.allowlist.sha256,
            byte_count=self.allowlist.byte_count,
            approvals=approvals,
        )
        with self.assertRaisesRegex(GameCatalogError, "unapproved Latin/English"):
            build_leakage_report(
                self.build,
                allowlist=incomplete,
                raw_closure=self.raw_closure,
                expansion_catalogs=self.expansion_catalogs,
                inputs={},
            )

    def test_near_copy_detector_catches_punctuation_or_typo_variants(self):
        classifications, similarity = _classify_candidate("Game Ove!", "Game Over")
        self.assertIn("near-english-copy", classifications)
        self.assertIn("latin-only-payload", classifications)
        self.assertGreaterEqual(similarity, 0.80)


if __name__ == "__main__":
    unittest.main()
