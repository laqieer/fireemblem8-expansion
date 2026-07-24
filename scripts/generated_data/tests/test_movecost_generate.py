import os
import unittest

from scripts.generated_data.movecost.generate import generate_c_source
from scripts.generated_data.movecost.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "movecost.json")


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
            "TerrainTable_MovCost_CommonT2Normal",
            "TerrainTable_MovCost_FlySnow",
            "TerrainTable_MovCost_DemonKing",
            "TerrainMoveCost_Ballista",
        ):
            self.assertIn(symbol, content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "constants/terrains.h"', content)
        self.assertIn("[TERRAIN_NONE] = ", content)
        self.assertIn("[TERRAIN_MAST] = ", content)

    def test_generated_output_defines_all_47_arrays(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertEqual(content.count(" s8 "), 47)


class DeclarationOrderTests(unittest.TestCase):
    """Emission order must match src/data_terrains.c field-for-field: all
    15 named profiles' normal slot, then DemonKing/Ballista's lone normal
    slot, then all 15 named profiles' rain slot, then all 15 named
    profiles' snow slot -- not JSON authoring order."""

    def test_normal_block_precedes_rain_block(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertLess(
            content.index("TerrainTable_MovCost_FlyNormal"),
            content.index("TerrainTable_MovCost_CommonT2Rain"),
        )

    def test_demon_king_and_ballista_between_normal_and_rain_blocks(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        fly_normal = content.index("TerrainTable_MovCost_FlyNormal")
        demon_king = content.index("TerrainTable_MovCost_DemonKing")
        ballista = content.index("TerrainMoveCost_Ballista")
        common_t2_rain = content.index("TerrainTable_MovCost_CommonT2Rain")
        self.assertLess(fly_normal, demon_king)
        self.assertLess(demon_king, ballista)
        self.assertLess(ballista, common_t2_rain)

    def test_snow_block_is_last(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        fly_rain = content.index("TerrainTable_MovCost_FlyRain")
        common_t2_snow = content.index("TerrainTable_MovCost_CommonT2Snow")
        self.assertLess(fly_rain, common_t2_snow)


class SnowSectionSplitTests(unittest.TestCase):
    """Issue #5 Batch 2: the 15 named profiles' snow slot is separated
    from the rest of the movement-cost arrays by Unk_TerrainTable_1, so
    the generated object must place these 15 symbols in a distinct
    section for ldscript.txt to slot each half in at its own exact
    original address."""

    def test_normal_and_rain_stay_in_default_const_data_section(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertIn("CONST_DATA s8 TerrainTable_MovCost_CommonT2Normal[] = {", content)
        self.assertIn("CONST_DATA s8 TerrainTable_MovCost_FlyRain[] = {", content)
        self.assertIn("CONST_DATA s8 TerrainTable_MovCost_DemonKing[] = {", content)
        self.assertIn("CONST_DATA s8 TerrainMoveCost_Ballista[] = {", content)

    def test_snow_arrays_get_their_own_dedicated_section(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertIn('SECTION(".data.movecostsnow") s8 TerrainTable_MovCost_CommonT2Snow[] = {', content)
        self.assertIn('SECTION(".data.movecostsnow") s8 TerrainTable_MovCost_FlySnow[] = {', content)
        self.assertNotIn("CONST_DATA s8 TerrainTable_MovCost_CommonT2Snow", content)
        self.assertNotIn("CONST_DATA s8 TerrainTable_MovCost_FlySnow", content)


class FixtureGenerateTests(unittest.TestCase):
    def test_fixture_generation_produces_snow_symbol(self):
        records = load_records(fixture_path("movecost", "valid.json"))
        content = generate_c_source(records, fixture_path("movecost", "valid.json"))
        self.assertIn("TerrainTable_MovCost_CommonT2Snow", content)
        self.assertIn("TerrainTable_MovCost_DemonKing", content)


if __name__ == "__main__":
    unittest.main()
