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
SUPERVISOR_AST = ast.parse((REPO / "scripts/ci_calibration/supervisor.py").read_text())
QUOTA_MODEL = SOURCE_PROBING = OLD_BUDGET_CHARGE = OLD_CALIBRATION_FACTORY = None
OLD_TELEMETRY_SNAPSHOT = OLD_TELEMETRY_PROTOCOL = None
CORRECTED_SOURCE_INPUTS = None
WORKER_AST = ast.parse((REPO / "scripts/ci_calibration/worker.py").read_text())


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
        self.stack.enter_context(mock.patch.object(observation_failure, "_CLOCK", time.monotonic))

    def budget(self):
        return worker.calibration_budget(budgeting.Limits, budgeting.ProbeBudget, 3700.0)[0]

    def session(self, budget, *, closed=True):
        return SimpleNamespace(
            budget=budget, processes_used=16, syscalls_used=1000, observations_used=100,
            files_created=2, pending_commands_peak=1, live_process_peak=4, memory_peak=16 * policy.MIB,
            pending_commands=0, parked_capsules=[], make_depth=0, _file_owners={},
            base=None if closed else Path("/owned/base"), _views=[], owner_thread=threading.get_ident(),
        )

    def changes(self):
        return policy.changed_path_set(b"A\0include/new.h\0D\0include/removed.h\0M\0src/current.c\0")

    def binding(self):
        return policy.validate_report_binding({
            "source_revision": policy.GRAPH, "base_revision": policy.BASE, "harness_revision": "a" * 40,
            "run_id": "12345", "run_attempt": 1, "run_number": 1, "workload_kind": policy.WORKLOAD_KIND,
            "api": policy.REPORT_API, "profile": policy.PROFILE, "source_phases": True, "lifecycle": True,
            "changed_paths": policy.changed_path_binding(self.changes()), "tracked_paths": 100,
        })

    def raw_report(self, budget, session):
        owners = {
            name: {"edge_id": f"edge-{index}", "edge_type": "compile-owner",
                   "evidence_id": f"evidence-{index}", "evidence_type": "compile",
                   "gate": f"private authority {index}", "reason": "private original explanation"}
            for index, name in enumerate(self.changes())
        }
        return {
            "schema_version": 1, "policy": {
                "classification": "framework-capability", "validation_effect": "report-only",
                "narrowing_authorized": False, "review_invalidation": "resolved-edge-authority",
            },
            "coverage": {"tracked_paths": 100, "owned_paths": 98, "fail_closed_exclusions": 2, "path_rules": 8},
            "artifact": {
                "artifact_id": "private-artifact", "current_disposition": "retained",
                "executable_consumer": "consumer", "consistency_check": "consistency",
                "executable_lifecycle": [
                    {"trigger_event_id": f"event-{index}", "trigger_type": kind, "proof_id": f"proof-{index}",
                     "removal": "fail", "restoration": "pass", "reason": "private lifecycle reason",
                     "semantics": "verified-dispatch-and-shared-checker",
                     "verified_routes": ["consumer", "consistency"]}
                    for index, kind in enumerate(("artifact_checkpoint", "dependency_changed", "pre_graduation"))
                ],
            },
            "measurement": {
                "source_case": "private-oracle", "oracle_seal": "b" * 64, "probe_count": 2,
                "false_positive_selections": 0, "false_negative_selections": 0,
                "estimated_maintenance_minutes": 5, "max_maintenance_minutes": 10,
                "probes": [
                    {"path": "src/current.c", "surface": "surface", "owners": [
                        {"edge_type": "compile-owner", "evidence_id": "evidence-2"},
                    ]},
                    {"path": "excluded", "exclusion": "excluded-gitlink"},
                ],
            },
            "resolutions": [
                {"path": name, "rule": "rule", "surface": "surface", "surface_type": "source",
                 "git_mode": "100644", "admission": "selected-base-tree" if kind == "D" else "exact-ownership-rule",
                 "graph_origin": "introduced-rules-over-base" if kind == "D" else "selected-tree",
                 "owners": [owners[name]]}
                for name, kind in self.changes().items()
            ],
            "selected_gates": [
                {"evidence_id": row["evidence_id"], "evidence_type": row["evidence_type"], "gate": row["gate"],
                 "reasons": [{"path": name, "edge_type": row["edge_type"], "explanation": row["reason"]}]}
                for name, row in owners.items()
            ],
            "review_invalidation": {
                "invalidated": True, "reason": "ownership-graph-introduced", "changed_edge_ids": ["edge-0"],
            },
            "seals": dict.fromkeys(("schema", "graph", "resolved_edges"), "c" * 64),
            "execution": {
                "revision": policy.GRAPH, "base_revision": policy.BASE, "runs": budget.runs, "states": budget.states,
                "bytes": dict(budget.bytes), "processes": session.processes_used,
                "live_process_peak": session.live_process_peak, "syscalls": session.syscalls_used,
            },
        }

    def report_result(self):
        budget = self.budget()
        budget.plan(1)
        budget.runs = 28
        budget.charge("control", 104697218)
        budget.closed = True
        budget.session_started = True
        session = self.session(budget)
        counters = policy.counter_snapshot(budget, session)
        self.last_accounting = policy.AccountingRegistry(budget).observe(counters, 11.0, final=True)
        raw = self.raw_report(budget, session)
        return {
            "version": 1, "binding": self.binding(),
            "states": {**dict.fromkeys(policy.REPORT_STATES, 1), "completed": True},
            "summary": policy.summarize_report(raw, self.changes(), counters, self.binding()),
            "serialized_bytes": len(policy.encoded(raw)), "counters": counters,
            "cleanup": {**root_stage.cleanup_state(session, budget), "constructor_restored": True,
                        "report_released": True, "serialization_released": True,
                        "source_imports_restored": True, "source_imports_released": True},
        }

    def report_phase(self):
        report = self.report_result()
        return {
            "mode": "report", "first_cause": None, "returncode": 0, "deadline": 3700.0,
            "report_starts": 1, "report_check_attempts": 1, "report_returned": True, "report_completed": True,
            **policy.ABSENT_WORKLOADS, "empty": True,
            "empty_before_outer_cleanup": True, "watchdog_reaped": True,
            "lifetime_writer_closed": True, "output_exceeded": False,
            "worker": {
                "report": report, "validation": policy.validate_report_result(report, self.binding()),
                "counters": {
                    "phase": "completed-report", "counters": report["counters"],
                    "accounting": self.last_accounting,
                    "semantics": "Observed cumulative counters and funded VM peaks; not an atomic grant or physical RSS.",
                },
                **policy.ABSENT_WORKLOADS,
                "timing": {"worker_started": 100.0, "source_verified": 101.0, "report_finished": 110.0, "finalized": 111.0},
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

    def test_only_first_owner_public_creation_can_plan_the_report(self):
        result = policy.validate_event(self.event(), **self.authorization())
        self.assertEqual(result["graph_sha"], policy.GRAPH)
        self.assertEqual(result["report_api"], policy.REPORT_API)
        self.assertFalse(result["production_acceptance"])
        self.assertTrue(result["source_phases"])
        self.assertTrue(result["lifecycle"])
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

    def test_profile_uses_only_cumulative_query_and_original_hard_limits(self):
        original = dataclasses.asdict(budgeting.Limits())
        self.assertEqual(original, policy.ORIGINAL_LIMITS)
        with self.assertRaises(budgeting.MakeProbeError):
            budgeting.Limits(control_bytes=original["total_bytes"])
        budget, limits, captured, manifest = worker.calibration_budget(
            budgeting.Limits, budgeting.ProbeBudget, 3700.0,
        )
        self.assertEqual(captured, original)
        self.assertIs(type(limits), budgeting.Limits)
        self.assertIs(budget.limits, limits)
        self.assertEqual(dataclasses.asdict(limits), original)
        self.assertEqual({name for name in original if manifest[name]["diagnostic"] != original[name]}, set())
        self.assertEqual(len(policy.RELAXED), 14)
        self.assertEqual({name for name in original if budget.cumulative_limit(name) != (
            limits.observation_count if name == "observations" else original[name]
        )}, set(policy.RELAXED))
        self.assertTrue(all(budget.cumulative_limit(name) == policy.POLICY_SENTINEL for name in policy.RELAXED))
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

    def test_every_category_keeps_hard_limits_and_finite_cumulative_representability(self):
        for category in policy.BYTE_CATEGORIES:
            with self.subTest(category=category):
                budget = self.budget()
                cap = budget.cumulative_limit(category + "_bytes")
                self.assertEqual(getattr(budget.limits, category + "_bytes"), policy.ORIGINAL_LIMITS[category + "_bytes"])
                if category == "pending":
                    budget.bytes[category] = cap - 1
                    budget.charge(category, 1)
                else:
                    budget.charge(category, cap)
                self.assertEqual(budget.bytes[category], cap)
                with self.assertRaises(budgeting.MakeProbeError):
                    budget.charge(category, 1)
                self.assertEqual(budget.bytes[category], cap)
        budget = self.budget()
        budget.charge("snapshot", 1)
        budget.charge("control", policy.POLICY_SENTINEL - 1)
        self.assertEqual(sum(budget.bytes.values()), policy.POLICY_SENTINEL)
        with self.assertRaises(budgeting.MakeProbeError):
            budget.charge("control", 1)
        self.assertEqual(budget.bytes["control"], policy.POLICY_SENTINEL - 1)

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

    def test_old_control_policy_and_raised_hard_limits_break_accounting_oracle(self):
        def oracle():
            budget = self.budget()
            self.assertEqual(dataclasses.asdict(budget.limits), policy.ORIGINAL_LIMITS)
            budget.charge("snapshot", policy.ORIGINAL_LIMITS["snapshot_bytes"] + 1)
            self.assertEqual(budget.bytes["snapshot"], policy.ORIGINAL_LIMITS["snapshot_bytes"] + 1)
        oracle()
        with mock.patch.object(policy, "RELAXED", {"control_bytes": "old control-only policy"}), \
             self.assertRaises((policy.GuardError, budgeting.MakeProbeError, AssertionError)):
            oracle()
        raised = dataclasses.make_dataclass(
            "RaisedHardLimits", [("control_bytes", int, dataclasses.field(default=policy.POLICY_SENTINEL))],
            bases=(budgeting.Limits,), frozen=True,
        )
        with self.assertRaises(policy.GuardError):
            worker.calibration_budget(raised, budgeting.ProbeBudget, 3700.0)
        oracle()

    def test_component_root_graph_source_and_h1_routes_refuse_before_candidate_import(self):
        with mock.patch.object(root_stage, "candidate_api") as imported:
            for function in (worker.root, worker.graph, worker.component):
                with self.assertRaises(policy.GuardError):
                    function({"mode": "component"})
            with mock.patch.object(worker, "require_contained", return_value={}), \
                 mock.patch.object(kernel, "emit"):
                for mode in ("component", "root", "graph", "verifier", "source-phase", "h1", "ordinary"):
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
        for kind in ("component-start", "root-start", "graph-start", "unknown"):
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
        result = self.report_result()
        self.assertFalse(policy.validate_report_result(result, self.binding())["production_acceptance"])
        for key in policy.REPORT_STATES:
            altered = copy.deepcopy(result)
            altered["states"][key] = 0
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                policy.validate_report_result(altered, self.binding())
        for key, value in (("profile", "control-sizing128"), ("source_revision", "b" * 40),
                           ("base_revision", "c" * 40), ("harness_revision", "d" * 40),
                           ("run_id", "12346"), ("run_attempt", 2), ("source_phases", False),
                           ("lifecycle", False), ("api", "another")):
            altered = copy.deepcopy(result)
            altered["binding"][key] = value
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                policy.validate_report_result(altered, self.binding())
        for key in result["summary"]:
            altered = copy.deepcopy(result)
            del altered["summary"][key]
            with self.subTest(missing=key), self.assertRaises(policy.GuardError):
                policy.validate_report_result(altered, self.binding())
        for key, value in (("children", 1), ("waiters", 1), ("retained_owners", 1), ("active_views", 1),
                           ("session_base_removed", None), ("budget_closed", False),
                           ("constructor_restored", False), ("report_released", None), ("serialization_released", None)):
            altered = copy.deepcopy(result)
            altered["cleanup"][key] = value
            with self.assertRaises(policy.GuardError):
                policy.validate_report_result(altered, self.binding())
            phase = self.report_phase()
            phase["worker"]["report"] = altered
            self.assertTrue(supervisor.report_retention(phase))
        bad = copy.deepcopy(result)
        bad["counters"]["budget"]["failed"] = True
        with self.assertRaises(policy.GuardError):
            policy.validate_report_result(bad, self.binding())
        for size in (0, True, -1, 1 << 63, None):
            with self.assertRaises(policy.GuardError):
                policy.validate_report_result({**result, "serialized_bytes": size}, self.binding())
        self.assertTrue(supervisor.report_retention({"mode": "report"}))
        for unknown in (None, [], {}, {"mode": "root"}):
            self.assertTrue(supervisor.report_retention(unknown))

    def test_counter_snapshot_is_complete_numeric_and_distinguishes_vm_from_ledgers(self):
        result = self.report_result()
        counts = result["counters"]
        self.assertEqual(set(counts["budget"]["categories"]), set(policy.BYTE_CATEGORIES))
        self.assertEqual(counts["budget"]["categories"]["control"]["charged"], 104697218)
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

    def test_report_phase_requires_complete_timing_counters_and_both_cleanups(self):
        phase = self.report_phase()
        supervisor.validate_report_phase(phase, self.binding())
        for key, value in (("returncode", 1), ("returncode", False), ("first_cause", {}), ("report_check_attempts", 2),
                           ("report_starts", 2), ("report_returned", False), ("report_completed", False),
                           ("empty", False), ("watchdog_reaped", False), ("lifetime_writer_closed", False),
                           ("output_exceeded", True), ("cleanup_errors", [{"failed": True}])):
            changed = copy.deepcopy(phase)
            changed[key] = value
            if key == "first_cause":
                changed[key] = {"type": "failure"}
            with self.assertRaises(policy.GuardError):
                supervisor.validate_report_phase(changed, self.binding())
        for key, value in (("finalized", 3701.0), ("report_finished", 99.0), ("worker_started", True),
                           ("finalized", float("nan")), ("source_verified", None)):
            changed = copy.deepcopy(phase)
            changed["worker"]["timing"][key] = value
            with self.assertRaises(policy.GuardError):
                supervisor.validate_report_phase(changed, self.binding())
        changed = copy.deepcopy(phase)
        changed["worker"]["counters"]["phase"] = "public-report"
        with self.assertRaises(policy.GuardError):
            supervisor.validate_report_phase(changed, self.binding())

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
            f"{'a' * 40} {supervisor.MAKE_CONTEXT_SHA}",
            f"{supervisor.MAKE_CONTEXT_SHA} {supervisor.IMPORT_RELEASE_SHA}",
            f"{supervisor.IMPORT_RELEASE_SHA} {supervisor.ORIGINAL_IMPORT_SHA}",
            f"{supervisor.ORIGINAL_IMPORT_SHA} {supervisor.TRACELESS_ANCHOR_SHA}",
            f"{supervisor.TRACELESS_ANCHOR_SHA} {supervisor.PARTIAL_ANCHOR_SHA}",
            f"{supervisor.PARTIAL_ANCHOR_SHA} {supervisor.PYTHON_REPORT_SHA}",
            f"{supervisor.PYTHON_REPORT_SHA} {supervisor.CORRECTED_REPORT_SHA}",
            f"{supervisor.CORRECTED_REPORT_SHA} {supervisor.REPORT_CODE_METADATA_SHA}",
            f"{supervisor.REPORT_CODE_METADATA_SHA} {supervisor.REPORT_REGISTRATION_SHA}",
            f"{supervisor.REPORT_REGISTRATION_SHA} {supervisor.REPORT_LOCALIZATION_SHA}",
            f"{supervisor.REPORT_LOCALIZATION_SHA} {supervisor.REPORT_REBIND_SHA}",
            f"{supervisor.REPORT_REBIND_SHA} {supervisor.REPORT_TELEMETRY_SHA}",
            f"{supervisor.REPORT_TELEMETRY_SHA} {supervisor.REPORT_ACCOUNTING_SHA}",
            f"{supervisor.REPORT_ACCOUNTING_SHA} {supervisor.REPORT_FINALIZATION_SHA}",
            f"{supervisor.REPORT_FINALIZATION_SHA} {supervisor.REPORT_ERROR_SHA}",
            f"{supervisor.REPORT_ERROR_SHA} {supervisor.REPORT_PREPARATION_SHA}",
            f"{supervisor.REPORT_PREPARATION_SHA} {supervisor.REPORT_BASE_SHA}",
            f"{supervisor.REPORT_BASE_SHA} {supervisor.CORRECTION_BASE_SHA}",
            f"{supervisor.CORRECTION_BASE_SHA} {supervisor.COMPONENT_BASE_SHA}",
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

    def test_pre_kill_empty_must_be_observed_true_not_credit_from_outer_cleanup(self):
        original = self.report_phase()
        supervisor.validate_report_phase(original, self.binding())
        self.assertFalse(supervisor.report_retention(original))
        for before in (False, None, 0, 1, "true", [], {}):
            with self.subTest(before=before):
                changed = copy.deepcopy(original)
                changed["empty_before_outer_cleanup"] = before
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_report_phase(changed, self.binding())
                self.assertTrue(supervisor.report_retention(changed))
        changed = copy.deepcopy(original)
        del changed["empty_before_outer_cleanup"]
        with self.assertRaises(policy.GuardError):
            supervisor.validate_report_phase(changed, self.binding())
        self.assertTrue(supervisor.report_retention(changed))
        for field in ("empty", "watchdog_reaped", "lifetime_writer_closed"):
            changed = copy.deepcopy(original)
            changed[field] = False
            with self.assertRaises(policy.GuardError):
                supervisor.validate_report_phase(changed, self.binding())
            self.assertTrue(supervisor.report_retention(changed))
        neutral = json.loads(json.dumps(original, sort_keys=True))
        self.assertEqual(supervisor.validate_report_phase(neutral, self.binding()),
                         supervisor.validate_report_phase(original, self.binding()))

    def test_correction_inventory_is_nonempty_fixed_modifications_only(self):
        data = b"".join(b"M\0" + path.encode() + b"\0" for path in sorted(supervisor.CORRECTION_PATHS))
        supervisor.validate_correction_inventory(data)
        for bad in (
            b"", b"M\0", b"M\0scripts/ci_calibration/root_stage.py",
            b"A\0scripts/ci_calibration/root_stage.py\0",
            b"D\0scripts/ci_calibration/root_stage.py\0",
            b"M\0" + policy.WORKFLOW.encode() + b"\0",
            b"M\0scripts/ci_calibration/kernel.py\0",
            b"M\0scripts/validation_ownership/budget.py\0", data + data,
        ):
            with self.subTest(bad=bad), self.assertRaises(policy.GuardError):
                supervisor.validate_correction_inventory(bad)

    def test_new_inventory_preserves_every_old_workflow_and_only_admits_normal_modifications(self):
        data = b"".join(
            (b"A" if path == policy.FULL_REPORT_WORKFLOW else b"M") + b"\0" + path.encode() + b"\0"
            for path in sorted(supervisor.REPORT_PATHS)
        )
        supervisor.validate_report_inventory(data)
        for bad in (
            b"", data + data, data[:-1], data.replace(b"A\0", b"M\0"),
            data + b"M\0" + policy.COMPONENT_WORKFLOW.encode() + b"\0",
            data + b"M\0" + policy.PREVIOUS_WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/entry.py\0",
            data + b"M\0scripts/validation_ownership/graph_report.py\0",
            data.replace(b"M\0scripts/ci_calibration/policy.py", b"A\0scripts/ci_calibration/policy.py"),
            data.replace(b"M\0scripts/ci_calibration/policy.py", b"D\0scripts/ci_calibration/policy.py"),
        ):
            with self.subTest(bad=bad[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_report_inventory(bad)

    def test_error_correction_inventory_is_nonempty_M_only_and_keeps_the_full_review_baseline(self):
        data = b"".join(b"M\0" + name.encode() + b"\0" for name in sorted(supervisor.REPORT_ERROR_PATHS))
        supervisor.validate_report_error_inventory(data)
        for bad in (
            b"", data[:-1], data + data, data.replace(b"M\0", b"A\0", 1),
            data.replace(b"M\0", b"D\0", 1),
            data + b"M\0" + policy.WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/entry.py\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/ci_calibration/observation_failure.py\0",
            data + b"M\0scripts/validation_ownership/budget.py\0",
        ):
            with self.subTest(bad=bad[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_report_error_inventory(bad)

    def test_source_rebind_inventory_is_exact_and_cannot_change_workload_or_physical_code(self):
        data = b"".join(b"M\0" + name.encode() + b"\0" for name in sorted(supervisor.SOURCE_REBIND_PATHS))
        supervisor.validate_source_rebind_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.replace(b"M\0", b"A\0", 1),
            data.replace(b"M\0", b"D\0", 1), data.split(b"\0", 2)[2],
            data + b"M\0scripts/ci_calibration/worker.py\0",
            data + b"M\0scripts/ci_calibration/kernel.py\0",
            data + b"M\0scripts/validation_ownership/budget.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_source_rebind_inventory(changed)

    def test_localization_inventory_and_event_cannot_reopen_the_spent_workflow(self):
        data = b"".join(
            (b"A" if name == policy.LOCALIZATION_WORKFLOW else b"M") + b"\0" + name.encode() + b"\0"
            for name in sorted(supervisor.LOCALIZATION_PATHS)
        )
        supervisor.validate_localization_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.replace(b"A\0", b"M\0"),
            data.replace(b"M\0scripts/ci_calibration/observation_failure.py\0", b""),
            data + b"M\0" + policy.FULL_REPORT_WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/ci_calibration/entry.py\0",
            data + b"M\0scripts/validation_ownership/graph_report.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_localization_inventory(changed)
        with self.assertRaises(policy.GuardError):
            policy.validate_event(
                {**self.event(), "ref": "refs/heads/calibration/issue-180-full-report-sizing-1"},
                **self.authorization(),
            )

    def test_registration_inventory_keeps_workflow_policy_and_containment_unchanged(self):
        data = b"".join(b"M\0" + name.encode() + b"\0" for name in sorted(supervisor.REGISTRATION_PATHS))
        supervisor.validate_registration_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.replace(b"M\0", b"A\0", 1),
            data.replace(b"M\0", b"D\0", 1), data.split(b"\0", 2)[2],
            data + b"M\0" + policy.WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/policy.py\0",
            data + b"M\0scripts/ci_calibration/worker.py\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/validation_ownership/make_probe.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_registration_inventory(changed)

    def test_corrected_report_inventory_is_exact_and_preserves_all_spent_mechanisms(self):
        data = b"".join(
            (b"A" if name == policy.CORRECTED_REPORT_WORKFLOW else b"M") + b"\0" + name.encode() + b"\0"
            for name in sorted(supervisor.CORRECTED_REPORT_PATHS)
        )
        supervisor.validate_corrected_report_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.replace(b"A\0", b"M\0"),
            data.replace(b"M\0", b"A\0", 1), data.split(b"\0", 2)[2],
            data + b"M\0" + policy.LOCALIZATION_WORKFLOW.encode() + b"\0",
            data + b"M\0" + policy.FULL_REPORT_WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/worker.py\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/ci_calibration/observation_failure.py\0",
            data + b"M\0scripts/ci_calibration/kernel.py\0",
            data + b"M\0scripts/validation_ownership/reporter.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_corrected_report_inventory(changed)
        rows = data.split(b"\0")
        reversed_rows = b"".join(kind + b"\0" + name + b"\0"
                                 for kind, name in reversed(list(zip(rows[:-1:2], rows[1:-1:2]))))
        supervisor.validate_corrected_report_inventory(reversed_rows)

    def test_corrected_source_binding_uses_actual_diff_and_rejects_spent_source_and_events(self):
        self.assertIsNotNone(CORRECTED_SOURCE_INPUTS, "requires the inspected corrected-source runner")
        inputs = CORRECTED_SOURCE_INPUTS
        self.assertEqual((inputs.contract["source"], inputs.contract["base"]), (policy.GRAPH, policy.BASE))
        changes = policy.changed_path_set(inputs.diff)
        actual = policy.changed_path_binding(changes)
        self.assertEqual(actual["count"], inputs.contract["changed_paths"]["count"])
        self.assertEqual(actual["added"], inputs.contract["changed_paths"]["A"])
        self.assertEqual(actual["modified"], inputs.contract["changed_paths"]["M"])
        self.assertEqual(actual["deleted"], inputs.contract["changed_paths"].get("D", 0))
        self.assertEqual(actual["type_changed"], inputs.contract["changed_paths"].get("T", 0))
        scope = policy.validate_event(self.event(), **self.authorization())
        scope.update(changed_paths=actual, tracked_paths=inputs.contract["tracked_paths"])
        binding = policy.report_binding(scope)
        self.assertEqual(policy.validate_report_binding(dict(reversed(tuple(binding.items())))), binding)
        with mock.patch.object(policy, "GRAPH", policy.LOCALIZATION_SOURCE):
            previous = policy.changed_path_binding(policy.changed_path_set(inputs.previous_diff))
        self.assertNotEqual(actual["sha256"], previous["sha256"])
        for field, value in (
            ("source_revision", policy.LOCALIZATION_SOURCE), ("base_revision", policy.GRAPH),
            ("source_phases", False), ("lifecycle", False),
        ):
            with self.subTest(field=field), self.assertRaises(policy.GuardError):
                policy.validate_report_binding({**binding, field: value})
        error = policy.unavailable_report_error(
            binding, policy.component_secondary_error(RuntimeError("private inert failure")), stage="check",
        )
        self.assertEqual(error["source_locations"]["source_revision"], policy.GRAPH)
        parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES, report_binding=binding, deadline=3700.0)
        parser.feed(policy.encoded({"scope": "12345/report", "kind": "error", "data": error}) + b"\n")
        self.assertTrue(parser.failed)
        self.assertFalse(parser.finished)
        changed = copy.deepcopy(error)
        changed["source_locations"]["source_revision"] = policy.LOCALIZATION_SOURCE
        with self.assertRaises(policy.GuardError):
            policy.validate_report_error(changed, binding)
        for branch in ("calibration/issue-180-full-report-sizing-1", "calibration/issue-180-report-localization-1"):
            with self.subTest(branch=branch), self.assertRaises(policy.GuardError):
                policy.validate_event({**self.event(), "ref": "refs/heads/" + branch}, **self.authorization())

    def test_accounting_workflow_history_cannot_rebind_to_the_new_active_source(self):
        self.assertIsNotNone(CORRECTED_SOURCE_INPUTS, "requires immutable historical workflow inputs")
        inputs = CORRECTED_SOURCE_INPUTS
        supervisor.validate_accounting_workflow(inputs.historical_before, inputs.historical_after)
        altered = inputs.historical_after.replace(
            policy.LOCALIZATION_SOURCE.encode("ascii"), policy.GRAPH.encode("ascii"),
        )
        self.assertNotEqual(altered, inputs.historical_after)
        with self.assertRaises(policy.GuardError):
            supervisor.validate_accounting_workflow(inputs.historical_before, altered)

    def test_python_report_inventory_cannot_edit_sizing2_or_accepted_mechanisms(self):
        data = b"".join(
            (b"A" if name == policy.PYTHON_REPORT_WORKFLOW else b"M") + b"\0" + name.encode() + b"\0"
            for name in sorted(supervisor.PYTHON_REPORT_PATHS)
        )
        supervisor.validate_python_report_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.replace(b"A\0", b"M\0"),
            data.replace(b"M\0", b"A\0", 1), data.split(b"\0", 2)[2],
            data + b"M\0" + policy.CORRECTED_REPORT_WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/worker.py\0",
            data + b"M\0scripts/ci_calibration/observation_failure.py\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/ci_calibration/kernel.py\0",
            data + b"M\0scripts/validation_ownership/make_probe.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_python_report_inventory(changed)
        with self.assertRaises(policy.GuardError):
            supervisor.validate_corrected_report_inventory(data)

    def test_python_corrected_binding_rejects_sizing2_without_changing_retention(self):
        self.assertIsNotNone(CORRECTED_SOURCE_INPUTS, "requires the inspected Python-corrected runner")
        inputs = CORRECTED_SOURCE_INPUTS
        self.assertEqual(inputs.contract["previous_source"], policy.CORRECTED_REPORT_SOURCE)
        current = policy.changed_path_binding(policy.changed_path_set(inputs.diff))
        with mock.patch.object(policy, "GRAPH", policy.CORRECTED_REPORT_SOURCE):
            prior = policy.changed_path_binding(policy.changed_path_set(inputs.python_previous_diff))
        self.assertNotEqual(current["sha256"], prior["sha256"])
        self.assertIsNone(inputs.contract["actual_attempt_path_value"])
        self.assertTrue(inputs.contract["strict_lifecycle_path_guard_preserved"])
        self.assertTrue(inputs.contract["captured_dispatch_image_expression_preserved"])
        with self.assertRaises(policy.GuardError):
            policy.validate_report_binding({**self.binding(), "source_revision": policy.CORRECTED_REPORT_SOURCE})
        with self.assertRaises(policy.GuardError):
            policy.validate_event({**self.event(), "ref": "refs/heads/calibration/issue-180-full-report-sizing-2"},
                                  **self.authorization())
        for failed in (True, None, False):
            with self.subTest(failed=failed):
                phase = self.report_phase()
                report = phase["worker"]["report"]
                error = policy.unavailable_report_error(
                    self.binding(), policy.component_secondary_error(RuntimeError("private inert failure")),
                    stage="check", source_cleanup_failures=0,
                )
                error["cleanup"] = report["cleanup"]
                error["counters"] = None if failed is None else copy.deepcopy(report["counters"])
                if failed is not None:
                    error["counters"]["budget"]["failed"] = failed
                policy.validate_report_error(error, self.binding())
                phase.update(worker=None, returncode=1, report_returned=False, report_completed=False,
                             first_cause={"type": "worker-error", "error": error})
                self.assertIs(supervisor.report_retention(phase), failed is not False)
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_report_phase(phase, self.binding())

    def test_partial_anchor_inventory_and_event_cannot_reopen_spent_sizing3(self):
        data = b"".join(
            (b"A" if name == policy.PARTIAL_ANCHOR_WORKFLOW else b"M") + b"\0" + name.encode() + b"\0"
            for name in sorted(supervisor.PARTIAL_ANCHOR_PATHS)
        )
        supervisor.validate_partial_anchor_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.replace(b"A\0", b"M\0"),
            data.replace(b"M\0", b"A\0", 1), data.split(b"\0", 2)[2],
            data + b"M\0" + policy.PYTHON_REPORT_WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/worker.py\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/ci_calibration/kernel.py\0",
            data + b"M\0scripts/validation_ownership/phase_census.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_partial_anchor_inventory(changed)
        with self.assertRaises(policy.GuardError):
            policy.validate_event({**self.event(), "ref": "refs/heads/calibration/issue-180-full-report-sizing-3"},
                                  **self.authorization())

    def test_traceless_inventory_and_event_cannot_reopen_spent_partial_anchors(self):
        data = b"".join(
            (b"A" if name == policy.TRACELESS_ANCHOR_WORKFLOW else b"M") + b"\0" + name.encode() + b"\0"
            for name in sorted(supervisor.TRACELESS_ANCHOR_PATHS)
        )
        supervisor.validate_traceless_anchor_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.replace(b"A\0", b"M\0"),
            data.replace(b"M\0", b"A\0", 1), data.split(b"\0", 2)[2],
            data + b"M\0" + policy.PARTIAL_ANCHOR_WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/worker.py\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/ci_calibration/kernel.py\0",
            data + b"M\0scripts/validation_ownership/phase_census.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_traceless_anchor_inventory(changed)
        with self.assertRaises(policy.GuardError):
            policy.validate_event({**self.event(), "ref": "refs/heads/calibration/issue-180-report-localization-2"},
                                  **self.authorization())

    def test_original_import_inventory_cannot_reopen_spent_workflows_or_change_source(self):
        data = b"".join(
            (b"A" if name == policy.ORIGINAL_IMPORT_WORKFLOW else b"M") + b"\0" + name.encode() + b"\0"
            for name in sorted(supervisor.ORIGINAL_IMPORT_PATHS)
        )
        supervisor.validate_original_import_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.replace(b"A\0", b"M\0"),
            data.replace(b"M\0", b"A\0", 1), data.split(b"\0", 2)[2],
            data + b"M\0" + policy.TRACELESS_ANCHOR_WORKFLOW.encode() + b"\0",
            data + b"M\0" + policy.PARTIAL_ANCHOR_WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/kernel.py\0",
            data + b"M\0scripts/ci_calibration/entry.py\0",
            data + b"M\0scripts/validation_ownership/graph_report.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_original_import_inventory(changed)
        with self.assertRaises(policy.GuardError):
            policy.validate_event({**self.event(), "ref": "refs/heads/calibration/issue-180-report-localization-3"},
                                  **self.authorization())

    def test_missing_import_restoration_or_release_cannot_qualify_result_or_cleanup(self):
        for field in ("source_imports_restored", "source_imports_released"):
            for value in (None, False, 0, 1, "true"):
                with self.subTest(field=field, value=value):
                    phase = self.report_phase()
                    phase["worker"]["report"]["cleanup"][field] = value
                    with self.assertRaises(policy.GuardError):
                        supervisor.validate_report_phase(phase, self.binding())
                    if value is None or type(value) is bool:
                        self.assertTrue(supervisor.report_retention(phase))
                    else:
                        with self.assertRaises(policy.GuardError):
                            supervisor.report_retention(phase)
        phase = self.report_phase()
        del phase["worker"]["report"]["cleanup"]["source_imports_restored"]
        with self.assertRaises(policy.GuardError):
            supervisor.validate_report_phase(phase, self.binding())

    def test_import_release_inventory_preserves_original_observer_endpoint_and_workflow(self):
        data = b"".join(b"M\0" + path.encode() + b"\0" for path in sorted(supervisor.IMPORT_RELEASE_PATHS))
        supervisor.validate_import_release_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.split(b"\0", 2)[2],
            data.replace(b"M\0", b"A\0", 1), data.replace(b"M\0", b"D\0", 1),
            data + b"M\0" + policy.WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/policy.py\0",
            data + b"M\0scripts/ci_calibration/entry.py\0",
            data + b"M\0scripts/ci_calibration/kernel.py\0",
            data + b"M\0scripts/validation_ownership/graph_report.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_import_release_inventory(changed)
        original = b"".join(
            (b"A" if path == policy.ORIGINAL_IMPORT_WORKFLOW else b"M") + b"\0" + path.encode() + b"\0"
            for path in sorted(supervisor.ORIGINAL_IMPORT_PATHS)
        )
        supervisor.validate_original_import_inventory(original)
        with self.assertRaises(policy.GuardError):
            supervisor.validate_import_release_inventory(original)
        with self.assertRaises(policy.GuardError):
            supervisor.validate_original_import_inventory(data)

    def test_make_context_inventory_preserves_all_old_endpoints_and_the_spent_import_workflow(self):
        data = b"".join(
            (b"A" if name == policy.WORKFLOW else b"M") + b"\0" + name.encode() + b"\0"
            for name in sorted(supervisor.MAKE_CONTEXT_PATHS)
        )
        supervisor.validate_make_context_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.split(b"\0", 2)[2],
            data.replace(b"A\0", b"M\0"), data.replace(b"M\0", b"A\0", 1),
            data + b"M\0" + policy.ORIGINAL_IMPORT_WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/ci_calibration/worker.py\0",
            data + b"M\0scripts/ci_calibration/kernel.py\0",
            data + b"M\0scripts/validation_ownership/graph_probe.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_make_context_inventory(changed)
        with self.assertRaises(policy.GuardError):
            policy.validate_event(
                {**self.event(), "ref": "refs/heads/calibration/issue-180-original-import-localization-1"},
                **self.authorization(),
            )

    def test_make_boundary_inventory_keeps_the_full_context_edge_and_fixed_workflow(self):
        data = b"".join(b"M\0" + path.encode() + b"\0" for path in sorted(supervisor.MAKE_BOUNDARY_PATHS))
        supervisor.validate_make_boundary_inventory(data)
        for changed in (
            b"", data[:-1], data + data, data.split(b"\0", 2)[2],
            data.replace(b"M\0", b"A\0", 1), data.replace(b"M\0", b"D\0", 1),
            data + b"M\0" + policy.WORKFLOW.encode() + b"\0",
            data + b"M\0scripts/ci_calibration/policy.py\0",
            data + b"M\0scripts/ci_calibration/root_stage.py\0",
            data + b"M\0scripts/ci_calibration/worker.py\0",
            data + b"M\0scripts/validation_ownership/graph_probe.py\0",
        ):
            with self.subTest(changed=changed[:64]), self.assertRaises(policy.GuardError):
                supervisor.validate_make_boundary_inventory(changed)
        original = b"".join(
            (b"A" if name == policy.WORKFLOW else b"M") + b"\0" + name.encode() + b"\0"
            for name in sorted(supervisor.MAKE_CONTEXT_PATHS)
        )
        supervisor.validate_make_context_inventory(original)
        with self.assertRaises(policy.GuardError):
            supervisor.validate_make_boundary_inventory(original)
        with self.assertRaises(policy.GuardError):
            supervisor.validate_make_context_inventory(data)

    def test_complete_diff_binding_preserves_additions_deletions_and_both_rename_sides(self):
        changes = self.changes()
        binding = policy.changed_path_binding(changes)
        self.assertEqual((binding["count"], binding["added"], binding["modified"], binding["deleted"]), (3, 1, 1, 1))
        self.assertEqual(policy.changed_path_binding(dict(reversed(list(changes.items())))), binding)
        for different in (
            {key: value for key, value in changes.items() if value != "D"},
            {**changes, "include/removed.h": "M"},
            {**changes, "other.c": "A"},
        ):
            self.assertNotEqual(policy.changed_path_binding(different), binding)
        for bad in (
            b"", b"M\0a", b"M\0a\0M\0a\0", b"R100\0old\0new\0", b"C100\0a\0b\0",
            b"D\0../outside\0", b"A\0/absolute\0", b"M\0bad\\path\0", b"M\0bad\npath\0",
            b"M\0\xff\0", b"U\0unmerged\0",
        ):
            with self.subTest(bad=bad), self.assertRaises(policy.GuardError):
                policy.changed_path_set(bad)
        self.assertEqual(policy.changed_path_binding(policy.changed_path_set(b"T\0mode-change\0"))["type_changed"], 1)

    def test_real_shape_projection_requires_nonempty_authority_oracle_and_all_path_sides(self):
        budget = self.budget()
        budget.plan(2)
        budget.runs = 28
        budget.charge("control", 104697218)
        budget.closed = True
        session = self.session(budget)
        counters = policy.counter_snapshot(budget, session)
        raw = self.raw_report(budget, session)
        expected = policy.summarize_report(raw, self.changes(), counters, self.binding())
        self.assertEqual((expected["resolved_paths"], expected["current_paths"], expected["base_paths"]), (3, 2, 1))
        self.assertEqual((expected["authority_evidence"], expected["oracle_probes"], expected["lifecycle_cases"]), (3, 2, 3))
        self.assertIsNone(expected["source_phase_counts"])
        self.assertNotIn(b"private", policy.encoded(expected))
        neutral = json.loads(json.dumps(raw, sort_keys=True))
        self.assertEqual(policy.summarize_report(neutral, self.changes(), counters, self.binding()), expected)
        mutations = (
            lambda x: x["policy"].update(narrowing_authorized=True),
            lambda x: x["coverage"].update(tracked_paths=99, owned_paths=97),
            lambda x: x["coverage"].update(owned_paths=0, fail_closed_exclusions=100),
            lambda x: x["coverage"].update(path_rules=False),
            lambda x: x["resolutions"].pop(),
            lambda x: x["resolutions"].append(copy.deepcopy(x["resolutions"][0])),
            lambda x: x["resolutions"][1].update(admission="exact-ownership-rule"),
            lambda x: x["resolutions"][0].update(admission="selected-base-tree"),
            lambda x: x["resolutions"][0].update(owners=[]),
            lambda x: x["selected_gates"].clear(),
            lambda x: x["selected_gates"][0]["reasons"][0].update(path="foreign.c"),
            lambda x: x["selected_gates"].append(copy.deepcopy(x["selected_gates"][0])),
            lambda x: x["measurement"].update(probe_count=0, probes=[]),
            lambda x: x["measurement"].update(false_positive_selections=1),
            lambda x: x["measurement"].update(false_negative_selections=True),
            lambda x: x["measurement"]["probes"].pop(),
            lambda x: x["measurement"]["probes"][0]["owners"].clear(),
            lambda x: x["measurement"].update(oracle_seal=None),
            lambda x: x["seals"].pop("resolved_edges"),
            lambda x: x["artifact"]["executable_lifecycle"].pop(),
            lambda x: x["artifact"]["executable_lifecycle"][0].update(removal="pass"),
            lambda x: x["artifact"]["executable_lifecycle"][1].update(restoration="fail"),
            lambda x: x["artifact"]["executable_lifecycle"][0].update(verified_routes=["consumer"]),
            lambda x: x["artifact"]["executable_lifecycle"][1].update(proof_id="proof-0"),
            lambda x: x["artifact"]["executable_lifecycle"][1].update(trigger_type="artifact_checkpoint"),
            lambda x: x["review_invalidation"].update(reason="comparison-not-requested"),
            lambda x: x["review_invalidation"].update(changed_edge_ids=[]),
            lambda x: x["execution"].update(base_revision=None),
            lambda x: x["execution"].update(revision="b" * 40),
            lambda x: x["execution"].update(runs=True),
            lambda x: x["execution"].update(processes=0),
            lambda x: x["execution"]["bytes"].update(control=0),
            lambda x: x.update(raw_source="private"),
        )
        for index, mutate in enumerate(mutations):
            altered = copy.deepcopy(raw)
            mutate(altered)
            with self.subTest(index=index), self.assertRaises(policy.GuardError):
                policy.summarize_report(altered, self.changes(), counters, self.binding())

    def test_closed_report_stream_rejects_foreign_replay_partial_and_failed_success(self):
        binding = self.binding()
        start = {
            "binding": binding, "limits": policy.profile_manifest(policy.ORIGINAL_LIMITS, observation_count=32768),
            "deadline": 3700.0, "check_attempts": 0,
        }
        result = self.report_phase()["worker"]
        def frame(kind, value):
            return policy.encoded({"scope": "12345/report", "kind": kind, "data": value}) + b"\n"
        def parser():
            value = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES, report_binding=binding, deadline=3700.0)
            value.feed(frame("ready", {}))
            return value
        stream = parser()
        stream.feed(frame("report-start", start))
        encoded = frame("result", result)
        self.assertFalse(stream.feed(encoded[:-1]))
        self.assertFalse(stream.finished)
        self.assertTrue(stream.buffer)
        stream.feed(encoded[-1:])
        self.assertTrue(stream.finished)
        with self.assertRaises(policy.GuardError):
            stream.feed(encoded)
        for bad in (
            {**result, "extra": "private"},
            {**result, "component_attempts": 1},
            {**result, "report": {**result["report"], "serialized_bytes": 0}},
        ):
            value = parser()
            value.feed(frame("report-start", start))
            with self.assertRaises(policy.GuardError):
                value.feed(frame("result", bad))
        value = parser()
        with self.assertRaises(policy.GuardError):
            value.feed(frame("result", result))
        value.feed(frame("report-start", start))
        with self.assertRaises(policy.GuardError):
            value.feed(frame("report-start", start))
        replay = copy.deepcopy(result)
        replay["report"]["binding"]["run_id"] = "12346"
        with self.assertRaises(policy.GuardError):
            value.feed(frame("result", replay))
        failed = {
            "binding": binding, "stage": "check", "error": policy.component_error_record(RuntimeError("private")),
            "states": {**dict.fromkeys(policy.REPORT_STATES, 0), "completed": False},
            "cleanup": None, "counters": None, "accounting": None, "secondary": [], "source_cleanup_failures": 0,
            "summary": None, "serialized_bytes": None,
            "source_locations": observation_failure.location_unavailable("binding-not-ready"),
            "observation_failure": observation_failure.unavailable("binding-not-ready"),
            "budget_admission": observation_failure.unavailable("binding-not-ready"),
        }
        value.feed(frame("error", failed))
        with self.assertRaises(policy.GuardError):
            value.feed(encoded)
        for key in ("error", "counters", "secondary", "budget_admission", "cleanup"):
            bad = {**failed, key: {"raw": "private"}}
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                policy.validate_report_error(bad, binding)
        for fields in ({"serialized_bytes": 10}, {"summary": result["report"]["summary"]}):
            with self.assertRaises(policy.GuardError):
                policy.validate_report_error({**failed, **fields}, binding)

    def test_original_cap_stricter_inputs_and_exact_retained_boundaries(self):
        for name, value in policy.ORIGINAL_LIMITS.items():
            if value is None:
                continue
            with self.subTest(name=name):
                budgeting.Limits(**{name: value})
                budgeting.Limits(**{name: value - 1})
                with self.assertRaises(budgeting.MakeProbeError):
                    budgeting.Limits(**{name: value + 1})
        budget = self.budget()
        budget.charge("control", 104697218)
        normal = budgeting.ProbeBudget()
        normal.started = 100.0
        with self.assertRaises(budgeting.MakeProbeError):
            normal.charge("control", 104697218)
        for category in policy.BYTE_CATEGORIES:
            maximum = policy.diagnostic_limit(category + "_bytes")
            for amount, request, succeeds in ((maximum - 1, 1, True), (maximum, 1, False)):
                issued = self.budget()
                if category == "pending":
                    issued.bytes[category] = amount
                else:
                    issued.charge(category, amount)
                if succeeds:
                    issued.charge(category, request)
                    self.assertEqual(issued.bytes[category], maximum)
                else:
                    with self.assertRaises(budgeting.MakeProbeError):
                        issued.charge(category, request)
                    self.assertEqual(issued.bytes[category], maximum)
        for name, cap in (("memory_peak", "address_space_bytes"), ("pending_commands_peak", "pending"),
                          ("live_process_peak", "processes"), ("files_created", "created_files")):
            value = self.report_result()["counters"]
            value["session"][name] = policy.ORIGINAL_LIMITS[cap]
            policy.validate_component_counters(value, complete=True)
            value["session"][name] += 1
            with self.assertRaises(policy.GuardError):
                policy.validate_component_counters(value, complete=True)

    def test_pre_kill_guard_restoration_and_neutral_refactor_are_behavioral_controls(self):
        bad = self.report_phase()
        bad["empty_before_outer_cleanup"] = False
        functions = {
            node.name: node for node in SUPERVISOR_AST.body if isinstance(node, ast.FunctionDef)
        }
        class RemoveObservation(ast.NodeTransformer):
            def visit_Compare(self, node):
                node = self.generic_visit(node)
                if isinstance(node.left, ast.Call) and any(
                    isinstance(arg, ast.Constant) and arg.value == "empty_before_outer_cleanup"
                    for arg in node.left.args
                ):
                    return ast.copy_location(ast.Constant(value=False), node)
                return node
            def visit_Tuple(self, node):
                node.elts = [element for element in node.elts if not (
                    isinstance(element, ast.Constant) and element.value == "empty_before_outer_cleanup"
                )]
                return self.generic_visit(node)
        class RenameLocal(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id == "result":
                    node.id = "observed"
                return node
            def visit_arg(self, node):
                if node.arg == "result":
                    node.arg = "observed"
                return node
        def variant(name, transform):
            tree = ast.Module(body=[transform.visit(copy.deepcopy(functions[name]))], type_ignores=[])
            ast.fix_missing_locations(tree)
            namespace = dict(vars(supervisor))
            exec(compile(tree, "<inert-security-contract-mutation>", "exec"), namespace)
            return namespace[name]
        def phase_oracle():
            with self.assertRaises(policy.GuardError):
                supervisor.validate_report_phase(bad, self.binding())
        def retention_oracle():
            self.assertTrue(supervisor.report_retention(bad))
        for name, oracle in (("validate_report_phase", phase_oracle), ("report_retention", retention_oracle)):
            oracle()
            with mock.patch.object(supervisor, name, variant(name, RemoveObservation())), self.assertRaises(AssertionError):
                oracle()
            with mock.patch.object(supervisor, name, variant(name, RenameLocal())):
                oracle()
            oracle()
        self.report_result()
        with mock.patch.object(policy, "POLICY_SENTINEL", policy.ORIGINAL_LIMITS["control_bytes"]), \
             self.assertRaises((budgeting.MakeProbeError, policy.GuardError)):
            self.report_result()
        self.report_result()

    def test_artifact_exact_boundaries_and_five_file_inventory_are_not_report_output(self):
        owner = object.__new__(supervisor.Artifacts)
        owner.root = Path("/owned/artifacts")
        sizes = {}
        def info(path):
            return SimpleNamespace(st_size=sizes[path.name], st_mode=stat.S_IFREG | 0o644)
        with mock.patch.object(Path, "exists", side_effect=lambda path: path.name in sizes, autospec=True), \
             mock.patch.object(Path, "is_symlink", return_value=False), \
             mock.patch.object(Path, "stat", side_effect=info, autospec=True), \
             mock.patch.object(Path, "lstat", side_effect=info, autospec=True), \
             mock.patch.object(Path, "iterdir", side_effect=lambda: [owner.root / name for name in sizes]):
            for name in policy.ARTIFACT_NAMES:
                maximum = policy.METRICS_BYTES if name == "metrics.jsonl" else (
                    policy.PROGRESS_BYTES if name == "progress.jsonl" else policy.OUTPUT_BYTES
                )
                owner._capacity(name, maximum, False)
                with self.assertRaises(policy.GuardError):
                    owner._capacity(name, maximum + 1, False)
            sizes["scope.json"] = policy.OUTPUT_BYTES
            owner._capacity("result.json", policy.OUTPUT_BYTES, False)
            sizes["metrics.jsonl"] = 1
            with self.assertRaises(policy.GuardError):
                owner._capacity("result.json", policy.OUTPUT_BYTES, False)
            with self.assertRaises(policy.GuardError):
                owner._capacity("raw-report.json", 1, False)

    def test_secondary_error_metadata_is_bounded_and_explicit_when_unavailable(self):
        value = policy.component_secondary_error(OSError(5, "private source"))
        self.assertEqual(value["chain"], [{"type": "OSError", "errno": 5}])
        self.assertIs(policy.validate_component_error_record(value), value)
        with mock.patch.object(policy, "component_error_record", side_effect=ValueError("private formatter")):
            unavailable = policy.component_secondary_error(RuntimeError("original secondary"))
        self.assertEqual(unavailable, {"chain": [], "complete": False, "reason": "secondary-format-failed"})
        policy.validate_component_error_record(unavailable)
        for bad in (
            {**value, "message": "private"}, {**value, "chain": [{"type": "OSError", "errno": True}]},
            {**value, "chain": [{"type": "private value", "errno": None}]},
            {**value, "complete": False, "reason": "invented"}, {**value, "chain": []},
        ):
            with self.assertRaises(policy.GuardError):
                policy.validate_component_error_record(bad)

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


class AccountingControls(Inert):
    def test_factory_distinguishes_omission_from_every_explicit_limits_object(self):
        for limits in (
            budgeting.Limits(), budgeting.Limits(control_bytes=policy.ORIGINAL_LIMITS["control_bytes"]),
            budgeting.Limits(control_bytes=7, total_bytes=11),
            budgeting.Limits(entries=8), budgeting.Limits(entries=8, observations=None),
            budgeting.Limits(entries=8, observations=16), budgeting.Limits(seconds=30),
        ):
            with self.subTest(limits=dataclasses.asdict(limits)):
                budget, actual, _, _ = worker.calibration_budget(
                    budgeting.Limits, budgeting.ProbeBudget, 3700.0, limits=limits,
                )
                self.assertIs(actual, limits)
                self.assertIs(budget.limits, limits)
                self.assertEqual(budget.started, 100.0)
                self.assertEqual(budget.deadline, 100.0 + limits.seconds)
                for name in policy.RELAXED:
                    self.assertEqual(budget.cumulative_limit(name), limits.observation_count if name == "observations"
                                     else getattr(limits, name))
        for invalid in (None, False, {}, object()):
            with self.assertRaises(policy.GuardError):
                worker.calibration_budget(budgeting.Limits, budgeting.ProbeBudget, 3700.0, limits=invalid)
        with mock.patch.object(time, "monotonic", return_value=131.0), self.assertRaises(policy.GuardError):
            worker.calibration_budget(budgeting.Limits, budgeting.ProbeBudget, 3700.0, limits=budgeting.Limits(seconds=30))

    def test_constructor_injection_does_not_rebind_captured_default_factories(self):
        original = budgeting.Limits
        factory = budgeting.ProbeBudget.__dataclass_fields__["limits"].default_factory
        self.assertIs(factory, original)
        replacement = mock.Mock(side_effect=AssertionError("rebound alias is not the captured constructor"))
        with mock.patch.object(budgeting, "Limits", replacement):
            ordinary = budgeting.ProbeBudget()
            budget, limits, _, _ = worker.calibration_budget(original, budgeting.ProbeBudget, 3700.0)
        replacement.assert_not_called()
        self.assertIs(type(ordinary.limits), original)
        self.assertIs(type(limits), original)
        self.assertIs(budget.limits, limits)
        self.assertIs(budgeting.ProbeBudget.__dataclass_fields__["limits"].default_factory, factory)
        self.assertEqual(ordinary.cumulative_limit("control_bytes"), policy.ORIGINAL_LIMITS["control_bytes"])
        self.assertEqual(budget.cumulative_limit("control_bytes"), policy.POLICY_SENTINEL)
        for name, method in vars(budgeting.ProbeBudget).items():
            if name.startswith("__") or name == "cumulative_limit" or not (
                callable(method) or isinstance(method, (staticmethod, classmethod, property))
            ):
                continue
            self.assertIs(next(base.__dict__[name] for base in type(budget).__mro__ if name in base.__dict__), method)

    def test_all_cumulative_byte_thresholds_keep_real_charges_and_first_observed_crossings(self):
        for category in policy.BYTE_CATEGORIES:
            with self.subTest(category=category):
                cap = policy.ORIGINAL_LIMITS[category + "_bytes"]
                ordinary = budgeting.ProbeBudget()
                ordinary.started = 100.0
                ordinary.charge(category, cap)
                with self.assertRaises(budgeting.MakeProbeError):
                    ordinary.charge(category, 1)
                budget = self.budget()
                tracker = policy.AccountingRegistry(budget)
                budget.charge(category, cap)
                initial = tracker.observe(policy.counter_snapshot(budget), 0)
                self.assertIsNone(initial["records"][category + "_bytes"]["first_observed"])
                budget.charge(category, 1)
                crossed = tracker.observe(policy.counter_snapshot(budget), 1)
                row = crossed["records"][category + "_bytes"]
                self.assertEqual(row["latest"], cap + 1)
                self.assertTrue(row["would_exceed"])
                self.assertEqual(row["first_observed"], {"sequence": 2, "elapsed_seconds": 1, "value": cap + 1})
                budget.charge(category, 2)
                latest = tracker.observe(policy.counter_snapshot(budget), 2)
                self.assertEqual(latest["records"][category + "_bytes"]["first_observed"], row["first_observed"])
                self.assertEqual(budget.bytes[category], cap + 3)
                self.assertEqual(getattr(budget.limits, category + "_bytes"), cap)
        budget = self.budget()
        cap = policy.ORIGINAL_LIMITS["total_bytes"]
        budget.charge("snapshot", cap)
        tracker = policy.AccountingRegistry(budget)
        tracker.observe(policy.counter_snapshot(budget), 0)
        budget.charge("output", 1)
        final = tracker.observe(policy.counter_snapshot(budget), 1)
        self.assertEqual(final["records"]["total_bytes"]["latest"], cap + 1)
        self.assertTrue(final["records"]["total_bytes"]["would_exceed"])
        self.assertEqual(sum(budget.bytes.values()), cap + 1)

    def test_original_record_plan_pending_stream_and_clock_guards_still_stop_actual_apis(self):
        budget = self.budget()
        budget.charge("pending", policy.MIB)
        budget.charge("pending", policy.MIB)
        self.assertEqual(budget.bytes["pending"], 2 * policy.MIB)
        with self.assertRaises(budgeting.MakeProbeError):
            budget.charge("pending", policy.MIB + 1)
        budget = self.budget()
        budget.admit_planned_state(policy.MIB)
        with self.assertRaises(budgeting.MakeProbeError):
            budget.admit_planned_state(1)
        budget = self.budget()
        budget.plan(policy.ORIGINAL_LIMITS["states"])
        budget.plan(1)
        self.assertEqual(budget.states, policy.ORIGINAL_LIMITS["states"] + 1)
        with self.assertRaises(budgeting.MakeProbeError):
            budget.plan(1, pending=budget.limits.pending + 1)
        for category in ("snapshot", "output"):
            budget = self.budget()
            budget.runs = policy.ORIGINAL_LIMITS["runs"]
            with mock.patch.object(subprocess, "Popen") as process, self.assertRaises(budgeting.MakeProbeError):
                budget.run(["/inert"], env={}, category=category,
                           output_limit=getattr(budget.limits, category + "_bytes") + 1)
            process.assert_not_called()
            self.assertEqual(budget.runs, policy.ORIGINAL_LIMITS["runs"] + 1)
        budget = self.budget()
        before = budget.started, budget.deadline
        with mock.patch.object(time, "monotonic", return_value=3700.0), self.assertRaises(budgeting.MakeProbeError):
            budget.charge("control", 0)
        self.assertEqual((budget.started, budget.deadline), before)
        self.assertTrue(budget.failed)

    def test_actual_source_caps_and_resumption_keep_original_units_after_cumulative_crossing(self):
        self.assertIsNotNone(QUOTA_MODEL, "requires the inspected accounting runner")
        budget = self.budget()
        budget.bytes.update(
            control=budget.limits.control_bytes + 1,
            event=budget.limits.event_bytes + 1, sandbox=budget.limits.sandbox_bytes + 1,
        )
        prior = {"processes_used": budget.limits.descendants + 1, "syscalls_used": budget.limits.syscalls + 1,
                 "observations_used": budget.limits.observation_count + 1}
        session, config, _, controls = QUOTA_MODEL.native_model(budget, prior=prior, retain_controls=True)
        for key, expected in (
            ("descendant_limit", budget.limits.descendants), ("syscall_limit", budget.limits.syscalls),
            ("write_limit", budget.limits.sandbox_bytes), ("observation_count", budget.limits.observation_count),
            ("file_limit", budget.limits.file_bytes), ("observation_limit", budget.limits.file_bytes),
            ("creation_limit", budget.limits.created_files), ("process_limit", budget.limits.processes),
            ("memory_limit", budget.limits.address_space_bytes),
        ):
            self.assertEqual(config[key], expected)
        before = dict(budget.bytes)
        grants = controls["resume"]()
        self.assertEqual(grants, {name: config[name] for name in grants})
        self.assertEqual(budget.bytes, before)
        self.assertEqual((budget.started, budget.deadline), (100.0, 3700.0))
        for field, number in (
            ("processes", budget.limits.descendants + 1), ("syscalls", budget.limits.syscalls + 1),
            ("written_bytes", budget.limits.sandbox_bytes + 1),
            ("observations", budget.limits.observation_count + 1),
            ("observation_bytes", budget.limits.file_bytes + 1),
            ("created_files", budget.limits.created_files + 1),
            ("live_process_peak", budget.limits.processes + 1),
            ("memory_peak", budget.limits.address_space_bytes + 1),
        ):
            with self.subTest(field=field), self.assertRaises(budgeting.MakeProbeError):
                QUOTA_MODEL.native_model(self.budget(), counters={field: number})

    def test_actual_variants_cohort_does_not_use_the_cumulative_state_sentinel(self):
        self.assertIsNotNone(SOURCE_PROBING, "requires the inspected accounting runner")
        for size in (2, policy.ORIGINAL_LIMITS["states"] + 1):
            budget = self.budget()
            budget.states = policy.ORIGINAL_LIMITS["states"] + 10
            calls = []
            session = SimpleNamespace(budget=budget, make=lambda target, **kw: calls.append(kw) or kw)
            if size == 2:
                result = SOURCE_PROBING.ProbeSession.variants.__wrapped__(session, "all", [()] * size)
                self.assertEqual(len(result), 2)
            else:
                with self.assertRaises(budgeting.MakeProbeError):
                    SOURCE_PROBING.ProbeSession.variants.__wrapped__(session, "all", [()] * size)
                self.assertEqual(calls, [])

    def test_bounded_registry_final_observation_and_full_duration_progress_fit_existing_artifacts(self):
        budget = self.budget()
        budget.plan(1)
        budget.runs = 1
        budget.session_started = True
        session = self.session(budget)
        registry = policy.AccountingRegistry(budget)
        total = 0
        for number in range(1, 720):
            budget.charge("control", 65536)
            value = registry.observe(policy.counter_snapshot(budget, session), number * 5)
            progress = {
                "phase": "public-report", "counters": policy.counter_snapshot(budget, session),
                "accounting": policy.accounting_progress(value),
                "semantics": "Observed cumulative counters and funded VM peaks; not an atomic grant or physical RSS.",
            }
            policy.validate_report_progress(progress)
            total += len(policy.encoded({"scope": "12345/report", "kind": "progress", "data": progress})) + 1
            self.assertEqual(len(registry.records), len(policy.ACCOUNTING_COUNTERS))
        budget.close()
        final = registry.observe(policy.counter_snapshot(budget, session), 3600, final=True)
        policy.validate_accounting(final, policy.counter_snapshot(budget, session), final=True, fixed=True)
        self.assertEqual(final["records"]["control_bytes"]["first_observed"]["sequence"], 513)
        self.assertLess(total + len(policy.encoded(final)), policy.PROGRESS_BYTES)
        self.assertLess(len(policy.encoded(final)), policy.ERROR_BYTES)
        self.assertEqual(final["records"]["total_bytes"]["latest"], sum(budget.bytes.values()))
        with self.assertRaises(policy.GuardError):
            registry.observe(policy.counter_snapshot(budget, session), 3600, final=True)

    def test_registry_fails_closed_on_missing_decreasing_malformed_overflow_or_changed_snapshots(self):
        for fault in ("unknown", "negative", "bool", "float", "infinite", "nan", "overflow",
                      "decrease", "missing-session", "quota", "alias", "time", "clock"):
            with self.subTest(fault=fault):
                budget = self.budget()
                session = self.session(budget)
                registry = policy.AccountingRegistry(budget)
                budget.charge("control", 10)
                registry.observe(policy.counter_snapshot(budget, session), 0)
                data = policy.counter_snapshot(budget, session)
                if fault == "unknown":
                    budget.bytes["foreign"] = 1
                elif fault in ("negative", "bool", "float", "infinite", "nan", "overflow"):
                    budget.bytes["control"] = {
                        "negative": -1, "bool": True, "float": 11.0, "infinite": float("inf"),
                        "nan": float("nan"), "overflow": policy.POLICY_SENTINEL + 1,
                    }[fault]
                elif fault == "decrease":
                    budget.bytes["control"] = 9
                elif fault == "quota":
                    data["budget"]["quotas"]["runs"]["diagnostic"] -= 1
                elif fault == "alias":
                    data["budget"]["observation_alias"]["declared"] = True
                elif fault == "clock":
                    budget.started += 1
                elapsed = -1 if fault == "time" else 1
                with self.assertRaises(policy.GuardError):
                    registry.observe(
                        data if fault in ("quota", "alias") else policy.counter_snapshot(
                            budget, None if fault == "missing-session" else session,
                        ), elapsed,
                    )
        for session in (None, self.session(self.budget())):
            budget = self.budget()
            with self.assertRaises(policy.GuardError):
                policy.AccountingRegistry(budget).observe(policy.counter_snapshot(budget, session), 1, final=True)

    def test_explicit_alias_quotas_and_unknown_ledger_keys_are_observed_without_normalization(self):
        limits = budgeting.Limits(entries=8, observations=None, control_bytes=7)
        budget = worker.calibration_budget(budgeting.Limits, budgeting.ProbeBudget, 3700, limits=limits)[0]
        snapshot = policy.counter_snapshot(budget)
        self.assertEqual(snapshot["budget"]["observation_alias"], {"declared": None, "entries": 8, "effective": 8})
        self.assertEqual(snapshot["budget"]["quotas"]["observations"], {"original": 8, "diagnostic": 8})
        budget.charge("control", 7)
        with self.assertRaises(budgeting.MakeProbeError):
            budget.charge("control", 1)
        self.assertEqual(budget.bytes, {"control": 7})
        unusual = self.budget()
        unusual.charge("file", 1)
        self.assertEqual(unusual.bytes, {"file": 1})
        with self.assertRaises(policy.GuardError):
            policy.counter_snapshot(unusual)

    def test_missing_final_or_replayed_crossing_cannot_become_report_completion(self):
        valid = self.report_phase()["worker"]
        for mutation in ("missing", "not-final", "unknown", "value", "crossing"):
            value = copy.deepcopy(valid)
            if mutation == "missing":
                value["counters"]["accounting"] = None
            elif mutation == "not-final":
                value["counters"]["accounting"]["final"] = False
            elif mutation == "unknown":
                value["counters"]["accounting"]["records"]["unknown"] = {}
            elif mutation == "value":
                value["counters"]["accounting"]["records"]["control_bytes"]["latest"] -= 1
            else:
                value["counters"]["accounting"]["records"]["control_bytes"]["first_observed"] = None
            with self.subTest(mutation=mutation), self.assertRaises(policy.GuardError):
                policy.validate_report_worker(value, self.binding(), 3700.0)

    def test_actual_sampler_and_protocol_reject_counter_replay_decrease_or_missing_crossing(self):
        budget = self.budget()
        sampler = worker.Sampler("12345/report")
        sampler.budget, sampler.session, sampler.phase = budget, self.session(budget), "public-report"
        budget.charge("control", budget.limits.control_bytes)
        initial = sampler.snapshot()
        initial["accounting"] = policy.accounting_progress(initial["accounting"])
        budget.charge("control", 1)
        with mock.patch.object(time, "monotonic", return_value=101.0):
            crossing = sampler.snapshot()
        crossing["accounting"] = policy.accounting_progress(crossing["accounting"])
        def parser():
            value = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES,
                                        report_binding=self.binding(), deadline=3700.0)
            value.feed(policy.encoded({"scope": "12345/report", "kind": "ready", "data": {}}) + b"\n")
            value.feed(policy.encoded({"scope": "12345/report", "kind": "progress", "data": initial}) + b"\n")
            return value
        frame = policy.encoded({"scope": "12345/report", "kind": "progress", "data": crossing}) + b"\n"
        value = parser()
        value.feed(frame)
        self.assertEqual(set(value.accounting_crossings), {"control_bytes"})
        with self.assertRaises(policy.GuardError):
            value.feed(frame)
        for fault in ("missing", "decrease", "quota", "time"):
            changed = copy.deepcopy(crossing)
            if fault == "missing":
                changed["accounting"]["first_observed"].clear()
            elif fault == "decrease":
                budget.bytes["control"] = budget.limits.control_bytes - 1
                changed["counters"] = policy.counter_snapshot(budget, sampler.session)
                changed["accounting"]["first_observed"].clear()
            elif fault == "quota":
                changed["counters"]["budget"]["quotas"]["runs"]["diagnostic"] = 4096
            else:
                changed["accounting"]["elapsed_seconds"] = -1
            with self.subTest(fault=fault), self.assertRaises(policy.GuardError):
                parser().feed(policy.encoded({"scope": "12345/report", "kind": "progress", "data": changed}) + b"\n")
        neutral = json.loads(json.dumps(crossing, sort_keys=True))
        parser().feed(policy.encoded({"scope": "12345/report", "kind": "progress", "data": neutral}) + b"\n")

    def test_old_quota_and_raised_limits_restorations_fail_the_new_behavior_oracle(self):
        self.assertIsNotNone(OLD_BUDGET_CHARGE, "requires the inspected accounting runner")
        def oracle():
            budget = self.budget()
            budget.charge("snapshot", policy.ORIGINAL_LIMITS["snapshot_bytes"] + 1)
            self.assertEqual(budget.bytes["snapshot"], policy.ORIGINAL_LIMITS["snapshot_bytes"] + 1)
        oracle()
        with mock.patch.object(budgeting.ProbeBudget, "charge", OLD_BUDGET_CHARGE), self.assertRaises(budgeting.MakeProbeError):
            oracle()
        self.assertIsNotNone(OLD_CALIBRATION_FACTORY)
        with self.assertRaises((policy.GuardError, budgeting.MakeProbeError, AssertionError)):
            budget, limits, _, _ = OLD_CALIBRATION_FACTORY(budgeting.Limits, budgeting.ProbeBudget, 3700.0)
            self.assertIs(type(limits), budgeting.Limits)
            self.assertEqual(dataclasses.asdict(limits), policy.ORIGINAL_LIMITS)
        oracle()


class TelemetryControls(Inert):
    def issued(self):
        budget = self.budget()
        budget.plan(1)
        budget.runs = 28
        budget.session_started = True
        budget.charge("control", 104697218)
        budget.close()
        return budget, self.session(budget)

    def frame(self, kind, value):
        return policy.encoded({"scope": "12345/report", "kind": kind, "data": value}) + b"\n"

    def receiver(self, cls=None):
        parser = (supervisor.Protocol if cls is None else cls)(
            "12345/report", policy.OUTPUT_BYTES, report_binding=self.binding(), deadline=3700.0,
        )
        parser.feed(self.frame("ready", {}))
        parser.feed(self.frame("report-start", {
            "binding": self.binding(), "limits": policy.profile_manifest(policy.ORIGINAL_LIMITS, observation_count=32768),
            "deadline": 3700.0, "check_attempts": 0,
        }))
        return parser

    def progress(self, sample):
        return {**sample, "accounting": None if sample["accounting"] is None else
                policy.accounting_progress(sample["accounting"])}

    def result(self, final):
        result = self.report_phase()["worker"]
        self.assertEqual(result["report"]["counters"], final["counters"])
        result["counters"] = final
        policy.validate_report_worker(result, self.binding(), 3700.0)
        return result

    def publication(self, boundary):
        issued, session = self.issued()
        reads, captured = {"budget": 0, "session": 0}, []
        class Sample(worker.Sampler):
            def __getattribute__(self, name):
                values = object.__getattribute__(self, "__dict__")
                if name in reads and values.get("armed"):
                    reads[name] += 1
                    if name == "budget" and boundary == "before-budget" and reads[name] == 1:
                        self.budget, self.session = issued, session
                    current = object.__getattribute__(self, name)
                    if name == "budget" and boundary == "after-budget" and reads[name] == 1:
                        self.budget, self.session = issued, session
                    if name == "session" and boundary == "after-session" and reads[name] == 1:
                        self.session = session
                    return current
                return object.__getattribute__(self, name)
        sampler = Sample("12345/report")
        if boundary in {"after-session", "after-populated-counters", "registry", "clock"}:
            sampler.budget = issued
        sampler.armed = True
        original_snapshot, registry = policy.counter_snapshot, policy.AccountingRegistry
        def snapshot(budget, selected=None):
            captured.append((budget, selected))
            value = original_snapshot(budget, selected)
            if boundary == "after-empty-counters":
                sampler.budget, sampler.session = issued, session
            elif boundary == "after-populated-counters":
                sampler.session = session
            return value
        def construct(budget):
            self.assertIs(budget, issued)
            if boundary == "registry":
                sampler.session = session
            return registry(budget)
        def clock():
            if boundary == "clock":
                sampler.session = session
            return 111.0
        wire = io.BytesIO()
        with mock.patch.object(policy, "counter_snapshot", side_effect=snapshot), \
             mock.patch.object(policy, "AccountingRegistry", side_effect=construct), \
             mock.patch.object(time, "monotonic", side_effect=clock), \
             mock.patch.object(sampler.stop, "wait", side_effect=[False, True]), \
             mock.patch.object(kernel, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=wire))):
            sampler.run()
        sampler.armed = False
        return SimpleNamespace(sampler=sampler, budget=issued, session=session, captured=captured, reads=reads, wire=wire.getvalue())

    def test_one_captured_pair_survives_every_initial_publication_read_boundary(self):
        for boundary in ("before-budget", "after-budget", "after-empty-counters", "after-session",
                         "after-populated-counters", "registry", "clock"):
            with self.subTest(boundary=boundary):
                value = self.publication(boundary)
                self.assertIsNone(value.sampler.failure)
                self.assertIsNone(value.sampler.collection_failure)
                self.assertEqual(value.reads["budget"], 1)
                self.assertEqual(value.reads["session"], 0 if value.captured[0][0] is None else 1)
                self.assertEqual(len(value.captured), 1)
                parser = self.receiver()
                initial, = parser.feed(value.wire)
                captured_budget, captured_session = value.captured[0]
                if captured_budget is None:
                    self.assertEqual(initial["data"]["counters"], {"budget": None, "session": None})
                    self.assertIsNone(initial["data"]["accounting"])
                    self.assertIsNone(value.sampler.accounting)
                else:
                    self.assertIs(value.sampler.accounting.budget, captured_budget)
                    self.assertEqual(initial["data"]["counters"], policy.counter_snapshot(captured_budget, captured_session))
                value.sampler.phase = "completed-report"
                with mock.patch.object(time, "monotonic", return_value=111.0):
                    final = value.sampler.snapshot(final=True)
                result = self.result(final)
                parser.feed(self.frame("result", result))
                self.assertTrue(parser.finished)
                self.assertFalse(parser.failed)
                self.assertIsNone(parser.accounting_failure)
                self.assertTrue(supervisor.validate_report_phase(
                    {**self.report_phase(), "worker": result}, self.binding(),
                )["complete_repository_report"])
                self.assertEqual((value.budget.started, value.budget.deadline), (100.0, 3700.0))

    def test_initial_unavailability_is_valid_but_budget_registry_or_session_loss_is_permanent(self):
        for reference in ("budget", "accounting", "session"):
            with self.subTest(reference=reference):
                budget, session = self.issued()
                sampler = worker.Sampler("12345/report")
                initial = sampler.snapshot()
                self.assertEqual(initial["counters"], {"budget": None, "session": None})
                self.assertIsNone(initial["accounting"])
                sampler.budget, sampler.session = budget, session
                populated = sampler.snapshot()
                registry = sampler.accounting
                previous = getattr(sampler, reference)
                setattr(sampler, reference, None)
                with self.assertRaises(policy.GuardError) as missing:
                    sampler.snapshot()
                self.assertIs(sampler.collection_failure, missing.exception)
                self.assertIs(sampler.failure, missing.exception)
                setattr(sampler, reference, previous)
                with self.assertRaises(policy.GuardError) as restored:
                    sampler.snapshot(final=True)
                self.assertIs(restored.exception, missing.exception)
                self.assertIs(sampler.accounting, registry)
                self.assertEqual(populated["accounting"]["sequence"], 1)
                with mock.patch.object(sampler.thread, "join"), \
                     mock.patch.object(sampler.thread, "is_alive", return_value=False):
                    first, stage = worker.finish_report(sampler, budget, None, [])
                self.assertIs(first, missing.exception)
                self.assertEqual(stage, "sampler-close")
                self.assertTrue(budget.closed)

    def test_missing_counter_or_registry_collection_never_recovers_to_a_final_sample(self):
        for fault in ("none-counters", "no-budget", "no-session", "missing-accounting", "none-accounting"):
            with self.subTest(fault=fault):
                budget, session = self.issued()
                sampler = worker.Sampler("12345/report")
                sampler.budget, sampler.session = budget, session
                sampler.snapshot()
                if fault == "none-counters":
                    change = mock.patch.object(policy, "counter_snapshot", return_value=None)
                elif fault == "no-budget":
                    change = mock.patch.object(policy, "counter_snapshot", return_value={"budget": None, "session": None})
                elif fault == "no-session":
                    actual = policy.counter_snapshot(budget)
                    change = mock.patch.object(policy, "counter_snapshot", return_value=actual)
                elif fault == "missing-accounting":
                    change = mock.patch.object(sampler, "accounting", None)
                else:
                    change = mock.patch.object(sampler.accounting, "observe", return_value=None)
                with change:
                    with self.assertRaises((policy.GuardError, TypeError, AttributeError)) as missing:
                        sampler.snapshot()
                with self.assertRaises(BaseException) as restored:
                    sampler.snapshot(final=True)
                self.assertIs(restored.exception, missing.exception)

    def test_receiver_latches_each_missing_combination_and_rejects_restored_phase_success(self):
        budget, session = self.issued()
        sampler = worker.Sampler("12345/report")
        sampler.budget, sampler.session, sampler.phase = budget, session, "public-report"
        with mock.patch.object(time, "monotonic", return_value=111.0):
            initial = self.progress(sampler.snapshot())
            sampler.phase = "completed-report"
            result = self.result(sampler.snapshot(final=True))
        for fault in ("both-null", "accounting-null", "counter-null", "missing-accounting", "missing-counter",
                      "budget-null", "session-null", "accounting-empty"):
            with self.subTest(fault=fault):
                parser = self.receiver()
                parser.feed(self.frame("progress", initial))
                missing = copy.deepcopy(initial)
                if fault == "both-null":
                    missing.update(counters={"budget": None, "session": None}, accounting=None)
                elif fault == "accounting-null":
                    missing["accounting"] = None
                elif fault == "counter-null":
                    missing["counters"] = None
                elif fault == "missing-accounting":
                    del missing["accounting"]
                elif fault == "missing-counter":
                    del missing["counters"]
                elif fault == "budget-null":
                    missing["counters"] = {"budget": None, "session": None}
                elif fault == "session-null":
                    missing["counters"]["session"] = None
                    missing["accounting"] = {**missing["accounting"], "sequence": 2, "first_observed": {}}
                else:
                    missing["accounting"] = {}
                phase = {**self.report_phase(), "worker": None}
                with self.assertRaises(BaseException) as lost:
                    parser.feed(self.frame("progress", missing))
                phase["first_cause"] = {"type": "protocol", "error": policy.component_secondary_error(lost.exception)}
                self.assertIs(parser.accounting_failure, lost.exception)
                with self.assertRaises(BaseException) as restored:
                    parser.feed(self.frame("result", result))
                self.assertIs(restored.exception, lost.exception)
                self.assertFalse(parser.finished)
                self.assertIsNone(parser.report_result)
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_report_phase(phase, self.binding())
                failure = policy.unavailable_report_error(
                    self.binding(), policy.component_secondary_error(lost.exception), stage="counter-publication",
                )
                parser.feed(self.frame("error", failure))
                self.assertTrue(parser.failed)
                self.assertFalse(parser.finished)

    def test_receiver_accepts_only_genuine_initial_unavailability_and_neutral_order(self):
        budget, session = self.issued()
        sampler = worker.Sampler("12345/report")
        parser = self.receiver()
        empty = sampler.snapshot()
        for _ in range(2):
            parser.feed(self.frame("progress", empty))
        sampler.budget, sampler.session, sampler.phase = budget, session, "public-report"
        with mock.patch.object(time, "monotonic", return_value=111.0):
            initial = self.progress(sampler.snapshot())
            sampler.phase = "completed-report"
            result = self.result(sampler.snapshot(final=True))
        initial = json.loads(json.dumps(initial, sort_keys=True))
        parser.feed(self.frame("progress", initial))
        parser.feed(self.frame("result", result))
        self.assertTrue(parser.finished)
        self.assertIsNone(parser.accounting_failure)
        self.assertTrue(supervisor.validate_report_phase(
            {**self.report_phase(), "worker": result}, self.binding(),
        )["complete_repository_report"])

    def test_exact_old_sampler_restoration_recovers_the_false_initial_failure(self):
        self.assertIsNotNone(OLD_TELEMETRY_SNAPSHOT, "requires the inspected telemetry runner")
        self.assertIsNone(self.publication("after-empty-counters").sampler.failure)
        with mock.patch.object(worker.Sampler, "snapshot", OLD_TELEMETRY_SNAPSHOT):
            broken = self.publication("after-empty-counters")
        self.assertIsInstance(broken.sampler.failure, policy.GuardError)
        self.assertTrue(broken.sampler.accounting.failed)
        self.assertEqual(broken.wire, b"")
        self.assertIsNone(self.publication("after-empty-counters").sampler.failure)

    def test_neutral_local_reference_renaming_preserves_coherent_publication(self):
        sampler = next(node for node in WORKER_AST.body if isinstance(node, ast.ClassDef) and node.name == "Sampler")
        original = next(node for node in sampler.body if isinstance(node, ast.FunctionDef) and node.name == "snapshot")
        class Rename(ast.NodeTransformer):
            def visit_Name(self, node):
                node.id = {"budget": "issued", "session": "captured"}.get(node.id, node.id)
                return node
        tree = ast.Module(body=[Rename().visit(copy.deepcopy(original))], type_ignores=[])
        ast.fix_missing_locations(tree)
        namespace = dict(vars(worker))
        exec(compile(tree, "<inert-neutral-sampler>", "exec"), namespace)
        with mock.patch.object(worker.Sampler, "snapshot", namespace["snapshot"]):
            value = self.publication("after-empty-counters")
            self.assertIsNone(value.sampler.failure)
            self.assertIsNone(value.sampler.accounting)
            self.assertEqual(value.reads["budget"], 1)
            self.assertEqual(value.captured, [(None, None)])

    def test_exact_old_sender_and_receiver_restoration_recovers_false_phase_completion(self):
        self.assertIsNotNone(OLD_TELEMETRY_SNAPSHOT)
        self.assertIsNotNone(OLD_TELEMETRY_PROTOCOL)
        budget, session = self.issued()
        sampler = worker.Sampler("12345/report")
        sampler.budget, sampler.session, sampler.phase = budget, session, "public-report"
        with mock.patch.object(worker.Sampler, "snapshot", OLD_TELEMETRY_SNAPSHOT), \
             mock.patch.object(time, "monotonic", return_value=111.0):
            initial = self.progress(sampler.snapshot())
            sampler.budget = None
            missing = sampler.snapshot()
            sampler.budget, sampler.phase = budget, "completed-report"
            result = self.result(sampler.snapshot(final=True))
        old = self.receiver(OLD_TELEMETRY_PROTOCOL)
        for kind, value in (("progress", initial), ("progress", missing), ("result", result)):
            old.feed(self.frame(kind, value))
        self.assertTrue(old.finished)
        self.assertFalse(old.failed)
        self.assertTrue(supervisor.validate_report_phase(
            {**self.report_phase(), "worker": result}, self.binding(),
        )["complete_repository_report"])
        fixed = self.receiver()
        fixed.feed(self.frame("progress", initial))
        with self.assertRaises(policy.GuardError):
            fixed.feed(self.frame("progress", missing))
        with self.assertRaises(policy.GuardError):
            fixed.feed(self.frame("result", result))
        self.assertFalse(fixed.finished)


if __name__ == "__main__":
    unittest.main()
