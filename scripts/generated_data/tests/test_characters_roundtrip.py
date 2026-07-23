import os
import unittest

from scripts.generated_data import character_refs
from scripts.generated_data.characters import parser as characters_parser
from scripts.generated_data.characters.schema import load_records
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REAL_SOURCE = os.path.join(REPO_ROOT, "src", "data", "characters.json")
REAL_HAND_FILE = os.path.join(REPO_ROOT, "src", "data_characters.c")

FIXTURE_VALID_HAND = fixture_path("characters", "valid_hand.c")
FIXTURE_MISMATCHED_HAND = fixture_path("characters", "mismatched_hand.c")


def _fixture_designators(records, characters_enum):
    return {r.designator(characters_enum) for r in records}


class HandWrittenParserTests(unittest.TestCase):
    def test_parses_fixture_characters(self):
        characters_enum = character_refs.read_character_designators()
        records = load_records(fixture_path("characters", "valid.json"))
        designators = _fixture_designators(records, characters_enum)
        hand_records = characters_parser.parse_hand_written(
            FIXTURE_VALID_HAND, characters_enum, designators
        )
        self.assertEqual(set(hand_records), designators)
        self.assertEqual(designators, {1, 27, 256})

    def test_missing_character_designator_raises(self):
        characters_enum = character_refs.read_character_designators()
        with self.assertRaises(Exception):
            characters_parser.parse_hand_written(
                FIXTURE_VALID_HAND, characters_enum, {1, 27, 256, 999}
            )


class FullRoundTripTests(unittest.TestCase):
    """Every one of the 256 vanilla gCharacterData[] records (94 symbolic +
    162 raw, including the unreachable [0x100 - 1] padding slot) must
    round-trip semantically against src/data_characters.c -- not just
    examples (Issue #5 Batch 2b DONE)."""

    def test_generated_model_matches_every_hand_written_character(self):
        characters_enum = character_refs.read_character_designators()
        generated_records = load_records(REAL_SOURCE)
        self.assertEqual(len(generated_records), 256)

        symbolic = [r for r in generated_records if r.character is not None]
        raw = [r for r in generated_records if r.character is None]
        self.assertEqual(len(symbolic), 94)
        self.assertEqual(len(raw), 162)

        designators = _fixture_designators(generated_records, characters_enum)
        self.assertEqual(designators, set(range(1, 257)))

        hand_records = characters_parser.parse_hand_written(
            REAL_HAND_FILE, characters_enum, designators
        )
        errors = characters_parser.compare_records(
            generated_records, hand_records, hand_path=REAL_HAND_FILE
        )
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_designator_256_padding_record_round_trips_number_zero(self):
        characters_enum = character_refs.read_character_designators()
        generated_records = load_records(REAL_SOURCE)
        padding = [r for r in generated_records if r.designator(characters_enum) == 256]
        self.assertEqual(len(padding), 1)
        self.assertEqual(padding[0].resolved_number(characters_enum), 0)


class FixtureRoundTripTests(unittest.TestCase):
    def test_matching_fixture_passes(self):
        characters_enum = character_refs.read_character_designators()
        generated_records = load_records(fixture_path("characters", "valid.json"))
        designators = _fixture_designators(generated_records, characters_enum)
        hand_records = characters_parser.parse_hand_written(
            FIXTURE_VALID_HAND, characters_enum, designators
        )
        errors = characters_parser.compare_records(
            generated_records, hand_records, hand_path=FIXTURE_VALID_HAND
        )
        self.assertEqual(errors, [], msg="\n".join(str(e) for e in errors))

    def test_mismatched_fixture_detected(self):
        characters_enum = character_refs.read_character_designators()
        generated_records = load_records(fixture_path("characters", "valid.json"))
        designators = _fixture_designators(generated_records, characters_enum)
        hand_records = characters_parser.parse_hand_written(
            FIXTURE_MISMATCHED_HAND, characters_enum, designators
        )
        errors = characters_parser.compare_records(
            generated_records, hand_records, hand_path=FIXTURE_MISMATCHED_HAND
        )
        messages = [str(e) for e in errors]
        self.assertTrue(
            any("fields mismatch for character=CHARACTER_EIRIKA" in m for m in messages), messages
        )


class RoundTripMismatchDetectionTests(unittest.TestCase):
    def test_detects_missing_hand_character(self):
        characters_enum = character_refs.read_character_designators()
        generated_records = load_records(fixture_path("characters", "valid.json"))
        designators = _fixture_designators(generated_records, characters_enum)
        hand_records = characters_parser.parse_hand_written(
            FIXTURE_VALID_HAND, characters_enum, designators
        )
        del hand_records[27]

        errors = characters_parser.compare_records(
            generated_records, hand_records, hand_path=FIXTURE_VALID_HAND
        )
        messages = [str(e) for e in errors]
        self.assertTrue(any("characterId=27" in m for m in messages), messages)

    def test_detects_extra_hand_character(self):
        characters_enum = character_refs.read_character_designators()
        generated_records = [
            r
            for r in load_records(fixture_path("characters", "valid.json"))
            if r.designator(characters_enum) != 27
        ]
        designators = _fixture_designators(
            load_records(fixture_path("characters", "valid.json")), characters_enum
        )
        hand_records = characters_parser.parse_hand_written(
            FIXTURE_VALID_HAND, characters_enum, designators
        )

        errors = characters_parser.compare_records(
            generated_records, hand_records, hand_path=FIXTURE_VALID_HAND
        )
        messages = [str(e) for e in errors]
        self.assertTrue(
            any("hand-written character" in m and "0x1b" in m for m in messages), messages
        )


if __name__ == "__main__":
    unittest.main()
