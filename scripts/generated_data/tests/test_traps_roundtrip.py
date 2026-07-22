import os
import unittest

from scripts.generated_data.traps import parser as traps_parser
from scripts.generated_data.traps.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "ch2_traps.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "events_trapdata.c")


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_ch2_trivial_arrays(self):
        records = traps_parser.parse_hand_written(
            REAL_HAND_FILE, ["TrapData_Event_Ch2", "TrapData_Event_Ch2Hard"]
        )
        self.assertEqual(records["TrapData_Event_Ch2"]["entries"], [])
        self.assertEqual(records["TrapData_Event_Ch2Hard"]["entries"], [])

    def test_parses_ch7_ballista_entries_generally(self):
        records = traps_parser.parse_hand_written(REAL_HAND_FILE, ["TrapData_Event_Ch7"])
        entries = records["TrapData_Event_Ch7"]["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "TRAP_BALLISTA")
        self.assertEqual(entries[0][1], 17)
        self.assertEqual(entries[0][2], 8)

    def test_missing_symbol_raises(self):
        with self.assertRaises(Exception):
            traps_parser.parse_hand_written(REAL_HAND_FILE, ["TrapData_TotallyMadeUp"])


class FullRoundTripTests(unittest.TestCase):
    def test_generated_model_matches_hand_written_ch2_arrays(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records = traps_parser.parse_hand_written(
            REAL_HAND_FILE, [r.symbol for r in generated_records]
        )
        errors = traps_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))


class FixtureRoundTripTests(unittest.TestCase):
    def test_matching_fixture_passes(self):
        generated_records = load_records(fixture_path("traps", "valid.json"))
        hand_records = traps_parser.parse_hand_written(
            fixture_path("traps", "valid_hand.c"), ["TrapData_Fixture_Test"]
        )
        errors = traps_parser.compare_records(
            generated_records, hand_records, hand_path=fixture_path("traps", "valid_hand.c")
        )
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_mismatched_fixture_detected(self):
        generated_records = load_records(fixture_path("traps", "valid.json"))
        hand_records = traps_parser.parse_hand_written(
            fixture_path("traps", "mismatched_hand.c"), ["TrapData_Fixture_Test"]
        )
        errors = traps_parser.compare_records(
            generated_records, hand_records, hand_path=fixture_path("traps", "mismatched_hand.c")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("entries mismatch" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
