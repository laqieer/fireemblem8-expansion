"""Preparation controls only; every process/foreign/filesystem effect is inert."""

import ast
import builtins
import copy
import ctypes
import dataclasses
import io
import json
import linecache
import os
from pathlib import Path
import resource
import selectors
import shlex
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import yaml

from scripts.ci_calibration import kernel, observation_failure, policy, root_stage, supervisor, volume_mount, worker
from scripts.validation_ownership import budget as budgeting


REPO = Path(__file__).resolve().parents[2]
WORKFLOW_TEXT = (REPO / policy.WORKFLOW).read_text()


class Inert(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.forbidden = []
        self.addCleanup(lambda: self.assertEqual(self.forbidden, [], "unmodeled effect attempted"))
        self.stack.enter_context(mock.patch.object(linecache, "getline", return_value=""))
        self.stack.enter_context(mock.patch.object(linecache, "checkcache"))

        def trap(name):
            def refused(*args, **kwargs):
                self.forbidden.append(name)
                raise AssertionError("unmodeled effect: " + name)
            return refused

        for module, names in (
            (os, ("fork", "forkpty", "waitid", "waitpid", "pidfd_open", "kill", "killpg", "_exit",
                  "setuid", "seteuid", "setgid", "setegid", "setresuid", "setresgid", "setgroups",
                  "getuid", "getgid", "geteuid", "getresuid", "getresgid", "getpid", "getppid",
                  "open", "fdopen", "close", "read", "write", "pread", "pipe", "pipe2", "dup", "dup2",
                  "fstat", "statvfs", "stat", "lstat", "listdir", "scandir", "readlink", "lseek",
                  "set_blocking", "mkdir", "rmdir", "unlink", "remove", "rename", "replace", "link", "symlink",
                  "chdir", "chroot", "chown", "fchown", "chmod", "fchmod", "utime", "system",
                  "execv", "execve", "posix_spawn", "posix_spawnp")),
            (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
            (selectors, ("DefaultSelector",)), (socket, ("socket",)),
            (resource, ("setrlimit", "prlimit")),
            (signal, ("pidfd_send_signal", "raise_signal", "pause")),
            (threading.Thread, ("start", "join")),
            (ctypes, ("CDLL",)), (builtins, ("open",)), (io, ("open",)),
            (Path, ("open", "mkdir", "rmdir", "unlink", "write_text", "write_bytes", "touch",
                    "read_text", "read_bytes", "iterdir")),
            (kernel, ("read", "prctl", "drop_identity", "mount_tmpfs", "mount_private_proc", "pivot_root")),
            (supervisor, ("tool", "git")), (root_stage, ("candidate_api",)),
        ):
            for name in names:
                self.stack.enter_context(mock.patch.object(module, name, side_effect=trap(name)))
        for name, value in (
            ("pthread_sigmask", set()), ("signal", signal.SIG_DFL), ("getsignal", signal.SIG_DFL),
            ("sigpending", set()), ("sigtimedwait", None),
        ):
            self.stack.enter_context(mock.patch.object(signal, name, return_value=value))
        self.stack.enter_context(mock.patch.object(time, "monotonic", return_value=100.0))

    def budget(self):
        return worker.calibration_budget(budgeting.Limits, budgeting.ProbeBudget, 3700.0)[0]

    def session(self, budget, *, closed=True):
        return SimpleNamespace(
            budget=budget, processes_used=16, syscalls_used=1000, observations_used=100,
            files_created=2, pending_commands_peak=1, live_process_peak=4, memory_peak=16 * policy.MIB,
            pending_commands=0, parked_capsules=[], make_depth=0, _file_owners={},
            base=None if closed else Path("/owned/base"),
        )

    def component_result(self):
        budget = self.budget()
        budget.plan(1)
        budget.runs = 28
        budget.charge("control", 33708478)
        budget.closed = True
        session = self.session(budget)
        return {
            "version": 1, "workload_kind": policy.WORKLOAD_KIND, "fixture_version": policy.FIXTURE_VERSION,
            "source_revision": policy.GRAPH, "base_revision": policy.BASE, "profile": policy.PROFILE,
            "method": policy.COMPONENT_CASE + "." + policy.COMPONENT_METHOD, "target": policy.COMPONENT_TARGET,
            "source_phases": False, "component_attempts": 1, "component_completed": True,
            **policy.ABSENT_WORKLOADS,
            "fixture": {"preexisting_query": True, "genuine_headers": 3, "header_parents_absent": True,
                        "text_producer": False, "empty_final_target": True},
            "observation": {
                "make_attempts": 1, "make_returned": 1, "checker_occurrences": 2, "recipe_receipts": 2,
                "dispatch_sequences": [1, 7], "producer_slots": [2, 8],
                "stage_names": [list(policy.STAGES), list(policy.STAGES)],
                "intermediate_versions": [1, 1], "intermediate_complete": [True, True],
                "retirement_absent": [True, True], "retired_nlinks": [0, 0], "assembly_extents": [128, 128],
                "content_equal": True, "bindings_distinct": True, "raw_receipts_distinct": True,
                "semantic_records": 1, "typed_references": 2,
            },
            "counters": policy.counter_snapshot(budget, session),
            "cleanup": {"budget_closed": True, "children": 0, "waiters": 0, "retained_owners": 0,
                        "session_base_removed": True, "fixture_removed": True},
        }

    def component_phase(self):
        component = self.component_result()
        return {
            "mode": "component", "first_cause": None, "returncode": 0, "deadline": 3700.0,
            "component_attempts": 1, **policy.ABSENT_WORKLOADS, "empty": True,
            "watchdog_reaped": True, "lifetime_writer_closed": True, "output_exceeded": False,
            "worker": {
                "component": component, "validation": policy.validate_component_result(component),
                "counters": {"phase": "completed-component", "counters": component["counters"], "semantics": "inert"},
                "component_attempts": 1, "component_completed": True, **policy.ABSENT_WORKLOADS,
                "timing": {"worker_started": 100.0, "source_verified": 101.0, "method_finished": 110.0, "finalized": 111.0},
            },
        }


class CalibrationControls(Inert):
    def event(self):
        return {
            "ref": "refs/heads/" + policy.BRANCH, "before": "0" * 40, "after": "a" * 40,
            "created": True, "deleted": False,
            "repository": {"full_name": policy.REPOSITORY, "private": False}, "sender": {"login": "laqieer"},
        }

    def authorization(self, **changes):
        return {"sha": "a" * 40, "run_id": "12345", "attempt": "1", "run_number": "1",
                "environment": "github-hosted", "operating_system": "Linux", "event_name": "push", **changes}

    def facts(self):
        return {
            "memory_total": 16 * policy.GIB, "memory_available": 12 * policy.GIB,
            "disk_available": 24 * policy.GIB, "cpus": 4, "threads_max": 100000, "threads_current": 500,
            "cgroup_ancestors": [{"path": "/", "memory_max": None, "memory_current": 0,
                                  "pids_max": None, "pids_current": 0}],
        }

    def test_only_first_owner_public_creation_can_plan_the_component(self):
        result = policy.validate_event(self.event(), **self.authorization())
        self.assertEqual(result["graph_sha"], policy.GRAPH)
        self.assertEqual(result["component_method"], policy.COMPONENT_CASE + "." + policy.COMPONENT_METHOD)
        self.assertFalse(result["production_acceptance"])
        self.assertFalse(result["source_phases"])
        for change in (
            {"attempt": "2"}, {"run_number": "2"}, {"environment": "self-hosted"},
            {"operating_system": "Windows"}, {"event_name": "workflow_dispatch"},
            {"run_id": "../unsafe"}, {"sha": "B" * 40},
        ):
            with self.subTest(change=change), self.assertRaises(policy.GuardError):
                policy.validate_event(self.event(), **self.authorization(**change))
        for key, value in (("created", False), ("deleted", True), ("before", "b" * 40),
                           ("ref", "refs/heads/calibration/issue-180-ci-baseline-20"), ("after", "b" * 40)):
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                policy.validate_event({**self.event(), key: value}, **self.authorization())
        for key, value in (("repository", {"full_name": policy.REPOSITORY, "private": True}),
                           ("sender", {"login": "foreign"})):
            with self.assertRaises(policy.GuardError):
                policy.validate_event({**self.event(), key: value}, **self.authorization())

    def test_envelope_keeps_effective_capacity_formulas_and_reserved_headroom(self):
        observed = policy.choose_envelope(self.facts())
        self.assertEqual((observed["memory_max"], observed["disk_bytes"], observed["pids_max"]),
                         (8 * policy.GIB, 8 * policy.GIB, 128))
        self.assertEqual((observed["memory_swap_max"], observed["memory_oom_group"]), (0, 1))
        limited = self.facts()
        limited["cgroup_ancestors"].append({
            "memory_max": 8 * policy.GIB, "memory_current": 9 * policy.GIB // 2,
            "pids_max": 220, "pids_current": 80,
        })
        observed = policy.choose_envelope(limited)
        self.assertEqual((observed["memory_max"], observed["pids_max"]), (3 * policy.GIB // 2, 76))
        for key, value in (("memory_available", 2 * policy.GIB), ("disk_available", 4 * policy.GIB),
                           ("cpus", 1), ("threads_max", 600), ("cpus", True), ("cgroup_ancestors", [])):
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                policy.choose_envelope({**self.facts(), key: value})

    def test_profile_changes_only_control_to_the_unchanged_aggregate(self):
        original = dataclasses.asdict(budgeting.Limits())
        self.assertEqual(original, policy.ORIGINAL_LIMITS)
        with self.assertRaises(budgeting.MakeProbeError):
            budgeting.Limits(control_bytes=original["total_bytes"])
        budget, limits, captured, manifest = worker.calibration_budget(
            budgeting.Limits, budgeting.ProbeBudget, 3700.0,
        )
        self.assertEqual(captured, original)
        self.assertEqual(dataclasses.asdict(limits), {**original, "control_bytes": original["total_bytes"]})
        self.assertEqual({name for name in original if manifest[name]["diagnostic"] != original[name]}, {"control_bytes"})
        self.assertEqual(set(manifest), set(original))
        self.assertEqual((limits.observations, limits.observation_count), (None, 32768))
        self.assertEqual((budget.started, budget.deadline), (100.0, 3700.0))
        for name in ("run", "charge", "remaining", "plan", "admit_planned_state", "read_bytes", "close"):
            self.assertIs(getattr(type(budget), name), getattr(budgeting.ProbeBudget, name))
        self.assertIs(type(limits).__post_init__, budgeting.Limits.__post_init__)
        self.assertEqual(dataclasses.asdict(budgeting.Limits()), original)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            limits.control_bytes = 1

    def test_actual_retained_request_preserves_preimage_and_all_charges(self):
        normal = budgeting.ProbeBudget()
        normal.started = 100.0
        normal.charge("control", 31668852)
        before = normal.bytes.copy()
        with self.assertRaises(budgeting.MakeProbeError):
            normal.charge("control", 2039626)
        self.assertEqual(normal.bytes, before)
        diagnostic = self.budget()
        diagnostic.charge("control", 31668852)
        diagnostic.charge("control", 2039626)
        self.assertEqual(diagnostic.bytes["control"], 33708478)
        self.assertEqual(diagnostic.limits.total_bytes, 805306368)
        diagnostic.close()
        self.assertEqual(diagnostic.bytes["control"], 33708478)
        with self.assertRaises(budgeting.MakeProbeError):
            diagnostic.charge("control", 1)

    def test_every_category_exact_limit_and_aggregate_one_over_remain_enforced(self):
        for category in policy.BYTE_CATEGORIES:
            with self.subTest(category=category):
                budget = self.budget()
                cap = getattr(budget.limits, category + "_bytes")
                budget.charge(category, cap)
                self.assertEqual(budget.bytes[category], cap)
                with self.assertRaises(budgeting.MakeProbeError):
                    budget.charge(category, 1)
                self.assertEqual(budget.bytes[category], cap)
        budget = self.budget()
        budget.charge("snapshot", 1)
        budget.charge("control", policy.CONTROL_CEILING - 1)
        self.assertEqual(sum(budget.bytes.values()), policy.CONTROL_CEILING)
        with self.assertRaises(budgeting.MakeProbeError):
            budget.charge("control", 1)
        self.assertEqual(budget.bytes["control"], policy.CONTROL_CEILING - 1)

    def test_pending_record_plan_and_original_nonbyte_limits_cannot_borrow_control(self):
        budget = self.budget()
        self.assertEqual((budgeting.MAX_PENDING_RECORD_BYTES, budgeting.MAX_PLANNED_STATE_BYTES),
                         (policy.MIB, policy.MIB))
        with self.assertRaises(budgeting.MakeProbeError):
            budget.charge("pending", policy.MIB + 1)
        self.assertEqual(budget.bytes, {})
        budget = self.budget()
        budget.admit_planned_state(policy.MIB)
        with self.assertRaises(budgeting.MakeProbeError):
            budget.admit_planned_state(1)
        self.assertEqual(budget.planned_state_bytes, policy.MIB)
        for field in ("processes", "pending", "entries", "created_files", "file_bytes",
                      "process_output_bytes", "address_space_bytes", "runs", "states", "syscalls", "descendants"):
            self.assertEqual(getattr(self.budget().limits, field), policy.ORIGINAL_LIMITS[field])

    def test_default_drift_unknown_fields_and_wrong_effective_observations_refuse(self):
        original = policy.ORIGINAL_LIMITS
        for data in ({**original, "unknown": 1}, {**original, "control_bytes": 64 * policy.MIB},
                     {key: value for key, value in original.items() if key != "observations"}):
            with self.assertRaises(policy.GuardError):
                policy.profile_manifest(data, observation_count=32768)
        for effective in (None, False, 0, 32768.0, policy.POLICY_SENTINEL):
            with self.assertRaises(policy.GuardError):
                policy.profile_manifest(original, observation_count=effective)
        wrong = dataclasses.make_dataclass(
            "WrongObservations", [], bases=(budgeting.Limits,), frozen=True,
            namespace={"observation_count": property(lambda self: 0)},
        )
        with self.assertRaises(policy.GuardError):
            worker.calibration_budget(wrong, budgeting.ProbeBudget, 3700.0)
        for deadline in (100.0, 3701.0, float("nan")):
            with self.assertRaises(policy.GuardError):
                worker.calibration_budget(budgeting.Limits, budgeting.ProbeBudget, deadline)

    def test_old_all_category_sentinel_breaks_the_one_override_oracle(self):
        original = policy.diagnostic_limit
        def oracle():
            budget = self.budget()
            self.assertEqual(dataclasses.asdict(budget.limits), {
                **policy.ORIGINAL_LIMITS, "control_bytes": policy.CONTROL_CEILING,
            })
        oracle()
        with mock.patch.object(policy, "RELAXED", {**policy.RELAXED, "total_bytes": "old"}), \
             self.assertRaises((policy.GuardError, AssertionError)):
            oracle()
        with mock.patch.object(policy, "diagnostic_limit", side_effect=lambda name: policy.POLICY_SENTINEL if name == "control_bytes" else original(name)):
            with self.assertRaises((policy.GuardError, AssertionError)):
                oracle()
        oracle()

    def test_root_graph_report_source_and_h1_routes_refuse_before_candidate_import(self):
        with mock.patch.object(root_stage, "candidate_api") as imported:
            for function in (worker.root, worker.graph):
                with self.assertRaises(policy.GuardError):
                    function({"mode": "component"})
            with mock.patch.object(worker, "require_contained", return_value={}), \
                 mock.patch.object(kernel, "emit"):
                for mode in ("root", "graph", "report", "verifier", "source-phase", "h1", "ordinary"):
                    with self.assertRaises(policy.GuardError):
                        worker.main({"mode": mode, "scope": "inert"})
            imported.assert_not_called()

    def test_protocol_scope_order_partial_duplicates_and_old_starts_fail_closed(self):
        ready = policy.encoded({"scope": "fixed", "kind": "ready", "data": {}}) + b"\n"
        parser = supervisor.Protocol("fixed", 4096)
        self.assertEqual(parser.feed(ready[:5]), [])
        self.assertEqual(parser.feed(ready[5:])[0]["kind"], "ready")
        with self.assertRaises(policy.GuardError):
            parser.feed(ready)
        for kind in ("root-start", "graph-start", "unknown"):
            with self.assertRaises(policy.GuardError):
                parser.feed(policy.encoded({"scope": "fixed", "kind": kind, "data": {}}) + b"\n")
        for kind in ("error", "result"):
            with self.assertRaises(policy.GuardError):
                parser.feed(policy.encoded({"scope": "fixed", "kind": kind, "data": {"source_refusal": {}}}) + b"\n")
        for data in (b'{"scope":"fixed","scope":"other","kind":"ready","data":{}}\n', b"not-json\n"):
            with self.assertRaises(policy.GuardError):
                supervisor.Protocol("fixed", 4096).feed(data)
        with self.assertRaises(supervisor.OutputLimitExceeded):
            supervisor.Protocol("fixed", 64).feed(b"x" * 65)
        output = supervisor.Protocol("fixed", 128, raw_after_ready=True)
        output.feed(ready)
        self.assertEqual(output.feed(b"not a frame"), [])
        self.assertFalse(output.buffer)
        with self.assertRaises(supervisor.OutputLimitExceeded):
            output.observe_output(b"x" * 129, stderr=True)

    def test_completed_data_is_not_a_prefix_failed_budget_or_outer_cleanup_certificate(self):
        result = self.component_result()
        self.assertFalse(policy.validate_component_result(result)["production_acceptance"])
        zero_slot = copy.deepcopy(result)
        zero_slot["observation"]["producer_slots"] = [0, 1]
        policy.validate_component_result(zero_slot)
        mutations = [
            ("component_completed", False), ("component_attempts", 2), ("source_phases", True),
            ("root_check_attempts", 1), ("graph_check_attempts", 1), ("method", "another"),
            ("profile", "control-sizing128"), ("source_revision", "b" * 40),
        ]
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                policy.validate_component_result({**result, key: value})
        for key in result["observation"]:
            altered = copy.deepcopy(result)
            del altered["observation"][key]
            with self.subTest(missing=key), self.assertRaises(policy.GuardError):
                policy.validate_component_result(altered)
        for key, value in (("children", 1), ("retained_owners", 1), ("fixture_removed", False),
                           ("session_base_removed", None), ("budget_closed", False)):
            altered = copy.deepcopy(result)
            altered["cleanup"][key] = value
            with self.assertRaises(policy.GuardError):
                policy.validate_component_result(altered)
            phase = self.component_phase()
            phase["worker"]["component"] = altered
            self.assertTrue(supervisor.component_retention(phase))
        bad = copy.deepcopy(result)
        bad["counters"]["budget"]["failed"] = True
        with self.assertRaises(policy.GuardError):
            policy.validate_component_result(bad)
        self.assertTrue(supervisor.component_retention({"mode": "component"}))
        for unknown in (None, [], {}, {"mode": "root"}):
            self.assertTrue(supervisor.component_retention(unknown))

    def test_counter_snapshot_is_complete_numeric_and_distinguishes_vm_from_ledgers(self):
        result = self.component_result()
        counts = result["counters"]
        self.assertEqual(set(counts["budget"]["categories"]), set(policy.BYTE_CATEGORIES))
        self.assertEqual(counts["budget"]["categories"]["control"]["charged"], 33708478)
        self.assertTrue(counts["budget"]["categories"]["control"]["exceeds_original"])
        self.assertEqual(counts["session"]["memory_peak"], 16 * policy.MIB)
        self.assertNotEqual(counts["budget"]["total"], counts["session"]["memory_peak"])
        for mutate in ("unknown", "total", "cap", "remaining", "bool", "live", "vm"):
            value = copy.deepcopy(counts)
            if mutate == "unknown":
                value["budget"]["categories"]["unknown"] = {}
            elif mutate == "total":
                value["budget"]["total"] += 1
            elif mutate == "cap":
                value["budget"]["categories"]["control"]["diagnostic_cap"] *= 2
            elif mutate == "remaining":
                value["budget"]["categories"]["control"]["remaining"] += 1
            elif mutate == "bool":
                value["budget"]["runs"] = True
            elif mutate == "live":
                value["session"]["children"] = 1
            else:
                value["session"]["memory_peak"] = policy.ORIGINAL_LIMITS["address_space_bytes"] + 1
            with self.subTest(mutate=mutate), self.assertRaises(policy.GuardError):
                policy.validate_component_counters(value, complete=True)
        neutral = json.loads(json.dumps(counts, sort_keys=True))
        self.assertEqual(policy.validate_component_counters(neutral, complete=True), counts)

    def test_component_phase_requires_complete_timing_counters_and_both_cleanups(self):
        phase = self.component_phase()
        supervisor.validate_component_phase(phase)
        for key, value in (("returncode", 1), ("returncode", False), ("first_cause", {}), ("component_attempts", 2),
                           ("empty", False), ("watchdog_reaped", False), ("lifetime_writer_closed", False),
                           ("output_exceeded", True), ("cleanup_errors", [{"failed": True}])):
            changed = copy.deepcopy(phase)
            changed[key] = value
            if key == "first_cause":
                changed[key] = {"type": "failure"}
            with self.assertRaises(policy.GuardError):
                supervisor.validate_component_phase(changed)
        for key, value in (("finalized", 3701.0), ("method_finished", 99.0), ("worker_started", True)):
            changed = copy.deepcopy(phase)
            changed["worker"]["timing"][key] = value
            with self.assertRaises(policy.GuardError):
                supervisor.validate_component_phase(changed)
        changed = copy.deepcopy(phase)
        changed["worker"]["counters"]["phase"] = "component-method"
        with self.assertRaises(policy.GuardError):
            supervisor.validate_component_phase(changed)

    def test_first_error_format_never_exports_messages_frames_or_arbitrary_locals(self):
        class PrivateError(RuntimeError):
            def __str__(self):
                raise AssertionError("private formatter called")
        first = PrivateError("secret command environment assembly")
        second = OSError(5, "private source")
        first.__cause__ = second
        value = policy.component_error_record(first)
        self.assertEqual(value["chain"], [{"type": "PrivateError", "errno": None}, {"type": "OSError", "errno": 5}])
        self.assertNotIn(b"private", policy.encoded(value))
        self.assertNotIn(b"secret", policy.encoded(value))
        second.__cause__ = first
        self.assertFalse(policy.component_error_record(first)["complete"])

    def test_exact_lineage_and_new_workflow_keep_first_attempt_and_closed20(self):
        chain = [
            f"{'a' * 40} {supervisor.COMPONENT_BASE_SHA}",
            f"{supervisor.COMPONENT_BASE_SHA} {supervisor.REVIEWED_HARNESS_SHA}",
            f"{supervisor.REVIEWED_HARNESS_SHA} {supervisor.ROOT18_HARNESS_SHA}",
            f"{supervisor.ROOT18_HARNESS_SHA} {supervisor.RETAINED_HARNESS_SHA}",
            f"{supervisor.RETAINED_HARNESS_SHA} {supervisor.PREPARATION_SHA}",
            f"{supervisor.PREPARATION_SHA} {policy.BASE}",
        ]
        supervisor.validate_harness_lineage(chain, "a" * 40)
        for bad in (chain[1:], chain + [chain[-1]], [*chain[:1], chain[-1]], [line.replace("a" * 40, "b" * 40) for line in chain]):
            with self.assertRaises(policy.GuardError):
                supervisor.validate_harness_lineage(bad, "a" * 40)
        data = yaml.load(WORKFLOW_TEXT, Loader=yaml.BaseLoader)
        self.assertEqual(data["on"], {"push": {"branches": [policy.BRANCH]}})
        self.assertEqual(data["permissions"], {"contents": "read"})
        self.assertEqual(data["concurrency"]["cancel-in-progress"], "false")
        job, = data["jobs"].values()
        self.assertEqual((job["runs-on"], job["timeout-minutes"]), ("ubuntu-latest", "90"))
        self.assertNotIn("container", job)
        self.assertNotIn("strategy", job)
        predicates = {part.strip() for part in job["if"].split("&&")}
        self.assertTrue({"github.run_number == 1", "github.run_attempt == 1",
                         "github.event.created == true", "github.event.repository.private == false",
                         "github.actor == 'laqieer'", "github.triggering_actor == 'laqieer'"} <= predicates)
        checkouts = [step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual([step["with"]["ref"] for step in checkouts], ["${{ github.sha }}", policy.GRAPH])
        self.assertEqual([step["with"]["path"] for step in checkouts], ["harness", "candidate"])
        self.assertTrue(all(step["with"]["persist-credentials"] == "false" for step in checkouts))
        upload, = [step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(upload["with"]["path"].splitlines(), [
            "${{ runner.temp }}/" + policy.OUTPUT_PREFIX + "${{ github.run_id }}/" + name
            for name in policy.ARTIFACT_NAMES
        ])
        for step in job["steps"]:
            self.assertNotIn("continue-on-error", step)
        run, = [step for step in job["steps"] if step.get("timeout-minutes") == "70"]
        arguments = shlex.split(run["run"])
        self.assertIn("run", arguments)
        self.assertEqual(arguments[arguments.index("--output") + 1], "$RUNNER_TEMP/" + policy.OUTPUT_PREFIX + "$GITHUB_RUN_ID")
        self.assertNotIn("secrets.", json.dumps(data))

    def test_fresh_qualifiers_cannot_credit_unrelated_failures_or_outer_kill(self):
        lifetime = {
            "mode": "lifetime", "identity": {"uid": 999}, "empty": True, "watchdog_reaped": True,
            "escaped": {"sid": 42}, "held_descendants_terminal": 2,
            "caller_lifetime_control_exercised": True, "empty_before_outer_cleanup": True,
            "first_cause": {"type": "lifetime-eof"},
        }
        supervisor.validate_probe(lifetime)
        for mutation in ({"empty_before_outer_cleanup": False}, {"held_descendants_terminal": 0},
                         {"caller_lifetime_control_exercised": False}, {"first_cause": {"type": "deadline"}},
                         {"supervisor_error": {"real": "failure"}}):
            with self.assertRaises(policy.GuardError):
                supervisor.validate_probe({**lifetime, **mutation})
        memory = {"mode": "memory", "identity": {}, "empty": True, "watchdog_reaped": True,
                  "kernel": {"memory_events": {"oom_kill": 1}}}
        supervisor.validate_probe(memory)
        memory["kernel"]["memory_events"]["oom_kill"] = 0
        with self.assertRaises(policy.GuardError):
            supervisor.validate_probe(memory)
        output = {"mode": "output", "identity": {}, "empty": True, "watchdog_reaped": True,
                  "first_cause": {"type": "output-bound"}, "output_exceeded": True, "output_bytes": 65537}
        supervisor.validate_probe(output)
        for key, value in (("output_bytes", 65536), ("output_exceeded", False), ("first_cause", {"type": "protocol"})):
            with self.assertRaises(policy.GuardError):
                supervisor.validate_probe({**output, key: value})

    def test_volume_control_preserves_exact_objects_fixed_syscalls_and_non_lazy_cleanup(self):
        parent = Path("/owned/vo-ci180-12345-unit/probe-volume")
        target, image, device = parent / "volume", parent / "workspace.img", Path("/dev/loop7")
        directory = lambda inode: [10, inode, stat.S_IFDIR | 0o755, 0, 0, 4096, 2, 0]
        identities = {
            parent: directory(20), target: directory(21),
            parent.parent: [10, 19, stat.S_IFDIR | 0o700, 0, 0, 4096, 2, 0],
            image: [10, 22, stat.S_IFREG | 0o600, 0, 0, 64 * policy.MIB, 1, 0],
            device: [11, 23, stat.S_IFBLK | 0o660, 0, 6, 0, 1, os.makedev(7, 7)],
        }
        spec = {"image": str(image), "target": str(target), "device": str(device),
                "image_identity": identities[image], "target_identity": identities[target],
                "device_identity": identities[device], "parent_identity": identities[parent],
                "worker_uid": 999, "worker_gid": 998}
        for fault in (None, "owner", "device", "backing", "mount", "replacement"):
            actual = copy.deepcopy(identities)
            if fault == "owner":
                actual[image][3] = 999
            elif fault == "device":
                actual[device][1] += 1
            elif fault == "replacement":
                actual[target][1] += 1
            def read(path, *ignored):
                if str(path) == "/proc/self/mountinfo":
                    return (f"90 1 7:8 / {target} rw,nosuid,nodev - ext4 /dev/loop8 rw\n".encode()
                            if fault == "mount" else b"")
                return b"/foreign/image" if fault == "backing" else str(image).encode()
            with self.subTest(fault=fault), mock.patch.object(os, "geteuid", return_value=0), \
                 mock.patch.object(volume_mount, "identity", side_effect=lambda path: actual[Path(path)]), \
                 mock.patch.object(kernel, "read", side_effect=read), \
                 mock.patch.object(volume_mount, "unmount_syscall") as unmount:
                if fault:
                    with self.assertRaises(policy.GuardError):
                        volume_mount.operate("unmount", spec)
                else:
                    self.assertEqual(volume_mount.inspect_mount(spec)["state"], "absent")
                unmount.assert_not_called()
        with mock.patch.object(kernel.LIBC, "mount", return_value=0) as mount, \
             mock.patch.object(kernel.LIBC, "umount2", return_value=0) as unmount:
            volume_mount.mount_syscall("/dev/loop7", "/owned/volume")
            volume_mount.unmount_syscall("/owned/volume")
            mount.assert_called_once_with(b"/dev/loop7", b"/owned/volume", b"ext4", 6, None)
            unmount.assert_called_once_with(b"/owned/volume", 0)

    def test_io_rates_are_sampled_and_counter_regressions_refuse(self):
        peaks = {}
        supervisor.io_peaks({}, {"7:1": {"rbytes": 100, "wbytes": 60}}, 2, peaks)
        supervisor.io_peaks({"7:1": {"rbytes": 100, "wbytes": 60}},
                            {"7:1": {"rbytes": 130, "wbytes": 180}}, 2, peaks)
        self.assertEqual((peaks["7:1/rbytes_per_second"], peaks["7:1/wbytes_per_second"]), (50, 60))
        with self.assertRaises(policy.GuardError):
            supervisor.io_peaks({"7:1": {"wbytes": 100}}, {"7:1": {"wbytes": 1}}, 1, {})


if __name__ == "__main__":
    unittest.main()
