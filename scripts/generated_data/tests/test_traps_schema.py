import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.traps import schema as traps_schema
from scripts.generated_data.tests._util import fixture_path


def _validate(fixture_name, capacity=None):
    records = traps_schema.load_records(fixture_path("traps", fixture_name))
    diagnostics = DiagnosticCollector()
    if capacity is None:
        traps_schema.validate(records, diagnostics)
    else:
        traps_schema.validate(records, diagnostics, capacity=capacity)
    return records, diagnostics


class TrapsSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0].entries), 2)
        self.assertEqual(records[0].entries[0].subtype, "ITEM_BALLISTA_REGULAR")
        self.assertEqual(records[0].entries[1].subtype, 0)


class TrapsSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_symbol_detected(self):
        _, diagnostics = _validate("duplicate_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate trap array symbol 'TrapData_Dup'" in m for m in messages), messages)


class TrapsSchemaReferenceTests(unittest.TestCase):
    def test_bad_trap_type_detected(self):
        _, diagnostics = _validate("bad_type.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined trap type reference 'TRAP_NOT_REAL'" in m for m in messages), messages)


class TrapsSchemaTerminatorTests(unittest.TestCase):
    def test_explicit_terminator_rejected(self):
        _, diagnostics = _validate("explicit_terminator.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("must not include the TRAP_NONE terminator explicitly" in m for m in messages), messages)


class TrapsSchemaRangeTests(unittest.TestCase):
    def test_bad_subtype_range_detected(self):
        _, diagnostics = _validate("bad_subtype_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("subtype 999 out of range [0, 255]" in m for m in messages), messages)

    def test_bad_position_range_detected(self):
        _, diagnostics = _validate("bad_position.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("xPosition 500 out of range [0, 255]" in m for m in messages), messages)


class TrapsSchemaCapacityTests(unittest.TestCase):
    def test_capacity_override_detects_exceeded_entries(self):
        _, diagnostics = _validate("valid.json", capacity=1)
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("trap entries count 2 exceeds fixed capacity 1" in m for m in messages), messages)


class TrapsHeaderConstantsTests(unittest.TestCase):
    def test_reads_trap_max_count_from_bmtrick_header(self):
        self.assertEqual(traps_schema.read_trap_max_count(), 64)

    def test_reads_trap_type_enum_without_unrelated_constants(self):
        types = traps_schema.read_trap_types()
        self.assertIn("TRAP_NONE", types)
        self.assertIn("TRAP_BALLISTA", types)
        self.assertNotIn("TRAP_MAX_COUNT", types)


if __name__ == "__main__":
    unittest.main()
