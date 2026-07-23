import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.terrainstats import schema as terrainstats_schema
from scripts.generated_data.tests._util import fixture_path

_SMALL_HEADER = fixture_path("terrainstats", "terrains_small.h")


def _validate(fixture_name, terrains_header=_SMALL_HEADER):
    records = terrainstats_schema.load_records(fixture_path("terrainstats", fixture_name))
    diagnostics = DiagnosticCollector()
    terrainstats_schema.validate(records, diagnostics, terrains_header=terrains_header)
    return records, diagnostics


class TerrainStatsSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 8)


class TerrainStatsSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_symbol_detected(self):
        _, diagnostics = _validate("duplicate_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("duplicate terrain-stats array 'TerrainTable_Avo_Common'" in m for m in messages), messages
        )


class TerrainStatsSchemaCoverageTests(unittest.TestCase):
    def test_missing_symbol_detected(self):
        _, diagnostics = _validate("missing_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("missing terrain-stats array 'TerrainTable_HealsStatus'" in m for m in messages), messages)

    def test_extra_symbol_detected(self):
        _, diagnostics = _validate("extra_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("unexpected terrain-stats symbol 'TerrainTable_NotReal'" in m for m in messages), messages)

    def test_gap_detected(self):
        _, diagnostics = _validate("gap.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("missing terrain coverage for 'TERRAIN_FOREST' in 'TerrainTable_Avo_Common'" in m for m in messages),
            messages,
        )

    def test_extra_terrain_key_detected(self):
        _, diagnostics = _validate("extra_terrain_key.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("TERRAIN_NOT_REAL" in m for m in messages), messages)


class TerrainStatsSchemaRangeTests(unittest.TestCase):
    def test_bad_range_detected(self):
        _, diagnostics = _validate("bad_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("200" in m and "out of range" in m for m in messages), messages)


class TerrainStatsSchemaFieldMobilityTests(unittest.TestCase):
    def test_bad_field_detected(self):
        _, diagnostics = _validate("bad_field.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("does not match expected 'terrainAvoid'" in m for m in messages), messages
        )

    def test_bad_mobility_detected(self):
        _, diagnostics = _validate("bad_mobility.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("does not match expected 'common'" in m for m in messages), messages
        )


class TerrainStatsHeaderConstantsTests(unittest.TestCase):
    def test_reads_terrain_enum_without_count_sentinel(self):
        names = terrainstats_schema.real_terrain_names()
        self.assertIn("TERRAIN_NONE", names)
        self.assertIn("TERRAIN_MAST", names)
        self.assertNotIn("TERRAIN_COUNT", names)

    def test_symbols_for_role(self):
        self.assertEqual(
            terrainstats_schema.symbols_for_role("terrainAvoid", "common"), "TerrainTable_Avo_Common"
        )
        self.assertEqual(
            terrainstats_schema.symbols_for_role("terrainResistance", "fly"), "TerrainTable_Res_Fly"
        )
        self.assertIsNone(terrainstats_schema.symbols_for_role("terrainAvoid", "swim"))


if __name__ == "__main__":
    unittest.main()
