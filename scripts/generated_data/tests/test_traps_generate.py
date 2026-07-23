import os
import unittest

from scripts.generated_data.traps.generate import generate_c_source
from scripts.generated_data.traps.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "ch2_traps.json")


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("traps", "valid.json"))
        first = generate_c_source(records, "fixtures/traps/valid.json")
        second = generate_c_source(records, "fixtures/traps/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_values_and_terminator(self):
        records = load_records(fixture_path("traps", "valid.json"))
        content = generate_c_source(records, "fixtures/traps/valid.json")
        self.assertIn("CONST_DATA u8 TrapData_Fixture_Test", content)
        self.assertIn("TRAP_BALLISTA,", content)
        self.assertIn("ITEM_BALLISTA_REGULAR,", content)
        self.assertIn("TRAP_GORGON_EGG,", content)
        self.assertIn("TRAP_NONE", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmtrick.h"', content)
        self.assertIn('#include "constants/items.h"', content)


class Ch2SectionSplitTests(unittest.TestCase):
    """Issue #5 Batch 3b: TrapData_Event_Ch2Hard's hand-written home in
    src/events_trapdata.c is a separate, non-adjacent hard-mode block, not
    a suffix of TrapData_Event_Ch2's own block, so the generated object
    must place the two symbols in distinct sections for ldscript.txt to
    slot each in at its own exact original address."""

    def test_ch2_normal_stays_in_default_const_data_section(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertIn("CONST_DATA u8 TrapData_Event_Ch2[] = {", content)

    def test_ch2_hard_gets_its_own_dedicated_section(self):
        records = load_records(REAL_SOURCE)
        content = generate_c_source(records, REAL_SOURCE)
        self.assertIn('SECTION(".data.trapch2hard") u8 TrapData_Event_Ch2Hard[] = {', content)
        self.assertNotIn("CONST_DATA u8 TrapData_Event_Ch2Hard", content)


if __name__ == "__main__":
    unittest.main()
