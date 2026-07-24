import os
import unittest

from scripts.generated_data.movecost import parser as movecost_parser
from scripts.generated_data.movecost.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "movecost.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "data_terrains.c")


def _all_symbols(records):
    return [symbol for r in records for _, symbol in r.symbols()]


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_all_47_real_arrays(self):
        records = load_records(REAL_SOURCE)
        symbols = _all_symbols(records)
        self.assertEqual(len(symbols), 47)
        hand_records = movecost_parser.parse_hand_written(REAL_HAND_FILE, symbols)
        self.assertEqual(set(hand_records), set(symbols))
        self.assertEqual(len(hand_records["TerrainTable_MovCost_CommonT2Normal"]["values"]), 65)

    def test_missing_symbol_raises(self):
        with self.assertRaises(Exception):
            movecost_parser.parse_hand_written(REAL_HAND_FILE, ["TerrainTable_MovCost_TotallyMadeUp"])

    def test_does_not_confuse_unrelated_arrays(self):
        # Unk_TerrainTable_1/2, the 8 terrainstats combat/heal arrays, and
        # the banim graphics arrays sit between (and around) the 47 in
        # scope -- must not be picked up.
        records = load_records(REAL_SOURCE)
        symbols = _all_symbols(records)
        hand_records = movecost_parser.parse_hand_written(REAL_HAND_FILE, symbols)
        for symbol in hand_records:
            self.assertNotIn("Unk_TerrainTable", symbol)
            self.assertNotIn("Banim", symbol)
            self.assertNotIn("Avo_", symbol)
            self.assertNotIn("HealAmount", symbol)


class FullRoundTripTests(unittest.TestCase):
    def test_generated_model_matches_hand_written_arrays(self):
        generated_records = load_records(REAL_SOURCE)
        symbols = _all_symbols(generated_records)
        hand_records = movecost_parser.parse_hand_written(REAL_HAND_FILE, symbols)
        errors = movecost_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))


class FixtureRoundTripTests(unittest.TestCase):
    def _single_slot_record(self, records, profile, slot_name):
        """Build a copy of the ``profile`` record with only ``slot_name``
        populated, so compare_records() only checks that one symbol
        (mirrors terrainstats' equivalent per-symbol filtering, but a
        movecost profile authors up to 3 symbols so filtering by profile
        alone is not enough)."""
        from scripts.generated_data.movecost.schema import MovecostRecord

        (record,) = [r for r in records if r.profile == profile]
        slots = {name: (record.slots[name] if name == slot_name else None) for name in ("normal", "rain", "snow")}
        return MovecostRecord(profile=record.profile, profile_loc=record.profile_loc, slots=slots, loc=record.loc)

    def test_matching_fixture_passes(self):
        generated_records = load_records(fixture_path("movecost", "valid.json"))
        hand_path = fixture_path("movecost", "valid_hand.c")
        hand_records = movecost_parser.parse_hand_written(hand_path, ["TerrainTable_MovCost_CommonT2Normal"])
        subset = [self._single_slot_record(generated_records, "CommonT2", "normal")]
        errors = movecost_parser.compare_records(subset, hand_records, hand_path=hand_path)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_mismatched_fixture_detected(self):
        generated_records = load_records(fixture_path("movecost", "valid.json"))
        hand_path = fixture_path("movecost", "mismatched_hand.c")
        hand_records = movecost_parser.parse_hand_written(hand_path, ["TerrainTable_MovCost_CommonT2Normal"])
        subset = [self._single_slot_record(generated_records, "CommonT2", "normal")]
        errors = movecost_parser.compare_records(subset, hand_records, hand_path=hand_path)
        messages = [str(e) for e in errors]
        self.assertTrue(any("values mismatch" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
