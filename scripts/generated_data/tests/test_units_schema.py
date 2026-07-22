import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.units import schema as units_schema
from scripts.generated_data.tests._util import fixture_path


def _validate(fixture_name):
    records = units_schema.load_records(fixture_path("units", fixture_name))
    diagnostics = DiagnosticCollector()
    units_schema.validate(records, diagnostics)
    return records, diagnostics


class UnitsSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0].units), 2)

    def test_generic_filler_unit_accepts_raw_int_char_index(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(records[0].units[1].char_index, 142)


class UnitsSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_group_symbol_detected(self):
        _, diagnostics = _validate("duplicate_group.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate unit group symbol 'UnitDef_Dup'" in m for m in messages), messages)


class UnitsSchemaReferenceTests(unittest.TestCase):
    def test_missing_character_reference_detected(self):
        _, diagnostics = _validate("missing_char_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined character reference 'CHARACTER_NOT_A_REAL_CHARACTER'" in m for m in messages), messages
        )

    def test_missing_class_reference_detected(self):
        _, diagnostics = _validate("missing_class_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined class reference 'CLASS_NOT_A_REAL_CLASS'" in m for m in messages), messages)

    def test_event_autoload_slot_sentinels_rejected_as_char_index(self):
        """CHARACTER_EVT_LEADER/ACTIVE/SLOTB/SLOT2 belong to the separate
        ``event_autoload_pid_idx`` enum (active-unit-slot indices, two of
        them negative) -- they share the ``CHARACTER_`` textual prefix
        with real designators but must never be accepted as a unit's
        ``charIndex``."""
        _, diagnostics = _validate("char_evt_sentinels.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        for sentinel in (
            "CHARACTER_EVT_LEADER", "CHARACTER_EVT_ACTIVE", "CHARACTER_EVT_SLOTB", "CHARACTER_EVT_SLOT2",
        ):
            self.assertTrue(
                any("undefined character reference '{}'".format(sentinel) in m for m in messages),
                (sentinel, messages),
            )

    def test_synthetic_sibling_enum_collision_excluded(self):
        """Wiring-level proof (not just the shared reader in isolation):
        a unit referencing the synthetic sibling enum's
        ``CHARACTER_SIBLING_FAKE`` must be rejected even though the
        primary block's own ``CHARACTER_ALPHA``/``CHARACTER_BETA``
        members (sharing the mini header's namespace) are accepted."""
        records = units_schema.load_records(fixture_path("units", "valid.json"))
        records[0].units[0].char_index = "CHARACTER_SIBLING_FAKE"
        diagnostics = DiagnosticCollector()
        units_schema.validate(
            records, diagnostics,
            characters_header=fixture_path("character_refs", "mini_characters_sibling_enum.h"),
        )
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined character reference 'CHARACTER_SIBLING_FAKE'" in m for m in messages), messages
        )

    def test_missing_item_reference_detected(self):
        _, diagnostics = _validate("missing_item_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined item reference 'ITEM_NOT_A_REAL_ITEM'" in m for m in messages), messages)


class UnitsSchemaRangeTests(unittest.TestCase):
    def test_out_of_range_level_detected(self):
        _, diagnostics = _validate("bad_level.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("level 99 out of range [1, 31]" in m for m in messages), messages)

    def test_out_of_range_position_detected(self):
        _, diagnostics = _validate("bad_position.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("xPosition 999 out of range [0, 63]" in m for m in messages), messages)

    def test_out_of_range_reda_detected(self):
        _, diagnostics = _validate("bad_reda_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("x 999 out of range [0, 63]" in m for m in messages), messages)


class UnitsSchemaCapacityTests(unittest.TestCase):
    def test_items_capacity_exceeded_detected(self):
        _, diagnostics = _validate("capacity_exceeded_items.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("items count 5 exceeds fixed capacity 4" in m for m in messages), messages)

    def test_ai_wrong_length_detected(self):
        _, diagnostics = _validate("bad_ai_length.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("ai array has 3 entries, expected exactly 4 (UDEF_AIIDX_MAX)" in m for m in messages), messages
        )


class UnitsSchemaSourceLocationTests(unittest.TestCase):
    def test_diagnostics_reference_the_fixture_path(self):
        _, diagnostics = _validate("bad_level.json")
        self.assertFalse(diagnostics.ok)
        for error in diagnostics.errors:
            self.assertIsNotNone(error.location)
            self.assertIn("bad_level.json", str(error.location.path))
            self.assertGreaterEqual(error.location.line, 1)


class UnitsHeaderConstantsTests(unittest.TestCase):
    def test_reads_four_from_bmunit_header(self):
        self.assertEqual(units_schema.read_unit_definition_item_count(), 4)

    def test_derives_four_ai_slots_from_bmunit_header(self):
        self.assertEqual(units_schema.read_udef_aiidx_max(), 4)


if __name__ == "__main__":
    unittest.main()
