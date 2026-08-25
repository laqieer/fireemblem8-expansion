"""Parsed public-contract checks for typed chapter objectives."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTOPLAY = ROOT / "docs" / "autoplay.md"
REGISTRY = ROOT / "docs" / "test-cases" / "registry.json"


class AutoplayObjectiveContractTests(unittest.TestCase):
    def test_tester_case_and_public_contract_are_compatible(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        case = next(item for item in registry["cases"] if item["id"] == "TC-AUTOPLAY-OBJECTIVE-001")
        self.assertIn("persistent event flags", case["expected_result"])
        self.assertIn("No save bytes", case["save_compatibility"])

        typed_section = AUTOPLAY.read_text(encoding="utf-8").split(
            "## Typed chapter objectives and AI groups", 1
        )[1].split("## Validation", 1)[0]
        self.assertIn("12-byte sentinel", typed_section)
        self.assertIn("28 bytes each", typed_section)
        self.assertIn("16-byte EWRAM", typed_section)
        self.assertNotIn("no target source, ROM bytes, RAM allocation", typed_section)


if __name__ == "__main__":
    unittest.main()
