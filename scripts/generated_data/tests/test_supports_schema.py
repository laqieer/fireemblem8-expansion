import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector, GeneratedDataError
from scripts.generated_data.supports import schema as supports_schema
from scripts.generated_data.tests._util import fixture_path


def _validate(fixture_name):
    records = supports_schema.load_records(fixture_path("supports", fixture_name))
    diagnostics = DiagnosticCollector()
    supports_schema.validate(records, diagnostics)
    return records, diagnostics


class SupportsSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 3)


class SupportsSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_owner_detected(self):
        _, diagnostics = _validate("duplicate_owner.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate owner 'CHARACTER_EIRIKA'" in m for m in messages), messages)

    def test_duplicate_partner_detected(self):
        _, diagnostics = _validate("duplicate_partner.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate partner 'CHARACTER_SETH'" in m for m in messages), messages)


class SupportsSchemaReferenceTests(unittest.TestCase):
    def test_missing_character_reference_detected(self):
        _, diagnostics = _validate("missing_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined character reference 'CHARACTER_NOT_A_REAL_CHARACTER'" in m for m in messages),
            messages,
        )

    def test_self_reference_detected(self):
        _, diagnostics = _validate("self_reference.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("lists its own owner" in m for m in messages), messages)


class SupportsSchemaRangeTests(unittest.TestCase):
    def test_out_of_range_value_detected(self):
        _, diagnostics = _validate("out_of_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("supportExpBase 300 out of range [0, 255]" in m for m in messages), messages)


class SupportsSchemaCountTests(unittest.TestCase):
    def test_count_mismatch_detected(self):
        _, diagnostics = _validate("bad_count.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("does not match 2 partner entries" in m for m in messages), messages)

    def test_capacity_exceeded_detected(self):
        _, diagnostics = _validate("capacity_exceeded.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("support partners count 8 exceeds fixed capacity 7" in m for m in messages), messages
        )


class SupportsSchemaReciprocalTests(unittest.TestCase):
    def test_reciprocal_mismatch_detected(self):
        _, diagnostics = _validate("reciprocal_mismatch.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("reciprocal mismatch" in m for m in messages), messages)

    def test_reciprocal_missing_detected(self):
        _, diagnostics = _validate("reciprocal_missing.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("does not list 'CHARACTER_EIRIKA' back" in m for m in messages), messages
        )


class SupportsSchemaSourceLocationTests(unittest.TestCase):
    def test_diagnostics_reference_the_fixture_path(self):
        _, diagnostics = _validate("out_of_range.json")
        self.assertFalse(diagnostics.ok)
        for error in diagnostics.errors:
            self.assertIsNotNone(error.location)
            self.assertIn("out_of_range.json", str(error.location.path))
            self.assertGreaterEqual(error.location.line, 1)
            self.assertGreaterEqual(error.location.column, 1)


class UnitSupportMaxCountTests(unittest.TestCase):
    def test_reads_seven_from_types_header(self):
        self.assertEqual(supports_schema.read_unit_support_max_count(), 7)


if __name__ == "__main__":
    unittest.main()
