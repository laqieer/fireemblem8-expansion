import os
import unittest

from scripts.generated_data.weapontriangle.generate import generate_c_source
from scripts.generated_data.weapontriangle.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "weapontriangle.json")


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(REAL_SOURCE)
        first = generate_c_source(records, REAL_SOURCE)
        second = generate_c_source(records, REAL_SOURCE)
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbol_and_includes(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmitem.h"', content)
        self.assertIn('#include "bmbattle.h"', content)
        self.assertIn("CONST_DATA struct WeaponTriangleRule sWeaponTriangleRules[] = {", content)

    def test_generated_output_authors_all_12_rules_plus_terminator(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertEqual(len(records), 12)
        table_start = content.index("sWeaponTriangleRules[] = {")
        body = content[table_start:]
        # 12 authored rule braces + 1 implicit terminator brace = 13 "{ " openers.
        self.assertEqual(body.count("{ "), 13)
        self.assertIn("{ ITYPE_SWORD, ITYPE_LANCE, -15, -1 },", content)
        self.assertIn("{ ITYPE_DARK, ITYPE_ANIMA, 15, 1 },", content)

    def test_generated_output_ends_with_implicit_terminator(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertIn("{ -1 },\n};\n", content)
        # The terminator must never come from the JSON itself.
        for record in records:
            self.assertNotEqual(record.attacker, -1)

    def test_terminator_is_not_authored_in_json(self):
        records = load_records(REAL_SOURCE)
        self.assertEqual(len(records), 12)
        for record in records:
            self.assertIsInstance(record.attacker, str)


class FixtureGenerateTests(unittest.TestCase):
    def test_fixture_generation_matches_real_source_shape(self):
        records = load_records(fixture_path("weapontriangle", "valid.json"))
        content = generate_c_source(records, fixture_path("weapontriangle", "valid.json"))
        self.assertEqual(len(records), 12)
        self.assertIn("{ -1 },\n};\n", content)


if __name__ == "__main__":
    unittest.main()
