import unittest

from scripts.generated_data.classes.generate import generate_c_source
from scripts.generated_data.classes.schema import load_records
from scripts.generated_data.tests._util import fixture_path


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("classes", "valid.json"))
        first = generate_c_source(records, "fixtures/classes/valid.json")
        second = generate_c_source(records, "fixtures/classes/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_and_values(self):
        records = load_records(fixture_path("classes", "valid.json"))
        content = generate_c_source(records, "fixtures/classes/valid.json")
        self.assertIn("CONST_DATA struct ClassData gClassData[] = {", content)
        self.assertIn("[CLASS_FIXTURE_A - 1] = {", content)
        self.assertIn(".number = CLASS_FIXTURE_A,", content)
        self.assertIn(".promotion = CLASS_FIXTURE_B,", content)
        self.assertIn(".slowWalking = UNIT_WALKSPEED_SLOW,", content)
        self.assertIn(".baseHP = 10,", content)
        self.assertIn(".pBattleAnimDef = AnimConf_Fixture,", content)
        self.assertIn("[ITYPE_SWORD] = WPN_EXP_D,", content)
        self.assertIn("._pU50 = &Unk_TerrainTable_Fixture,", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmunit.h"', content)
        self.assertIn('#include "bmitem.h"', content)
        self.assertIn('#include "ekrbattle.h"', content)
        self.assertIn('#include "constants/classes.h"', content)

    def test_null_mov_cost_table_entries_emit_null_pointer_literal(self):
        records = load_records(fixture_path("classes", "valid.json"))
        content = generate_c_source(records, "fixtures/classes/valid.json")
        fixture_b = content.split("[CLASS_FIXTURE_B - 1] = {", 1)[1].split("};", 1)[0]
        self.assertIn(
            ".pMovCostTable = {\n\t\t\t(void *)0x00000000,\n\t\t\t(void *)0x00000000,\n\t\t\t(void *)0x00000000,\n\t\t},",
            fixture_b,
        )

    def test_default_fields_are_omitted(self):
        records = load_records(fixture_path("classes", "valid.json"))
        content = generate_c_source(records, "fixtures/classes/valid.json")
        fixture_b_entry = content.split("[CLASS_FIXTURE_B - 1] = {", 1)[1].split("};", 1)[0]
        self.assertNotIn(".nameTextId", fixture_b_entry)
        self.assertNotIn(".descTextId", fixture_b_entry)
        self.assertNotIn(".promotion =", fixture_b_entry)
        self.assertNotIn(".slowWalking", fixture_b_entry)
        self.assertNotIn(".sort_order", fixture_b_entry)
        self.assertNotIn(".classRelativePower", fixture_b_entry)
        self.assertNotIn(".attributes", fixture_b_entry)
        self.assertNotIn(".baseRanks", fixture_b_entry)
        self.assertNotIn(".pBattleAnimDef", fixture_b_entry)
        self.assertNotIn("._pU50", fixture_b_entry)
        self.assertIn(".SMSId = 2,", fixture_b_entry)
        self.assertIn(".defaultPortraitId = 2,", fixture_b_entry)

    def test_attributes_are_sorted_ascending_by_bit_value_regardless_of_json_order(self):
        records = load_records(fixture_path("classes", "valid.json"))
        for record in records:
            if record.class_name == "CLASS_FIXTURE_A":
                record.attributes = list(reversed(record.attributes))
        first_order = generate_c_source(records, "fixtures/classes/valid.json")

        records2 = load_records(fixture_path("classes", "valid.json"))
        second_order = generate_c_source(records2, "fixtures/classes/valid.json")
        self.assertEqual(first_order, second_order)


if __name__ == "__main__":
    unittest.main()
