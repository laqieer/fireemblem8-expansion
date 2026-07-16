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


if __name__ == "__main__":
    unittest.main()
