import os
import unittest
from unittest import mock

from scripts.generated_data.items import parser as items_parser
from scripts.generated_data.items.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "items.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "data_items.c")

FIXTURE_ITEM_NAMES = ["ITEM_FIXTURE_NONE", "ITEM_FIXTURE_A", "ITEM_FIXTURE_B"]
ALIAS_BMITEM_HEADER = fixture_path("items", "mini_bmitem_alias.h")


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_fixture_items(self):
        records = items_parser.parse_hand_written(fixture_path("items", "valid_hand.c"), FIXTURE_ITEM_NAMES)
        self.assertEqual(set(records), set(FIXTURE_ITEM_NAMES))

    def test_missing_item_symbol_raises(self):
        with self.assertRaises(Exception):
            items_parser.parse_hand_written(fixture_path("items", "valid_hand.c"), ["ITEM_TOTALLY_MADE_UP"])


class FullRoundTripTests(unittest.TestCase):
    """Every one of the 206 vanilla gItemData[] records must round-trip
    semantically against src/data_items.c -- not just examples (Issue #5
    Batch 1 DONE)."""

    def test_generated_model_matches_every_hand_written_item(self):
        generated_records = load_records(REAL_SOURCE)
        self.assertEqual(len(generated_records), 206)

        item_names = [r.item for r in generated_records]
        hand_records = items_parser.parse_hand_written(REAL_HAND_FILE, item_names)
        errors = items_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))


class FixtureRoundTripTests(unittest.TestCase):
    def test_matching_fixture_passes(self):
        generated_records = load_records(fixture_path("items", "valid.json"))
        hand_records = items_parser.parse_hand_written(
            fixture_path("items", "valid_hand.c"), FIXTURE_ITEM_NAMES
        )
        errors = items_parser.compare_records(
            generated_records, hand_records, hand_path=fixture_path("items", "valid_hand.c")
        )
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_mismatched_fixture_detected(self):
        generated_records = load_records(fixture_path("items", "valid.json"))
        hand_records = items_parser.parse_hand_written(
            fixture_path("items", "mismatched_hand.c"), FIXTURE_ITEM_NAMES
        )
        errors = items_parser.compare_records(
            generated_records, hand_records, hand_path=fixture_path("items", "mismatched_hand.c")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("fields mismatch for ITEM_FIXTURE_A" in m for m in messages), messages)


class WeaponTypeAliasRoundTripTests(unittest.TestCase):
    """Regression coverage: ``weaponType`` must round-trip through the
    live ``ITYPE_*`` mapping (symbol-blind, like ``attributes``/
    ``requiredWexp``/``weaponEffect``), not a raw string compare. The
    real ``include/bmitem.h`` ``ITYPE_*`` enum happens to have no two
    names sharing a value, so this uses a synthetic fixture header
    (``mini_bmitem_alias.h``) declaring ``ITYPE_SWORD_ALIAS`` as a second
    name for ``ITYPE_SWORD``'s value (0) to prove the equivalence."""

    def test_equal_valued_weapon_type_aliases_round_trip_clean(self):
        generated_records = load_records(fixture_path("items", "alias_valid.json"))
        with mock.patch("scripts.generated_data.items.parser.BMITEM_HEADER", ALIAS_BMITEM_HEADER):
            hand_records = items_parser.parse_hand_written(
                fixture_path("items", "alias_valid_hand.c"), FIXTURE_ITEM_NAMES
            )
            errors = items_parser.compare_records(
                generated_records, hand_records, hand_path=fixture_path("items", "alias_valid_hand.c")
            )
        # JSON authors ITEM_FIXTURE_A.weaponType as "ITYPE_SWORD_ALIAS"; the hand
        # file uses "ITYPE_SWORD" -- two distinct symbols, same numeric value (0).
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_different_weapon_type_value_still_mismatches(self):
        generated_records = load_records(fixture_path("items", "alias_valid.json"))
        with mock.patch("scripts.generated_data.items.parser.BMITEM_HEADER", ALIAS_BMITEM_HEADER):
            hand_records = items_parser.parse_hand_written(
                fixture_path("items", "alias_mismatch_hand.c"), FIXTURE_ITEM_NAMES
            )
            errors = items_parser.compare_records(
                generated_records, hand_records, hand_path=fixture_path("items", "alias_mismatch_hand.c")
            )
        # Hand file uses ITYPE_LANCE (value 1) for ITEM_FIXTURE_A instead of an
        # ITYPE_SWORD alias (value 0) -- a genuinely different encoding.
        messages = [str(e) for e in errors]
        self.assertTrue(any("fields mismatch for ITEM_FIXTURE_A" in m for m in messages), messages)


class RoundTripMismatchDetectionTests(unittest.TestCase):
    def test_detects_missing_hand_item(self):
        generated_records = load_records(fixture_path("items", "valid.json"))
        hand_records = items_parser.parse_hand_written(
            fixture_path("items", "valid_hand.c"), FIXTURE_ITEM_NAMES
        )
        del hand_records["ITEM_FIXTURE_B"]

        errors = items_parser.compare_records(
            generated_records, hand_records, hand_path=fixture_path("items", "valid_hand.c")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("ITEM_FIXTURE_B" in m for m in messages), messages)

    def test_detects_extra_hand_item(self):
        generated_records = [
            r for r in load_records(fixture_path("items", "valid.json")) if r.item != "ITEM_FIXTURE_B"
        ]
        hand_records = items_parser.parse_hand_written(
            fixture_path("items", "valid_hand.c"), FIXTURE_ITEM_NAMES
        )

        errors = items_parser.compare_records(
            generated_records, hand_records, hand_path=fixture_path("items", "valid_hand.c")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(
            any("hand-written item 'ITEM_FIXTURE_B' has no counterpart" in m for m in messages), messages
        )


if __name__ == "__main__":
    unittest.main()
