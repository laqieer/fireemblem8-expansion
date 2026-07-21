import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gba_playtest


def valid_scenario():
    return {
        "schema_version": 1,
        "name": "unit-test",
        "frames": [{"start": 2, "end": 3, "keys": ["A", "RIGHT"]}],
        "checkpoints": [
            {
                "name": "checkpoint",
                "frame": 5,
                "framebuffer": True,
                "probes": [
                    {"address": "0x02000000", "size": 4, "expected": "0x00000000"},
                    {"address": "0x03000000", "size": 1},
                ],
            }
        ],
    }


def sram_hash_scenario():
    """Same as valid_scenario(), but with sram_hash enabled on the sole
    checkpoint -- the required precondition for sram_hash_exclude_ranges,
    used as the base fixture for SramHashExcludeRangeTests below."""
    data = valid_scenario()
    data["checkpoints"][0]["sram_hash"] = True
    return data


class ScenarioParsingTests(unittest.TestCase):
    def test_parses_frame_state_and_probes(self):
        scenario = gba_playtest.parse_scenario_data(valid_scenario())
        self.assertEqual(scenario.inputs[0].key_mask, (1 << 0) | (1 << 4))
        self.assertEqual(scenario.checkpoints[0].probes[0].address, 0x02000000)

    def test_rejects_unknown_key_name(self):
        data = valid_scenario()
        data["frames"][0]["keys"] = ["FIRE"]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "invalid key name.*FIRE"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_non_string_key_name(self):
        data = valid_scenario()
        data["frames"][0]["keys"] = [["A"]]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "only string"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_rom_and_unmapped_addresses(self):
        for address in ("0x08000000", "0x03008000"):
            with self.subTest(address=address):
                data = valid_scenario()
                data["checkpoints"][0]["probes"][0]["address"] = address
                with self.assertRaisesRegex(gba_playtest.PlaytestError, "must be in EWRAM"):
                    gba_playtest.parse_scenario_data(data)

    def test_rejects_unaligned_and_boundary_crossing_probe(self):
        data = valid_scenario()
        data["checkpoints"][0]["probes"][0]["address"] = "0x02000002"
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "aligned"):
            gba_playtest.parse_scenario_data(data)

        data = valid_scenario()
        data["checkpoints"][0]["probes"][0]["address"] = "0x0203fffe"
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "crosses"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_overlapping_frame_ranges(self):
        data = valid_scenario()
        data["frames"].append({"start": 3, "end": 4, "keys": []})
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "overlaps"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_unknown_fields(self):
        data = valid_scenario()
        data["surprise"] = True
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "unknown field.*surprise"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text(
                '{"schema_version":1,"name":"first","name":"second",'
                '"frames":[],"checkpoints":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(gba_playtest.PlaytestError, "duplicate JSON key"):
                gba_playtest.load_scenario(path)

    def test_disabled_stub_requires_blocker(self):
        data = valid_scenario()
        data["disabled"] = True
        data["checkpoints"] = []
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "blocker is required"):
            gba_playtest.parse_scenario_data(data)

        data["blocker"] = "Needs a deterministic route."
        scenario = gba_playtest.parse_scenario_data(data)
        self.assertTrue(scenario.disabled)


class SramHashExcludeRangeTests(unittest.TestCase):
    """Negative-path coverage for gba_playtest.py's
    sram_hash_exclude_ranges parsing (docs/save_format.md's "SRAM hash
    policy: exact vs. normalized"), plus the valid boundary cases."""

    def test_rejects_ranges_without_sram_hash(self):
        data = valid_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [{"offset": 0, "length": 1}]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, "sram_hash_exclude_ranges requires sram_hash true"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_empty_list_of_ranges(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = []
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, "sram_hash_exclude_ranges must be a non-empty array"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_non_list_ranges(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = {"offset": 0, "length": 1}
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, "sram_hash_exclude_ranges must be a non-empty array"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_malformed_range_object(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = ["not-an-object"]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "must be an object"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_range_missing_a_key(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [{"offset": 0}]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, "missing required field.*length"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_range_with_extra_key(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": 0, "length": 1, "surprise": True}
        ]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, "unknown field.*surprise"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_non_int_offset(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [{"offset": "0", "length": 1}]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, r"\.offset must be an integer from 0 through"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_negative_offset(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [{"offset": -1, "length": 1}]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, r"\.offset must be an integer from 0 through"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_out_of_bounds_offset(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": gba_playtest.SRAM_IMAGE_SIZE, "length": 1}
        ]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, r"\.offset must be an integer from 0 through"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_non_int_length(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [{"offset": 0, "length": "1"}]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, r"\.length must be an integer from 1 through"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_zero_length(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [{"offset": 0, "length": 0}]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, r"\.length must be an integer from 1 through"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_too_large_length(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": 0, "length": gba_playtest.SRAM_IMAGE_SIZE + 1}
        ]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, r"\.length must be an integer from 1 through"
        ):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_offset_plus_length_overflow(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": gba_playtest.SRAM_IMAGE_SIZE - 1, "length": 2}
        ]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "exceeds the"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_overlapping_ranges(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": 0, "length": 4},
            {"offset": 2, "length": 4},
        ]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "overlaps or is not ordered"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_same_offset_ranges(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": 4, "length": 2},
            {"offset": 4, "length": 2},
        ]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "overlaps or is not ordered"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_out_of_order_ranges(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": 8, "length": 2},
            {"offset": 4, "length": 2},
        ]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "overlaps or is not ordered"):
            gba_playtest.parse_scenario_data(data)

    def test_rejects_more_than_64_ranges(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": offset, "length": 1} for offset in range(0, 130, 2)
        ]
        self.assertEqual(len(data["checkpoints"][0]["sram_hash_exclude_ranges"]), 65)
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            r"checkpoint 'checkpoint' has 65 ranges, exceeding the 64-range limit",
        ):
            gba_playtest.parse_scenario_data(data)

    def test_accepts_one_range_covering_whole_sram(self):
        data = sram_hash_scenario()
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = [
            {"offset": 0, "length": gba_playtest.SRAM_IMAGE_SIZE}
        ]
        scenario = gba_playtest.parse_scenario_data(data)
        self.assertEqual(
            scenario.checkpoints[0].sram_hash_exclude_ranges,
            ((0, gba_playtest.SRAM_IMAGE_SIZE),),
        )

    def test_accepts_exactly_64_ordered_non_overlapping_ranges(self):
        data = sram_hash_scenario()
        ranges = [{"offset": offset, "length": 1} for offset in range(0, 128, 2)]
        self.assertEqual(len(ranges), 64)
        data["checkpoints"][0]["sram_hash_exclude_ranges"] = ranges
        scenario = gba_playtest.parse_scenario_data(data)
        self.assertEqual(len(scenario.checkpoints[0].sram_hash_exclude_ranges), 64)
        self.assertEqual(
            scenario.checkpoints[0].sram_hash_exclude_ranges,
            tuple((offset, 1) for offset in range(0, 128, 2)),
        )


if __name__ == "__main__":
    unittest.main()
