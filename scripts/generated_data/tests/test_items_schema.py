import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.items import schema as items_schema
from scripts.generated_data.tests._util import fixture_path

_MINI_ITEMS_HEADER = fixture_path("items", "mini_items.h")
_MINI_BMITEM_HEADER = fixture_path("items", "mini_bmitem.h")
_MINI_VARIABLES_HEADER = fixture_path("items", "mini_variables.h")
_MINI_MSG_HEADER = fixture_path("items", "mini_msg.h")
_MINI_ICON_SOURCE = fixture_path("items", "mini_item_icon.c")


def _validate(fixture_name):
    records = items_schema.load_records(fixture_path("items", fixture_name))
    diagnostics = DiagnosticCollector()
    items_schema.validate(
        records,
        diagnostics,
        items_header=_MINI_ITEMS_HEADER,
        bmitem_header=_MINI_BMITEM_HEADER,
        variables_header=_MINI_VARIABLES_HEADER,
        msg_header=_MINI_MSG_HEADER,
        icon_source=_MINI_ICON_SOURCE,
    )
    return records, diagnostics


class ItemsSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 3)

    def test_encoded_range_and_pointer_fields_resolved(self):
        records, _ = _validate("valid.json")
        by_item = {r.item: r for r in records}
        self.assertEqual(by_item["ITEM_FIXTURE_A"].encoded_range, 0x11)
        self.assertEqual(by_item["ITEM_FIXTURE_B"].stat_bonuses, "ItemBonus_FixtureBonus")
        self.assertEqual(by_item["ITEM_FIXTURE_B"].effectiveness, "ItemEffectiveness_FixtureTarget")


class ItemsSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_item_detected(self):
        _, diagnostics = _validate("duplicate_id.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate item entry 'ITEM_FIXTURE_A'" in m for m in messages), messages)


class ItemsSchemaCoverageTests(unittest.TestCase):
    def test_gap_in_coverage_detected(self):
        _, diagnostics = _validate("gap.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("missing item coverage for 'ITEM_FIXTURE_A' (index 1)" in m for m in messages), messages
        )


class ItemsSchemaReferenceTests(unittest.TestCase):
    def test_bad_item_reference_detected(self):
        _, diagnostics = _validate("bad_item_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined item reference 'ITEM_NOT_A_REAL_ITEM'" in m for m in messages), messages)

    def test_bad_weapon_type_detected(self):
        _, diagnostics = _validate("bad_weapon_type.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined weapon type reference 'ITYPE_NOT_A_REAL_TYPE'" in m for m in messages), messages
        )

    def test_bad_required_wexp_detected(self):
        _, diagnostics = _validate("bad_required_wexp.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined weapon-exp threshold reference 'WPN_EXP_NOT_REAL'" in m for m in messages), messages
        )

    def test_bad_weapon_effect_detected(self):
        _, diagnostics = _validate("bad_weapon_effect.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined weapon effect reference 'WPN_EFFECT_NOT_REAL'" in m for m in messages), messages
        )

    def test_bad_stat_bonuses_pointer_detected(self):
        _, diagnostics = _validate("bad_stat_bonuses_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("ItemBonus_NotReal" in m for m in messages), messages)

    def test_bad_effectiveness_pointer_detected(self):
        _, diagnostics = _validate("bad_effectiveness_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("ItemEffectiveness_NotReal" in m for m in messages), messages)


class ItemsSchemaAttributeTests(unittest.TestCase):
    def test_bad_attribute_flag_detected(self):
        _, diagnostics = _validate("bad_attribute.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined item attribute reference 'IA_NOT_A_REAL_FLAG'" in m for m in messages), messages
        )

    def test_duplicate_attribute_flag_detected(self):
        _, diagnostics = _validate("duplicate_attribute.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("duplicate item attribute 'IA_WEAPON' in the same list" in m for m in messages), messages
        )


class ItemsSchemaRangeTests(unittest.TestCase):
    def test_bad_text_id_detected(self):
        _, diagnostics = _validate("bad_text_id.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("nameTextId 9999 out of range [0, 9]" in m for m in messages), messages)

    def test_bad_range_nibble_detected(self):
        _, diagnostics = _validate("bad_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("range.max 99 out of range [0, 15]" in m for m in messages), messages)

    def test_bad_icon_id_detected(self):
        _, diagnostics = _validate("bad_icon_id.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("iconId 500 out of range [0, 1]" in m for m in messages), messages)

    def test_bad_use_effect_detected(self):
        _, diagnostics = _validate("bad_use_effect.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("useEffect 999 out of range [0, 255]" in m for m in messages), messages)


class ItemsHeaderConstantsTests(unittest.TestCase):
    def test_reads_item_attributes_without_helper_combos(self):
        flags = items_schema.read_item_attributes()
        self.assertIn("IA_WEAPON", flags)
        self.assertIn("IA_STAFF", flags)
        self.assertNotIn("IA_REQUIRES_WEXP", flags)
        self.assertNotIn("IA_LOCK_ANY", flags)

    def test_reads_live_msg_count(self):
        self.assertGreater(items_schema.read_msg_count(), 0)

    def test_reads_live_item_icon_count(self):
        self.assertGreater(items_schema.read_item_icon_count(), 0)


if __name__ == "__main__":
    unittest.main()
