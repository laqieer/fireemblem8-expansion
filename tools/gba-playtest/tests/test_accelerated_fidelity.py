"""Issue #88 accelerated-fidelity schema, backend, and comparator checks."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gba_playtest
from homebrew_fixture import build_homebrew_rom


PROBE_ADDRESS = "0x02000000"
CONFIG_ADDRESS = "0x02000004"
ROOT = Path(__file__).resolve().parents[3]


def temporary_directory(prefix: str):
    build_root = ROOT / "build"
    build_root.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=build_root)


def profile_data(name: str) -> dict:
    profile = {
        "name": name,
        "trace": [{"address": PROBE_ADDRESS, "size": 4}],
    }
    if name == gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY:
        profile.update(
            config_apply_frame=1,
            play_state_config={"address": CONFIG_ADDRESS, "size": 4},
        )
    return {
        "schema_version": 3,
        "name": f"homebrew-{name}",
        "description": "Every fixture frame runs through the libmGBA core.",
        "frames": [{"start": 2, "end": 2, "keys": ["A"]}],
        "run_until": {
            "max_frames": 5,
            "terminal_conditions": [
                {
                    "reason": "success",
                    "all": [
                        {
                            "address": PROBE_ADDRESS,
                            "size": 4,
                            "operator": "eq",
                            "value": "0x000003fe",
                        }
                    ],
                }
            ],
            "checkpoint": {
                "name": "semantic-terminal",
                "framebuffer": False,
                "probes": [{"address": PROBE_ADDRESS, "size": 4}],
            },
        },
        "execution_profile": profile,
    }


class AcceleratedFidelitySchemaTests(unittest.TestCase):
    def test_schema_three_is_explicit_and_plan_five_omits_semantic_hashes(self):
        accelerated = gba_playtest.parse_scenario_data(
            profile_data(gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY)
        )
        normal = gba_playtest.parse_scenario_data(
            profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        )
        self.assertEqual(accelerated.schema_version, 3)
        self.assertEqual(
            accelerated.execution_profile.name,
            gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY,
        )
        self.assertEqual(normal.execution_profile.config_apply_frame, None)
        with temporary_directory("gba-accelerated-fidelity-plan-") as temporary:
            plan = Path(temporary) / "accelerated.plan"
            gba_playtest._write_plan(plan, accelerated)
            plan_text = plan.read_text(encoding="ascii")
        self.assertTrue(plan_text.startswith("GBA_PLAYTEST_PLAN 5\n"))
        self.assertIn("\nPROFILE 1 1 33554436\n", plan_text)
        self.assertIn("\nTRACE 1\n", plan_text)
        self.assertIn("\n4 1 0 0 0 0 0\n", plan_text)

    def test_profiles_fail_closed_when_configuration_shape_is_wrong(self):
        normal_with_config = profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        normal_with_config["execution_profile"]["config_apply_frame"] = 1
        normal_with_config["execution_profile"]["play_state_config"] = {
            "address": CONFIG_ADDRESS,
            "size": 4,
        }
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            "normal-fidelity must not apply",
        ):
            gba_playtest.parse_scenario_data(normal_with_config)

        missing_accelerated_config = profile_data(
            gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY
        )
        del missing_accelerated_config["execution_profile"]["play_state_config"]
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            "requires config_apply_frame and play_state_config",
        ):
            gba_playtest.parse_scenario_data(missing_accelerated_config)

        duplicate_trace = profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        duplicate_trace["execution_profile"]["trace"].append(
            {"address": PROBE_ADDRESS, "size": 4}
        )
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "duplicate probes"):
            gba_playtest.parse_scenario_data(duplicate_trace)


class AcceleratedFidelityBackendTests(unittest.TestCase):
    def _capture(self, name: str) -> dict:
        scenario = gba_playtest.parse_scenario_data(profile_data(name))
        with temporary_directory("gba-accelerated-fidelity-") as temporary:
            rom = Path(temporary) / "fixture.gba"
            build_homebrew_rom(rom)
            return gba_playtest.capture(rom, scenario)

    def test_accelerated_profile_keeps_all_frames_and_skips_unused_hash(self):
        normal = self._capture(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        accelerated = self._capture(
            gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY
        )
        self.assertEqual(normal["format_version"], 4)
        self.assertEqual(accelerated["format_version"], 4)
        self.assertEqual(normal["terminal"]["frame"], 2)
        self.assertEqual(accelerated["terminal"]["frame"], 2)
        self.assertNotIn("framebuffer_hash", normal["checkpoints"][0])
        self.assertNotIn("framebuffer_hash", accelerated["checkpoints"][0])
        self.assertEqual(normal["trace"][0]["frame"], 0)
        self.assertEqual(accelerated["trace"][0]["frame"], 0)
        self.assertEqual(
            accelerated["profile"]["config_apply_frame"],
            1,
        )
        after = int(accelerated["profile"]["config_after"], 16)
        self.assertTrue(after & gba_playtest.PLAYST_CONFIG_GAME_SPEED_MASK)
        self.assertEqual(
            after & gba_playtest.PLAYST_CONFIG_ANIMATION_TYPE_MASK,
            gba_playtest.PLAYST_CONFIG_ANIMATION_TYPE_OFF,
        )
        gba_playtest.validate_fingerprint(normal, "<normal>", policy="behavior")
        gba_playtest.validate_fingerprint(
            accelerated,
            "<accelerated>",
            policy="behavior",
        )

    def test_perturbed_semantic_trace_is_not_equivalent(self):
        capture = self._capture(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        changed = copy.deepcopy(capture)
        changed["trace"][-1]["probes"][0]["value"] = "0x00000000"
        differences = gba_playtest.compare_fingerprints(
            capture,
            changed,
            policy="behavior",
        )
        self.assertTrue(
            any("trace" in difference for difference in differences),
            differences,
        )


if __name__ == "__main__":
    unittest.main()
