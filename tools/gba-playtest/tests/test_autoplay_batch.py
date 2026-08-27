"""Issue #91 deterministic finite autoplay batch reports."""

from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
PLAYTEST_DIR = TESTS_DIR.parent
ROOT = TESTS_DIR.parents[2]
for extra in (str(PLAYTEST_DIR), str(TESTS_DIR)):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import autoplay_batch
import gba_playtest
from homebrew_fixture import build_seed_batch_rom


WORK_ROOT = ROOT / "build" / "test-artifacts" / "autoplay-batch"
PROBE_SEED = "0x02000000"
PROBE_TURN = "0x02000004"
PROBE_ACTION = "0x02000008"


def scenario_data() -> dict:
    def probe(address: str) -> dict:
        return {"address": address, "size": 4}

    def comparison(address: str) -> dict:
        return {**probe(address), "operator": "gt", "value": "0x00000000"}

    return {
        "schema_version": 2,
        "name": "batch-seed-fixture",
        "description": "Bounded normal-fidelity seed-injection fixture.",
        "frames": [],
        "run_until": {
            "max_frames": 3,
            "terminal_conditions": [{"reason": "success", "all": [comparison(PROBE_TURN)]}],
            "turn_limit": {"maximum": 32, **probe(PROBE_TURN)},
            "action_limit": {"maximum": 32, **probe(PROBE_ACTION)},
            "checkpoint": {
                "name": "terminal",
                "framebuffer": False,
                "probes": [probe(PROBE_TURN), probe(PROBE_ACTION)],
            },
        },
    }


def specification_data() -> dict:
    def probe(address: str) -> dict:
        return {"address": address, "size": 4}

    return {
        "schema_version": 1,
        "name": "batch-normal-fixture",
        "configuration": "modern-debug-fixture",
        "profile": {"id": "normal-fixture", "fidelity": "normal"},
        "seeding": {**probe(PROBE_SEED), "frame": 0},
        "metrics": [
            {"id": "terminal", "kind": "terminal_reason"},
            {"id": "frames", "kind": "emulated_frames"},
            {"id": "turns", "kind": "turns"},
            {"id": "actions", "kind": "committed_actions"},
            {
                "id": "factions",
                "kind": "faction_group_counts",
                "groups": [
                    {
                        "faction": "blue",
                        "group": "main",
                        "survivors": probe(PROBE_TURN),
                        "casualties": probe(PROBE_ACTION),
                    }
                ],
            },
            {
                "id": "events",
                "kind": "event_flag_outcomes",
                "events": [
                    {
                        "id": "village-a",
                        "kind": "village",
                        "probe": probe(PROBE_TURN),
                        "success_value": 1,
                    }
                ],
            },
            {
                "id": "exp",
                "kind": "group_deltas",
                "delta_kind": "exp",
                "groups": [{"id": "blue", "probe": probe(PROBE_TURN)}],
            },
            {
                "id": "items",
                "kind": "group_deltas",
                "delta_kind": "item",
                "groups": [{"id": "blue", "probe": probe(PROBE_ACTION)}],
            },
            {
                "id": "resources",
                "kind": "group_deltas",
                "delta_kind": "resource",
                "groups": [{"id": "blue", "probe": probe(PROBE_ACTION)}],
            },
        ],
    }


class BatchFixtureTestCase(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(dir=WORK_ROOT)
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.rom = self.root / "fixture.gba"
        self.elf = self.root / "fixture.elf"
        self.scenario = self.root / "scenario.json"
        self.specification = self.root / "specification.json"
        build_seed_batch_rom(self.rom)
        self.elf.write_bytes(b"ELF IS NOT NEEDED FOR LITERAL FIXTURE PROBES")
        self.scenario.write_text(
            json.dumps(scenario_data(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.specification.write_text(
            json.dumps(specification_data(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.backend_builds: list[Path] = []
        self.capture_backends: list[Path] = []

    def arguments(self, output: Path, *, jobs: int = 1, seeds: str = "1,2,3") -> list[str]:
        return [
            "run",
            "--rom",
            str(self.rom),
            "--elf",
            str(self.elf),
            "--scenario",
            str(self.scenario),
            "--specification",
            str(self.specification),
            "--seeds",
            seeds,
            "--max-jobs",
            str(jobs),
            "--max-frames",
            "3",
            "--max-turns",
            "32",
            "--max-actions",
            "32",
            "--output",
            str(output),
        ]

    def output(self, name: str) -> Path:
        return self.root / name


class AutoplayBatchHostTests(BatchFixtureTestCase):
    def _fake_capture(
        self,
        rom,
        scenario,
        sram_image=None,
        retries=0,
        scheduled_write=None,
        work_dir=None,
        backend_path=None,
    ):
        self.assertIsNone(sram_image)
        self.assertEqual(scenario.name, "batch-seed-fixture")
        self.assertIsNotNone(scheduled_write)
        self.assertIsNotNone(backend_path)
        self.assertTrue(backend_path.is_file())
        self.capture_backends.append(backend_path)
        seed = scheduled_write.value
        action_value = 32 if seed == 2 else seed
        baseline_values = {
            PROBE_TURN: 0,
            PROBE_ACTION: 32 if seed == 2 else 2,
        }
        return {
            "baseline_probes": [
                {
                    "address": probe.binding,
                    "size": probe.size,
                    "value": f"0x{baseline_values.get(probe.binding, 0):0{probe.size * 2}x}",
                }
                for probe in scheduled_write.baseline_probes
            ],
            "rom": gba_playtest.rom_provenance(rom),
            "terminal": {
                "reason": "success" if seed != 2 else "max_actions",
                "frame": 1,
                "turn": {"address": PROBE_TURN, "size": 4, "value": f"0x{seed:08x}"},
                "actions": {
                    "address": PROBE_ACTION,
                    "size": 4,
                    "value": f"0x{action_value:08x}",
                },
            },
            "checkpoints": [
                {
                    "frame": 1,
                    "name": "terminal",
                    "probes": [
                        {"address": PROBE_TURN, "size": 4, "value": f"0x{seed:08x}"},
                        {
                            "address": PROBE_ACTION,
                            "size": 4,
                            "value": f"0x{action_value:08x}",
                        },
                    ],
                }
            ],
        }

    def _fake_build_backend(self, output, retries=0):
        self.assertEqual(retries, 0)
        self.backend_builds.append(output)
        output.write_bytes(b"shared fake backend")

    def _run_fake(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            gba_playtest,
            "build_backend",
            side_effect=self._fake_build_backend,
        ), mock.patch.object(gba_playtest, "capture", side_effect=self._fake_capture):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = autoplay_batch.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def _refresh_report_summary(self, report: dict) -> None:
        metric_definitions = {
            metric["id"]: metric
            for metric in report["provenance"]["specification"]["definition"][
                "metrics"
            ]
        }
        success_count = sum(run["status"] == "success" for run in report["runs"])
        terminal_reasons: dict[str, int] = {}
        for run in report["runs"]:
            if "terminal" in run:
                reason = run["terminal"]["reason"]
                terminal_reasons[reason] = terminal_reasons.get(reason, 0) + 1
        report["summary"] = {
            "failure_count": len(report["runs"]) - success_count,
            "metric_distributions": autoplay_batch._metric_distributions(
                report["runs"],
                metric_definitions,
            ),
            "run_count": len(report["runs"]),
            "success_count": success_count,
            "terminal_reasons": dict(sorted(terminal_reasons.items())),
        }

    def _refresh_specification_digest(self, report: dict) -> None:
        specification = report["provenance"]["specification"]
        specification["definition_sha256"] = autoplay_batch._canonical_sha256(
            specification["definition"]
        )

    def _refresh_scenario_digest(self, report: dict) -> None:
        scenario = report["provenance"]["scenario"]
        scenario["definition_sha256"] = autoplay_batch._canonical_sha256(
            scenario["definition"]
        )

    def _assert_compare_rejects(
        self,
        baseline: Path,
        candidate_data: dict,
        name: str,
        expected: str,
    ) -> None:
        candidate = self.output(f"{name}.json")
        candidate.write_text(
            autoplay_batch.serialize_report(candidate_data),
            encoding="utf-8",
        )
        comparison = self.output(f"{name}-comparison.json")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = autoplay_batch.main(
                [
                    "compare",
                    "--baseline",
                    str(baseline),
                    "--candidate",
                    str(candidate),
                    "--output",
                    str(comparison),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn(expected, stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertFalse(comparison.exists())
        self.assertFalse(autoplay_batch._temporary_output_path(comparison).exists())

    def test_serial_parallel_reports_are_byte_identical_and_failures_remain_visible(self):
        serial = self.output("host-serial.json")
        parallel = self.output("host-parallel.json")
        serial_code, _, serial_error = self._run_fake(self.arguments(serial, jobs=1))
        parallel_code, _, parallel_error = self._run_fake(self.arguments(parallel, jobs=3))
        self.assertEqual(serial_code, 1)
        self.assertEqual(parallel_code, 1)
        self.assertEqual(serial_error, "")
        self.assertEqual(parallel_error, "")
        self.assertEqual(serial.read_bytes(), parallel.read_bytes())
        self.assertEqual(len(self.backend_builds), 2)
        self.assertEqual(len(set(self.capture_backends[:3])), 1)
        self.assertEqual(len(set(self.capture_backends[3:])), 1)
        report = autoplay_batch.validate_report(
            json.loads(serial.read_text(encoding="utf-8")), str(serial)
        )
        self.assertEqual([run["seed"] for run in report["runs"]], [1, 2, 3])
        failed = report["runs"][1]
        self.assertEqual(failed["status"], "terminal_failure")
        self.assertEqual(failed["terminal"]["reason"], "max_actions")
        self.assertEqual(failed["metrics"]["turns"], 2)
        self.assertEqual(
            failed["metrics"]["factions"],
            [{"casualties": 32, "faction": "blue", "group": "main", "survivors": 2}],
        )
        self.assertEqual(report["summary"]["failure_count"], 1)
        self.assertEqual(
            report["summary"]["metric_distributions"]["turns"],
            [
                {"count": 1, "value": 1},
                {"count": 1, "value": 2},
                {"count": 1, "value": 3},
            ],
        )
        self.assertEqual(report["provenance"]["profile"]["fidelity"], "normal")
        self.assertEqual(
            [run["metrics"]["items"][0]["delta"] for run in report["runs"]],
            [-1, 0, 1],
        )
        self.assertEqual(
            report["runs"][0]["metrics"]["items"][0],
            {
                "baseline": 2,
                "delta": -1,
                "group": "blue",
                "kind": "item",
                "terminal": 1,
            },
        )
        self.assertRegex(
            report["provenance"]["scenario"]["definition_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertRegex(
            report["provenance"]["specification"]["definition_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_preflight_rejects_invalid_inputs_before_capture(self):
        cases = (
            ("duplicate-seeds", self.arguments(self.output("duplicate.json"), seeds="1,1"), "duplicate"),
            ("implicit-seeds", self.arguments(self.output("implicit.json"), seeds=""), "explicit"),
            (
                "missing-hard-bound",
                self.arguments(self.output("missing-bound.json"))[:-4]
                + ["--max-actions", "0", "--output", str(self.output("missing-bound.json"))],
                "positive",
            ),
            (
                "save-reuse",
                self.arguments(self.output("save-reuse.json"))
                + ["--sram-image", str(self.root / "reuse.sav")],
                "never reuse writable save",
            ),
        )
        for name, arguments, expected in cases:
            with self.subTest(name=name):
                stderr = io.StringIO()
                with mock.patch.object(
                    gba_playtest,
                    "capture",
                    side_effect=AssertionError("preflight reached capture"),
                ):
                    with redirect_stderr(stderr):
                        code = autoplay_batch.main(arguments)
                self.assertEqual(code, 2)
                self.assertIn(expected, stderr.getvalue())
        unsupported = specification_data()
        unsupported["metrics"][0]["kind"] = "score"
        self.specification.write_text(
            json.dumps(unsupported, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with mock.patch.object(
            gba_playtest,
            "capture",
            side_effect=AssertionError("preflight reached capture"),
        ):
            with redirect_stderr(stderr):
                code = autoplay_batch.main(self.arguments(self.output("unsupported.json")))
        self.assertEqual(code, 2)
        self.assertIn("must be one of", stderr.getvalue())
        collision = self.output("collision.json")
        collision.write_text("existing", encoding="utf-8")
        stderr = io.StringIO()
        with mock.patch.object(
            gba_playtest,
            "capture",
            side_effect=AssertionError("preflight reached capture"),
        ):
            with redirect_stderr(stderr):
                code = autoplay_batch.main(self.arguments(collision))
        self.assertEqual(code, 2)
        self.assertIn("collision", stderr.getvalue())

    def test_schema_and_execution_profile_fail_before_backend_or_capture(self):
        cases = []
        version_three = scenario_data()
        version_three["schema_version"] = 3
        version_three["execution_profile"] = {
            "name": "normal-fidelity",
            "trace": [{"address": PROBE_TURN, "size": 4}],
        }
        cases.append(("schema-v3", version_three, "schema_version exactly 2"))
        profile_on_v2 = scenario_data()
        profile_on_v2["execution_profile"] = {
            "name": "normal-fidelity",
            "trace": [{"address": PROBE_TURN, "size": 4}],
        }
        cases.append(("profile-on-v2", profile_on_v2, "unknown field"))

        for name, scenario, expected in cases:
            with self.subTest(name=name):
                self.scenario.write_text(
                    json.dumps(scenario, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                output = self.output(f"{name}.json")
                stderr = io.StringIO()
                with mock.patch.object(
                    gba_playtest,
                    "build_backend",
                    side_effect=AssertionError("preflight reached backend setup"),
                ), mock.patch.object(
                    gba_playtest,
                    "capture",
                    side_effect=AssertionError("preflight reached capture"),
                ), redirect_stderr(stderr):
                    code = autoplay_batch.main(self.arguments(output))
                self.assertEqual(code, 2)
                self.assertIn(expected, stderr.getvalue())
                self.assertFalse(output.exists())
                self.assertFalse(autoplay_batch._temporary_output_path(output).exists())

    def test_semantic_digests_and_comparison_detect_same_name_definition_changes(self):
        baseline = self.output("semantic-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        baseline_data = json.loads(baseline.read_text(encoding="utf-8"))

        scenario = scenario_data()
        scenario["run_until"]["terminal_conditions"][0]["all"][0]["value"] = "0x00000001"
        self.scenario.write_text(
            json.dumps(scenario, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        specification = specification_data()
        specification["metrics"][5]["events"][0]["success_value"] = 2
        self.specification.write_text(
            json.dumps(specification, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        candidate = self.output("semantic-candidate.json")
        self.assertEqual(self._run_fake(self.arguments(candidate))[0], 1)
        candidate_data = json.loads(candidate.read_text(encoding="utf-8"))

        self.assertEqual(
            baseline_data["provenance"]["scenario"]["name"],
            candidate_data["provenance"]["scenario"]["name"],
        )
        self.assertNotEqual(
            baseline_data["provenance"]["scenario"]["definition_sha256"],
            candidate_data["provenance"]["scenario"]["definition_sha256"],
        )
        self.assertNotEqual(
            baseline_data["provenance"]["specification"]["definition_sha256"],
            candidate_data["provenance"]["specification"]["definition_sha256"],
        )
        comparison = self.output("semantic-comparison.json")
        self.assertEqual(
            autoplay_batch.main(
                [
                    "compare",
                    "--baseline",
                    str(baseline),
                    "--candidate",
                    str(candidate),
                    "--output",
                    str(comparison),
                ]
            ),
            0,
        )
        changed_fields = {
            change["field"]
            for change in json.loads(comparison.read_text(encoding="utf-8"))[
                "comparison"
            ]["provenance_changes"]
        }
        self.assertIn("scenario.definition_sha256", changed_fields)
        self.assertIn("specification.definition_sha256", changed_fields)

    def test_imported_spec_reapplies_complete_required_metric_contract(self):
        baseline = self.output("metric-contract-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))

        cases = (
            ("missing-terminal", "terminal_reason", None, "required metric kind 'terminal_reason'"),
            ("missing-item-delta", "group_deltas", "item", "required group delta kind 'item'"),
        )
        for name, kind, delta_kind, expected in cases:
            with self.subTest(name=name):
                candidate = copy.deepcopy(valid)
                definitions = candidate["provenance"]["specification"]["definition"][
                    "metrics"
                ]
                removed = next(
                    metric
                    for metric in definitions
                    if metric["kind"] == kind
                    and (
                        delta_kind is None
                        or metric.get("delta_kind") == delta_kind
                    )
                )
                definitions.remove(removed)
                for run in candidate["runs"]:
                    if "metrics" in run:
                        run["metrics"].pop(removed["id"])
                self._refresh_specification_digest(candidate)
                self._refresh_report_summary(candidate)
                self._assert_compare_rejects(
                    baseline,
                    candidate,
                    name,
                    expected,
                )

        duplicate_kind = copy.deepcopy(valid)
        definitions = duplicate_kind["provenance"]["specification"]["definition"][
            "metrics"
        ]
        next(metric for metric in definitions if metric["id"] == "frames")[
            "kind"
        ] = "terminal_reason"
        self._refresh_specification_digest(duplicate_kind)
        self._assert_compare_rejects(
            baseline,
            duplicate_kind,
            "duplicate-required-kind",
            "required metric kind 'terminal_reason'; found 2",
        )

        duplicate_delta = copy.deepcopy(valid)
        definitions = duplicate_delta["provenance"]["specification"]["definition"][
            "metrics"
        ]
        next(metric for metric in definitions if metric["id"] == "resources")[
            "delta_kind"
        ] = "item"
        self._refresh_specification_digest(duplicate_delta)
        self._assert_compare_rejects(
            baseline,
            duplicate_delta,
            "duplicate-required-delta",
            "required group delta kind 'item'; found 2",
        )

    def test_imported_bounds_counters_and_metrics_cannot_exceed_scenario(self):
        baseline = self.output("bounds-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))

        for field in ("max_frames", "max_turns", "max_actions"):
            with self.subTest(bound=field):
                candidate = copy.deepcopy(valid)
                candidate["provenance"]["bounds"][field] += 1
                self._assert_compare_rejects(
                    baseline,
                    candidate,
                    f"mismatched-{field}",
                    "bounds must exactly match canonical scenario",
                )

        frame = copy.deepcopy(valid)
        frame["runs"][0]["terminal"]["frame"] = 3
        frame["runs"][0]["metrics"]["frames"] = 4
        self._refresh_report_summary(frame)
        self._assert_compare_rejects(
            baseline,
            frame,
            "overbound-frame",
            ".terminal.frame must be an integer from 0 through 2",
        )

        for counter_name, metric_id in (("turn", "turns"), ("actions", "actions")):
            with self.subTest(counter=counter_name):
                candidate = copy.deepcopy(valid)
                candidate["runs"][0]["terminal"][counter_name]["value"] = "0x00000021"
                candidate["runs"][0]["metrics"][metric_id] = 33
                self._refresh_report_summary(candidate)
                self._assert_compare_rejects(
                    baseline,
                    candidate,
                    f"overbound-{counter_name}",
                    "exceeds declared bound 32",
                )

        wrong_probe = copy.deepcopy(valid)
        wrong_probe["runs"][0]["terminal"]["turn"]["address"] = PROBE_ACTION
        self._assert_compare_rejects(
            baseline,
            wrong_probe,
            "wrong-turn-probe",
            "address/size must match the canonical scenario counter",
        )

    def test_imported_terminal_reasons_match_declared_backend_invariants(self):
        baseline = self.output("terminal-reason-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))

        cases = (
            (
                "undeclared-objective",
                "objective_failure",
                1,
                None,
                None,
                "not declared by the scenario",
            ),
            (
                "unconfigured-stall",
                "engine_stall",
                1,
                None,
                None,
                "engine_stall is not configured",
            ),
            (
                "early-max-frames",
                "max_frames",
                1,
                None,
                None,
                "final bounded frame",
            ),
            (
                "early-max-turns",
                "max_turns",
                1,
                1,
                None,
                "max_turns did not reach",
            ),
            (
                "early-max-actions",
                "max_actions",
                1,
                None,
                1,
                "max_actions did not reach",
            ),
        )
        for name, reason, frame, turn, actions, expected in cases:
            with self.subTest(name=name):
                candidate = copy.deepcopy(valid)
                run = candidate["runs"][0]
                run["status"] = "terminal_failure"
                run["terminal"]["reason"] = reason
                run["terminal"]["frame"] = frame
                run["metrics"]["terminal"] = reason
                run["metrics"]["frames"] = frame + 1
                if turn is not None:
                    run["terminal"]["turn"]["value"] = f"0x{turn:08x}"
                    run["metrics"]["turns"] = turn
                if actions is not None:
                    run["terminal"]["actions"]["value"] = f"0x{actions:08x}"
                    run["metrics"]["actions"] = actions
                self._refresh_report_summary(candidate)
                self._assert_compare_rejects(
                    baseline,
                    candidate,
                    name,
                    expected,
                )

        scenario = gba_playtest.parse_scenario_data(
            valid["provenance"]["scenario"]["definition"]
        )
        for reason, frame, turn, actions in (
            ("success", 1, 1, 1),
            ("max_frames", 2, 1, 1),
            ("max_turns", 1, 32, 1),
            ("max_actions", 1, 1, 32),
        ):
            with self.subTest(valid_reason=reason):
                gba_playtest.validate_run_until_terminal_outcome(
                    scenario,
                    reason,
                    frame,
                    True,
                    turn,
                    True,
                    actions,
                )

    def test_imported_seed_binding_is_resolved_writable_and_in_frame(self):
        baseline = self.output("seed-binding-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))

        cases = (
            ("rom-seed", 0x08000000, 0, "writable EWRAM or IWRAM"),
            ("past-ewram", 0x02040000, 0, "writable EWRAM or IWRAM"),
            ("late-seed-frame", int(PROBE_SEED, 16), 3, "must be below canonical"),
        )
        for name, resolved_address, frame, expected in cases:
            with self.subTest(name=name):
                candidate = copy.deepcopy(valid)
                provenance_seed = candidate["provenance"]["seed_injection"]
                definition_seed = candidate["provenance"]["specification"][
                    "definition"
                ]["seeding"]
                provenance_seed["resolved_address"] = resolved_address
                definition_seed["resolved_address"] = resolved_address
                provenance_seed["frame"] = frame
                definition_seed["frame"] = frame
                self._refresh_specification_digest(candidate)
                self._assert_compare_rejects(
                    baseline,
                    candidate,
                    name,
                    expected,
                )

        unresolved = copy.deepcopy(valid)
        unresolved["provenance"]["seed_injection"]["resolved_address"] = (
            "gUnresolvedSeed"
        )
        unresolved["provenance"]["specification"]["definition"]["seeding"][
            "resolved_address"
        ] = "gUnresolvedSeed"
        self._refresh_specification_digest(unresolved)
        self._assert_compare_rejects(
            baseline,
            unresolved,
            "unresolved-seed",
            "resolved_address must be an integer",
        )

        mismatched = copy.deepcopy(valid)
        mismatched["provenance"]["seed_injection"]["resolved_address"] += 4
        self._assert_compare_rejects(
            baseline,
            mismatched,
            "mismatched-seed-provenance",
            "does not match literal address",
        )

    def test_imported_metric_probes_must_exist_in_terminal_checkpoint(self):
        baseline = self.output("probe-membership-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))
        autoplay_batch.validate_report(valid, "shared-probe-positive")
        definitions = valid["provenance"]["specification"]["definition"]["metrics"]
        items_probe = next(
            metric for metric in definitions if metric["id"] == "items"
        )["groups"][0]["probe"]
        resources_probe = next(
            metric for metric in definitions if metric["id"] == "resources"
        )["groups"][0]["probe"]
        self.assertEqual(items_probe, resources_probe)

        unrelated = copy.deepcopy(valid)
        definition = next(
            metric
            for metric in unrelated["provenance"]["specification"]["definition"][
                "metrics"
            ]
            if metric["id"] == "exp"
        )
        definition["groups"][0]["probe"]["address"] = "0x0200000c"
        definition["groups"][0]["probe"]["resolved_address"] = 0x0200000C
        self._refresh_specification_digest(unrelated)
        self._assert_compare_rejects(
            baseline,
            unrelated,
            "unrelated-metric-probe",
            "not present in scenario",
        )

        wrong_size = copy.deepcopy(valid)
        definition = next(
            metric
            for metric in wrong_size["provenance"]["specification"]["definition"][
                "metrics"
            ]
            if metric["id"] == "exp"
        )
        definition["groups"][0]["probe"]["size"] = 2
        self._refresh_specification_digest(wrong_size)
        self._assert_compare_rejects(
            baseline,
            wrong_size,
            "wrong-size-metric-probe",
            "not present in scenario",
        )

        missing = copy.deepcopy(valid)
        checkpoint = missing["provenance"]["scenario"]["definition"]["run_until"][
            "checkpoint"
        ]
        checkpoint["probes"] = [
            probe
            for probe in checkpoint["probes"]
            if probe["address"] != PROBE_ACTION
        ]
        self._refresh_scenario_digest(missing)
        self._assert_compare_rejects(
            baseline,
            missing,
            "missing-terminal-probe",
            "not present in scenario",
        )

    def test_imported_width_backed_metrics_fit_declared_probe_sizes(self):
        baseline = self.output("metric-width-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))

        for size in (1, 2, 4):
            maximum = (1 << (size * 8)) - 1
            for metric_id, value_field, probe_field in (
                ("factions", "survivors", "survivors"),
                ("exp", "delta", "probe"),
            ):
                with self.subTest(size=size, metric=metric_id):
                    boundary = copy.deepcopy(valid)
                    definition = next(
                        metric
                        for metric in boundary["provenance"]["specification"][
                            "definition"
                        ]["metrics"]
                        if metric["id"] == metric_id
                    )
                    definition["groups"][0][probe_field]["size"] = size
                    checkpoint_probes = boundary["provenance"]["scenario"][
                        "definition"
                    ]["run_until"]["checkpoint"]["probes"]
                    if not any(
                        probe["address"]
                        == definition["groups"][0][probe_field]["address"]
                        and probe["size"] == size
                        for probe in checkpoint_probes
                    ):
                        checkpoint_probes.append(
                            {
                                "address": definition["groups"][0][probe_field][
                                    "address"
                                ],
                                "size": size,
                            }
                        )
                        checkpoint_probes.sort(
                            key=lambda probe: (
                                int(probe["address"], 16),
                                probe["size"],
                            )
                        )
                    self._refresh_scenario_digest(boundary)
                    value = boundary["runs"][0]["metrics"][metric_id][0]
                    value[value_field] = maximum
                    if metric_id == "exp":
                        value["baseline"] = 0
                        value["terminal"] = maximum
                    self._refresh_specification_digest(boundary)
                    self._refresh_report_summary(boundary)
                    autoplay_batch.validate_report(
                        boundary,
                        f"{metric_id}-{size}-byte-boundary",
                    )

                    overflow = copy.deepcopy(boundary)
                    overflow["runs"][0]["metrics"][metric_id][0][
                        value_field
                    ] = maximum + 1
                    self._refresh_report_summary(overflow)
                    self._assert_compare_rejects(
                        baseline,
                        overflow,
                        f"{metric_id}-{size}-byte-overflow",
                        f"declared {size}-byte probe",
                    )

    def test_imported_metric_lists_enforce_cardinality_and_identity(self):
        baseline = self.output("metric-list-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))

        cases = (
            ("factions", "groups", "group", "faction/group"),
            ("events", "events", "id", ".id must be sorted and unique"),
            ("exp", "groups", "id", ".id must be sorted and unique"),
        )
        for metric_id, list_field, identity_field, duplicate_error in cases:
            with self.subTest(metric=metric_id):
                excessive = copy.deepcopy(valid)
                definition = next(
                    metric
                    for metric in excessive["provenance"]["specification"][
                        "definition"
                    ]["metrics"]
                    if metric["id"] == metric_id
                )
                template_definition = definition[list_field][0]
                definition[list_field] = []
                for run in excessive["runs"]:
                    run["metrics"][metric_id] = []
                for index in range(65):
                    definition_entry = copy.deepcopy(template_definition)
                    identifier = f"entry-{index:02d}"
                    definition_entry[identity_field] = identifier
                    definition[list_field].append(definition_entry)
                    for run in excessive["runs"]:
                        value_entry = copy.deepcopy(
                            valid["runs"][run["seed"] - 1]["metrics"][metric_id][0]
                        )
                        if metric_id == "factions":
                            value_entry["group"] = identifier
                        elif metric_id == "events":
                            value_entry["id"] = identifier
                        else:
                            value_entry["group"] = identifier
                        run["metrics"][metric_id].append(value_entry)
                self._refresh_specification_digest(excessive)
                self._refresh_report_summary(excessive)
                self._assert_compare_rejects(
                    baseline,
                    excessive,
                    f"{metric_id}-over-cap",
                    "65 entries, exceeding 64",
                )

                duplicate = copy.deepcopy(valid)
                definition = next(
                    metric
                    for metric in duplicate["provenance"]["specification"][
                        "definition"
                    ]["metrics"]
                    if metric["id"] == metric_id
                )
                definition[list_field].append(
                    copy.deepcopy(definition[list_field][0])
                )
                for run in duplicate["runs"]:
                    run["metrics"][metric_id].append(
                        copy.deepcopy(run["metrics"][metric_id][0])
                    )
                self._refresh_specification_digest(duplicate)
                self._refresh_report_summary(duplicate)
                self._assert_compare_rejects(
                    baseline,
                    duplicate,
                    f"{metric_id}-duplicate",
                    duplicate_error,
                )

    def test_seed_values_fit_declared_probe_width_before_backend_setup(self):
        scenario = gba_playtest.parse_scenario_data(scenario_data())
        for size in (1, 2, 4):
            maximum = (1 << (size * 8)) - 1
            probe = gba_playtest.Probe(
                PROBE_SEED,
                int(PROBE_SEED, 16),
                size,
                None,
            )
            for boundary in (0, maximum):
                gba_playtest.validate_scheduled_write(
                    scenario,
                    gba_playtest.ScheduledWrite(0, probe, boundary),
                )
            for invalid in (True, -1, maximum + 1):
                with self.subTest(size=size, invalid=invalid):
                    with self.assertRaisesRegex(
                        gba_playtest.PlaytestError,
                        f"{size}-byte binding",
                    ):
                        gba_playtest.validate_scheduled_write(
                            scenario,
                            gba_playtest.ScheduledWrite(0, probe, invalid),
                        )

        for size, seed in ((1, "256"), (2, "65536"), (4, "4294967296"), (4, "-1")):
            with self.subTest(preflight_size=size, seed=seed):
                specification = specification_data()
                specification["seeding"]["size"] = size
                self.specification.write_text(
                    json.dumps(specification, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                output = self.output(f"seed-{size}-{seed.replace('-', 'negative')}.json")
                stderr = io.StringIO()
                with mock.patch.object(
                    gba_playtest,
                    "build_backend",
                    side_effect=AssertionError("invalid seed reached backend setup"),
                ), mock.patch.object(
                    gba_playtest,
                    "capture",
                    side_effect=AssertionError("invalid seed reached capture"),
                ), redirect_stderr(stderr):
                    code = autoplay_batch.main(
                        self.arguments(output, seeds=seed)
                    )
                self.assertEqual(code, 2)
                self.assertFalse(output.exists())
                self.assertFalse(autoplay_batch._temporary_output_path(output).exists())

    def test_imported_run_count_is_bounded_unique_and_ascending(self):
        baseline = self.output("run-count-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))

        maximum = copy.deepcopy(valid)
        template = maximum["runs"][0]
        maximum["runs"] = []
        for seed in range(256):
            run = copy.deepcopy(template)
            run["seed"] = seed
            maximum["runs"].append(run)
        self._refresh_report_summary(maximum)
        autoplay_batch.validate_report(maximum, "maximum-report")

        cases = []
        empty = copy.deepcopy(valid)
        empty["runs"] = []
        cases.append(("empty-runs", empty, "non-empty array"))
        excessive = copy.deepcopy(maximum)
        extra = copy.deepcopy(template)
        extra["seed"] = 256
        excessive["runs"].append(extra)
        cases.append(("excessive-runs", excessive, "exceeding the 256-run limit"))
        duplicate = copy.deepcopy(valid)
        duplicate["runs"][1]["seed"] = duplicate["runs"][0]["seed"]
        cases.append(("duplicate-runs", duplicate, "unique ascending"))
        descending = copy.deepcopy(valid)
        descending["runs"].reverse()
        cases.append(("descending-runs", descending, "unique ascending"))
        for name, report, expected in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    gba_playtest.PlaytestError,
                    expected,
                ):
                    autoplay_batch.validate_report(report, name)

    def test_compare_rejects_malformed_nested_reports_without_tracebacks(self):
        baseline = self.output("deep-baseline.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        valid = json.loads(baseline.read_text(encoding="utf-8"))

        mutations = (
            ("provenance-list", lambda data: data.__setitem__("provenance", []), ".provenance must be an object"),
            ("rom-null", lambda data: data["provenance"].__setitem__("rom", None), ".rom must be an object"),
            ("rom-size-bool", lambda data: data["provenance"]["rom"].__setitem__("size", True), ".rom.size"),
            (
                "scenario-digest-mismatch",
                lambda data: data["provenance"]["scenario"].__setitem__(
                    "definition_sha256", "0" * 64
                ),
                "definition_sha256 does not match",
            ),
            ("runs-null", lambda data: data.__setitem__("runs", None), ".runs must be a non-empty array"),
            ("terminal-list", lambda data: data["runs"][0].__setitem__("terminal", []), ".terminal must be an object"),
            ("terminal-frame-object", lambda data: data["runs"][0]["terminal"].__setitem__("frame", {}), ".terminal.frame"),
            ("metrics-list", lambda data: data["runs"][0].__setitem__("metrics", []), ".metrics must be an object"),
            ("metric-bool", lambda data: data["runs"][0]["metrics"].__setitem__("turns", True), ".metrics.turns"),
            ("summary-count-bool", lambda data: data["summary"].__setitem__("failure_count", False), ".failure_count"),
            ("distribution-null", lambda data: data["summary"]["metric_distributions"].__setitem__("turns", None), ".metric_distributions.turns"),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                malformed = copy.deepcopy(valid)
                mutate(malformed)
                candidate = self.output(f"malformed-{name}.json")
                candidate.write_text(
                    json.dumps(malformed, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                comparison = self.output(f"malformed-{name}-comparison.json")
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    code = autoplay_batch.main(
                        [
                            "compare",
                            "--baseline",
                            str(baseline),
                            "--candidate",
                            str(candidate),
                            "--output",
                            str(comparison),
                        ]
                    )
                self.assertEqual(code, 2)
                self.assertIn(expected, stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
                self.assertFalse(comparison.exists())
                self.assertFalse(
                    autoplay_batch._temporary_output_path(comparison).exists()
                )

    def test_setup_failure_is_status_two_without_seed_records_and_retry_succeeds(self):
        for jobs in (1, 3):
            with self.subTest(jobs=jobs):
                output = self.output(f"setup-{jobs}.json")
                stderr = io.StringIO()
                with mock.patch.object(
                    gba_playtest,
                    "build_backend",
                    side_effect=gba_playtest.PlaytestError("compiler setup failed"),
                ) as build_backend, mock.patch.object(
                    gba_playtest,
                    "capture",
                    side_effect=AssertionError("setup failure reached a seed"),
                ), redirect_stderr(stderr):
                    code = autoplay_batch.main(self.arguments(output, jobs=jobs))
                self.assertEqual(code, 2)
                self.assertEqual(build_backend.call_count, 1)
                self.assertIn("compiler setup failed", stderr.getvalue())
                self.assertFalse(output.exists())
                self.assertFalse(autoplay_batch._temporary_output_path(output).exists())

                retry_code, _, _ = self._run_fake(self.arguments(output, jobs=jobs))
                self.assertEqual(retry_code, 1)
                self.assertTrue(output.is_file())

    def test_seed_execution_failure_is_status_one_in_serial_and_parallel_reports(self):
        reports = []

        def fail_one_seed(*args, **kwargs):
            if kwargs["scheduled_write"].value == 2:
                raise gba_playtest.PlaytestError("seed execution failed")
            return self._fake_capture(*args, **kwargs)

        for jobs in (1, 3):
            output = self.output(f"execution-{jobs}.json")
            with mock.patch.object(
                gba_playtest,
                "build_backend",
                side_effect=self._fake_build_backend,
            ), mock.patch.object(gba_playtest, "capture", side_effect=fail_one_seed):
                code = autoplay_batch.main(self.arguments(output, jobs=jobs))
            self.assertEqual(code, 1)
            reports.append(output.read_bytes())
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["runs"][1]["status"], "execution_failure")
            self.assertEqual(
                report["runs"][1]["error"],
                "PlaytestError [scenario='batch-seed-fixture' rom='fixture.gba']: "
                "seed execution failed",
            )
        self.assertEqual(reports[0], reports[1])

    def test_execution_errors_normalize_ephemeral_workspace_paths(self):
        reports = []
        for jobs, label in ((1, "serial-a1b2"), (3, "parallel-c3d4")):
            output = self.output(f"normalized-error-{jobs}.json")

            def fail_with_workspace(*args, **kwargs):
                seed = kwargs["scheduled_write"].value
                raise gba_playtest.PlaytestError(
                    f"libmGBA backend failed while loading "
                    f"{self.root}/gba-playtest-{label}-{seed}/input.gba"
                )

            with mock.patch.object(
                gba_playtest,
                "build_backend",
                side_effect=self._fake_build_backend,
            ), mock.patch.object(
                gba_playtest,
                "capture",
                side_effect=fail_with_workspace,
            ):
                self.assertEqual(
                    autoplay_batch.main(self.arguments(output, jobs=jobs)),
                    1,
                )
            reports.append(output.read_bytes())
            report = json.loads(output.read_text(encoding="utf-8"))
            for run in report["runs"]:
                self.assertEqual(
                    run["error"],
                    "PlaytestError [scenario='batch-seed-fixture' "
                    "rom='fixture.gba']: libmGBA backend failed while loading "
                    "<gba-playtest-workspace>",
                )
        self.assertEqual(reports[0], reports[1])

    def test_delta_aliases_share_one_numeric_baseline_observation(self):
        resolver = autoplay_batch.ElfSymbolResolver(self.elf)
        scenario = gba_playtest.load_scenario(self.scenario, resolver)
        specification = autoplay_batch.load_specification(
            self.specification,
            resolver,
        )
        metrics = []
        for metric in specification.metrics:
            if metric.identifier != "items":
                metrics.append(metric)
                continue
            definition = copy.deepcopy(metric.definition)
            definition["groups"][0]["probe"]["address"] = "gSharedDeltaAlias"
            probe = metric.probes[0]
            metrics.append(
                replace(
                    metric,
                    definition=definition,
                    probes=(
                        autoplay_batch.MetricProbe(
                            "gSharedDeltaAlias",
                            probe.address,
                            probe.size,
                        ),
                    ),
                )
            )
        specification = replace(specification, metrics=tuple(metrics))
        baseline_counts = []

        def capture_aliases(*args, **kwargs):
            baseline_counts.append(
                len(kwargs["scheduled_write"].baseline_probes)
            )
            return self._fake_capture(*args, **kwargs)

        with mock.patch.object(
            gba_playtest,
            "build_backend",
            side_effect=self._fake_build_backend,
        ), mock.patch.object(
            gba_playtest,
            "capture",
            side_effect=capture_aliases,
        ):
            report = autoplay_batch.run_batch(
                self.rom,
                scenario,
                specification,
                (1,),
                max_jobs=1,
                max_frames=3,
                max_turns=32,
                max_actions=32,
                work_dir=self.root,
            )
        autoplay_batch.validate_report(report, "alias-report")
        self.assertEqual(baseline_counts, [2])
        left_entry = report["runs"][0]["metrics"]["items"][0]
        right_entry = report["runs"][0]["metrics"]["resources"][0]
        self.assertEqual(
            (
                left_entry["baseline"],
                left_entry["terminal"],
                left_entry["delta"],
            ),
            (
                right_entry["baseline"],
                right_entry["terminal"],
                right_entry["delta"],
            ),
        )

    def test_symbolic_run_until_counter_emits_numeric_report_identity(self):
        data = scenario_data()
        alias = "gPlaySt+0x10"
        data["run_until"]["terminal_conditions"][0]["all"][0][
            "address"
        ] = alias
        data["run_until"]["turn_limit"]["address"] = alias
        data["run_until"]["checkpoint"]["probes"][0]["address"] = alias

        def resolve(symbol):
            self.assertEqual(symbol, "gPlaySt")
            return int(PROBE_TURN, 16) - 0x10, 0x100

        scenario = gba_playtest.parse_scenario_data(data, symbol_resolver=resolve)
        specification = autoplay_batch.load_specification(
            self.specification,
            autoplay_batch.ElfSymbolResolver(self.elf),
        )
        with mock.patch.object(
            gba_playtest,
            "build_backend",
            side_effect=self._fake_build_backend,
        ), mock.patch.object(
            gba_playtest,
            "capture",
            side_effect=self._fake_capture,
        ):
            report = autoplay_batch.run_batch(
                self.rom,
                scenario,
                specification,
                (1,),
                max_jobs=1,
                max_frames=3,
                max_turns=32,
                max_actions=32,
                work_dir=self.root,
            )
        autoplay_batch.validate_report(report, "symbolic-counter-report")
        self.assertEqual(
            report["runs"][0]["terminal"]["turn"]["address"],
            PROBE_TURN,
        )
        self.assertEqual(
            report["provenance"]["scenario"]["definition"]["run_until"][
                "turn_limit"
            ]["address"],
            PROBE_TURN,
        )

    def test_output_must_be_strict_child_of_build_root(self):
        absent_build_root = self.root / "absent-build-root"
        with mock.patch.object(autoplay_batch, "BUILD_ROOT", absent_build_root):
            with self.assertRaisesRegex(
                gba_playtest.PlaytestError,
                "not the build root itself",
            ):
                autoplay_batch._output_path(absent_build_root)
        self.assertFalse(absent_build_root.exists())

        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            "not the build root itself",
        ):
            autoplay_batch._output_path(autoplay_batch.BUILD_ROOT)

        alias = self.root / "build-root-alias"
        alias.symlink_to(autoplay_batch.BUILD_ROOT, target_is_directory=True)
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            "not the build root itself",
        ):
            autoplay_batch._output_path(alias)

        child = self.output("strict-child.json")
        self.assertEqual(autoplay_batch._output_path(child), child.resolve())
        self.assertFalse(child.exists())

    def test_atomic_output_collision_cleanup_and_corrected_retry(self):
        output = self.output("atomic.json")
        temporary = autoplay_batch._temporary_output_path(output)
        temporary.write_text("concurrent reservation", encoding="utf-8")
        stderr = io.StringIO()
        with mock.patch.object(
            gba_playtest,
            "build_backend",
            side_effect=AssertionError("collision reached backend setup"),
        ), redirect_stderr(stderr):
            code = autoplay_batch.main(self.arguments(output))
        self.assertEqual(code, 2)
        self.assertIn("temporary output collision", stderr.getvalue())
        self.assertFalse(output.exists())
        temporary.unlink()

        with mock.patch.object(
            gba_playtest,
            "build_backend",
            side_effect=gba_playtest.PlaytestError("global setup failed"),
        ):
            self.assertEqual(autoplay_batch.main(self.arguments(output)), 2)
        self.assertFalse(output.exists())
        self.assertFalse(temporary.exists())

        self.assertEqual(self._run_fake(self.arguments(output))[0], 1)
        self.assertTrue(output.is_file())
        self.assertFalse(temporary.exists())

        serialization_output = self.output("serialization.json")
        serialization_temporary = autoplay_batch._temporary_output_path(
            serialization_output
        )
        with mock.patch.object(
            gba_playtest,
            "build_backend",
            side_effect=self._fake_build_backend,
        ), mock.patch.object(
            gba_playtest,
            "capture",
            side_effect=self._fake_capture,
        ), mock.patch.object(
            autoplay_batch,
            "serialize_report",
            side_effect=gba_playtest.PlaytestError("serialization failed"),
        ):
            self.assertEqual(
                autoplay_batch.main(self.arguments(serialization_output)),
                2,
            )
        self.assertFalse(serialization_output.exists())
        self.assertFalse(serialization_temporary.exists())

        race_output = self.output("destination-race.json")
        race_temporary = autoplay_batch._temporary_output_path(race_output)
        serialize_report = autoplay_batch.serialize_report

        def create_competing_destination(report):
            race_output.write_bytes(b"competing creator")
            return serialize_report(report)

        stderr = io.StringIO()
        with mock.patch.object(
            gba_playtest,
            "build_backend",
            side_effect=self._fake_build_backend,
        ), mock.patch.object(
            gba_playtest,
            "capture",
            side_effect=self._fake_capture,
        ), mock.patch.object(
            autoplay_batch,
            "serialize_report",
            side_effect=create_competing_destination,
        ), redirect_stderr(stderr):
            self.assertEqual(autoplay_batch.main(self.arguments(race_output)), 2)
        self.assertIn("will not be overwritten", stderr.getvalue())
        self.assertEqual(race_output.read_bytes(), b"competing creator")
        self.assertFalse(race_temporary.exists())

    def test_comparison_reports_metric_changes_and_never_rewrites_inputs(self):
        baseline = self.output("baseline.json")
        candidate = self.output("candidate.json")
        self.assertEqual(self._run_fake(self.arguments(baseline))[0], 1)
        candidate_data = json.loads(baseline.read_text(encoding="utf-8"))
        candidate_data["runs"][0]["metrics"]["turns"] = 9
        candidate_data["runs"][0]["terminal"]["turn"]["value"] = "0x00000009"
        metric_definitions = {
            metric["id"]: metric
            for metric in candidate_data["provenance"]["specification"]["definition"][
                "metrics"
            ]
        }
        candidate_data["summary"]["metric_distributions"] = (
            autoplay_batch._metric_distributions(
                candidate_data["runs"],
                metric_definitions,
            )
        )
        candidate.write_text(autoplay_batch.serialize_report(candidate_data), encoding="utf-8")
        before = baseline.read_bytes()
        comparison = self.output("comparison.json")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = autoplay_batch.main(
                [
                    "compare",
                    "--baseline",
                    str(baseline),
                    "--candidate",
                    str(candidate),
                    "--output",
                    str(comparison),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(before, baseline.read_bytes())
        data = json.loads(comparison.read_text(encoding="utf-8"))
        self.assertIn("does not infer statistical significance", data["notice"])
        change = data["comparison"]["changed_runs"][0]
        self.assertEqual(change["seed"], 1)
        self.assertEqual(change["changes"]["metrics"][0]["id"], "turns")


class AutoplayBatchLibmGBAIntegrationTests(BatchFixtureTestCase):
    def _run_or_skip(self, arguments: list[str]) -> int:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = autoplay_batch.main(arguments)
        if code == 2:
            unavailable = (
                "C compiler ",
                "mgba/core/core.h: No such file",
                "'mgba/core/core.h' file not found",
                "cannot find -lmgba",
                "library not found for -lmgba",
            )
            if any(marker in stderr.getvalue() for marker in unavailable):
                self.skipTest(f"libmGBA integration skipped explicitly: {stderr.getvalue()}")
        self.assertEqual(code, 0, stderr.getvalue())
        return code

    def test_three_seed_clean_boot_fixture_is_serial_parallel_identical(self):
        serial = self.output("runtime-serial.json")
        parallel = self.output("runtime-parallel.json")
        self._run_or_skip(self.arguments(serial, jobs=1))
        self._run_or_skip(self.arguments(parallel, jobs=3))
        self.assertEqual(serial.read_bytes(), parallel.read_bytes())
        report = autoplay_batch.validate_report(
            json.loads(serial.read_text(encoding="utf-8")), str(serial)
        )
        self.assertEqual(report["summary"]["success_count"], 3)
        self.assertEqual(report["summary"]["failure_count"], 0)
        for seed, run in zip((1, 2, 3), report["runs"]):
            self.assertEqual(run["seed"], seed)
            self.assertEqual(run["terminal"]["reason"], "success")
            self.assertEqual(run["metrics"]["turns"], seed)
            self.assertEqual(run["metrics"]["actions"], seed)
            for metric_id in ("exp", "items", "resources"):
                self.assertEqual(
                    run["metrics"][metric_id][0],
                    {
                        "baseline": 0,
                        "delta": seed,
                        "group": "blue",
                        "kind": (
                            "item"
                            if metric_id == "items"
                            else "resource"
                            if metric_id == "resources"
                            else "exp"
                        ),
                        "terminal": seed,
                    },
                )


if __name__ == "__main__":
    unittest.main()
