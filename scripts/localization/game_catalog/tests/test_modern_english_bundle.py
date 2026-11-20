import json
import tempfile
import unittest
from pathlib import Path

from scripts.localization.game_catalog.build import build_game_catalog
from scripts.localization.game_catalog.english_source import (
    load_english_definitions,
    load_english_source_entries,
)
from scripts.localization.game_catalog.model import GameCatalogError
from scripts.localization.game_locales.final_mapping import _canonical_english
from scripts.texttools.multilang_codec import pack_bytes
from scripts.texttools.tests.test_textprocess_publication import load_generator


ROOT = Path(__file__).resolve().parents[4]
TEST_DIR = Path(__file__).resolve().parent
MAPPING = ROOT / "texts" / "locales" / "mapping" / "fe8u_target_map.json"
REGRESSION_IDS = (0xD4D, 0xD4E, 0xD4F, 0xD50, 0xD54)


class SelectedSourceCompatibilityTests(unittest.TestCase):
    def test_native_and_localized_read_the_same_selected_messages(self):
        examples = (
            "#0000\nhttp://example[X]\n#0001\nB[X]\n",
            "#0000\n/* removed *///literal[X]\n#0001\nB[X]\n",
            "#0000\nA/* note\n#000F\n[UNKNOWN]\n*/B[X]\n#0001\nC[X]\n",
            "#0000/* note */2\nA[X]\n#0001\nB[X]\n",
            "#0001\nB[X]\n#0000\nA[X]\n",
            "## CUSTOM\nA[X]\n#0001\nB[X]\n",
        )
        for use_include in (False, True):
            for source in examples:
                with self.subTest(include=use_include, source=source), \
                     tempfile.TemporaryDirectory(dir=TEST_DIR) as tmp:
                    root = Path(tmp)
                    definitions = root / "defs.txt"
                    definitions.write_text("[X] = 0\n", encoding="utf-8")
                    selected = root / ("included.txt" if use_include else "texts.txt")
                    selected.write_text(source, encoding="utf-8")
                    main = root / "texts.txt"
                    if use_include:
                        main.write_text('#include "included.txt"\n', encoding="utf-8")

                    native = load_generator()
                    messages = native.order_messages(
                        native.process_file(main, native.load_control_chars(definitions), "utf8"),
                        str(main),
                    )
                    expected = []
                    for message in messages:
                        raw = bytearray()
                        for symbol in message.data:
                            raw.append(symbol & 0xFF)
                            if symbol >> 8:
                                raw.append(symbol >> 8)
                        expected.append(bytes(raw))

                    localized = load_english_source_entries(main, definitions, target_count=2)
                    self.assertEqual(
                        [entry.encoded_bytes for entry in localized],
                        expected,
                    )

    def test_native_and_localized_reject_invalid_ids_and_names(self):
        examples = (
            "#0000\nA[X]\n#0000\nB[X]\n",
            "#0000\nA[X]\n## MSG_000\nB[X]\n",
            "#0000\nA[X]\n## MSG_H\nB[X]\n",
            "#0000\nA[UNKNOWN][X]\n#0001\nB[X]\n",
            "#0000\nA[BROKEN[X]\n#0001\nB[X]\n",
        )
        for use_include in (False, True):
            for source in examples:
                with self.subTest(include=use_include, source=source), \
                     tempfile.TemporaryDirectory(dir=TEST_DIR) as tmp:
                    root = Path(tmp)
                    definitions = root / "defs.txt"
                    definitions.write_text("[X] = 0\n", encoding="utf-8")
                    selected = root / ("included.txt" if use_include else "texts.txt")
                    selected.write_text(source, encoding="utf-8")
                    main = root / "texts.txt"
                    if use_include:
                        main.write_text('#include "included.txt"\n', encoding="utf-8")
                    native = load_generator()
                    with self.assertRaises(ValueError):
                        native.order_messages(
                            native.process_file(main, native.load_control_chars(definitions), "utf8"),
                            str(main),
                        )
                    with self.assertRaises(GameCatalogError):
                        load_english_source_entries(main, definitions, target_count=2)

    def test_localized_rejects_unknown_directives_and_discarded_content(self):
        source = (
            "#0000\nA[X]\n"
            "#UNKNOWN\n[UNKNOWN_CONTROL][X]\n"
            "#0001\nB[X]\n"
        )
        for use_include in (False, True):
            with self.subTest(include=use_include), tempfile.TemporaryDirectory(
                dir=TEST_DIR
            ) as tmp:
                root = Path(tmp)
                definitions = root / "defs.txt"
                definitions.write_text("[X] = 0\n", encoding="utf-8")
                selected = root / ("included.txt" if use_include else "texts.txt")
                selected.write_text(source, encoding="utf-8")
                main = root / "texts.txt"
                if use_include:
                    main.write_text('#include "included.txt"\n', encoding="utf-8")

                with self.assertRaisesRegex(GameCatalogError, "unknown text directive"):
                    load_english_source_entries(main, definitions, target_count=2)

    def test_native_and_localized_report_include_cycles(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as tmp:
            root = Path(tmp)
            definitions = root / "defs.txt"
            definitions.write_text("[X] = 0\n", encoding="utf-8")
            selected = root / "texts.txt"
            selected.write_text('#include "child.txt"\n', encoding="utf-8")
            (root / "child.txt").write_text('#include "texts.txt"\n', encoding="utf-8")
            native = load_generator()
            with self.assertRaisesRegex(ValueError, "include cycle"):
                native.process_file(selected, native.load_control_chars(definitions), "utf8")
            with self.assertRaisesRegex(GameCatalogError, "include cycle"):
                load_english_source_entries(selected, definitions, target_count=1)

    def test_translation_queue_uses_the_same_parsed_message(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as tmp:
            root = Path(tmp)
            definitions = root / "defs.txt"
            definitions.write_text("[X] = 0\n[LF] = 1\n", encoding="utf-8")
            selected = root / "texts.txt"
            selected.write_text(
                "#0000\nhttp://example[LF]A/* note\n#000F\n"
                "[UNKNOWN]\n*/B[X]\n",
                encoding="utf-8",
            )
            entry, = load_english_source_entries(selected, definitions, target_count=1)
            canonical = _canonical_english(entry, load_english_definitions(definitions))
            self.assertEqual(canonical["english_canonical_text"], "http://exampleAB")
            self.assertEqual(
                [(control["name"], control["bytes_hex"]) for control in canonical["controls"]],
                [("LF", "01")],
            )
            self.assertEqual(canonical["source_text"], entry.source_text)


def _is_renderer_valid(data):
    index = 0
    while index < len(data):
        first = data[index]
        if first == 0:
            return index == len(data) - 1
        if first < 0x20:
            length = 3 if first == 0x10 else 1
            if index + length > len(data) - 1:
                return False
            index += length
            continue
        if first < 0x7F:
            index += 1
            continue
        if first == 0x7F:
            return False
        if first == 0x80:
            if index + 2 > len(data) - 1 or data[index + 1] == 0:
                return False
            index += 2
            continue

        if 0xC2 <= first <= 0xDF:
            length = 2
        elif 0xE0 <= first <= 0xEF:
            length = 3
        elif 0xF0 <= first <= 0xF4:
            length = 4
        else:
            return False
        if index + length > len(data) - 1:
            return False
        continuation = data[index + 1:index + length]
        if any(byte < 0x80 or byte > 0xBF for byte in continuation):
            return False
        second = continuation[0]
        if (
            (first == 0xE0 and second < 0xA0)
            or (first == 0xED and second >= 0xA0)
            or (first == 0xF0 and second < 0x90)
            or (first == 0xF4 and second >= 0x90)
        ):
            return False
        index += length
    return False


class ModernEnglishBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = build_game_catalog()
        cls.english = cls.build.english

    def test_all_3414_entries_round_trip_independently_and_end_at_exact_nul_bit(self):
        self.assertEqual(len(self.english.entries), 3414)
        code_map = self.english.catalog.model.code_map()

        for source, descriptor in zip(
            self.english.entries, self.english.catalog.entries
        ):
            with self.subTest(msg_id=f"0x{source.target_id:04X}"):
                decoded = self.english.catalog.decode_entry(source.target_id)
                self.assertEqual(decoded, source.encoded_bytes)
                self.assertTrue(_is_renderer_valid(decoded))

                symbols = pack_bytes(source.encoded_bytes)
                self.assertEqual(symbols[-1], 0)
                self.assertNotIn(0, symbols[:-1])
                bits_before_nul = sum(len(code_map[symbol]) for symbol in symbols[:-1])
                self.assertEqual(
                    bits_before_nul + len(code_map[0]),
                    descriptor.bit_length,
                )
                self.assertLess(bits_before_nul, descriptor.bit_length)
                self.assertEqual(
                    descriptor.compressed_size,
                    (descriptor.bit_length + 7) // 8,
                )

    def test_high_severity_regression_ids_are_standalone(self):
        expected = {
            0xD4D: b"The save data appears to be\x1F\x01"
            b"corrupted and cannot be read.\x00",
            0xD4E: b"The save data's expansion\x1F\x01"
            b"info is corrupted and\x1F\x01cannot be read.\x00",
            0xD4F: b"This save data is from an\x1F\x01"
            b"older version of this\x1F\x01expansion. It can be migrated\x1F\x01"
            b"using an external tool.\x00",
            0xD50: b"This save data is from a\x1F\x01"
            b"newer version of this\x1F\x01expansion and is not\x1F\x01"
            b"supported here.\x00",
            0xD54: b"Erase All Save Data\x00",
        }
        for msg_id in REGRESSION_IDS:
            with self.subTest(msg_id=f"0x{msg_id:04X}"):
                decoded = self.english.catalog.decode_entry(msg_id)
                self.assertEqual(decoded, expected[msg_id])
                self.assertEqual(decoded.count(b"\x00"), 1)
                self.assertEqual(decoded[-1:], b"\x00")

    def test_zero_cjk_fallback_preserves_the_shared_english_bundle(self):
        rows = json.loads(MAPPING.read_text(encoding="utf-8"))["rows"]
        fallback_ids = [
            int(row["target_id"], 16)
            for row in rows
            if row["source"]["kind"] == "english_fallback"
        ]
        self.assertEqual(fallback_ids, [])
        self.assertEqual(len(self.english.entries), 3414)
        self.assertEqual(
            self.build.report["shared_english"]["present_count"], 3414
        )
        for locale in ("ja", "zh-Hans"):
            self.assertEqual(
                self.build.report["locales"][locale]["present_count"], 3414
            )
            self.assertEqual(
                self.build.report["locales"][locale][
                    "explicit_fallback_count"
                ],
                0,
            )

    def test_parser_handles_explicit_ids_macros_includes_controls_and_comments(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as tmp:
            root = Path(tmp)
            (root / "defs.txt").write_text(
                "[X] = 0\n"
                "[LF] = 1\n"
                "[LoadFace] = 0x10\n"
                "[FID_Test] = 0x93, 0x94\n"
                "[DashedLine] = 0x7F\n"
                "[TAB] = 0x81, 0x40\n"
                "[LQuote] = 0x93\n"
                "[RQuote] = 0x94\n"
                "[AccentedE] = 0xE9\n",
                encoding="utf-8",
            )
            (root / "included.txt").write_text(
                "## MSG_INCLUDED\n"
                "[LoadFace][FID_Test]B[LF][X]\n",
                encoding="utf-8",
            )
            (root / "texts.txt").write_text(
                "#0x0\n"
                "A/* ignored */[DashedLine][LQuote]Q[RQuote]"
                "[TAB][AccentedE][X]\n"
                '#include "included.txt"\n'
                "## MSG_LAST\n"
                "// source-only comment\n"
                "C[X]\n",
                encoding="utf-8",
            )
            entries = load_english_source_entries(
                root / "texts.txt", root / "defs.txt", target_count=3
            )
            self.assertEqual(
                entries[0].encoded_bytes,
                b'A-"Q"' + "\u3000".encode("utf-8") + b"e\x00",
            )
            self.assertEqual(
                entries[1].encoded_bytes, b"\x10\x93\x94B\x01\x00"
            )
            self.assertEqual(entries[2].encoded_bytes, b"C\x00")

    def test_unknown_non_utf8_printable_definition_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as tmp:
            root = Path(tmp)
            (root / "defs.txt").write_text(
                "[X] = 0\n[UnknownGlyph] = 0x82\n", encoding="utf-8"
            )
            (root / "texts.txt").write_text(
                "#0x0\n[UnknownGlyph][X]\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(GameCatalogError, "no modern replacement"):
                load_english_source_entries(
                    root / "texts.txt", root / "defs.txt", target_count=1
                )


if __name__ == "__main__":
    unittest.main()
