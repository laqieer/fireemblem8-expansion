import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.shops import schema as shops_schema
from scripts.generated_data.tests._util import fixture_path


def _validate(fixture_name):
    records = shops_schema.load_records(fixture_path("shops", fixture_name))
    diagnostics = DiagnosticCollector()
    shops_schema.validate(records, diagnostics)
    return records, diagnostics


class ShopsSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].items, ["ITEM_SWORD_SLIM", "ITEM_SWORD_IRON", "ITEM_VULNERARY"])


class ShopsSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_symbol_detected(self):
        _, diagnostics = _validate("duplicate_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate shop symbol 'ShopList_Dup'" in m for m in messages), messages)


class ShopsSchemaReferenceTests(unittest.TestCase):
    def test_missing_item_reference_detected(self):
        _, diagnostics = _validate("missing_item_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined item reference 'ITEM_NOT_A_REAL_ITEM'" in m for m in messages), messages)


class ShopsSchemaTerminatorTests(unittest.TestCase):
    def test_empty_items_detected(self):
        _, diagnostics = _validate("empty_items.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("has no items" in m for m in messages), messages)

    def test_explicit_terminator_rejected(self):
        _, diagnostics = _validate("explicit_terminator.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("must not include the ITEM_NONE terminator explicitly" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
