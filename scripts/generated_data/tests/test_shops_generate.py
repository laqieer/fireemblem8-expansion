import unittest

from scripts.generated_data.shops.generate import generate_c_source
from scripts.generated_data.shops.schema import load_records
from scripts.generated_data.tests._util import fixture_path


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("shops", "valid.json"))
        first = generate_c_source(records, "fixtures/shops/valid.json")
        second = generate_c_source(records, "fixtures/shops/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_and_terminator(self):
        records = load_records(fixture_path("shops", "valid.json"))
        content = generate_c_source(records, "fixtures/shops/valid.json")
        self.assertIn("CONST_DATA u16 ShopList_Fixture_Test", content)
        self.assertIn("ITEM_SWORD_SLIM,", content)
        self.assertIn("ITEM_NONE,", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "constants/items.h"', content)


if __name__ == "__main__":
    unittest.main()
