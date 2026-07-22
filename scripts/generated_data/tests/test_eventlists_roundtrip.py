import os
import unittest

from scripts.generated_data.eventlists import parser as eventlists_parser
from scripts.generated_data.eventlists.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "ch2_eventlists.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "events", "ch2-eventinfo.h")


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_all_seven_ch2_lists_tutorial_and_manifest(self):
        records = load_records(REAL_SOURCE)
        hand_data = eventlists_parser.parse_hand_written(REAL_HAND_FILE, records)
        self.assertEqual(
            set(hand_data["lists_by_symbol"]),
            {
                "EventListScr_Ch2_Turn",
                "EventListScr_Ch2_Character",
                "EventListScr_Ch2_Location",
                "EventListScr_Ch2_Misc",
                "EventListScr_Ch2_SelectUnit",
                "EventListScr_Ch2_SelectDestination",
                "EventListScr_Ch2_UnitMove",
            },
        )
        self.assertEqual(len(hand_data["lists_by_symbol"]["EventListScr_Ch2_Turn"]["entries"]), 3)
        self.assertEqual(hand_data["tutorial"]["symbol"], "EventListScr_Ch2_Tutorial")
        self.assertEqual(len(hand_data["tutorial"]["entries"]), 30)
        self.assertEqual(hand_data["manifest"]["symbol"], "Ch2Events")
        self.assertEqual(hand_data["manifest"]["fields"]["playerUnitsChoice1InEncounter"], None)

    def test_macro_call_tokenizer_handles_nested_evflag_tmp(self):
        records = load_records(REAL_SOURCE)
        hand_data = eventlists_parser.parse_hand_written(REAL_HAND_FILE, records)
        char_calls = hand_data["lists_by_symbol"]["EventListScr_Ch2_Character"]["entries"]
        self.assertEqual(char_calls[0].macro, "CHAR")
        self.assertEqual(char_calls[0].args[0].kind, "call")
        self.assertEqual(char_calls[0].args[0].value.macro, "EVFLAG_TMP")
        self.assertEqual(char_calls[0].args[0].value.args[0].value, 7)

    def test_bare_zero_arg_macro_parsed(self):
        records = load_records(REAL_SOURCE)
        hand_data = eventlists_parser.parse_hand_written(REAL_HAND_FILE, records)
        misc_calls = hand_data["lists_by_symbol"]["EventListScr_Ch2_Misc"]["entries"]
        self.assertEqual(misc_calls[1].macro, "CauseGameOverIfLordDies")
        self.assertEqual(misc_calls[1].args, [])


class FullRoundTripTests(unittest.TestCase):
    """Every Ch2 event-list macro call, tutorial entry, and Ch2Events
    manifest field must round-trip semantically against
    src/events/ch2-eventinfo.h -- not just examples (Issue #5 Batch B
    DONE: "100% hand roundtrip event lists/manifest")."""

    def test_generated_model_matches_hand_written_source_100_percent(self):
        records = load_records(REAL_SOURCE)
        hand_data = eventlists_parser.parse_hand_written(REAL_HAND_FILE, records)
        errors = eventlists_parser.compare_records(records, hand_data, hand_path=REAL_HAND_FILE)
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))


class FixtureRoundTripTests(unittest.TestCase):
    def test_matching_fixture_passes(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        hand_data = eventlists_parser.parse_hand_written(
            fixture_path("eventlists", "valid_hand.h"), records
        )
        errors = eventlists_parser.compare_records(
            records, hand_data, hand_path=fixture_path("eventlists", "valid_hand.h")
        )
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_mismatched_macro_arg_detected(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        hand_data = eventlists_parser.parse_hand_written(
            fixture_path("eventlists", "mismatched_hand.h"), records
        )
        errors = eventlists_parser.compare_records(
            records, hand_data, hand_path=fixture_path("eventlists", "mismatched_hand.h")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("EventListScr_EL_Turn' entries mismatch" in m for m in messages), messages)


class RoundTripMismatchDetectionTests(unittest.TestCase):
    def test_detects_missing_hand_list(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        hand_data = eventlists_parser.parse_hand_written(
            fixture_path("eventlists", "valid_hand.h"), records
        )
        del hand_data["lists_by_symbol"]["EventListScr_EL_UnitMove"]

        errors = eventlists_parser.compare_records(
            records, hand_data, hand_path=fixture_path("eventlists", "valid_hand.h")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("EventListScr_EL_UnitMove" in m for m in messages), messages)

    def test_detects_tutorial_entry_mismatch(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        hand_data = eventlists_parser.parse_hand_written(
            fixture_path("eventlists", "valid_hand.h"), records
        )
        hand_data["tutorial"]["entries"] = list(hand_data["tutorial"]["entries"])
        hand_data["tutorial"]["entries"][0] = "EventScr_EL_Tutorial99"

        errors = eventlists_parser.compare_records(
            records, hand_data, hand_path=fixture_path("eventlists", "valid_hand.h")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("tutorial entries mismatch" in m for m in messages), messages)

    def test_detects_manifest_field_mismatch(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        hand_data = eventlists_parser.parse_hand_written(
            fixture_path("eventlists", "valid_hand.h"), records
        )
        hand_data["manifest"]["fields"]["endingSceneEvents"] = "EventScr_EL_SomethingElse"

        errors = eventlists_parser.compare_records(
            records, hand_data, hand_path=fixture_path("eventlists", "valid_hand.h")
        )
        messages = [str(e) for e in errors]
        self.assertTrue(
            any("manifest field 'endingSceneEvents' mismatch" in m for m in messages), messages
        )


if __name__ == "__main__":
    unittest.main()
