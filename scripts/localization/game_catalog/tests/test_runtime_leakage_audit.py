import sys
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.build import build_game_catalog
from scripts.localization.game_catalog.leakage import (
    DEFAULT_RAW_CLOSURE_PATH,
    DEFAULT_REPORT_PATH,
    DEFAULT_REVIEW_PATH,
    _classify_candidate,
    _latin_span_counts,
    build_leakage_report,
    canonical_json_bytes,
    input_record,
    load_expansion_catalogs,
    load_raw_closure,
    load_review,
)
from scripts.localization.game_catalog.model import GameCatalogError


class RuntimeLeakageAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = build_game_catalog()
        cls.review = load_review(DEFAULT_REVIEW_PATH)
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
            review=cls.review,
            raw_closure=cls.raw_closure,
            expansion_catalogs=cls.expansion_catalogs,
            inputs={
                name: input_record(path, repo_root=ROOT)
                for name, path in sorted(paths.items())
            },
        )

    def _build_report(self, review):
        return build_leakage_report(
            self.build,
            review=review,
            raw_closure=self.raw_closure,
            expansion_catalogs=self.expansion_catalogs,
            inputs={},
        )

    def test_committed_report_matches_full_materialized_span_audit(self):
        self.assertEqual(
            self.review.baseline_commit,
            "2d02ec12ddcba0640bcf1ecffcf7dc93d56deb4f",
        )
        self.assertEqual(
            (ROOT / DEFAULT_REPORT_PATH).read_bytes(),
            canonical_json_bytes(self.report),
        )
        self.assertEqual(self.report["summary"]["game_payload_count"], 3414 * 2)
        self.assertEqual(self.report["summary"]["raw_surface_payload_count"], 143 * 2)
        self.assertEqual(self.report["summary"]["unapproved_span_count"], 0)
        self.assertEqual(
            self.report["summary"]["unapproved_span_occurrence_count"], 0
        )
        self.assertEqual(self.report["summary"]["payload_mismatch_count"], 0)
        self.assertEqual(self.report["summary"]["stale_decision_count"], 0)
        self.assertEqual(
            self.report["baseline_review"]["summary"]["ja"][
                "mixed_script_bypass_payload_count"
            ],
            125,
        )
        self.assertEqual(
            self.report["baseline_review"]["summary"]["zh-Hans"][
                "mixed_script_bypass_payload_count"
            ],
            139,
        )
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

    def test_every_current_latin_span_has_an_exact_approval(self):
        expected_payload_counts = {"ja": 159, "zh-Hans": 161}
        for scope in ("game_catalog", "raw_surface"):
            for locale, report in self.report[scope]["locales"].items():
                self.assertEqual(report["unapproved_span_count"], 0, locale)
                self.assertEqual(report["payload_mismatch_count"], 0, locale)
                if scope == "game_catalog":
                    self.assertEqual(
                        report["latin_bearing_payload_count"],
                        expected_payload_counts[locale],
                    )
                for payload in report["latin_bearing_payloads"]:
                    self.assertTrue(payload["payload_matches_review"])
                    for span in payload["spans"]:
                        self.assertTrue(span["approved"])
                        self.assertEqual(span["review"]["decision"], "approved")
                        self.assertTrue(span["review"]["category"])
                        self.assertTrue(span["review"]["reason"])
                        self.assertTrue(span["review"]["source"])

    def test_mixed_script_examples_are_tokenized_after_control_stripping(self):
        self.assertEqual(_latin_span_counts("最大ＨＰが上昇"), Counter({"HP": 1}))
        self.assertEqual(
            _latin_span_counts("最大ＨＰ[CTRL:0001]ＨＰの上限"),
            Counter({"HP": 2}),
        )
        classifications, _ = _classify_candidate("最大ＨＰが上昇", "Maximum HP up")
        self.assertEqual(classifications, ())

    def test_required_and_discovered_english_is_localized(self):
        expected_ja = {
            target_id: "ＦＥ３ダミーメッセージ[CTRL:0003]"
            for target_id in range(0x0839, 0x0840)
        }
        expected_zh = {
            0x002B: "地图查看器",
            0x002C: "头像查看器",
            0x002D: "会话背景查看器",
            0x01A2: "击破首领：奥尼尔",
            0x01A7: "击破首领：萨尔",
            0x01A9: "击破首领：诺贝拉",
            0x01B4: "击破首领：利昂",
            0x01BD: "击破首领",
            0x0635: "打倒在地图上的敌军首领后，\n就能通过这一关。\n不需要打倒所有的敌人。",
            0x0922: (
                "本章只要将敌方首领打败\n"
                "就可以过关。[CTRL:0103][CTRL:0080][CTRL:0021]"
                "过关条件[CTRL:0080][CTRL:0021]会在画面\n"
                "的一角表示出来。[CTRL:0103]希望每到新的章节时，\n"
                "大家都能够看看。[CTRL:0103]那么，开始展开"
                "[CTRL:0080][CTRL:0021]攻击[CTRL:0080][CTRL:0021]"
                "吧。[CTRL:0003]"
            ),
        }
        expected_zh.update(
            {
                target_id: "FE3占位消息[CTRL:0003]"
                for target_id in range(0x0839, 0x0840)
            }
        )
        actual_ja = {
            entry.target_id: entry.source_text
            for entry in self.build.locale_bundle("ja").entries
        }
        actual_zh = {
            entry.target_id: entry.source_text
            for entry in self.build.locale_bundle("zh-Hans").entries
        }
        for target_id, payload in expected_ja.items():
            self.assertEqual(actual_ja[target_id], payload)
            self.assertNotIn("MSG", _latin_span_counts(payload))
        for target_id, payload in expected_zh.items():
            self.assertEqual(actual_zh[target_id], payload)
        self.assertNotIn("OK", _latin_span_counts(actual_zh[0x0CC3]))
        self.assertIn("只要按照同样的办法就行了", actual_zh[0x0CC3])
        self.assertNotIn("就就行了", actual_zh[0x0CC3])

    def test_missing_exact_target_locale_span_approval_fails_closed(self):
        reviews = dict(self.review.reviews)
        key = "game/ja/0x001C"
        target = reviews[key]
        spans = dict(target.spans)
        spans.pop("HP")
        reviews[key] = replace(target, spans=spans)
        incomplete = replace(self.review, reviews=reviews)
        with self.assertRaisesRegex(GameCatalogError, "unapproved Latin span"):
            self._build_report(incomplete)

    def test_stale_approval_or_payload_pin_fails_closed(self):
        reviews = dict(self.review.reviews)
        key = "game/ja/0x001C"
        reviews[key] = replace(
            reviews[key],
            current_payload_sha256="0" * 64,
        )
        stale = replace(self.review, reviews=reviews)
        with self.assertRaisesRegex(
            GameCatalogError, "payload mismatch.*stale span decision"
        ):
            self._build_report(stale)


if __name__ == "__main__":
    unittest.main()
