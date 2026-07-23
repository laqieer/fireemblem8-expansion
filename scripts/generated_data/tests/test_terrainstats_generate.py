import os
import unittest

from scripts.generated_data.terrainstats.generate import generate_c_source
from scripts.generated_data.terrainstats.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "terrainstats.json")


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(REAL_SOURCE)
        first = generate_c_source(records, REAL_SOURCE)
        second = generate_c_source(records, REAL_SOURCE)
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_and_includes(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        for symbol in (
            "TerrainTable_Avo_Common",
            "TerrainTable_Def_Common",
            "TerrainTable_Res_Common",
            "TerrainTable_Avo_Fly",
            "TerrainTable_Def_Fly",
            "TerrainTable_Res_Fly",
            "TerrainTable_HealAmount",
            "TerrainTable_HealsStatus",
        ):
            self.assertIn(symbol, content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "constants/terrains.h"', content)
        self.assertIn("[TERRAIN_NONE] = ", content)
        self.assertIn("[TERRAIN_MAST] = ", content)


class HealSectionSplitTests(unittest.TestCase):
    """Issue #5 Batch 1: TerrainTable_HealAmount/TerrainTable_HealsStatus's
    hand-written home in src/data_terrains.c is separated from the other 6
    arrays by 5 still-hand Unk_TerrainTable_N arrays, so the generated
    object must place these two symbols in a distinct section for
    ldscript.txt to slot each half in at its own exact original address."""

    def test_common_fly_arrays_stay_in_default_const_data_section(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertIn("CONST_DATA s8 TerrainTable_Avo_Common[] = {", content)
        self.assertIn("CONST_DATA s8 TerrainTable_Res_Fly[] = {", content)

    def test_heal_arrays_get_their_own_dedicated_section(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertIn('SECTION(".data.terrainheal") s8 TerrainTable_HealAmount[] = {', content)
        self.assertIn('SECTION(".data.terrainheal") s8 TerrainTable_HealsStatus[] = {', content)
        self.assertNotIn("CONST_DATA s8 TerrainTable_HealAmount", content)
        self.assertNotIn("CONST_DATA s8 TerrainTable_HealsStatus", content)


class FixtureGenerateTests(unittest.TestCase):
    def test_fixture_generation_produces_heal_symbol(self):
        records = load_records(fixture_path("terrainstats", "valid.json"))
        content = generate_c_source(records, fixture_path("terrainstats", "valid.json"))
        self.assertIn("TerrainTable_HealsStatus", content)


if __name__ == "__main__":
    unittest.main()
