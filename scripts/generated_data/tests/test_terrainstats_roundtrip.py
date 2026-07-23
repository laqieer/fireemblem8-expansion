import os
import unittest

from scripts.generated_data.terrainstats import parser as terrainstats_parser
from scripts.generated_data.terrainstats.schema import EXPECTED_SYMBOLS, load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "terrainstats.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "data_terrains.c")


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_all_8_real_arrays(self):
        records = terrainstats_parser.parse_hand_written(REAL_HAND_FILE, EXPECTED_SYMBOLS)
        self.assertEqual(set(records), set(EXPECTED_SYMBOLS))
        self.assertEqual(len(records["TerrainTable_Avo_Common"]["values"]), 65)

    def test_missing_symbol_raises(self):
        with self.assertRaises(Exception):
            terrainstats_parser.parse_hand_written(REAL_HAND_FILE, ["TerrainTable_TotallyMadeUp"])

    def test_does_not_confuse_unrelated_arrays(self):
        # Unk_TerrainTable_3 and the movement-cost/banim arrays sit between
        # (and around) the 8 in-scope arrays -- must not be picked up.
        records = terrainstats_parser.parse_hand_written(REAL_HAND_FILE, EXPECTED_SYMBOLS)
        for symbol in records:
            self.assertNotIn("Unk_TerrainTable", symbol)
            self.assertNotIn("Banim", symbol)


class FullRoundTripTests(unittest.TestCase):
    def test_generated_model_matches_hand_written_arrays(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records = terrainstats_parser.parse_hand_written(
            REAL_HAND_FILE, [r.symbol for r in generated_records]
        )
        errors = terrainstats_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))


class FixtureRoundTripTests(unittest.TestCase):
    def test_matching_fixture_passes(self):
        generated_records = load_records(fixture_path("terrainstats", "valid.json"))
        hand_path = fixture_path("terrainstats", "valid_hand.c")
        hand_records = terrainstats_parser.parse_hand_written(hand_path, ["TerrainTable_Avo_Common"])
        errors = terrainstats_parser.compare_records(
            [r for r in generated_records if r.symbol == "TerrainTable_Avo_Common"],
            hand_records,
            hand_path=hand_path,
        )
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_mismatched_fixture_detected(self):
        generated_records = load_records(fixture_path("terrainstats", "valid.json"))
        hand_path = fixture_path("terrainstats", "mismatched_hand.c")
        hand_records = terrainstats_parser.parse_hand_written(hand_path, ["TerrainTable_Avo_Common"])
        errors = terrainstats_parser.compare_records(
            [r for r in generated_records if r.symbol == "TerrainTable_Avo_Common"],
            hand_records,
            hand_path=hand_path,
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("values mismatch" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
