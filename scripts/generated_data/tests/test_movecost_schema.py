import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.movecost import schema as movecost_schema
from scripts.generated_data.tests._util import fixture_path

_SMALL_HEADER = fixture_path("movecost", "terrains_small.h")


def _validate(fixture_name, terrains_header=_SMALL_HEADER):
    records = movecost_schema.load_records(fixture_path("movecost", fixture_name))
    diagnostics = DiagnosticCollector()
    movecost_schema.validate(records, diagnostics, terrains_header=terrains_header)
    return records, diagnostics


class MovecostSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 17)

    def test_valid_fixture_authors_47_arrays(self):
        records, _ = _validate("valid.json")
        array_count = sum(1 for r in records for _ in r.symbols())
        self.assertEqual(array_count, 47)


class MovecostSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_profile_detected(self):
        _, diagnostics = _validate("duplicate_profile.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate movecost profile 'CommonT2'" in m for m in messages), messages)
        self.assertTrue(
            any("duplicate movecost array symbol 'TerrainTable_MovCost_CommonT2Normal'" in m for m in messages),
            messages,
        )


class MovecostSchemaCoverageTests(unittest.TestCase):
    def test_missing_profile_detected(self):
        _, diagnostics = _validate("missing_profile.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("missing movecost profile 'Fly'" in m for m in messages), messages)

    def test_extra_profile_detected(self):
        _, diagnostics = _validate("extra_profile.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("unexpected movecost profile 'TotallyMadeUp'" in m for m in messages), messages)

    def test_gap_detected(self):
        _, diagnostics = _validate("gap.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any(
                "missing terrain coverage for 'TERRAIN_FOREST' in 'TerrainTable_MovCost_CommonT2Normal'" in m
                for m in messages
            ),
            messages,
        )

    def test_extra_terrain_key_detected(self):
        _, diagnostics = _validate("extra_terrain_key.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("TERRAIN_NOT_REAL" in m for m in messages), messages)


class MovecostSchemaRangeTests(unittest.TestCase):
    def test_bad_range_detected(self):
        _, diagnostics = _validate("bad_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("200" in m and "out of range" in m for m in messages), messages)


class MovecostSchemaSymbolTests(unittest.TestCase):
    def test_bad_symbol_detected(self):
        _, diagnostics = _validate("bad_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("does not match expected 'TerrainTable_MovCost_CommonT2Normal'" in m for m in messages), messages
        )


class MovecostSchemaSlotPresenceTests(unittest.TestCase):
    def test_missing_required_slot_detected(self):
        _, diagnostics = _validate("missing_slot.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("missing required 'snow' slot for movecost profile 'CommonT2'" in m for m in messages), messages
        )

    def test_unexpected_single_variant_slot_detected(self):
        _, diagnostics = _validate("extra_slot.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("single-variant profile -- 'rain' slot must be null" in m for m in messages), messages
        )


class MovecostHeaderConstantsTests(unittest.TestCase):
    def test_reads_terrain_enum_without_count_sentinel(self):
        names = movecost_schema.real_terrain_names()
        self.assertIn("TERRAIN_NONE", names)
        self.assertIn("TERRAIN_MAST", names)
        self.assertNotIn("TERRAIN_COUNT", names)

    def test_expected_symbol_named_profile(self):
        self.assertEqual(
            movecost_schema.expected_symbol("CommonT2", "normal"), "TerrainTable_MovCost_CommonT2Normal"
        )
        self.assertEqual(
            movecost_schema.expected_symbol("Fly", "snow"), "TerrainTable_MovCost_FlySnow"
        )

    def test_expected_symbol_single_variant(self):
        self.assertEqual(
            movecost_schema.expected_symbol("DemonKing", "normal"), "TerrainTable_MovCost_DemonKing"
        )
        self.assertIsNone(movecost_schema.expected_symbol("DemonKing", "rain"))
        self.assertEqual(
            movecost_schema.expected_symbol("Ballista", "normal"), "TerrainMoveCost_Ballista"
        )
        self.assertIsNone(movecost_schema.expected_symbol("Ballista", "snow"))


class MovecostSlotSymbolIndexTests(unittest.TestCase):
    def test_named_profile_indexed_per_slot(self):
        records, _ = _validate("valid.json")
        index = movecost_schema.build_slot_symbol_index(records)
        self.assertEqual(index["normal"]["TerrainTable_MovCost_CommonT2Normal"], "CommonT2")
        self.assertEqual(index["rain"]["TerrainTable_MovCost_CommonT2Rain"], "CommonT2")
        self.assertEqual(index["snow"]["TerrainTable_MovCost_CommonT2Snow"], "CommonT2")

    def test_single_variant_profile_resolves_in_every_slot(self):
        records, _ = _validate("valid.json")
        index = movecost_schema.build_slot_symbol_index(records)
        for slot_name in ("normal", "rain", "snow"):
            self.assertEqual(index[slot_name]["TerrainTable_MovCost_DemonKing"], "DemonKing")


if __name__ == "__main__":
    unittest.main()
