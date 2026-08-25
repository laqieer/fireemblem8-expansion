"""Parsed public-contract checks for typed chapter objectives."""

import json
import unittest
from pathlib import Path

from scripts.generated_data.chapterobjectives import schema as objectives_schema

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "test-cases" / "registry.json"


class AutoplayObjectiveContractTests(unittest.TestCase):
    def test_tester_case_and_public_contract_are_compatible(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        case = next(item for item in registry["cases"] if item["id"] == "TC-AUTOPLAY-OBJECTIVE-001")
        compatibility = case["compatibility"]
        generated_data = compatibility["generated_data"]
        runtime = compatibility["runtime"]

        self.assertEqual(compatibility["dependent_issues"], [90, 91, 92])
        self.assertEqual(generated_data["bundle_capacity"], objectives_schema.BUNDLE_CAPACITY)
        self.assertEqual(generated_data["objective_capacity"], objectives_schema.OBJECTIVE_CAPACITY)
        self.assertEqual(generated_data["group_capacity"], objectives_schema.GROUP_CAPACITY)
        self.assertEqual(generated_data["group_member_capacity"], objectives_schema.GROUP_MEMBER_CAPACITY)
        self.assertEqual(generated_data["bundle_bytes"], 12)
        self.assertEqual(generated_data["objective_bytes"], 28)
        self.assertEqual(runtime["telemetry_ewram_bytes"], 16)
        self.assertEqual(runtime["total_ewram_bytes"], 20)
        self.assertFalse(runtime["archival_runtime"])
        self.assertFalse(runtime["serialized_objective_state"])


if __name__ == "__main__":
    unittest.main()
