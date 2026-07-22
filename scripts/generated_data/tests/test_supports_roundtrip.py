import os
import unittest

from scripts.generated_data.supports import parser as supports_parser
from scripts.generated_data.supports.schema import load_records
from scripts.generated_data.tests._util import scratch_dir

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "supports.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "data_supports.c")


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_all_33_vanilla_records(self):
        records = supports_parser.parse_hand_written(REAL_HAND_FILE)
        self.assertEqual(len(records), 33)
        symbols = {r["symbol"] for r in records}
        self.assertIn("SupportData_Eirika", symbols)
        self.assertIn("SupportData_Syrene", symbols)

    def test_no_records_found_raises(self):
        with scratch_dir() as tmp:
            empty_path = os.path.join(tmp, "empty.c")
            with open(empty_path, "w") as f:
                f.write("/* nothing here */\n")
            with self.assertRaises(Exception):
                supports_parser.parse_hand_written(empty_path)


class FullRoundTripTests(unittest.TestCase):
    """The full-fidelity check required by Issue #5 Slice 0: every one of
    the 33 vanilla SupportData records, not just examples."""

    def test_generated_model_matches_every_hand_written_record(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records = supports_parser.parse_hand_written(REAL_HAND_FILE)
        self.assertEqual(len(generated_records), 33)
        self.assertEqual(len(hand_records), 33)

        errors = supports_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_covers_every_symbol_no_subset(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records = supports_parser.parse_hand_written(REAL_HAND_FILE)
        self.assertEqual(
            {r.symbol for r in generated_records},
            {r["symbol"] for r in hand_records},
        )


class RoundTripMismatchDetectionTests(unittest.TestCase):
    """Prove the checker actually fails on injected drift (not a no-op)."""

    def test_detects_missing_hand_record(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records = supports_parser.parse_hand_written(REAL_HAND_FILE)
        hand_records = [r for r in hand_records if r["symbol"] != "SupportData_Eirika"]

        errors = supports_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertTrue(any("SupportData_Eirika" in str(e) for e in errors))

    def test_detects_value_mismatch(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records = supports_parser.parse_hand_written(REAL_HAND_FILE)
        for record in hand_records:
            if record["symbol"] == "SupportData_Eirika":
                record["supportExpBase"] = [999] + record["supportExpBase"][1:]

        errors = supports_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        messages = [str(e) for e in errors]
        self.assertTrue(any("supportExpBase mismatch for SupportData_Eirika" in m for m in messages), messages)

    def test_detects_extra_generated_record(self):
        generated_records = load_records(REAL_SOURCE)
        hand_records = supports_parser.parse_hand_written(REAL_HAND_FILE)
        hand_records = [r for r in hand_records if r["symbol"] != "SupportData_Syrene"]

        errors = supports_parser.compare_records(generated_records, hand_records, hand_path=REAL_HAND_FILE)
        self.assertTrue(any("generated record 'SupportData_Syrene'" in str(e) for e in errors))


if __name__ == "__main__":
    unittest.main()
