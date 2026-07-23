import unittest

from scripts.generated_data.characters.generate import generate_c_source
from scripts.generated_data.characters.schema import load_records
from scripts.generated_data.tests._util import fixture_path


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("characters", "valid.json"))
        first = generate_c_source(records, "fixtures/characters/valid.json")
        second = generate_c_source(records, "fixtures/characters/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_and_values(self):
        records = load_records(fixture_path("characters", "valid.json"))
        content = generate_c_source(records, "fixtures/characters/valid.json")
        self.assertIn("CONST_DATA struct CharacterData gCharacterData[] = {", content)
        self.assertIn("[CHARACTER_EIRIKA - 1] = {", content)
        self.assertIn(".number = CHARACTER_EIRIKA,", content)
        self.assertIn(".defaultClass = CLASS_MYRMIDON,", content)
        self.assertIn(".affinity = UNIT_AFFIN_LIGHT,", content)
        self.assertIn(".baseHP = 18,", content)
        self.assertIn("[ITYPE_SWORD] = WPN_EXP_E,", content)
        self.assertIn(".attributes = CA_LORD | CA_FEMALE,", content)
        self.assertIn(".pSupportData = &SupportData_Eirika,", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmunit.h"', content)
        self.assertIn('#include "constants/characters.h"', content)

    def test_raw_designator_uses_lowercase_hex_literal(self):
        records = load_records(fixture_path("characters", "valid.json"))
        content = generate_c_source(records, "fixtures/characters/valid.json")
        self.assertIn("[0x1b - 1] = {", content)
        self.assertIn(".number = 0x1b,", content)
        self.assertNotIn("0x1B", content)

    def test_designator_256_wraps_number_to_zero_without_overflow(self):
        records = load_records(fixture_path("characters", "valid.json"))
        content = generate_c_source(records, "fixtures/characters/valid.json")
        self.assertIn("[0x100 - 1] = {", content)
        entry = content.split("[0x100 - 1] = {", 1)[1].split("};", 1)[0]
        self.assertIn(".number = 0,", entry)
        self.assertNotIn(".number = 0x100,", entry)
        self.assertNotIn(".number = 256,", entry)

    def test_default_fields_are_omitted(self):
        records = load_records(fixture_path("characters", "valid.json"))
        content = generate_c_source(records, "fixtures/characters/valid.json")
        entry = content.split("[0x1b - 1] = {", 1)[1].split("};", 1)[0]
        self.assertNotIn(".portraitId", entry)
        self.assertNotIn(".miniPortrait = 0,", entry)
        self.assertNotIn(".affinity", entry)
        self.assertNotIn(".sort_order", entry)
        self.assertNotIn(".attributes", entry)
        self.assertNotIn(".pSupportData", entry)
        self.assertNotIn(".visit_group", entry)
        self.assertIn(".miniPortrait = 2,", entry)

    def test_attributes_are_sorted_ascending_by_bit_value_regardless_of_json_order(self):
        records = load_records(fixture_path("characters", "valid.json"))
        for record in records:
            if record.character == "CHARACTER_EIRIKA":
                record.attributes = list(reversed(record.attributes))
        first_order = generate_c_source(records, "fixtures/characters/valid.json")

        records2 = load_records(fixture_path("characters", "valid.json"))
        second_order = generate_c_source(records2, "fixtures/characters/valid.json")
        self.assertEqual(first_order, second_order)


if __name__ == "__main__":
    unittest.main()
