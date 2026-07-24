import os
import unittest

from scripts.generated_data.weapontriangle import parser as weapontriangle_parser
from scripts.generated_data.weapontriangle.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "weapontriangle.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "bmbattle.c")


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_all_12_real_rules_plus_terminator(self):
        hand_records, errors = weapontriangle_parser.parse_hand_written(REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))
        self.assertEqual(len(hand_records), 12)

    def test_does_not_confuse_procedural_functions(self):
        # BattleApplyWeaponTriangleEffect/BattleApplyReaverEffect consume
        # this table but must never themselves be matched by the
        # table-scoped regex.
        hand_records, errors = weapontriangle_parser.parse_hand_written(REAL_HAND_FILE)
        self.assertEqual(errors, [])
        for key in hand_records:
            self.assertIsInstance(key, tuple)
            self.assertEqual(len(key), 2)

    def test_table_not_found_reported(self):
        hand_records, errors = weapontriangle_parser.parse_hand_written(
            fixture_path("weapontriangle", "no_table.c")
        )
        self.assertEqual(hand_records, {})
        self.assertTrue(errors)
        self.assertTrue(any("not found" in str(e) for e in errors))

    def test_missing_terminator_detected(self):
        _, errors = weapontriangle_parser.parse_hand_written(
            fixture_path("weapontriangle", "missing_terminator.c")
        )
        self.assertTrue(any("missing its required { -1 } terminator" in str(e) for e in errors), errors)

    def test_bad_terminator_shape_detected(self):
        _, errors = weapontriangle_parser.parse_hand_written(
            fixture_path("weapontriangle", "bad_terminator_shape.c")
        )
        self.assertTrue(any("invalid terminator shape" in str(e) for e in errors), errors)

    def test_data_after_terminator_detected(self):
        # The parser's "must be the final entry" check runs before its
        # duplicate-terminator check, so a leading { -1 } followed by a
        # data entry is rejected as "not the final entry" (still a
        # correct rejection of the malformed shape).
        _, errors = weapontriangle_parser.parse_hand_written(
            fixture_path("weapontriangle", "data_after_terminator.c")
        )
        self.assertTrue(any("is not the final entry" in str(e) for e in errors), errors)

    def test_duplicate_terminator_detected(self):
        # Same reasoning as above: the earlier of the two { -1 } entries
        # is rejected as "not the final entry" before the duplicate
        # check is ever reached.
        _, errors = weapontriangle_parser.parse_hand_written(
            fixture_path("weapontriangle", "duplicate_terminator.c")
        )
        self.assertTrue(any("is not the final entry" in str(e) for e in errors), errors)


class FullRoundTripTests(unittest.TestCase):
    def test_generated_model_matches_hand_written_table(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records, hand_errors = weapontriangle_parser.parse_hand_written(REAL_HAND_FILE)
        self.assertEqual(hand_errors, [], msg="\n".join(str(e) for e in hand_errors))
        errors = weapontriangle_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))


class FixtureRoundTripTests(unittest.TestCase):
    def test_matching_fixture_passes(self):
        generated_records = load_records(fixture_path("weapontriangle", "valid.json"))
        hand_path = fixture_path("weapontriangle", "valid_hand.c")
        hand_records, errors = weapontriangle_parser.parse_hand_written(hand_path)
        self.assertEqual(errors, [])
        mismatch_errors = weapontriangle_parser.compare_records(generated_records, hand_records, hand_path=hand_path)
        self.assertEqual(mismatch_errors, [], msg="\n".join(str(e) for e in mismatch_errors))

    def test_mismatched_fixture_detected(self):
        generated_records = load_records(fixture_path("weapontriangle", "valid.json"))
        hand_path = fixture_path("weapontriangle", "mismatched_hand.c")
        hand_records, errors = weapontriangle_parser.parse_hand_written(hand_path)
        self.assertEqual(errors, [])
        mismatch_errors = weapontriangle_parser.compare_records(generated_records, hand_records, hand_path=hand_path)
        messages = [str(e) for e in mismatch_errors]
        self.assertTrue(any("values mismatch" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
