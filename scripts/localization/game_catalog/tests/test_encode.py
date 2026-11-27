import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.build import encode_canonical_text
from scripts.localization.game_catalog.english_source import encode_english_source_text
from scripts.localization.game_catalog.model import GameCatalogError


class EncodeCanonicalTextTests(unittest.TestCase):
    def test_literal_utf8_and_canonical_controls_use_engine_byte_stream(self):
        self.assertEqual(
            encode_canonical_text("A[CTRL:0080][CTRL:0020]中[CTRL:0165]"),
            b"A\x80\x20" + "中".encode("utf-8") + b"\x65\x01\x00",
        )

    def test_verified_fe8j_spacing_preserves_the_legacy_runtime_token(self):
        self.assertEqual(
            encode_canonical_text(
                "売る　　やめる",
                preserve_legacy_sjis_space=True,
            ),
            "売る".encode("utf-8")
            + b"\x81\x40\x81\x40"
            + "やめる".encode("utf-8")
            + b"\x00",
        )
        self.assertEqual(
            encode_canonical_text("　"),
            "\u3000".encode("utf-8") + b"\x00",
        )

    def test_unknown_and_malformed_controls_are_rejected(self):
        for text in ("[LF]", "[CTRL:001]", "[CTRL:ZZZZ]", "[CTRL:0001"):
            with self.subTest(text=text):
                with self.assertRaises(GameCatalogError):
                    encode_canonical_text(text)

    def test_physical_newline_is_rejected_but_explicit_0a_control_is_allowed(self):
        with self.assertRaisesRegex(GameCatalogError, "physical newline"):
            encode_canonical_text("line one\nline two")
        self.assertEqual(
            encode_canonical_text("[CTRL:000A]face"),
            b"\x0Aface\x00",
        )

    def test_embedded_nul_literal_and_zero_emitting_controls_are_rejected(self):
        with self.assertRaisesRegex(GameCatalogError, "embedded NUL"):
            encode_canonical_text("bad\x00text")
        with self.assertRaisesRegex(GameCatalogError, "embedded NUL"):
            encode_canonical_text("[CTRL:0000]")
        with self.assertRaisesRegex(GameCatalogError, "embedded NUL"):
            encode_canonical_text("[CTRL:0100]")

    def test_zero_bytes_are_allowed_only_inside_complete_control_operands(self):
        samples = (
            ("[CTRL:0010][CTRL:0100]A", b"\x10\x00\x01A\x00"),
            (
                "[CTRL:0010][CTRL:0001][CTRL:0000]A",
                b"\x10\x01\x00A\x00",
            ),
            (
                "[CTRL:0010][CTRL:0000][CTRL:0000]A",
                b"\x10\x00\x00A\x00",
            ),
            (
                "[CTRL:0080][CTRL:0001][CTRL:0000]B",
                b"\x80\x01\x00B\x00",
            ),
        )
        for text, expected in samples:
            with self.subTest(text=text):
                self.assertEqual(encode_canonical_text(text), expected)

        definitions = {
            "X": (0,),
            "LoadFace": (0x10,),
            "FID_Zero": (0, 1),
        }
        self.assertEqual(
            encode_english_source_text(
                "[LoadFace][FID_Zero]A[X]", definitions, source_name="fixture"
            ),
            b"\x10\x00\x01A\x00",
        )

        for text in ("[CTRL:0010][CTRL:0000]", "[CTRL:0080][CTRL:0001]"):
            with self.subTest(truncated=text):
                with self.assertRaises(GameCatalogError):
                    encode_canonical_text(text)


if __name__ == "__main__":
    unittest.main()
