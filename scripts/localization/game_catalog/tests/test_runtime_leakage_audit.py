import hashlib
import sys
import unittest
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.build import build_game_catalog
from scripts.localization.game_catalog.leakage import (
    DEFAULT_RAW_CLOSURE_PATH,
    DEFAULT_REPORT_PATH,
    DEFAULT_REVIEW_PATH,
    DEFAULT_SCRIPT_REVIEW_PATH,
    _classify_candidate,
    _latin_span_counts,
    _payload_artifacts,
    build_leakage_report,
    canonical_json_bytes,
    input_record,
    load_expansion_catalogs,
    load_raw_closure,
    load_review,
    load_script_review,
)
from scripts.localization.game_catalog.model import GameCatalogError


class RuntimeLeakageAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = build_game_catalog()
        cls.review = load_review(DEFAULT_REVIEW_PATH)
        cls.script_review = load_script_review(DEFAULT_SCRIPT_REVIEW_PATH)
        cls.raw_closure = load_raw_closure(DEFAULT_RAW_CLOSURE_PATH)
        cls.expansion_catalogs = load_expansion_catalogs(
            ROOT / "texts/expansion",
            cls.build.enabled_locales,
        )
        paths = {
            "english_definitions": ROOT / "texts/textdefs.txt",
            "english_texts": ROOT / "texts/texts.txt",
            "en_expansion": ROOT / "texts/expansion/catalog.en.json",
            "fixed_width_display_aliases": (
                ROOT / "texts/locales/fixed_width_display_aliases.json"
            ),
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
            script_review=cls.script_review,
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
            script_review=self.script_review,
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
        self.assertEqual(
            self.report["summary"]["display_alias_payload_count"],
            120,
        )
        self.assertEqual(self.report["summary"]["unapproved_span_count"], 0)
        self.assertEqual(
            self.report["summary"]["unapproved_span_occurrence_count"], 0
        )
        self.assertEqual(self.report["summary"]["payload_mismatch_count"], 0)
        self.assertEqual(self.report["summary"]["artifact_payload_count"], 0)
        self.assertEqual(
            self.report["summary"]["replacement_character_count"],
            0,
        )
        self.assertEqual(self.report["summary"]["c1_control_count"], 0)
        self.assertEqual(
            self.report["summary"]["mojibake_occurrence_count"],
            0,
        )
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
            135,
        )
        self.assertEqual(
            self.report["game_catalog"]["mapping_source_counts"],
            {
                "authored": 329,
                "english_fallback": 0,
                "indexed": 2955,
                "raw": 130,
                "unresolved": 0,
            },
        )

    def test_every_current_latin_span_has_an_exact_approval(self):
        expected_payload_counts = {"ja": 168, "zh-Hans": 164}
        for scope in ("game_catalog", "raw_surface", "display_aliases"):
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

    def test_unicode_latin_and_mojibake_bypasses_are_detected(self):
        self.assertEqual(_latin_span_counts("café"), Counter({"café": 1}))
        self.assertEqual(_latin_span_counts("東京"), Counter())
        classifications, similarity = _classify_candidate("Café", "Cafe")
        self.assertIn("exact-english-copy", classifications)
        self.assertEqual(similarity, 1.0)

        examples = {
            "replacement": "\uFFFD",
            "c1": "\u0085",
            "latin1": "Ã©",
            "windows1252": "â€™",
            "cjk_utf8_as_1252": "æ—¥",
        }
        for label, payload in examples.items():
            with self.subTest(label=label):
                artifacts = _payload_artifacts(payload)
                self.assertTrue(
                    artifacts["replacement_character_count"]
                    or artifacts["c1_control_count"]
                    or artifacts["mojibake_occurrence_count"]
                )

    def test_final_payload_gate_rejects_unicode_latin_and_artifact_bypasses(self):
        raw_target_ids = {
            int(target_id, 16)
            for row in self.raw_closure["rows"]
            for target_id in row.get("target_ids", [])
        }
        reviewed_target_ids = {
            int(review.key.rsplit("/", 1)[1], 16)
            for review in self.review.reviews.values()
            if review.key.startswith("game/ja/")
        }
        target_id = next(
            entry.target_id
            for entry in self.build.locale_bundle("ja").entries
            if entry.target_id not in raw_target_ids
            and entry.target_id not in reviewed_target_ids
            and not _latin_span_counts(entry.source_text or "")
        )
        ja_bundle = self.build.locale_bundle("ja")
        original = ja_bundle.entries[target_id]
        for label, payload, diagnostic in (
            ("unicode-latin", "café", "unapproved Latin span"),
            ("replacement", "\uFFFD", "replacement/C1/mojibake"),
            ("c1", "\u0085", "replacement/C1/mojibake"),
            ("latin1", "Ã©", "replacement/C1/mojibake"),
            ("windows1252", "â€™", "replacement/C1/mojibake"),
            ("cjk-utf8-as-1252", "æ—¥", "replacement/C1/mojibake"),
        ):
            with self.subTest(label=label):
                entries = list(ja_bundle.entries)
                entries[target_id] = replace(original, source_text=payload)
                modified_bundle = replace(ja_bundle, entries=tuple(entries))
                modified_build = replace(
                    self.build,
                    locales=(
                        modified_bundle,
                        self.build.locale_bundle("zh-Hans"),
                    ),
                )
                with self.assertRaisesRegex(GameCatalogError, diagnostic):
                    build_leakage_report(
                        modified_build,
                        review=self.review,
                        script_review=self.script_review,
                        raw_closure=self.raw_closure,
                        expansion_catalogs=self.expansion_catalogs,
                        inputs={},
                    )

    def test_display_alias_payloads_are_part_of_the_final_leakage_gate(self):
        aliases = {
            locale: {
                surface: dict(entries)
                for surface, entries in surfaces.items()
            }
            for locale, surfaces in self.build.display_aliases.items()
        }
        aliases["ja"]["item_name_56"][0x03AD] = "café"
        modified = replace(self.build, display_aliases=aliases)
        with self.assertRaisesRegex(GameCatalogError, "unapproved Latin span"):
            build_leakage_report(
                modified,
                review=self.review,
                script_review=self.script_review,
                raw_closure=self.raw_closure,
                expansion_catalogs=self.expansion_catalogs,
                inputs={},
            )

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
            self.assertEqual(
                actual_zh[target_id],
                payload.replace("\n", "[CTRL:0001]"),
            )
        self.assertNotIn("OK", _latin_span_counts(actual_zh[0x0CC3]))
        self.assertIn("只要按照同样的办法就行了", actual_zh[0x0CC3])
        self.assertNotIn("就就行了", actual_zh[0x0CC3])

    def test_script_allowlist_and_exact_math_approvals_are_closed(self):
        summary = self.report["summary"]
        self.assertEqual(summary["disallowed_script_symbol_count"], 0)
        self.assertEqual(summary["unapproved_script_symbol_count"], 0)
        self.assertEqual(summary["script_payload_mismatch_count"], 0)
        self.assertEqual(summary["stale_script_approval_count"], 0)
        self.assertGreater(summary["approved_script_symbol_count"], 0)

    def test_missing_exact_math_target_approval_fails_closed(self):
        approvals = dict(self.script_review.approvals)
        key = next(
            key for key in approvals if key.startswith("game/ja/")
        )
        approvals.pop(key)
        incomplete = replace(
            self.script_review,
            approvals=approvals,
        )
        with self.assertRaisesRegex(
            GameCatalogError,
            "unapproved Greek/math symbol",
        ):
            build_leakage_report(
                self.build,
                review=self.review,
                script_review=incomplete,
                raw_closure=self.raw_closure,
                expansion_catalogs=self.expansion_catalogs,
                inputs={},
            )

    def test_cyrillic_confusable_is_rejected_in_game_raw_and_alias_payloads(self):
        cyrillic_ok = "\u041e\u041a"

        ja_bundle = self.build.locale_bundle("ja")
        raw_target_ids = {
            int(target_id, 16)
            for row in self.raw_closure["rows"]
            for target_id in row.get("target_ids", [])
        }
        target_id = next(
            entry.target_id
            for entry in ja_bundle.entries
            if entry.target_id not in raw_target_ids
        )
        entries = list(ja_bundle.entries)
        entries[target_id] = replace(
            entries[target_id],
            source_text=cyrillic_ok,
        )
        modified_game = replace(
            self.build,
            locales=(
                replace(ja_bundle, entries=tuple(entries)),
                self.build.locale_bundle("zh-Hans"),
            ),
        )
        with self.assertRaisesRegex(
            GameCatalogError,
            "disallowed Unicode script",
        ):
            build_leakage_report(
                modified_game,
                review=self.review,
                script_review=self.script_review,
                raw_closure=self.raw_closure,
                expansion_catalogs=self.expansion_catalogs,
                inputs={},
            )

        modified_closure = deepcopy(self.raw_closure)
        modified_expansion = deepcopy(self.expansion_catalogs)
        expansion_row = next(
            row
            for row in modified_closure["rows"]
            if row["classification"] == "expansion_message"
        )
        expansion_key = expansion_row["expansion_key"]
        modified_expansion["ja"][expansion_key] = cyrillic_ok
        expansion_row["providers"]["ja"]["text_sha256"] = hashlib.sha256(
            cyrillic_ok.encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(
            GameCatalogError,
            "disallowed Unicode script",
        ):
            build_leakage_report(
                self.build,
                review=self.review,
                script_review=self.script_review,
                raw_closure=modified_closure,
                expansion_catalogs=modified_expansion,
                inputs={},
            )

        aliases = {
            locale: {
                surface: dict(values)
                for surface, values in surfaces.items()
            }
            for locale, surfaces in self.build.display_aliases.items()
        }
        alias_target = next(iter(aliases["ja"]["item_name_56"]))
        aliases["ja"]["item_name_56"][alias_target] = cyrillic_ok
        modified_aliases = replace(self.build, display_aliases=aliases)
        with self.assertRaisesRegex(
            GameCatalogError,
            "disallowed Unicode script",
        ):
            build_leakage_report(
                modified_aliases,
                review=self.review,
                script_review=self.script_review,
                raw_closure=self.raw_closure,
                expansion_catalogs=self.expansion_catalogs,
                inputs={},
            )

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
