import os
import unittest

from scripts.generated_data.shops import parser as shops_parser
from scripts.generated_data.shops.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "ch2_shops.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "events_shoplist.c")


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_ch2_armory(self):
        records = shops_parser.parse_hand_written(REAL_HAND_FILE, ["ShopList_Event_Ch2Armory"])
        self.assertEqual(
            records["ShopList_Event_Ch2Armory"]["items"],
            ["ITEM_SWORD_SLIM", "ITEM_SWORD_IRON", "ITEM_LANCE_SLIM", "ITEM_LANCE_IRON", "ITEM_AXE_IRON"],
        )

    def test_missing_symbol_raises(self):
        with self.assertRaises(Exception):
            shops_parser.parse_hand_written(REAL_HAND_FILE, ["ShopList_TotallyMadeUp"])


class FullRoundTripTests(unittest.TestCase):
    def test_generated_model_matches_hand_written_armory(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records = shops_parser.parse_hand_written(REAL_HAND_FILE, ["ShopList_Event_Ch2Armory"])
        errors = shops_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))


class FixtureRoundTripTests(unittest.TestCase):
    def test_matching_fixture_passes(self):
        generated_records = load_records(fixture_path("shops", "valid.json"))
        hand_records = shops_parser.parse_hand_written(
            fixture_path("shops", "valid_hand.c"), ["ShopList_Fixture_Test"]
        )
        errors = shops_parser.compare_records(
            generated_records, hand_records, hand_path=fixture_path("shops", "valid_hand.c")
        )
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_mismatched_fixture_detected(self):
        generated_records = load_records(fixture_path("shops", "valid.json"))
        hand_records = shops_parser.parse_hand_written(
            fixture_path("shops", "mismatched_hand.c"), ["ShopList_Fixture_Test"]
        )
        errors = shops_parser.compare_records(
            generated_records, hand_records, hand_path=fixture_path("shops", "mismatched_hand.c")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("items mismatch" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
