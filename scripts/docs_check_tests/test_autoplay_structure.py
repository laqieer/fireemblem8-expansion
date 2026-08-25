"""Structural contracts for the autoplay documentation."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTOPLAY = ROOT / "docs" / "autoplay.md"


class AutoplayStructureTests(unittest.TestCase):
    def test_typed_objectives_follow_accelerated_compatibility_block(self):
        text = AUTOPLAY.read_text(encoding="utf-8")
        self.assertLess(
            text.index("## Compatibility and budgets"),
            text.index("## Typed chapter objectives and AI groups"),
        )


if __name__ == "__main__":
    unittest.main()
