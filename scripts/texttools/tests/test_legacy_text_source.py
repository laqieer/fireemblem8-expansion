import unittest
from pathlib import Path

from scripts.texttools.legacy_text_source import (
    EXPECTED_MODERN_ONLY_IDS,
    build_legacy_source,
)


ROOT = Path(__file__).resolve().parents[3]


class LegacyTextSourceTests(unittest.TestCase):
    def test_repository_source_excludes_modern_only_suffix(self):
        source = (ROOT / "texts/texts.txt").read_bytes()
        legacy = build_legacy_source(source)

        self.assertLess(len(legacy), len(source))
        for msg_id in EXPECTED_MODERN_ONLY_IDS:
            self.assertNotIn(b"## " + msg_id, legacy)

    def test_rejects_unreviewed_modern_suffix_message(self):
        source = (
            b"## MSG_BASE\nBase[X]\n"
            b"## MSG_SAVE_COMPAT_LEGACY\nLegacy[X]\n"
            b"## MSG_UNREVIEWED\nUnexpected[X]\n"
        )

        with self.assertRaisesRegex(ValueError, "modern-only text suffix changed"):
            build_legacy_source(source)

    def test_requires_exactly_one_boundary(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            build_legacy_source(b"## MSG_BASE\nBase[X]\n")


if __name__ == "__main__":
    unittest.main()
