"""Narrow popup source-order checks complementing native CJK consumer tests.

The remaining assertion intentionally parses function bodies because the
exact ordering of message lookup and punctuation-slot emission is the
contract under test; no generated artifact or exported symbol represents
that local sequencing.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"^[A-Za-z_][A-Za-z0-9_\s\*]*\b{re.escape(name)}"
        r"\s*\([^;{}]*?\)\s*\{",
        source,
        re.M | re.S,
    )
    if match is None:
        raise AssertionError(f"missing function {name}")

    depth = 1
    index = match.end()
    while index < len(source) and depth:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    if depth:
        raise AssertionError(f"unterminated function {name}")
    return source[match.start():index]


class TextConsumerAuditTests(unittest.TestCase):
    def _read(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_popup_prefix_and_punctuation_slots_are_exact(self):
        popup = self._read("src/popup.c")
        got = popup.index("struct PopupInstruction CONST_DATA PopupScr_GotItem[]")
        stole = popup.index("struct PopupInstruction CONST_DATA PopupScr_StoleItem[]")
        self.assertLess(popup.index("POPUP_MSG(0x008)", got), popup.index("POPUP_ITEM_STR", got))
        self.assertLess(popup.index("POPUP_MSG(0x00A)", stole), popup.index("POPUP_ITEM_STR", stole))

        popup2 = self._read("src/popup2.c")
        drop = _function_body(popup2, "NewPopup2_DropItem")
        send = _function_body(popup2, "NewPopup2_SendItem")
        self.assertLess(drop.index("0x00F"), drop.index("0x022"))
        self.assertLess(send.index("0x010"), send.index("0x011"))

if __name__ == "__main__":
    unittest.main()
