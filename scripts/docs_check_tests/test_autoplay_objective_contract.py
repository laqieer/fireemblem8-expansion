"""Parsed public-contract checks for typed chapter objectives."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTOPLAY = ROOT / "docs" / "autoplay.md"
TUTORIAL = ROOT / "docs" / "generated_data_tutorial.md"
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
        self.assertIn("#85/#86's public control/telemetry", typed_section)
        self.assertIn(
            "Generated\n  `chapterobjectives` and `chapterbundle` data",
            typed_section,
        )
        self.assertNotIn("no target source, ROM bytes, RAM allocation", typed_section)
        self.assertNotIn("later integration\n  work (#89)", typed_section)
        self.assertNotIn("generated game data, or\n  localization change", typed_section)
        self.assertIn(
            "every `deactivationFlag` must\nalso differ from every referenced `eventFlag`, `failureFlag`, and\n`completionFlag`",
            TUTORIAL.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
