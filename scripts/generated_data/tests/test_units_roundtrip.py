import os
import unittest

from scripts.generated_data.units import parser as units_parser
from scripts.generated_data.units.schema import load_records
from scripts.generated_data.tests._util import fixture_path, scratch_dir

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "events_udefs.c")

REAL_GROUP_SYMBOLS = [
    "UnitDef_Event_Ch2Ally",
    "UnitDef_Ch2Enemy_0",
    "UnitDef_LordSplitAlly",
    "UnitDef_Ch2Ally",
    "UnitDef_Ch2NPC",
    "UnitDef_Ch2Enemy_1",
    "UnitDef_Ch2Enemy_2",
]


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_all_seven_ch2_groups(self):
        groups = units_parser.parse_hand_written(REAL_HAND_FILE, REAL_GROUP_SYMBOLS)
        self.assertEqual(set(groups), set(REAL_GROUP_SYMBOLS))
        self.assertEqual(len(groups["UnitDef_Event_Ch2Ally"]["units"]), 5)
        self.assertEqual(len(groups["UnitDef_Ch2Enemy_0"]["units"]), 6)
        self.assertEqual(len(groups["UnitDef_LordSplitAlly"]["units"]), 2)
        self.assertEqual(len(groups["UnitDef_Ch2Ally"]["units"]), 1)
        self.assertEqual(len(groups["UnitDef_Ch2NPC"]["units"]), 2)
        self.assertEqual(len(groups["UnitDef_Ch2Enemy_1"]["units"]), 2)
        self.assertEqual(len(groups["UnitDef_Ch2Enemy_2"]["units"]), 1)

    def test_missing_group_symbol_raises(self):
        with self.assertRaises(Exception):
            units_parser.parse_hand_written(REAL_HAND_FILE, ["UnitDef_TotallyMadeUp"])


class FullRoundTripTests(unittest.TestCase):
    """Every Ch2 UnitDefinition record must round-trip semantically against
    src/events_udefs.c -- not just examples (Issue #5 Batch A DONE)."""

    def test_generated_model_matches_every_hand_written_group(self):
        generated_groups = load_records(REAL_SOURCE)
        self.assertEqual({g.symbol for g in generated_groups}, set(REAL_GROUP_SYMBOLS))

        hand_groups = units_parser.parse_hand_written(REAL_HAND_FILE, REAL_GROUP_SYMBOLS)
        errors = units_parser.compare_groups(generated_groups, hand_groups, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))


class FixtureRoundTripTests(unittest.TestCase):
    def test_matching_fixture_passes(self):
        generated_groups = load_records(fixture_path("units", "valid.json"))
        hand_groups = units_parser.parse_hand_written(
            fixture_path("units", "valid_hand.c"), ["UnitDef_Fixture_Test"]
        )
        errors = units_parser.compare_groups(
            generated_groups, hand_groups, hand_path=fixture_path("units", "valid_hand.c")
        )
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_mismatched_fixture_detected(self):
        generated_groups = load_records(fixture_path("units", "valid.json"))
        hand_groups = units_parser.parse_hand_written(
            fixture_path("units", "mismatched_hand.c"), ["UnitDef_Fixture_Test"]
        )
        errors = units_parser.compare_groups(
            generated_groups, hand_groups, hand_path=fixture_path("units", "mismatched_hand.c")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("level mismatch" in m for m in messages), messages)


class RoundTripMismatchDetectionTests(unittest.TestCase):
    def test_detects_missing_hand_group(self):
        generated_groups = load_records(REAL_SOURCE)
        hand_groups = units_parser.parse_hand_written(REAL_HAND_FILE, REAL_GROUP_SYMBOLS)
        del hand_groups["UnitDef_Ch2Ally"]

        errors = units_parser.compare_groups(generated_groups, hand_groups, hand_path=REAL_HAND_FILE)
        self.assertTrue(any("UnitDef_Ch2Ally" in str(e) for e in errors))

    def test_detects_unit_count_mismatch(self):
        generated_groups = load_records(REAL_SOURCE)
        hand_groups = units_parser.parse_hand_written(REAL_HAND_FILE, REAL_GROUP_SYMBOLS)
        hand_groups["UnitDef_Ch2Ally"]["units"] = hand_groups["UnitDef_Ch2Ally"]["units"] * 2

        errors = units_parser.compare_groups(generated_groups, hand_groups, hand_path=REAL_HAND_FILE)
        messages = [str(e) for e in errors]
        self.assertTrue(any("unit count mismatch for UnitDef_Ch2Ally" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
