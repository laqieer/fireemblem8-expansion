import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.build import (
    _TEXT_TOKEN_RE,
    _encode_control_unit,
    build_game_catalog,
    generate,
)
from scripts.localization.game_catalog.english_source import (
    _encode_named_token,
    _strip_comments,
    load_english_definitions,
)
from scripts.localization.game_locales.controls import expand_canonical_text


class RealGenerateTests(unittest.TestCase):
    def _tmpdir(self):
        return tempfile.TemporaryDirectory(dir=TEST_DIR)

    def test_two_generate_runs_are_byte_identical_and_counts_match_committed_map(self):
        with self._tmpdir() as tmp_a, self._tmpdir() as tmp_b:
            out_a = Path(tmp_a)
            out_b = Path(tmp_b)
            written_a = generate(output_dir=out_a)
            written_b = generate(output_dir=out_b)
            for name in written_a:
                self.assertEqual(
                    written_a[name].read_bytes(),
                    written_b[name].read_bytes(),
                    name,
                )

            report = json.loads(written_a["report_json"].read_text(encoding="utf-8"))
            budget = json.loads(written_a["budget_json"].read_text(encoding="utf-8"))
            self.assertEqual(report["mapping_source_counts"]["indexed"], 2955)
            self.assertEqual(report["mapping_source_counts"]["raw"], 130)
            self.assertEqual(report["mapping_source_counts"]["authored"], 329)
            self.assertEqual(report["mapping_source_counts"]["english_fallback"], 0)
            self.assertEqual(report["mapping_source_counts"]["unresolved"], 0)
            self.assertEqual(report["shared_english"]["present_count"], 3414)
            self.assertEqual(report["shared_english"]["absent_count"], 0)
            self.assertEqual(
                report["shared_english"]["storage"]["required_bytes"], 4000
            )
            self.assertEqual(report["locales"]["ja"]["present_count"], 3414)
            self.assertEqual(report["locales"]["ja"]["provider_counts"]["raw"], 130)
            self.assertEqual(report["locales"]["ja"]["provider_unavailable_count"], 0)
            self.assertEqual(report["locales"]["zh-Hans"]["present_count"], 3414)
            self.assertEqual(report["locales"]["zh-Hans"]["explicit_fallback_count"], 0)
            self.assertTrue(report["locales"]["ja"]["storage"]["target_fits"])
            self.assertTrue(report["locales"]["zh-Hans"]["storage"]["target_fits"])
            self.assertEqual(report["locales"]["ja"]["storage"]["required_bytes"], 4715)
            self.assertEqual(report["locales"]["zh-Hans"]["storage"]["required_bytes"], 3743)
            self.assertEqual(
                report["locales"]["ja"]["hashes"]["source_framed_sha256"],
                report["locales"]["ja"]["hashes"]["round_trip_framed_sha256"],
            )
            self.assertEqual(
                report["locales"]["zh-Hans"]["hashes"]["source_framed_sha256"],
                report["locales"]["zh-Hans"]["hashes"]["round_trip_framed_sha256"],
            )
            self.assertIn("nodes", report["locales"]["ja"]["huffman"])
            self.assertIn("compressed_blob_hex", report["locales"]["zh-Hans"]["huffman"])
            self.assertIn("codec_budget", budget["locales"]["ja"])
            self.assertIn("codec_budget", budget["locales"]["zh-Hans"])
            self.assertIn("codec_budget", budget["shared_english"])
            self.assertEqual(budget["compiled_locales"], ["en", "ja", "zh-Hans"])
            for locale, inserted in (("ja", 2033), ("zh-Hans", 2062)):
                width = report["width_validation"][locale]
                self.assertEqual(width["target_count"], 3414)
                self.assertEqual(width["unclassified_target_count"], 0)
                self.assertEqual(width["generated_line_break_count"], inserted)
                self.assertEqual(
                    width["context_counts"],
                    {
                        "ending_layout": 99,
                        "fixed_width_label": 331,
                        "subtitle_help_scroll": 21,
                        "system_default_240": 1814,
                        "talk_dialogue_240": 1149,
                    },
                )

    def test_generated_c_uses_locale_data_section_and_has_target_entries(self):
        with self._tmpdir() as tmp:
            written = generate(output_dir=Path(tmp))
            source = written["source"].read_text(encoding="utf-8")
            header = written["header"].read_text(encoding="utf-8")
            config_header = written["config_header"].read_text(encoding="utf-8")
            self.assertIn('SECTION(".locale_data")', header)
            self.assertIn("GAME_LOCALIZATION_TARGET_COUNT 3414u", header)
            self.assertIn("FE8_GAME_LOCALIZATION_DATA_PRESENT 1", config_header)
            self.assertIn(
                "FE8_GAME_LOCALIZATION_MAX_DECODED_BYTES 4715u",
                config_header,
            )
            self.assertIn("gGameLocalizationEnglishEntries[]", source)
            self.assertIn("gGameLocalizationEnglishCatalog", source)
            self.assertIn("gGameLocalizationJaEntries[]", source)
            self.assertIn("gGameLocalizationZhHansEntries[]", source)
            self.assertIn("gGameLocalizationCatalogs[GAME_LOCALIZATION_LOCALE_COUNT]", source)
            self.assertIn("gGameLocalizationJaCompressedBlob +", source)

    def test_deferred_game_id_surfaces_emit_exact_japanese_and_chinese(self):
        build = build_game_catalog()
        ja = build.locale_bundle("ja")
        zh = build.locale_bundle("zh-Hans")
        decisions = json.loads(
            (
                ROOT / "texts/locales/mapping/raw_surface_decisions.json"
            ).read_text(encoding="utf-8")
        )
        raw = {
            row["import_id"]: row["text"]
            for row in json.loads(
                (ROOT / "texts/locales/zh-Hans/raw.json").read_text(
                    encoding="utf-8"
                )
            )["records"]
        }
        mapping = {
            row["target_id"]: row["source"]
            for row in json.loads(
                (
                    ROOT / "texts/locales/mapping/fe8u_target_map.json"
                ).read_text(encoding="utf-8")
            )["rows"]
        }
        ja_raw = json.loads(
            (ROOT / "texts/locales/ja/raw.json").read_text(encoding="utf-8")
        )["providers"]
        authored = {
            locale: json.loads(
                (
                    ROOT / f"texts/locales/authored/catalog.{locale}.json"
                ).read_text(encoding="utf-8")
            )["strings"]
            for locale in ("ja", "zh-Hans")
        }

        for decision in decisions["decisions"]:
            if decision["classification"] != "game_message":
                continue
            target_id = int(decision["target_id"], 16)
            source = mapping[decision["target_id"]]
            if source["kind"] == "authored":
                key = source["translation_key"]
                expected_ja = authored["ja"][key]
                expected_zh = authored["zh-Hans"][key]
            else:
                ja_source = source["regional_sources"]["ja"]
                expected_ja = (
                    ja_source["text"]
                    if ja_source["kind"] == "literal"
                    else ja_raw[decision["target_id"]]["text"]
                )
                expected_zh = raw[decision["import_id"]]
            with self.subTest(import_id=decision["import_id"]):
                self.assertEqual(
                    ja.entries[target_id].source_text,
                    expected_ja,
                )
                self.assertEqual(
                    zh.entries[target_id].source_text,
                    expected_zh,
                )

    def test_transformed_messages_fit_dedicated_runtime_scratch(self):
        build = build_game_catalog()
        substitution_controls = tuple(
            b"\x80" + bytes((payload,))
            for payload in (0x12, 0x13, 0x14, 0x15, 0x20, 0x22)
        )
        streams = [
            entry.encoded_bytes for entry in build.english.entries
        ]
        for locale in ("ja", "zh-Hans"):
            streams.extend(
                entry.encoded_bytes
                for entry in build.locale_bundle(locale).entries
                if entry.encoded_bytes is not None
            )

        bounds = []
        for stream in streams:
            substitution_count = sum(
                stream.count(control)
                for control in substitution_controls
            )
            if substitution_count == 0:
                continue
            bounds.append(
                len(stream)
                + substitution_count * (
                    0x100 - len(substitution_controls[0])
                )
            )

        self.assertTrue(bounds)
        self.assertLessEqual(max(bounds), 0x400)

    def test_every_0a_byte_in_final_localized_streams_comes_from_an_explicit_control(self):
        build = build_game_catalog()
        definitions = load_english_definitions(ROOT / "texts/textdefs.txt")

        def explicit_0a_count(text):
            token_names = _TEXT_TOKEN_RE.findall(text)
            named_tokens = [
                name for name in token_names if not name.startswith("CTRL:")
            ]
            if named_tokens:
                normalized = _strip_comments(
                    text,
                    source_name="localized encoded-stream audit",
                ).replace("\r", "").replace("\n", "")
                return sum(
                    _encode_named_token(
                        match.group(1),
                        definitions,
                        source_name="localized encoded-stream audit",
                    ).count(b"\x0A")
                    for match in _TEXT_TOKEN_RE.finditer(normalized)
                )

            count = 0
            for unit in expand_canonical_text(text):
                if isinstance(unit, str):
                    self.assertNotIn("\r", unit)
                    self.assertNotIn("\n", unit)
                    self.assertNotIn(0x0A, unit.encode("utf-8"))
                else:
                    count += _encode_control_unit(unit).count(b"\x0A")
            return count

        for locale in ("ja", "zh-Hans"):
            entries = build.locale_bundle(locale).entries
            for entry in entries:
                with self.subTest(locale=locale, target_id=entry.target_id):
                    self.assertEqual(
                        entry.encoded_bytes.count(b"\x0A"),
                        explicit_0a_count(entry.source_text),
                    )

            beginner = entries[0x0149]
            self.assertNotIn("\n", beginner.source_text)
            self.assertEqual(beginner.encoded_bytes.count(b"\x0A"), 0)
            self.assertEqual(beginner.encoded_bytes.count(b"\x01"), 4)

            for target_id, prefix in (
                (
                    0x0B4A,
                    "[CTRL:000B][CTRL:0010][CTRL:0114][CTRL:000D]"
                    "[CTRL:0010][CTRL:0102][CTRL:000E][CTRL:0110]"
                    "[CTRL:0001][CTRL:000E]",
                ),
                (
                    0x0B91,
                    "[CTRL:000C][CTRL:0010][CTRL:0170][CTRL:000E]"
                    "[CTRL:0010][CTRL:0149][CTRL:000F][CTRL:0110]"
                    "[CTRL:0001][CTRL:000E]",
                ),
            ):
                entry = entries[target_id]
                if locale == "zh-Hans":
                    self.assertTrue(entry.source_text.startswith(prefix))
                self.assertNotIn("\n", entry.source_text)
                self.assertEqual(
                    entry.encoded_bytes.count(b"\x0A"),
                    explicit_0a_count(entry.source_text),
                )

            explicit_face_control = entries[0x0884]
            self.assertTrue(explicit_face_control.source_text.startswith("[OpenLeft]"))
            self.assertGreater(
                explicit_face_control.encoded_bytes.count(b"\x0A"),
                0,
            )
            self.assertEqual(
                explicit_face_control.encoded_bytes.count(b"\x0A"),
                explicit_0a_count(explicit_face_control.source_text),
            )

    def test_profile_specific_outputs_exclude_disabled_payloads_and_size_capacity(self):
        with (
            self._tmpdir() as ja_tmp,
            self._tmpdir() as ja_repeat_tmp,
            self._tmpdir() as zh_tmp,
            self._tmpdir() as both_tmp,
        ):
            ja = generate(output_dir=Path(ja_tmp), enabled_locales=("ja",))
            ja_repeat = generate(
                output_dir=Path(ja_repeat_tmp), enabled_locales=("ja",)
            )
            zh = generate(output_dir=Path(zh_tmp), enabled_locales=("zh-Hans",))
            both = generate(
                output_dir=Path(both_tmp), enabled_locales=("ja", "zh-Hans")
            )

            for name in ja:
                self.assertEqual(ja[name].read_bytes(), ja_repeat[name].read_bytes(), name)

            ja_source = ja["source"].read_text(encoding="utf-8")
            zh_source = zh["source"].read_text(encoding="utf-8")
            both_source = both["source"].read_text(encoding="utf-8")
            ja_header = ja["header"].read_text(encoding="utf-8")
            zh_header = zh["header"].read_text(encoding="utf-8")
            ja_config = ja["config_header"].read_text(encoding="utf-8")
            zh_config = zh["config_header"].read_text(encoding="utf-8")

            self.assertIn("gGameLocalizationJaCompressedBlob[]", ja_source)
            self.assertIn("gGameLocalizationEnglishCompressedBlob[]", ja_source)
            self.assertNotIn("gGameLocalizationZhHansCompressedBlob[]", ja_source)
            self.assertIn("gGameLocalizationZhHansCompressedBlob[]", zh_source)
            self.assertIn("gGameLocalizationEnglishCompressedBlob[]", zh_source)
            self.assertNotIn("gGameLocalizationJaCompressedBlob[]", zh_source)
            self.assertIn("gGameLocalizationJaCompressedBlob[]", both_source)
            self.assertIn("gGameLocalizationZhHansCompressedBlob[]", both_source)
            self.assertEqual(ja_source.count(
                "(const struct GameLocalizationLocaleCatalog *)0,"
            ), 5)
            self.assertEqual(zh_source.count(
                "(const struct GameLocalizationLocaleCatalog *)0,"
            ), 5)
            self.assertEqual(
                both_source.count(
                    "(const struct GameLocalizationLocaleCatalog *)0,"
                ),
                4,
            )

            self.assertIn("GAME_LOCALIZATION_JA_ENABLED 1u", ja_header)
            self.assertIn(
                "GAME_LOCALIZATION_SHARED_ENGLISH_ENABLED 1u", ja_header
            )
            self.assertIn("GAME_LOCALIZATION_ZH_HANS_ENABLED 0u", ja_header)
            self.assertNotIn("extern const u32 gGameLocalizationZhHansNodes[];", ja_header)
            self.assertIn("GAME_LOCALIZATION_JA_ENABLED 0u", zh_header)
            self.assertIn("GAME_LOCALIZATION_ZH_HANS_ENABLED 1u", zh_header)
            self.assertNotIn("extern const u32 gGameLocalizationJaNodes[];", zh_header)
            self.assertIn(
                "FE8_GAME_LOCALIZATION_MAX_DECODED_BYTES 4715u", ja_config
            )
            self.assertIn(
                "FE8_GAME_LOCALIZATION_MAX_DECODED_BYTES 4000u", zh_config
            )

            ja_report = json.loads(ja["report_json"].read_text(encoding="utf-8"))
            zh_report = json.loads(zh["report_json"].read_text(encoding="utf-8"))
            both_report = json.loads(
                both["report_json"].read_text(encoding="utf-8")
            )
            ja_budget = json.loads(ja["budget_json"].read_text(encoding="utf-8"))
            zh_budget = json.loads(zh["budget_json"].read_text(encoding="utf-8"))
            both_budget = json.loads(
                both["budget_json"].read_text(encoding="utf-8")
            )

            self.assertEqual(ja_report["enabled_locales"], ["ja"])
            self.assertEqual(ja_report["compiled_locales"], ["en", "ja"])
            self.assertEqual(set(ja_report["locales"]), {"ja"})
            self.assertEqual(zh_report["enabled_locales"], ["zh-Hans"])
            self.assertEqual(
                zh_report["compiled_locales"], ["en", "zh-Hans"]
            )
            self.assertEqual(set(zh_report["locales"]), {"zh-Hans"})
            self.assertEqual(
                both_report["enabled_locales"], ["ja", "zh-Hans"]
            )
            self.assertEqual(
                both_report["compiled_locales"], ["en", "ja", "zh-Hans"]
            )
            self.assertEqual(
                ja_budget["shared_english"]["estimated_total_c_bytes"],
                zh_budget["shared_english"]["estimated_total_c_bytes"],
            )
            self.assertEqual(
                ja_budget["shared_english"]["estimated_total_c_bytes"],
                both_budget["shared_english"]["estimated_total_c_bytes"],
            )
            self.assertEqual(ja_budget["locales"]["ja"]["max_decoded_bytes"], 4715)
            self.assertEqual(
                zh_budget["locales"]["zh-Hans"]["max_decoded_bytes"], 3743
            )
            self.assertLess(
                ja_budget["totals"]["estimated_total_c_bytes"],
                both_budget["totals"]["estimated_total_c_bytes"],
            )
            self.assertLess(
                zh_budget["totals"]["estimated_total_c_bytes"],
                both_budget["totals"]["estimated_total_c_bytes"],
            )
            self.assertEqual(
                ja_budget["totals"]["shared_english_bytes"],
                both_budget["totals"]["shared_english_bytes"],
            )


if __name__ == "__main__":
    unittest.main()
