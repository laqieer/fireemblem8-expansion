import unittest

from scripts.generated_data.diagnostics import GeneratedDataError, SourceLocation
from scripts.generated_data.escape_hatch import CSymbolRefField
from scripts.generated_data.tests._util import fixture_path

_HEADER = fixture_path("escape_hatch", "demo_header.h")
_LOC = SourceLocation("demo.json", 3, 5)


class CSymbolRefFieldTests(unittest.TestCase):
    def setUp(self):
        self.field = CSymbolRefField(_HEADER, description="demo callback")

    def test_declared_symbols_finds_function_and_extern(self):
        declared = self.field.declared_symbols()
        self.assertIn("DemoCallback_OnSupportGained", declared)
        self.assertIn("DemoCallback_CanSupport", declared)
        self.assertIn("gDemoAllowlistedTable", declared)

    def test_valid_symbol_passes(self):
        errors = self.field.validate("DemoCallback_OnSupportGained", _LOC, "demo.callback")
        self.assertEqual(errors, [])

    def test_valid_extern_variable_symbol_passes(self):
        errors = self.field.validate("gDemoAllowlistedTable", _LOC, "demo.table")
        self.assertEqual(errors, [])

    def test_malformed_identifier_rejected(self):
        for bad in ("1LeadingDigit", "has space", "trailing;semicolon", "", "kebab-case"):
            with self.subTest(bad=bad):
                errors = self.field.validate(bad, _LOC, "demo.callback")
                self.assertEqual(len(errors), 1)
                self.assertIn("not a valid C identifier", str(errors[0]))

    def test_undeclared_symbol_rejected(self):
        errors = self.field.validate("TotallyMadeUpSymbol", _LOC, "demo.callback")
        self.assertEqual(len(errors), 1)
        self.assertIn("is not declared in", str(errors[0]))
        self.assertIn(_HEADER, str(errors[0]))

    def test_missing_symbol_location_and_reference_path_preserved(self):
        errors = self.field.validate("Nope", _LOC, "demo.callback[2]")
        self.assertEqual(errors[0].location, _LOC)
        self.assertEqual(errors[0].reference_path, "demo.callback[2]")

    def test_emit_is_unquoted_passthrough(self):
        self.assertEqual(CSymbolRefField.emit("DemoCallback_OnSupportGained"), "DemoCallback_OnSupportGained")

    def test_declared_symbols_not_duplicated_across_patterns(self):
        # A function declaration must not also be picked up by the extern
        # object pattern (they're mutually exclusive by construction).
        declared = self.field.declared_symbols()
        self.assertEqual(len(declared), 3)


if __name__ == "__main__":
    unittest.main()
