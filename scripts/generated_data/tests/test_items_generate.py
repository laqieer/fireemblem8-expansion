import unittest

from scripts.generated_data.items.generate import generate_c_source
from scripts.generated_data.items.schema import load_records
from scripts.generated_data.tests._util import fixture_path


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("items", "valid.json"))
        first = generate_c_source(records, "fixtures/items/valid.json")
        second = generate_c_source(records, "fixtures/items/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_and_values(self):
        records = load_records(fixture_path("items", "valid.json"))
        content = generate_c_source(records, "fixtures/items/valid.json")
        self.assertIn("CONST_DATA struct ItemData gItemData[] = {", content)
        self.assertIn("[ITEM_FIXTURE_NONE] = {", content)
        self.assertIn(".number = ITEM_FIXTURE_NONE,", content)
        self.assertIn(".weaponType = ITYPE_SWORD,", content)
        self.assertIn(".iconId = 0,", content)
        self.assertIn(".attributes = IA_WEAPON,", content)
        self.assertIn(".encodedRange = 17,", content)
        self.assertIn(".weaponRank = WPN_EXP_E,", content)
        self.assertIn(".pStatBonuses = &ItemBonus_FixtureBonus,", content)
        self.assertIn(".pEffectiveness = ItemEffectiveness_FixtureTarget,", content)
        self.assertIn(".weaponEffectId = WPN_EFFECT_POISON,", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmitem.h"', content)
        self.assertIn('#include "constants/items.h"', content)

    def test_default_fields_are_omitted(self):
        records = load_records(fixture_path("items", "valid.json"))
        content = generate_c_source(records, "fixtures/items/valid.json")
        none_entry = content.split("[ITEM_FIXTURE_NONE] = {", 1)[1].split("},", 1)[0]
        self.assertNotIn(".nameTextId", none_entry)
        self.assertNotIn(".attributes", none_entry)
        self.assertNotIn(".weaponRank", none_entry)
        self.assertNotIn(".weaponEffectId", none_entry)
        self.assertIn(".weaponType = ITYPE_SWORD,", none_entry)
        self.assertIn(".iconId = 0,", none_entry)

    def test_attributes_are_sorted_ascending_by_bit_value_regardless_of_json_order(self):
        records = load_records(fixture_path("items", "valid.json"))
        for record in records:
            if record.item == "ITEM_FIXTURE_A":
                record.attributes = ["IA_MAGIC", "IA_WEAPON"]
        content = generate_c_source(records, "fixtures/items/valid.json")
        self.assertIn(".attributes = IA_WEAPON | IA_MAGIC,", content)


if __name__ == "__main__":
    unittest.main()
