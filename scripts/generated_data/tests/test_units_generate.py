import unittest

from scripts.generated_data.units.generate import generate_c_source
from scripts.generated_data.units.schema import load_records
from scripts.generated_data.tests._util import fixture_path


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("units", "valid.json"))
        first = generate_c_source(records, "fixtures/units/valid.json")
        second = generate_c_source(records, "fixtures/units/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_and_values(self):
        records = load_records(fixture_path("units", "valid.json"))
        content = generate_c_source(records, "fixtures/units/valid.json")
        self.assertIn("CONST_DATA struct UnitDefinition UnitDef_Fixture_Test", content)
        self.assertIn(".charIndex = CHARACTER_EIRIKA", content)
        self.assertIn(".charIndex = 142", content)
        self.assertIn(".ai = { 0, 17, 9, 0 }", content)
        self.assertIn("{ 0 },", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmunit.h"', content)
        self.assertIn('#include "muctrl.h"', content)

    def test_group_order_preserved_from_source(self):
        records = load_records(fixture_path("units", "valid.json"))
        content = generate_c_source(records, "src")
        self.assertIn("UnitDef_Fixture_Test", content)


if __name__ == "__main__":
    unittest.main()
