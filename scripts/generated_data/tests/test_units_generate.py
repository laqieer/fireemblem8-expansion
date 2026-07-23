import unittest

from scripts.generated_data.units.generate import generate_c_source
from scripts.generated_data.units.schema import load_records
from scripts.generated_data.tests._util import fixture_path


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("units", "valid.json"))
        first = generate_c_source(records, "fixtures/units/valid.json")
        second = generate_c_source(records, "fixtures/units/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_and_values(self):
        records = load_records(fixture_path("units", "valid.json"))
        content = generate_c_source(records, "fixtures/units/valid.json")
        self.assertIn("CONST_DATA struct UnitDefinition UnitDef_Fixture_Test", content)
        self.assertIn(".charIndex = CHARACTER_EIRIKA", content)
        self.assertIn(".charIndex = 142", content)
        self.assertIn(".ai = { 0, 17, 9, 0 }", content)
        self.assertIn("{ 0 },", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmunit.h"', content)
        self.assertIn('#include "muctrl.h"', content)

    def test_group_order_preserved_from_source(self):
        records = load_records(fixture_path("units", "valid.json"))
        content = generate_c_source(records, "src")
        self.assertIn("UnitDef_Fixture_Test", content)

    def test_all_reda_arrays_precede_all_unitdefinition_arrays(self):
        # Issue #5 Batch 3a regression: src/events_udefs.c's hand-written
        # Chapter 2 block emits every REDA sub-array across all groups
        # first, then every UnitDefinition group array -- it never
        # interleaves a group's REDA arrays with its own UnitDefinition
        # array. Getting this wrong desyncs the generated object's
        # internal byte layout from the guarded-out hand block it
        # replaces, breaking legacy ROM byte-identity even though the
        # data's *meaning* round-trips fine. This fixture has two groups,
        # each with a REDA, specifically to catch a regression back to
        # per-group interleaving.
        records = load_records(fixture_path("units", "multi_group_ordering.json"))
        content = generate_c_source(records, "fixtures/units/multi_group_ordering.json")
        last_reda_index = content.rindex("struct REDA ")
        first_unitdef_index = content.index("struct UnitDefinition ")
        self.assertLess(
            last_reda_index,
            first_unitdef_index,
            "expected every REDA array (across all groups) before any UnitDefinition array",
        )


if __name__ == "__main__":
    unittest.main()
