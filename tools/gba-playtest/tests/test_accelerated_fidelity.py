"""Issue #88 accelerated-fidelity schema, backend, and comparator checks."""

from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gba_playtest
from homebrew_fixture import build_homebrew_rom
import run_accelerated_fidelity_checks as accelerated_fidelity_checks


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

    def test_trace_probes_are_canonicalized_and_resource_bounded(self):
        reverse_order = profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        reverse_order["execution_profile"]["trace"] = [
            {"address": CONFIG_ADDRESS, "size": 4},
            {"address": PROBE_ADDRESS, "size": 4},
        ]
        scenario = gba_playtest.parse_scenario_data(reverse_order)
        self.assertEqual(
            [probe.binding for probe in scenario.execution_profile.trace_probes],
            [PROBE_ADDRESS, CONFIG_ADDRESS],
        )

        at_limit = profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        at_limit["run_until"]["max_frames"] = gba_playtest.MAX_PROFILE_TRACE_RECORDS
        gba_playtest.parse_scenario_data(at_limit)

        over_limit = profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        over_limit["run_until"]["max_frames"] = (
            gba_playtest.MAX_PROFILE_TRACE_RECORDS + 1
        )
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "aggregate limit"):
            gba_playtest.parse_scenario_data(over_limit)


class AcceleratedFidelityBackendTests(unittest.TestCase):
    def _capture_data(self, data: dict) -> dict:
        scenario = gba_playtest.parse_scenario_data(data)
        with temporary_directory("gba-accelerated-fidelity-") as temporary:
            rom = Path(temporary) / "fixture.gba"
            build_homebrew_rom(rom)
            return gba_playtest.capture(rom, scenario)

    def _capture(self, name: str) -> dict:
        return self._capture_data(profile_data(name))

    def test_backend_rejects_trace_record_budget(self):
        with temporary_directory("gba-accelerated-fidelity-") as temporary:
            scenario = gba_playtest.parse_scenario_data(
                profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
            )
            plan = Path(temporary) / "over-budget.plan"
            backend = Path(temporary) / "gba-playtest-backend"
            gba_playtest._write_plan(plan, scenario)
            max_frames = gba_playtest.MAX_PROFILE_TRACE_RECORDS + 1
            plan_text = plan.read_text(encoding="ascii").replace(
                "4 1 0 0 0 0 0\n",
                f"{max_frames - 1} 1 0 0 0 0 0\n",
            )
            plan.write_text(
                plan_text.replace("RUN_UNTIL 5\n", f"RUN_UNTIL {max_frames}\n"),
                encoding="ascii",
            )
            gba_playtest.build_backend(backend)
            result = subprocess.run(
                [str(backend), "unused.gba", str(plan)],
                capture_output=True,
                check=False,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("trace record budget exceeds", result.stderr)

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
        self.assertEqual(gba_playtest.PLAYST_CONFIG_ANIMATION_TYPE_OFF, 0x1 << 17)
        self.assertEqual(
            after & gba_playtest.PLAYST_CONFIG_ANIMATION_TYPE_MASK,
            0x1 << 17,
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

    def test_reverse_ordered_trace_validates_as_canonical_fingerprint(self):
        data = profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        data["execution_profile"]["trace"] = [
            {"address": CONFIG_ADDRESS, "size": 4},
            {"address": PROBE_ADDRESS, "size": 4},
        ]
        capture = self._capture_data(data)
        self.assertEqual(
            [probe["address"] for probe in capture["trace"][0]["probes"]],
            [PROBE_ADDRESS, CONFIG_ADDRESS],
        )
        gba_playtest.validate_fingerprint(capture, "<canonical>", policy="behavior")
        with mock.patch.object(gba_playtest, "MAX_PROFILE_TRACE_RECORDS", 1):
            with self.assertRaisesRegex(gba_playtest.PlaytestError, "aggregate limit"):
                gba_playtest.validate_fingerprint(
                    capture,
                    "<over-budget-fingerprint>",
                    policy="behavior",
                )

    def test_semantic_comparison_requires_same_rom_provenance(self):
        baseline = self._capture(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        different_rom = copy.deepcopy(baseline)
        different_rom["rom"]["sha1"] = "f" * 40
        differences = accelerated_fidelity_checks.compare_semantics(
            baseline,
            different_rom,
        )
        self.assertTrue(
            any("rom.sha1" in difference for difference in differences),
            differences,
        )


if __name__ == "__main__":
    unittest.main()
