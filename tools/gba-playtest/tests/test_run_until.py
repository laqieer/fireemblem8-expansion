"""Issue #86 bounded semantic run-until scenarios."""

from __future__ import annotations

import copy
import json
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
import run_autoplay_bounds_checks as autoplay_bounds


PROBE_ADDRESS = "0x02000000"
RELEASED = 0x000003FF
A_HELD = 0x000003FE
ROOT = Path(__file__).resolve().parents[3]
FINGERPRINTS = ROOT / "tools" / "gba-playtest" / "fingerprints"


def comparison(value: int, operator: str = "eq") -> dict:
    return {
        "address": PROBE_ADDRESS,
        "size": 4,
        "operator": operator,
        "value": f"0x{value:08x}",
    }


def run_until_data(
    *,
    success_value: int = A_HELD,
    terminal_reason: str | None = None,
    terminal_value: int = RELEASED,
    max_frames: int = 5,
    stall: bool = False,
    turn_limit: int | None = None,
    action_limit: int | None = None,
) -> dict:
    terminal_conditions = [
        {
            "reason": "success",
            "all": [comparison(success_value)],
        }
    ]
    if terminal_reason is not None:
        terminal_conditions.append(
            {
                "reason": terminal_reason,
                "all": [comparison(terminal_value)],
            }
        )
    run_until = {
        "max_frames": max_frames,
        "terminal_conditions": terminal_conditions,
        "checkpoint": {
            "name": "terminal",
            "framebuffer": False,
            "probes": [{"address": PROBE_ADDRESS, "size": 4}],
        },
    }
    if stall:
        run_until["stall"] = {
            "max_unchanged_frames": 1,
            "progress": {"address": PROBE_ADDRESS, "size": 4},
            "work_expected": comparison(RELEASED),
        }
    if turn_limit is not None:
        run_until["turn_limit"] = {
            "maximum": turn_limit,
            "address": PROBE_ADDRESS,
            "size": 4,
        }
    if action_limit is not None:
        run_until["action_limit"] = {
            "maximum": action_limit,
            "address": PROBE_ADDRESS,
            "size": 4,
        }
    return {
        "schema_version": 2,
        "name": "homebrew-run-until",
        "description": "Bounded semantic terminal-reason fixture.",
        "frames": (
            [{"start": 2, "end": 2, "keys": ["A"]}]
            if success_value == A_HELD and terminal_reason is None and not stall
            else []
        ),
        "run_until": run_until,
    }


def fingerprint_data() -> dict:
    return {
        "format_version": 3,
        "scenario": "homebrew-run-until",
        "rom": {
            "sha1": "0" * 40,
            "size": 0x400,
            "title": "GPTFIXTURE",
            "game_code": "GPT0",
        },
        "terminal": {
            "reason": "success",
            "frame": 2,
            "turn": None,
            "actions": None,
        },
        "checkpoints": [
            {
                "frame": 2,
                "name": "terminal",
                "probes": [
                    {
                        "address": PROBE_ADDRESS,
                        "size": 4,
                        "value": "0x000003fe",
                    }
                ],
            }
        ],
    }


class RunUntilSchemaTests(unittest.TestCase):
    def test_schema_v1_remains_fixed_and_schema_v2_is_typed(self):
        fixed = gba_playtest.parse_scenario_data(
            {
                "schema_version": 1,
                "name": "fixed",
                "frames": [],
                "checkpoints": [
                    {
                        "name": "fixed",
                        "frame": 2,
                        "framebuffer": False,
                        "probes": [{"address": PROBE_ADDRESS, "size": 4}],
                    }
                ],
            }
        )
        self.assertEqual(fixed.schema_version, 1)
        self.assertIsNone(fixed.run_until)

        bounded = gba_playtest.parse_scenario_data(run_until_data())
        self.assertEqual(bounded.schema_version, 2)
        self.assertEqual(bounded.run_until.max_frames, 5)
        self.assertEqual(bounded.checkpoints[0].frame, 4)
        self.assertEqual(
            bounded.run_until.terminal_conditions[0].reason,
            "success",
        )

    def test_missing_or_impossible_bounds_fail_closed(self):
        missing = run_until_data()
        del missing["run_until"]["max_frames"]
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "missing.*max_frames"):
            gba_playtest.parse_scenario_data(missing)

        zero = run_until_data()
        zero["run_until"]["max_frames"] = 0
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "max_frames"):
            gba_playtest.parse_scenario_data(zero)

        non_integer_version = run_until_data()
        non_integer_version["schema_version"] = 2.0
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "must be integer"):
            gba_playtest.parse_scenario_data(non_integer_version)

        stall = run_until_data(stall=True)
        stall["run_until"]["stall"]["max_unchanged_frames"] = 5
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, "strictly below max_frames"
        ):
            gba_playtest.parse_scenario_data(stall)

        counter = run_until_data(turn_limit=1)
        counter["run_until"]["turn_limit"]["maximum"] = 0
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "maximum"):
            gba_playtest.parse_scenario_data(counter)

    def test_malformed_duplicate_and_unknown_conditions_fail_closed(self):
        unsupported = run_until_data()
        unsupported["run_until"]["terminal_conditions"][0]["all"][0][
            "operator"
        ] = "contains"
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "operator must be"):
            gba_playtest.parse_scenario_data(unsupported)

        unknown = run_until_data()
        unknown["run_until"]["terminal_conditions"][0]["reason"] = "softlock"
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "reason must be"):
            gba_playtest.parse_scenario_data(unknown)

        duplicate = run_until_data()
        predicate = duplicate["run_until"]["terminal_conditions"][0]["all"][0]
        duplicate["run_until"]["terminal_conditions"][0]["all"].append(
            dict(predicate)
        )
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "duplicate condition"):
            gba_playtest.parse_scenario_data(duplicate)

        duplicate_reason = run_until_data(terminal_reason="objective_failure")
        duplicate_reason["run_until"]["terminal_conditions"][1][
            "reason"
        ] = "success"
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "duplicates terminal"):
            gba_playtest.parse_scenario_data(duplicate_reason)

    def test_contradictory_and_overlapping_terminals_fail_closed(self):
        contradictory = run_until_data()
        contradictory["run_until"]["terminal_conditions"][0]["all"].append(
            comparison(A_HELD + 1)
        )
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, "internally contradictory"
        ):
            gba_playtest.parse_scenario_data(contradictory)

        overlapping = run_until_data(
            terminal_reason="objective_failure",
            terminal_value=RELEASED,
        )
        overlapping["run_until"]["terminal_conditions"][1]["all"][0][
            "operator"
        ] = "ne"
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "overlap"):
            gba_playtest.parse_scenario_data(overlapping)

    def test_success_precluded_by_counter_bound_is_rejected(self):
        data = run_until_data(success_value=0x11, turn_limit=0x10)
        data["run_until"]["terminal_conditions"][0]["all"][0]["operator"] = "ge"
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            "success condition cannot occur within.*bounds",
        ):
            gba_playtest.parse_scenario_data(data)

    def test_unbound_and_aliasing_symbols_fail_before_backend_execution(self):
        unbound = run_until_data()
        unbound["run_until"]["terminal_conditions"][0]["all"][0][
            "address"
        ] = "gSemanticState"
        scenario = gba_playtest.parse_scenario_data(unbound)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                gba_playtest.PlaytestError, "no resolved execution address"
            ):
                gba_playtest._write_plan(Path(temporary) / "plan", scenario)

        aliasing = run_until_data(stall=True)
        aliasing["run_until"]["terminal_conditions"][0]["all"][0][
            "address"
        ] = "gSuccess"
        aliasing["run_until"]["stall"]["progress"]["address"] = "gEpochAlias"
        aliasing["run_until"]["stall"]["work_expected"]["address"] = "gSuccess"

        def resolver(_symbol):
            return (0x02000000, 4)

        with self.assertRaisesRegex(
            gba_playtest.PlaytestError, "overlapping resolved byte spans"
        ):
            gba_playtest.parse_scenario_data(
                aliasing, symbol_resolver=resolver
            )

    def test_overlapping_resolved_probe_spans_fail_closed(self):
        same_start = run_until_data()
        first = same_start["run_until"]["terminal_conditions"][0]["all"][0]
        first["size"] = 1
        first["value"] = "0x00"
        same_start["run_until"]["terminal_conditions"][0]["all"].append(
            {
                "address": PROBE_ADDRESS,
                "size": 2,
                "operator": "eq",
                "value": "0xffff",
            }
        )
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            r"'0x02000000'/2 span \[0x02000000, 0x02000002\) overlaps "
            r"'0x02000000'/1 span \[0x02000000, 0x02000001\)",
        ):
            gba_playtest.parse_scenario_data(same_start)

        partial = run_until_data()
        partial["run_until"]["terminal_conditions"][0]["all"].append(
            {
                "address": "0x02000002",
                "size": 2,
                "operator": "eq",
                "value": "0x0000",
            }
        )
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            r"'0x02000002'/2 span \[0x02000002, 0x02000004\) overlaps "
            r"'0x02000000'/4 span \[0x02000000, 0x02000004\)",
        ):
            gba_playtest.parse_scenario_data(partial)

    def test_adjacent_resolved_probe_spans_remain_valid(self):
        data = run_until_data()
        data["run_until"]["terminal_conditions"][0]["all"].append(
            {
                "address": "0x02000004",
                "size": 2,
                "operator": "eq",
                "value": "0x0000",
            }
        )
        scenario = gba_playtest.parse_scenario_data(data)
        self.assertEqual(
            [
                (comparison.probe.address, comparison.probe.size)
                for comparison in scenario.run_until.terminal_conditions[
                    0
                ].comparisons
            ],
            [(0x02000000, 4), (0x02000004, 2)],
        )

    def test_plan_versions_preserve_fixed_mode(self):
        fixed = gba_playtest.parse_scenario_data(
            {
                "schema_version": 1,
                "name": "fixed",
                "frames": [],
                "checkpoints": [
                    {
                        "name": "fixed",
                        "frame": 1,
                        "framebuffer": False,
                        "probes": [{"address": PROBE_ADDRESS, "size": 4}],
                    }
                ],
            }
        )
        bounded = gba_playtest.parse_scenario_data(run_until_data())
        plan_root = ROOT / "build" / "test-artifacts" / "run-until-plans"
        plan_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=plan_root) as temporary:
            fixed_plan = Path(temporary) / "fixed.plan"
            bounded_plan = Path(temporary) / "bounded.plan"
            scheduled_plan = Path(temporary) / "scheduled.plan"
            gba_playtest._write_plan(fixed_plan, fixed)
            gba_playtest._write_plan(bounded_plan, bounded)
            self.assertTrue(
                fixed_plan.read_text(encoding="ascii").startswith(
                    "GBA_PLAYTEST_PLAN 3\n"
                )
            )
            bounded_text = bounded_plan.read_text(encoding="ascii")
            self.assertTrue(bounded_text.startswith("GBA_PLAYTEST_PLAN 4\n"))
            self.assertIn("\nRUN_UNTIL 5\n", bounded_text)
            scheduled_write = gba_playtest.ScheduledWrite(
                0,
                gba_playtest.Probe(
                    PROBE_ADDRESS,
                    int(PROBE_ADDRESS, 16),
                    4,
                    None,
                ),
                1,
            )
            gba_playtest._write_plan(
                scheduled_plan,
                bounded,
                scheduled_write,
            )
            scheduled_text = scheduled_plan.read_text(encoding="ascii")
            self.assertTrue(scheduled_text.startswith("GBA_PLAYTEST_PLAN 7\n"))
            self.assertIn("\nRUN_UNTIL 5\n", scheduled_text)
            self.assertIn("\nBASELINE_PROBES 0\n", scheduled_text)
            self.assertIn(
                f"\nSEED_WRITE 0 {int(PROBE_ADDRESS, 16)} 4 1\n",
                scheduled_text,
            )
            with self.assertRaisesRegex(
                gba_playtest.PlaytestError,
                "scheduled writes require a bounded run-until scenario",
            ):
                gba_playtest._write_plan(
                    Path(temporary) / "invalid-fixed.plan",
                    fixed,
                    scheduled_write,
                )
            self.assertFalse((Path(temporary) / "invalid-fixed.plan").exists())

    def test_scheduled_write_acknowledgement_is_exact_and_precedes_terminal(self):
        scenario = gba_playtest.parse_scenario_data(run_until_data())
        scheduled_write = gba_playtest.ScheduledWrite(
            0,
            gba_playtest.Probe(
                PROBE_ADDRESS,
                int(PROBE_ADDRESS, 16),
                4,
                None,
            ),
            1,
        )
        checkpoint = (
            "TERMINAL\tsuccess\t2\t0\t0\t0\t0\n"
            "CHECKPOINT\t0\t2\t0000000000000000\n"
            "PROBE\t0\t0\t1022\n"
        )
        acknowledgement = (
            f"SEED_WRITE_APPLIED\t0\t{int(PROBE_ADDRESS, 16)}\t4\t1\n"
        )
        captured = gba_playtest._parse_backend_output(
            acknowledgement + checkpoint,
            scenario,
            scheduled_write,
        )
        self.assertEqual(
            captured["scheduled_write"],
            {
                "address": PROBE_ADDRESS,
                "frame": 0,
                "size": 4,
                "value": 1,
            },
        )

        cases = (
            (
                "early-terminal",
                checkpoint,
                "terminal record preceded scheduled write acknowledgement",
            ),
            (
                "duplicate",
                acknowledgement + acknowledgement + checkpoint,
                "duplicate scheduled write acknowledgement",
            ),
            (
                "mismatched",
                (
                    f"SEED_WRITE_APPLIED\t0\t{int(PROBE_ADDRESS, 16)}\t4\t2\n"
                    + checkpoint
                ),
                "does not match the request",
            ),
        )
        for name, output, expected in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(gba_playtest.PlaytestError, expected):
                    gba_playtest._parse_backend_output(
                        output,
                        scenario,
                        scheduled_write,
                    )

        baseline_write = gba_playtest.ScheduledWrite(
            scheduled_write.frame,
            scheduled_write.probe,
            scheduled_write.value,
            (scheduled_write.probe,),
        )
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            "preceded baseline probes",
        ):
            gba_playtest._parse_backend_output(
                acknowledgement
                + "BASELINE\t0\t0\n"
                + checkpoint,
                scenario,
                baseline_write,
            )

        late_write = gba_playtest.ScheduledWrite(
            2,
            scheduled_write.probe,
            scheduled_write.value,
        )
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            "terminal record preceded scheduled write acknowledgement",
        ):
            gba_playtest._parse_backend_output(
                (
                    "TERMINAL\tsuccess\t0\t0\t0\t0\t0\n"
                    "CHECKPOINT\t0\t0\t0000000000000000\n"
                    "PROBE\t0\t0\t1022\n"
                ),
                scenario,
                late_write,
            )

    def test_capture_rejects_fixed_scheduled_write_before_backend_start(self):
        fixed = gba_playtest.parse_scenario_data(
            {
                "schema_version": 1,
                "name": "fixed",
                "frames": [],
                "checkpoints": [
                    {
                        "name": "fixed",
                        "frame": 1,
                        "framebuffer": False,
                        "probes": [{"address": PROBE_ADDRESS, "size": 4}],
                    }
                ],
            }
        )
        scheduled_write = gba_playtest.ScheduledWrite(
            0,
            gba_playtest.Probe(
                PROBE_ADDRESS,
                int(PROBE_ADDRESS, 16),
                4,
                None,
            ),
            1,
        )
        with mock.patch.object(
            gba_playtest,
            "build_backend",
            side_effect=AssertionError("fixed scheduled write reached backend"),
        ), self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            "scheduled writes require a bounded run-until scenario",
        ):
            gba_playtest.capture(
                ROOT / "build" / "not-opened.gba",
                fixed,
                scheduled_write=scheduled_write,
            )

    def test_scheduled_write_dataclass_is_validated_before_backend_start(self):
        bounded = gba_playtest.parse_scenario_data(run_until_data())
        for size in (1, 2, 4):
            maximum = (1 << (size * 8)) - 1
            for value in (0, maximum):
                gba_playtest.validate_scheduled_write(
                    bounded,
                    gba_playtest.ScheduledWrite(
                        0,
                        gba_playtest.Probe(
                            PROBE_ADDRESS,
                            int(PROBE_ADDRESS, 16),
                            size,
                            None,
                        ),
                        value,
                    ),
                )

        cases = (
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, int(PROBE_ADDRESS, 16), True, None),
                    0,
                ),
                "size must be integer 1, 2, or 4",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, int(PROBE_ADDRESS, 16), 3, None),
                    0,
                ),
                "size must be integer 1, 2, or 4",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, None, 4, None),
                    0,
                ),
                "address must be a resolved integer",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, True, 1, None),
                    0,
                ),
                "address must be a resolved integer",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, 0x02000001, 2, None),
                    0,
                ),
                "aligned to size 2",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, 0x0203FFFF, 2, None),
                    0,
                ),
                "aligned to size 2",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, 0x08000000, 4, None),
                    0,
                ),
                "writable EWRAM or IWRAM",
            ),
            (
                gba_playtest.ScheduledWrite(
                    False,
                    gba_playtest.Probe(PROBE_ADDRESS, int(PROBE_ADDRESS, 16), 4, None),
                    0,
                ),
                "frame must be an integer",
            ),
            (
                gba_playtest.ScheduledWrite(
                    -1,
                    gba_playtest.Probe(PROBE_ADDRESS, int(PROBE_ADDRESS, 16), 4, None),
                    0,
                ),
                "frame must be an integer",
            ),
            (
                gba_playtest.ScheduledWrite(
                    5,
                    gba_playtest.Probe(PROBE_ADDRESS, int(PROBE_ADDRESS, 16), 4, None),
                    0,
                ),
                "frame must be an integer",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, int(PROBE_ADDRESS, 16), 1, None),
                    True,
                ),
                "value must be an integer",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, int(PROBE_ADDRESS, 16), 1, None),
                    -1,
                ),
                "value must be an integer",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(PROBE_ADDRESS, int(PROBE_ADDRESS, 16), 1, None),
                    0x100,
                ),
                "value must be an integer",
            ),
            (
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(
                        PROBE_ADDRESS,
                        int(PROBE_ADDRESS, 16),
                        4,
                        None,
                    ),
                    1,
                    (
                        gba_playtest.Probe(
                            "gAlias",
                            int(PROBE_ADDRESS, 16),
                            4,
                            None,
                        ),
                        gba_playtest.Probe(
                            PROBE_ADDRESS,
                            int(PROBE_ADDRESS, 16),
                            4,
                            None,
                        ),
                    ),
                ),
                "duplicate baseline probe",
            ),
        )
        for scheduled_write, expected in cases:
            with self.subTest(expected=expected, scheduled_write=scheduled_write):
                with mock.patch.object(
                    gba_playtest,
                    "build_backend",
                    side_effect=AssertionError(
                        "invalid scheduled write reached backend"
                    ),
                ), self.assertRaisesRegex(gba_playtest.PlaytestError, expected):
                    gba_playtest.capture(
                        ROOT / "build" / "not-opened.gba",
                        bounded,
                        scheduled_write=scheduled_write,
                    )

    def test_capture_preserves_preexisting_positional_parameter_order(self):
        fixed = gba_playtest.parse_scenario_data(
            {
                "schema_version": 1,
                "name": "fixed-positional",
                "frames": [],
                "checkpoints": [
                    {
                        "name": "fixed",
                        "frame": 1,
                        "framebuffer": False,
                        "probes": [{"address": PROBE_ADDRESS, "size": 4}],
                    }
                ],
            }
        )
        work_root = ROOT / "build" / "test-artifacts" / "capture-positional"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work_root) as temporary:
            temporary_path = Path(temporary)
            rom = temporary_path / "fixture.gba"
            backend = temporary_path / "prebuilt-backend"
            build_homebrew_rom(rom)
            backend.write_bytes(b"prebuilt")
            completed = mock.Mock(
                returncode=0,
                stderr="",
                stdout=(
                    "CHECKPOINT\t0\t1\t0000000000000000\n"
                    "PROBE\t0\t0\t0\n"
                ),
            )
            with mock.patch.object(
                gba_playtest,
                "_run_transient_retryable",
                return_value=completed,
            ), mock.patch.object(
                gba_playtest.tempfile,
                "tempdir",
                str(temporary_path),
            ):
                fingerprint = gba_playtest.capture(
                    rom,
                    fixed,
                    None,
                    0,
                    backend,
                    None,
                )
        self.assertEqual(fingerprint["scenario"], "fixed-positional")


class RunUntilFingerprintTests(unittest.TestCase):
    def test_format_three_validates_terminal_and_checkpoint(self):
        data = fingerprint_data()
        self.assertEqual(
            gba_playtest.validate_fingerprint(data, "<fingerprint>"),
            data,
        )

        wrong_frame = copy.deepcopy(data)
        wrong_frame["terminal"]["frame"] = 1
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "must equal"):
            gba_playtest.validate_fingerprint(wrong_frame, "<fingerprint>")

        unknown = copy.deepcopy(data)
        unknown["terminal"]["reason"] = "unknown"
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "reason must be"):
            gba_playtest.validate_fingerprint(unknown, "<fingerprint>")

    def test_behavior_policy_compares_typed_terminal_outcome(self):
        expected = fingerprint_data()
        actual = copy.deepcopy(expected)
        actual["terminal"]["reason"] = "max_frames"
        differences = gba_playtest.compare_fingerprints(
            expected, actual, policy="behavior"
        )
        self.assertTrue(
            any(difference.startswith("terminal.reason:") for difference in differences)
        )

    def test_budget_reasons_require_only_their_corresponding_counter(self):
        missing_turn = fingerprint_data()
        missing_turn["terminal"]["reason"] = "max_turns"
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            r"\.terminal\.turn must be non-null when reason is 'max_turns'",
        ):
            gba_playtest.validate_fingerprint(missing_turn, "<fingerprint>")

        missing_actions = fingerprint_data()
        missing_actions["terminal"]["reason"] = "max_actions"
        with self.assertRaisesRegex(
            gba_playtest.PlaytestError,
            r"\.terminal\.actions must be non-null when reason is 'max_actions'",
        ):
            gba_playtest.validate_fingerprint(missing_actions, "<fingerprint>")

        max_turns = fingerprint_data()
        max_turns["terminal"]["reason"] = "max_turns"
        max_turns["terminal"]["turn"] = {
            "address": PROBE_ADDRESS,
            "size": 4,
            "value": "0x00000003",
        }
        validated = gba_playtest.validate_fingerprint(
            max_turns, "<fingerprint>"
        )
        self.assertIsNone(validated["terminal"]["actions"])

        max_actions = fingerprint_data()
        max_actions["terminal"]["reason"] = "max_actions"
        max_actions["terminal"]["actions"] = {
            "address": PROBE_ADDRESS,
            "size": 4,
            "value": "0x00000006",
        }
        validated = gba_playtest.validate_fingerprint(
            max_actions, "<fingerprint>"
        )
        self.assertIsNone(validated["terminal"]["turn"])

    def test_backend_output_rejects_impossible_terminal_outcomes(self):
        def output(
            reason,
            frame,
            turn_present=0,
            turn_value=0,
            action_present=0,
            action_value=0,
        ):
            return (
                f"TERMINAL\t{reason}\t{frame}\t{turn_present}\t{turn_value}"
                f"\t{action_present}\t{action_value}\n"
                f"CHECKPOINT\t0\t{frame}\t0000000000000000\n"
                "PROBE\t0\t0\t0\n"
            )

        cases = (
            (run_until_data(), output("objective_failure", 0), "not declared"),
            (run_until_data(), output("engine_stall", 0), "not configured"),
            (run_until_data(), output("max_frames", 0), "final bounded frame"),
            (run_until_data(), output("max_turns", 0), "not configured"),
            (run_until_data(), output("max_actions", 0), "not configured"),
            (
                run_until_data(success_value=1, turn_limit=2),
                output("max_turns", 0, turn_present=1, turn_value=1),
                "configured limit",
            ),
            (
                run_until_data(success_value=1, action_limit=2),
                output("max_actions", 0, action_present=1, action_value=1),
                "configured limit",
            ),
        )
        for data, backend_output, error in cases:
            with self.subTest(error=error):
                scenario = gba_playtest.parse_scenario_data(data)
                with self.assertRaisesRegex(gba_playtest.PlaytestError, error):
                    gba_playtest._parse_backend_output(backend_output, scenario)

    def test_backend_output_preserves_symbolic_counter_bindings(self):
        data = run_until_data(turn_limit=3, action_limit=4)
        data["run_until"]["turn_limit"]["address"] = "gTurnCounter"
        data["run_until"]["action_limit"]["address"] = "gActionCounter"
        addresses = {
            "gTurnCounter": 0x02000004,
            "gActionCounter": 0x02000008,
        }

        def resolve(symbol):
            return addresses[symbol], 4

        scenario = gba_playtest.parse_scenario_data(
            data,
            symbol_resolver=resolve,
        )
        captured = gba_playtest._parse_backend_output(
            "TERMINAL\tmax_frames\t4\t1\t1\t1\t2\n"
            "CHECKPOINT\t0\t4\t0000000000000000\n"
            "PROBE\t0\t0\t1023\n",
            scenario,
        )
        self.assertEqual(
            captured["terminal"]["turn"]["address"],
            "gTurnCounter",
        )
        self.assertEqual(
            captured["terminal"]["actions"]["address"],
            "gActionCounter",
        )


class AutoplayBoundsEvidenceTests(unittest.TestCase):
    def test_checked_real_rom_evidence_satisfies_semantic_contract(self):
        cases = (
            (
                "autoplay-bounded-computer-modern-debug",
                autoplay_bounds._check_positive,
                (),
            ),
            (
                "autoplay-bounded-player-default-modern-debug",
                autoplay_bounds._check_default,
                ("debug",),
            ),
            (
                "autoplay-bounded-player-default-modern-release",
                autoplay_bounds._check_default,
                ("release",),
            ),
        )
        for name, semantic_check, args in cases:
            with self.subTest(name=name):
                path = FINGERPRINTS / f"{name}.json"
                self.assertTrue(path.is_file(), f"missing runtime fingerprint {path}")
                fingerprint = gba_playtest.validate_fingerprint(
                    json.loads(path.read_text(encoding="utf-8")),
                    str(path),
                    policy="behavior",
                )
                self.assertEqual(semantic_check(fingerprint, *args), [])

    def test_real_scenarios_use_bounded_semantic_probes(self):
        positive = gba_playtest.parse_scenario_data(
            autoplay_bounds._positive_data()
        )
        self.assertEqual(positive.schema_version, 2)
        self.assertEqual(positive.run_until.max_frames, 18001)
        self.assertEqual(positive.run_until.turn_limit.maximum, 3)
        self.assertEqual(positive.run_until.action_limit.maximum, 62)
        self.assertEqual(
            positive.run_until.stall.progress.binding,
            "gExpansionAutoplayTelemetry+0x18",
        )
        self.assertEqual(
            [condition.reason for condition in positive.run_until.terminal_conditions],
            ["success", "objective_failure"],
        )
        for config in ("debug", "release"):
            negative = gba_playtest.parse_scenario_data(
                autoplay_bounds._negative_data(config)
            )
            self.assertEqual(negative.schema_version, 2)
            self.assertEqual(negative.run_until.max_frames, 3951)


class RunUntilBackendIntegrationTests(unittest.TestCase):
    def _build_backend_or_skip(self, backend: Path) -> None:
        try:
            gba_playtest.build_backend(backend)
        except gba_playtest.PlaytestError as exc:
            unavailable_markers = (
                "C compiler ",
                "mgba/core/core.h: No such file",
                "'mgba/core/core.h' file not found",
                "cannot find -lmgba",
                "library not found for -lmgba",
            )
            if any(marker in str(exc) for marker in unavailable_markers):
                raise unittest.SkipTest(
                    f"libmGBA integration skipped explicitly: {exc}"
                ) from exc
            raise

    def _capture_or_skip(self, rom: Path, scenario: gba_playtest.Scenario, **kwargs):
        try:
            return gba_playtest.capture(rom, scenario, **kwargs)
        except gba_playtest.PlaytestError as exc:
            unavailable_markers = (
                "C compiler ",
                "mgba/core/core.h: No such file",
                "'mgba/core/core.h' file not found",
                "cannot find -lmgba",
                "library not found for -lmgba",
            )
            if any(marker in str(exc) for marker in unavailable_markers):
                raise unittest.SkipTest(
                    f"libmGBA integration skipped explicitly: {exc}"
                ) from exc
            raise

    def test_backend_rejects_unaligned_format_6_and_7_seed_writes(self):
        scenario = gba_playtest.parse_scenario_data(run_until_data())
        with tempfile.TemporaryDirectory(prefix="gba-run-until-plan-") as temporary:
            root = Path(temporary)
            backend = root / "gba-playtest-backend"
            valid_plan = root / "valid.plan"
            missing_rom = root / "missing.gba"
            self._build_backend_or_skip(backend)
            gba_playtest._write_plan(
                valid_plan,
                scenario,
                gba_playtest.ScheduledWrite(
                    0,
                    gba_playtest.Probe(
                        PROBE_ADDRESS,
                        int(PROBE_ADDRESS, 16),
                        4,
                        None,
                    ),
                    1,
                ),
            )
            valid_text = valid_plan.read_text(encoding="ascii")

            for version in (6, 7):
                for size, address in ((2, 0x02000001), (4, 0x02000002)):
                    with self.subTest(version=version, size=size, address=address):
                        plan = root / f"invalid-{version}-{size}.plan"
                        text = valid_text.replace(
                            "GBA_PLAYTEST_PLAN 7",
                            f"GBA_PLAYTEST_PLAN {version}",
                            1,
                        ).replace(
                            f"SEED_WRITE 0 {int(PROBE_ADDRESS, 16)} 4 1",
                            f"SEED_WRITE 0 {address} {size} 1",
                            1,
                        )
                        plan.write_text(text, encoding="ascii")
                        result = subprocess.run(
                            [str(backend), str(missing_rom), str(plan)],
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(result.returncode, 2)
                        self.assertIn("malformed SEED_WRITE record", result.stderr)

                aligned_plan = root / f"aligned-{version}.plan"
                aligned_plan.write_text(
                    valid_text.replace(
                        "GBA_PLAYTEST_PLAN 7",
                        f"GBA_PLAYTEST_PLAN {version}",
                        1,
                    ),
                    encoding="ascii",
                )
                result = subprocess.run(
                    [str(backend), str(missing_rom), str(aligned_plan)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("malformed SEED_WRITE record", result.stderr)

    def test_generated_homebrew_covers_every_terminal_reason_once(self):
        cases = (
            ("success", run_until_data(), 2),
            (
                "objective_failure",
                run_until_data(
                    terminal_reason="objective_failure",
                    stall=True,
                ),
                0,
            ),
            (
                "controller_exhausted",
                run_until_data(terminal_reason="controller_exhausted"),
                0,
            ),
            (
                "engine_stall",
                run_until_data(
                    success_value=0,
                    max_frames=3,
                    stall=True,
                ),
                1,
            ),
            (
                "max_frames",
                run_until_data(success_value=0, max_frames=3),
                2,
            ),
            (
                "max_turns",
                run_until_data(
                    success_value=0,
                    max_frames=3,
                    turn_limit=RELEASED,
                ),
                0,
            ),
            (
                "max_actions",
                run_until_data(
                    success_value=0,
                    max_frames=3,
                    action_limit=RELEASED,
                ),
                0,
            ),
        )
        with tempfile.TemporaryDirectory(prefix="gba-run-until-test-") as temporary:
            rom = Path(temporary) / "fixture.gba"
            build_homebrew_rom(rom)
            for expected_reason, data, expected_frame in cases:
                with self.subTest(reason=expected_reason):
                    scenario = gba_playtest.parse_scenario_data(data)
                    fingerprint = self._capture_or_skip(rom, scenario)
                    self.assertEqual(fingerprint["format_version"], 3)
                    self.assertEqual(
                        fingerprint["terminal"]["reason"], expected_reason
                    )
                    self.assertEqual(
                        fingerprint["terminal"]["frame"], expected_frame
                    )
                    self.assertEqual(len(fingerprint["checkpoints"]), 1)
                    self.assertEqual(
                        fingerprint["checkpoints"][0]["frame"],
                        expected_frame,
                    )
                    gba_playtest.validate_fingerprint(
                        fingerprint, "<captured>"
                    )
                    if expected_reason == "max_turns":
                        self.assertEqual(
                            fingerprint["terminal"]["turn"]["value"],
                            "0x000003ff",
                        )
                    if expected_reason == "max_actions":
                        self.assertEqual(
                            fingerprint["terminal"]["actions"]["value"],
                            "0x000003ff",
                        )

    def test_semantic_failure_is_not_retried(self):
        scenario = gba_playtest.parse_scenario_data(
            run_until_data(terminal_reason="objective_failure")
        )
        with tempfile.TemporaryDirectory(prefix="gba-run-until-retry-") as temporary:
            rom = Path(temporary) / "fixture.gba"
            build_homebrew_rom(rom)
            original = gba_playtest._run_transient_retryable
            backend_attempts = 0

            def observed(command, *, timeout, retries, operation):
                nonlocal backend_attempts
                if operation == "libmGBA backend":
                    backend_attempts += 1
                return original(
                    command,
                    timeout=timeout,
                    retries=retries,
                    operation=operation,
                )

            with mock.patch.object(
                gba_playtest,
                "_run_transient_retryable",
                side_effect=observed,
            ):
                fingerprint = self._capture_or_skip(
                    rom, scenario, retries=gba_playtest.MAX_RETRIES_CAP
                )
            self.assertEqual(
                fingerprint["terminal"]["reason"],
                "objective_failure",
            )
            self.assertEqual(backend_attempts, 1)

    def test_regressing_progress_epoch_fails_deterministically(self):
        data = run_until_data(success_value=0, max_frames=5, stall=True)
        data["frames"] = [{"start": 2, "end": 2, "keys": ["A"]}]
        data["run_until"]["stall"]["max_unchanged_frames"] = 4
        scenario = gba_playtest.parse_scenario_data(data)
        with tempfile.TemporaryDirectory(prefix="gba-run-until-regress-") as temporary:
            rom = Path(temporary) / "fixture.gba"
            build_homebrew_rom(rom)
            with self.assertRaisesRegex(
                gba_playtest.PlaytestError,
                "progress epoch regressed at frame 2",
            ):
                self._capture_or_skip(rom, scenario)


if __name__ == "__main__":
    unittest.main()
