import unittest

from scripts.generated_data.traps.generate import generate_c_source
from scripts.generated_data.traps.schema import load_records
from scripts.generated_data.tests._util import fixture_path


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("traps", "valid.json"))
        first = generate_c_source(records, "fixtures/traps/valid.json")
        second = generate_c_source(records, "fixtures/traps/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_values_and_terminator(self):
        records = load_records(fixture_path("traps", "valid.json"))
        content = generate_c_source(records, "fixtures/traps/valid.json")
        self.assertIn("CONST_DATA u8 TrapData_Fixture_Test", content)
        self.assertIn("TRAP_BALLISTA,", content)
        self.assertIn("ITEM_BALLISTA_REGULAR,", content)
        self.assertIn("TRAP_GORGON_EGG,", content)
        self.assertIn("TRAP_NONE", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmtrick.h"', content)
        self.assertIn('#include "constants/items.h"', content)


if __name__ == "__main__":
    unittest.main()
