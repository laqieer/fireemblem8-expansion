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

        for address in (
            "0x08000000",
            "0x06000000",
            "0x05000000",
            "0x07000000",
            "0x0e000000",
        ):
            invalid_binding = profile_data(
                gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY
            )
            invalid_binding["execution_profile"]["play_state_config"]["address"] = address
            with self.assertRaisesRegex(
                gba_playtest.PlaytestError,
                "writable EWRAM or IWRAM",
            ):
                gba_playtest.parse_scenario_data(invalid_binding)

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

    def test_profile_covers_all_factions_events_flags_and_objective_result(self):
        data = accelerated_fidelity_checks.profile_data(
            gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY
        )
        endpoint = {
            (probe["address"], probe["size"])
            for probe in data["run_until"]["checkpoint"]["probes"]
        }
        for array_name in ("gUnitArrayBlue", "gUnitArrayRed", "gUnitArrayGreen"):
            self.assertIn((f"{array_name}+0x00c", 4), endpoint)
        self.assertNotIn(("gUnitArrayRed+0x174", 4), endpoint)
        self.assertNotIn(("gUnitArrayGreen+0x09c", 4), endpoint)
        for probe in (
            ("gEventSlots+0x30", 4),
            ("gEventSlotCounter", 4),
            ("gChapterFlagBits", 4),
            ("gPermanentFlagBits", 1),
        ):
            self.assertIn(probe, endpoint)

        trace = {
            (probe["address"], probe["size"])
            for probe in data["execution_profile"]["trace"]
        }
        event_poll_probes = {
            ("gEventSlots+0x30", 4),
            ("gEventSlotCounter", 4),
            ("gChapterFlagBits", 4),
            ("gPermanentFlagBits", 1),
        }
        self.assertFalse(event_poll_probes & trace)
        self.assertIn(
            (accelerated_fidelity_checks.EVENT_TRACE_SYMBOL, 4),
            endpoint,
        )
        self.assertLessEqual(
            len(data["run_until"]["checkpoint"]["probes"]),
            gba_playtest.MAX_PROBES_PER_CHECKPOINT,
        )
        self.assertIn(
            {"address": accelerated_fidelity_checks.POLICY_PROBE_SYMBOL, "size": 4},
            data["run_until"]["checkpoint"]["probes"],
        )

    def test_event_and_flag_probe_binding_rejects_out_of_span_access(self):
        bound = accelerated_fidelity_checks._bind_event_and_flag_probe(
            {"address": "gEventSlots+0x30", "size": 4},
            lambda _symbol: 0x030004B8,
        )
        self.assertEqual(bound["address"], "0x030004e8")
        with self.assertRaisesRegex(ValueError, "outside the documented"):
            accelerated_fidelity_checks._bind_event_and_flag_probe(
                {"address": "gEventSlots+0x38", "size": 1},
                lambda _symbol: 0x030004B8,
            )

    def test_event_and_flag_binding_surfaces_missing_exact_symbol(self):
        data = accelerated_fidelity_checks.profile_data(
            gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY
        )
        with mock.patch.object(
            accelerated_fidelity_checks,
            "resolve_elf_symbol_address",
            side_effect=accelerated_fidelity_checks.ProbeBindingError("missing event symbol"),
        ):
            with self.assertRaisesRegex(gba_playtest.PlaytestError, "missing event symbol"):
                accelerated_fidelity_checks._bind_event_and_flag_probes(
                    data,
                    Path("missing.elf"),
                )


class AcceleratedFidelityBackendTests(unittest.TestCase):
    def _capture_data(self, data: dict) -> dict:
        scenario = gba_playtest.parse_scenario_data(data)
        with temporary_directory("gba-accelerated-fidelity-") as temporary:
            rom = Path(temporary) / "fixture.gba"
            build_homebrew_rom(rom)
            return gba_playtest.capture(rom, scenario)

    def _capture(self, name: str) -> dict:
        return self._capture_data(profile_data(name))

    @staticmethod
    def _external_trace_snapshot(frame: int, probe_count: int) -> dict:
        return {
            "frame": frame,
            "probes": [
                {
                    "address": f"0x{0x02010000 + index * 4:08x}",
                    "size": 4,
                    "value": "0x00000000",
                }
                for index in range(probe_count)
            ],
        }

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

    def test_backend_rejects_ignored_profile_write(self):
        with temporary_directory("gba-accelerated-fidelity-") as temporary:
            scenario = gba_playtest.parse_scenario_data(
                profile_data(gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY)
            )
            plan = Path(temporary) / "ignored-profile-write.plan"
            backend = Path(temporary) / "gba-playtest-backend"
            rom = Path(temporary) / "fixture.gba"
            gba_playtest._write_plan(plan, scenario)
            plan.write_text(
                plan.read_text(encoding="ascii").replace(
                    f"PROFILE 1 1 {int(CONFIG_ADDRESS, 16)}",
                    "PROFILE 1 1 134217728",
                ),
                encoding="ascii",
            )
            build_homebrew_rom(rom)
            gba_playtest.build_backend(backend)
            result = subprocess.run(
                [str(backend), str(rom), str(plan)],
                capture_output=True,
                check=False,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("accelerated config write was not applied", result.stderr)

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

    def test_repeated_profile_samples_require_terminal_and_trace_frames(self):
        capture = self._capture(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        shifted_terminal = copy.deepcopy(capture)
        shifted_terminal["terminal"]["frame"] += 1
        shifted_terminal["checkpoints"][0]["frame"] += 1
        terminal_differences = accelerated_fidelity_checks.compare_profile_samples(
            capture,
            shifted_terminal,
        )
        self.assertTrue(
            any("terminal.frame" in difference for difference in terminal_differences),
            terminal_differences,
        )

        shifted_trace = copy.deepcopy(capture)
        shifted_trace["trace"][-1]["frame"] += 1
        trace_differences = accelerated_fidelity_checks.compare_profile_samples(
            capture,
            shifted_trace,
        )
        self.assertTrue(
            any("trace" in difference and ".frame" in difference for difference in trace_differences),
            trace_differences,
        )

    def test_format_four_rejects_terminal_impossible_profile_and_trace_frames(self):
        capture = self._capture(gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY)
        post_terminal_config = copy.deepcopy(capture)
        post_terminal_config["profile"]["config_apply_frame"] = (
            post_terminal_config["terminal"]["frame"] + 1
        )
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "must not exceed"):
            gba_playtest.validate_fingerprint(
                post_terminal_config,
                "<post-terminal-config>",
                policy="behavior",
            )

        post_terminal_trace = copy.deepcopy(capture)
        snapshot = copy.deepcopy(post_terminal_trace["trace"][-1])
        snapshot["frame"] = post_terminal_trace["terminal"]["frame"] + 1
        post_terminal_trace["trace"].append(snapshot)
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "must not exceed"):
            gba_playtest.validate_fingerprint(
                post_terminal_trace,
                "<post-terminal-trace>",
                policy="behavior",
            )

    def test_external_format_four_requires_initial_full_trace_snapshot(self):
        capture = self._capture(gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY)
        gba_playtest.validate_fingerprint(
            capture,
            "<backend-initial-trace-round-trip>",
            policy="behavior",
        )

        missing_initial = copy.deepcopy(capture)
        missing_initial["trace"] = []
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "non-empty"):
            gba_playtest.validate_fingerprint(
                missing_initial,
                "<missing-initial-trace>",
                policy="behavior",
            )

        late_initial = copy.deepcopy(capture)
        late_initial["trace"][0]["frame"] = 1
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "initial full trace snapshot"):
            gba_playtest.validate_fingerprint(
                late_initial,
                "<late-initial-trace>",
                policy="behavior",
            )

    def test_external_format_four_bounds_each_trace_snapshot_and_aggregate(self):
        capture = self._capture(gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY)
        at_limit = copy.deepcopy(capture)
        at_limit["trace"] = [
            self._external_trace_snapshot(0, gba_playtest.MAX_PROFILE_TRACE_PROBES)
        ]
        gba_playtest.validate_fingerprint(
            at_limit,
            "<trace-snapshot-at-limit>",
            policy="behavior",
        )

        over_snapshot_limit = copy.deepcopy(at_limit)
        over_snapshot_limit["trace"] = [
            self._external_trace_snapshot(
                0,
                gba_playtest.MAX_PROFILE_TRACE_PROBES + 1,
            )
        ]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "512-probe limit"):
            gba_playtest.validate_fingerprint(
                over_snapshot_limit,
                "<trace-snapshot-over-limit>",
                policy="behavior",
            )

        over_aggregate_limit = copy.deepcopy(at_limit)
        over_aggregate_limit["trace"].append(
            self._external_trace_snapshot(1, gba_playtest.MAX_PROFILE_TRACE_PROBES)
        )
        with mock.patch.object(
            gba_playtest,
            "MAX_PROFILE_TRACE_RECORDS",
            gba_playtest.MAX_PROFILE_TRACE_PROBES,
        ):
            with self.assertRaisesRegex(gba_playtest.PlaytestError, "aggregate limit"):
                gba_playtest.validate_fingerprint(
                    over_aggregate_limit,
                    "<trace-aggregate-over-limit>",
                    policy="behavior",
                )

    def test_external_accelerated_fingerprint_requires_exact_config_transition(self):
        capture = self._capture(gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY)
        gba_playtest.validate_fingerprint(capture, "<backend-round-trip>", policy="behavior")
        before = int(capture["profile"]["config_before"], 16)
        expected = int(capture["profile"]["config_after"], 16)
        cases = {
            "speed-disabled": expected & ~gba_playtest.PLAYST_CONFIG_GAME_SPEED_MASK,
            "animation-not-off": (
                expected & ~gba_playtest.PLAYST_CONFIG_ANIMATION_TYPE_MASK
            ) | (0x2 << 17),
            "unrelated-bit-changed": expected ^ 1,
        }
        for label, invalid_after in cases.items():
            with self.subTest(label=label):
                invalid = copy.deepcopy(capture)
                invalid["profile"]["config_before"] = f"0x{before:08x}"
                invalid["profile"]["config_after"] = f"0x{invalid_after:08x}"
                with self.assertRaisesRegex(
                    gba_playtest.PlaytestError,
                    "accelerated transformation",
                ):
                    gba_playtest.validate_fingerprint(
                        invalid,
                        f"<{label}>",
                        policy="behavior",
                    )

    def test_external_accelerated_fingerprint_rejects_visual_evidence(self):
        accelerated = self._capture(gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY)
        accelerated["checkpoints"][0]["framebuffer_hash"] = "fnv1a64-rgb24:0000000000000000"
        accelerated["checkpoints"][0]["regions"] = [
            {
                "name": "visible",
                "x": 0,
                "y": 0,
                "width": 1,
                "height": 1,
                "hash": "fnv1a64-region:0000000000000000",
            }
        ]
        accelerated["checkpoints"][0]["pixel_probes"] = [
            {"x": 0, "y": 0, "value": "0x000000"}
        ]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "forbids"):
            gba_playtest.validate_fingerprint(
                accelerated,
                "<accelerated-visual-evidence>",
                policy="behavior",
            )

        normal = self._capture(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        normal["checkpoints"][0]["framebuffer_hash"] = "fnv1a64-rgb24:0000000000000000"
        normal["checkpoints"][0]["regions"] = [
            {
                "name": "visible",
                "x": 0,
                "y": 0,
                "width": 1,
                "height": 1,
                "hash": "fnv1a64-region:0000000000000000",
            }
        ]
        normal["checkpoints"][0]["pixel_probes"] = [
            {"x": 0, "y": 0, "value": "0x000000"}
        ]
        gba_playtest.validate_fingerprint(normal, "<normal-visual-evidence>", policy="behavior")

    def test_benchmark_failure_clears_stale_output_before_capture(self):
        with temporary_directory("gba-accelerated-fidelity-benchmark-") as temporary:
            output = Path(temporary)
            benchmark = output / "accelerated-fidelity-benchmark.json"
            benchmark.write_text("{\"stale\": true}\n", encoding="utf-8")
            with mock.patch.object(
                gba_playtest,
                "build_backend",
                side_effect=accelerated_fidelity_checks.CheckError("capture failed"),
            ):
                result = accelerated_fidelity_checks.main(
                    [
                        "--rom",
                        str(output / "missing.gba"),
                        "--elf",
                        str(output / "missing.elf"),
                        "--out-dir",
                        str(output),
                        "--rom-commit",
                        "0" * 40,
                        "--configuration",
                        "test",
                        "--samples",
                        "1",
                    ]
                )
        self.assertEqual(result, 1)
        self.assertFalse(benchmark.exists())

    def test_benchmark_failure_never_publishes_success_json(self):
        capture = {
            "rom": {"sha1": "0" * 40, "size": 1, "title": "", "game_code": ""},
            "scenario": "fixture",
            "terminal": {"frame": 0, "reason": "success", "turn": None, "actions": None},
            "checkpoints": [{"probes": []}],
            "trace": [{"probes": []}],
        }
        with temporary_directory("gba-accelerated-fidelity-benchmark-") as temporary:
            output = Path(temporary)
            benchmark = output / "accelerated-fidelity-benchmark.json"
            with mock.patch.object(gba_playtest, "build_backend"), \
                mock.patch.object(accelerated_fidelity_checks, "_capture", side_effect=[capture, capture]), \
                mock.patch.object(accelerated_fidelity_checks, "_check_capture", return_value=["semantic failure"]), \
                mock.patch.object(accelerated_fidelity_checks, "compare_semantics", return_value=[]), \
                mock.patch.object(accelerated_fidelity_checks, "_perturbed_trace_is_rejected", return_value=True), \
                mock.patch.object(accelerated_fidelity_checks, "_write_benchmark") as write_benchmark:
                result = accelerated_fidelity_checks.main(
                    [
                        "--rom",
                        str(output / "fixture.gba"),
                        "--elf",
                        str(output / "fixture.elf"),
                        "--out-dir",
                        str(output),
                        "--rom-commit",
                        "0" * 40,
                        "--configuration",
                        "test",
                        "--samples",
                        "1",
                    ]
                )
        self.assertEqual(result, 1)
        write_benchmark.assert_not_called()
        self.assertFalse(benchmark.exists())

    def test_benchmark_success_replaces_output_atomically(self):
        capture = {
            "rom": {"sha1": "0" * 40, "size": 1, "title": "", "game_code": ""},
            "scenario": "fixture",
            "terminal": {"frame": 0},
        }
        with temporary_directory("gba-accelerated-fidelity-benchmark-") as temporary:
            benchmark = Path(temporary) / "accelerated-fidelity-benchmark.json"
            accelerated_fidelity_checks._write_benchmark(
                benchmark,
                "test",
                "0" * 40,
                capture,
                capture,
                [],
            )
            self.assertTrue(benchmark.is_file())
            self.assertFalse((Path(temporary) / f".{benchmark.name}.tmp").exists())

    def test_event_transition_endpoint_divergence_is_not_equivalent(self):
        baseline = self._capture(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        changed = copy.deepcopy(baseline)
        changed["checkpoints"][0]["probes"][0]["value"] = "0x00000000"
        differences = accelerated_fidelity_checks.compare_semantics(baseline, changed)
        self.assertTrue(
            any("checkpoint_probes" in difference for difference in differences),
            differences,
        )

    def test_event_transition_overflow_is_rejected(self):
        capture = {
            "checkpoints": [
                {
                    "probes": [
                        {
                            "address": accelerated_fidelity_checks.EVENT_TRACE_SYMBOL,
                            "value": "0x00000001",
                        },
                        {
                            "address": (
                                f"{accelerated_fidelity_checks.EVENT_TRACE_SYMBOL}+0x004"
                            ),
                            "value": "0x00000001",
                        },
                    ]
                }
            ]
        }
        self.assertEqual(
            accelerated_fidelity_checks._event_trace_failures(capture),
            ["event transition telemetry overflowed"],
        )

    def test_accelerated_profile_rejects_presentation_evidence(self):
        accelerated = profile_data(gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY)
        accelerated["run_until"]["checkpoint"]["framebuffer"] = True
        accelerated["run_until"]["checkpoint"]["regions"] = [
            {"name": "visible", "x": 0, "y": 0, "width": 1, "height": 1}
        ]
        accelerated["run_until"]["checkpoint"]["pixel_probes"] = [{"x": 0, "y": 0}]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "semantic-only"):
            gba_playtest.parse_scenario_data(accelerated)

        normal = profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        normal["run_until"]["checkpoint"]["framebuffer"] = True
        normal["run_until"]["checkpoint"]["regions"] = [
            {"name": "visible", "x": 0, "y": 0, "width": 1, "height": 1}
        ]
        normal["run_until"]["checkpoint"]["pixel_probes"] = [{"x": 0, "y": 0}]
        gba_playtest.parse_scenario_data(normal)

    def test_unit_probe_layout_covers_all_pointer_free_gameplay_fields(self):
        self.assertEqual(
            accelerated_fidelity_checks.UNIT_GAMEPLAY_PROBE_LAYOUT,
            (
                (0x08, 2), (0x0A, 2), (0x0C, 4), (0x10, 2),
                (0x12, 2), (0x14, 2), (0x16, 2), (0x18, 2),
                (0x1A, 2), (0x1C, 2),
                (0x1E, 2), (0x20, 2), (0x22, 2), (0x24, 2), (0x26, 2),
                (0x28, 4), (0x2C, 4), (0x30, 2),
                (0x32, 2), (0x34, 2), (0x36, 2), (0x38, 2), (0x40, 2),
                (0x42, 2), (0x44, 2), (0x46, 1),
            ),
        )


if __name__ == "__main__":
    unittest.main()
