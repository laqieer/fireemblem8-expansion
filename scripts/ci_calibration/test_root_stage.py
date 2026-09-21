"""Effect-modeled component composition; never import or run the selected native case."""

import builtins
import copy
import dataclasses
import errno
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import unittest
from unittest import mock
from contextlib import ExitStack

from scripts.ci_calibration import kernel, policy, root_stage, supervisor, worker
from scripts.ci_calibration.test_ci_calibration import Inert, budgeting


def observation(fault=None):
    receipts = []
    for index in (1, 2):
        stages = [{"stage": name} for name in policy.STAGES]
        proof = {
            "version": 1, "complete": True,
            "writer": {"completed": {"extent": 128, "sha256": "a" * 64}},
            "retirement": {"path_absent": True, "after_identity": [1, 2, 3, 4, 5, 6, 0]},
        }
        stages[-1].update(
            intermediate=proof, launch_scope=f"private-{index}", launch_binding=f"binding-{index}",
            workspace=[1, index, 3],
        )
        receipts.append({
            "native_dispatch_sequence": 1 if index == 1 else 7,
            "producer_slot": 2 if index == 1 else 8, "stages": stages,
        })
    if fault == "missing-checker":
        receipts.pop()
    elif fault == "retirement":
        receipts[1]["stages"][-1]["intermediate"]["retirement"]["path_absent"] = False
    elif fault == "content":
        receipts[1]["stages"][-1]["intermediate"]["writer"]["completed"]["sha256"] = "b" * 64
    elif fault == "binding":
        receipts[1]["stages"][-1].update(
            launch_scope=receipts[0]["stages"][-1]["launch_scope"],
            launch_binding=receipts[0]["stages"][-1]["launch_binding"],
            workspace=receipts[0]["stages"][-1]["workspace"],
        )
    elif fault == "stage":
        receipts[1]["stages"].pop()
    raw = tuple(policy.encoded(row) for row in receipts)
    reference = {"kind": "toolchain-intermediate-ref", "version": 1, "role": "stage4-assembly"}
    commands = [{"command": {"toolchain_check": True, "runtime_probes": [{"argv": [reference, reference]}]}}]
    if fault == "semantic":
        commands.append(copy.deepcopy(commands[0]))
    return SimpleNamespace(
        toolchain_receipts=raw, results=(object(), object()),
        semantics={
            "native_dispatches": [
                {"sequence": sequence, "job": {"target": "expansion-modern-toolchain-check"}} for sequence in (1, 7)
            ],
            "dynamic_commands": commands,
        },
    )


class RootStageControls(Inert):
    def composition(self, fault=None):
        budget = self.budget()
        sampler = SimpleNamespace(phase="candidate-import", session=None)
        events, sessions, cases = [], [], []
        first = RuntimeError("private source first failure")
        state = {"removed": False}
        root = Path("/repo/build/test-artifacts/component")

        class Entries:
            def __init__(self, values, *, budget):
                self.values, self.budget = values, budget

        class Loader:
            def __init__(self, root, entries, *, budget):
                self.root, self.entries, self.budget = root, entries, budget

        class Commands:
            def __init__(self, session):
                self.session = session

        class Session:
            def __init__(self, loader, *, scratch_root, budget, runtime_files):
                events.append(("session", budget, loader.entries.budget))
                self.loader, self.budget, self.base = loader, budget, root / "base"
                self._file_owners = {}
                self.parked_capsules = []
                self.pending_commands = self.make_depth = 0
                self.pending_commands_peak, self.live_process_peak, self.memory_peak = 1, 4, 16 * policy.MIB
                self.processes_used, self.syscalls_used, self.observations_used, self.files_created = 16, 1000, 100, 2
                self.runtime_files = runtime_files
                sessions.append(self)
                if fault == "foreign-budget":
                    self.budget = object()
            def __enter__(self):
                self.budget.plan(1)
                self.budget.charge("control", 33708478)
                return self
            def __exit__(self, kind, value, traceback):
                events.append(("session-exit", value))
                self.budget.close()
                if fault == "retained":
                    self._file_owners["retained"] = SimpleNamespace(retained=True)
                else:
                    self.base = None
                if fault == "session-close":
                    raise OSError("inert session close")
            def make(self, target, **keywords):
                events.append(("make", target))
                self.budget.runs += 28
                if fault == "make":
                    raise first
                return observation(fault)

        class SelectedCase(unittest.TestCase):
            def setUp(self):
                cases.append(self)
                self.fixture = SimpleNamespace(
                    root=root / "repo", directory=root, scratch=root / "repo/build/probe",
                    entries={"include/a.h": object(), "src/query.c": object(), "Makefile": object()},
                )
                events.append(("setup",))
                if fault == "setup":
                    raise first
            def tearDown(self):
                events.append(("teardown",))
                if fault == "fixture-close":
                    raise OSError("inert fixture close")
                state["removed"] = True
            def session(self):
                raise AssertionError("stock fresh-budget seam called")
            def capture(self, session):
                target = "foreign" if fault == "target" else policy.COMPONENT_TARGET
                value = session.make(target, commands=Commands(session))
                if fault == "second-make":
                    session.make(target, commands=Commands(session))
                self.results = value.results
                return value, self.results[0]
            def test_one_make_two_checker_typed_intermediate_component(self):
                events.append(("selected-method",))
                if fault == "no-session":
                    return
                with self.session() as session:
                    self.capture(session)
                    if fault == "method":
                        raise first
                    if fault == "second-session":
                        self.session()
                self.assertTrue(session.budget.closed)
                self.assertIsNone(session.base)
                self.assertFalse(session._file_owners)
                events.append(("source-clean-assertions",))

        api = SimpleNamespace(
            case=SelectedCase, method=SelectedCase.test_one_make_two_checker_typed_intermediate_component,
            root=Path("/repo"), loader=Loader, entries=Entries, session=Session,
            budget_type=budgeting.ProbeBudget, runtime_files=("fixed-runtime",), commands=Commands,
        )
        if fault == "method-replacement":
            api.method = lambda case: None
        def exists(path):
            if path == root:
                return not state["removed"]
            if path == root / "repo/build/native":
                return False
            raise AssertionError("unmodeled source path")
        result = error = None
        with mock.patch.object(worker, "require_contained", return_value={}), \
             mock.patch.object(root_stage, "candidate_api", return_value=api), \
             mock.patch.object(Path, "read_bytes", return_value=b'#include "global.h"\n'), \
             mock.patch.object(Path, "read_text", return_value="\nexpansion-modern-all: ;\n"), \
             mock.patch.object(Path, "exists", exists), mock.patch.object(Path, "is_symlink", return_value=False):
            try:
                result = root_stage.run(Path("/repo"), budget, {"mode": "component", "deadline": 3700.0}, sampler)
            except BaseException as caught:
                error = caught
        return SimpleNamespace(
            result=result, error=error, first=first, budget=budget, sampler=sampler,
            api=api, events=events, sessions=sessions, cases=cases, state=state,
        )

    def test_exact_inherited_method_uses_one_budget_session_and_make(self):
        value = self.composition()
        self.assertIsNone(value.error)
        self.assertEqual(len(value.sessions), 1)
        self.assertEqual(value.events.count(("selected-method",)), 1)
        self.assertEqual(value.events.count(("make", policy.COMPONENT_TARGET)), 1)
        session, = value.sessions
        self.assertIs(session.budget, value.budget)
        self.assertIs(session.loader.budget, value.budget)
        self.assertIs(session.loader.entries.budget, value.budget)
        self.assertIs(value.sampler.session, session)
        self.assertIs(getattr(type(value.cases[0]), policy.COMPONENT_METHOD), value.api.method)
        self.assertNotIn("make", vars(session))
        self.assertTrue(value.state["removed"])
        self.assertIn(("source-clean-assertions",), value.events)
        self.assertFalse(hasattr(value.cases[0], "results"))
        self.assertEqual(value.result["observation"]["checker_occurrences"], 2)
        self.assertEqual(value.result["counters"]["budget"]["categories"]["control"]["charged"], 33708478)
        self.assertFalse(policy.validate_component_result(value.result)["production_acceptance"])

    def test_no_import_before_real_containment_or_for_a_foreign_workload(self):
        budget = self.budget()
        with mock.patch.object(worker, "require_contained", side_effect=policy.GuardError("not contained")), \
             mock.patch.object(root_stage, "candidate_api") as imported:
            with self.assertRaises(policy.GuardError):
                root_stage.run(Path("/repo"), budget, {"mode": "component", "deadline": 3700.0}, object())
            imported.assert_not_called()
        with mock.patch.object(worker, "require_contained", return_value={}), \
             mock.patch.object(root_stage, "candidate_api") as imported:
            for root, mode, deadline in ((Path("/other"), "component", 3700), (Path("/repo"), "root", 3700),
                                         (Path("/repo"), "component", 3800)):
                with self.assertRaises(policy.GuardError):
                    root_stage.run(root, budget, {"mode": mode, "deadline": deadline}, object())
            imported.assert_not_called()

    def test_partial_foreign_replayed_and_content_changed_pairs_do_not_complete(self):
        for fault in ("make", "target", "second-make", "second-session", "no-session", "foreign-budget",
                      "method-replacement", "missing-checker", "retirement", "content", "binding", "stage", "semantic"):
            with self.subTest(fault=fault):
                value = self.composition(fault)
                self.assertIsNotNone(value.error)
                self.assertIsNone(value.result)
                self.assertLessEqual(sum(row[0] == "make" for row in value.events), 1)
                for session in value.sessions:
                    self.assertNotIn("make", vars(session))

    def test_method_failure_after_make_and_uncertain_cleanup_preserve_first_cause(self):
        value = self.composition("method")
        self.assertIs(value.error, value.first)
        self.assertEqual(value.events.count(("make", policy.COMPONENT_TARGET)), 1)
        self.assertNotIn(("source-clean-assertions",), value.events)
        self.assertTrue(value.budget.closed)
        self.assertIsNone(value.result)
        for fault in ("setup", "retained", "session-close", "fixture-close"):
            with self.subTest(fault=fault):
                value = self.composition(fault)
                self.assertIsNotNone(value.error)
                self.assertIsNone(value.result)
                if fault in ("setup", "retained", "fixture-close"):
                    self.assertFalse(value.state["removed"])
                if fault == "retained":
                    self.assertNotIn(("teardown",), value.events)
                    self.assertEqual(value.error.component_cleanup_state["retained_owners"], 1)

    def test_summary_is_metadata_only_and_neutral_order_preserves_actual_comparisons(self):
        raw = observation()
        result = root_stage.summarize(raw, raw.results, 1, 1)
        data = policy.encoded(result)
        self.assertNotIn(b"private-", data)
        self.assertNotIn(b"binding-", data)
        self.assertNotIn(b"aaaaaaaa", data)
        reversed_records = [
            dict(reversed(list(policy.parse_json(row).items()))) for row in raw.toolchain_receipts
        ]
        raw.toolchain_receipts = tuple(policy.encoded(row) for row in reversed_records)
        self.assertEqual(root_stage.summarize(raw, raw.results, 1, 1), result)
        for bad in (0, 2, True):
            with self.assertRaises(policy.GuardError):
                root_stage.summarize(raw, raw.results, bad, 1)

    def test_removing_component_admission_or_content_equality_breaks_its_oracle(self):
        def oracle():
            value = self.composition("content")
            self.assertIsNotNone(value.error)
            self.assertIsNone(value.result)
        oracle()
        validate = policy.validate_component_observation
        def omit_content(value):
            changed = {**value, "content_equal": True}
            return validate(changed)
        with mock.patch.object(policy, "validate_component_observation", side_effect=omit_content), self.assertRaises(AssertionError):
            oracle()
        oracle()

    def worker_case(self, *, stage_failure=False, sampler_failure=False, budget_failure=False, publication_failure=False):
        budget = self.budget()
        first = RuntimeError("private workload failure")
        closing = OSError("private cleanup")
        events, frames = [], []
        limits = budget.limits
        original = dataclasses.asdict(budgeting.Limits())
        manifest = policy.profile_manifest(original, observation_count=32768)
        result = self.component_result()
        session = self.session(budget)

        class SelectedSession:
            def _sandbox_run(self):
                pass

        actual_import = builtins.__import__
        def selected_import(name, *args, **kwargs):
            if name == "scripts.validation_ownership.authority":
                return SimpleNamespace(git=lambda root, same_budget, *arguments:
                    (policy.GRAPH if arguments[-1] == "HEAD" else policy.BASE).encode())
            if name == "scripts.validation_ownership.budget":
                return budgeting
            if name == "scripts.validation_ownership.make_probe":
                return SimpleNamespace(ProbeSession=SelectedSession)
            return actual_import(name, *args, **kwargs)

        def stage(root, same_budget, config, sampler):
            self.assertIs(same_budget, budget)
            sampler.session = session
            events.append("stage")
            budget.plan(1)
            budget.runs = 28
            budget.charge("control", 33708478)
            budget.close()
            if stage_failure:
                first.component_cleanup_state = result["cleanup"]
                raise first
            result["counters"] = policy.counter_snapshot(budget, session)
            return result

        def close_sampler(sampler):
            events.append("sampler-close")
            if sampler_failure:
                raise closing
        original_close = budget.close
        def close_budget():
            events.append("budget-close")
            original_close()
            if budget_failure and events[-2:] != ["stage", "budget-close"]:
                raise closing
        def emit(scope, kind, data):
            frames.append((kind, data))
            if publication_failure and kind in ("error", "cleanup-error"):
                raise BrokenPipeError("inert channel")
        returned = failure = None
        with mock.patch.object(worker, "require_contained", return_value={}), \
             mock.patch.object(builtins, "__import__", side_effect=selected_import), \
             mock.patch.object(sys, "path", list(sys.path)), \
             mock.patch.object(worker, "calibration_budget", return_value=(budget, limits, original, manifest)), \
             mock.patch.object(threading.Thread, "start"), \
             mock.patch.object(worker.Sampler, "close", close_sampler), \
             mock.patch.object(budget, "close", side_effect=close_budget), \
             mock.patch.object(root_stage, "run", side_effect=stage), \
             mock.patch.object(kernel, "emit", side_effect=emit):
            try:
                returned = worker.component({"mode": "component", "scope": "inert/component", "deadline": 3700.0})
            except BaseException as error:
                failure = error
        return SimpleNamespace(returned=returned, failure=failure, first=first, events=events, frames=frames, budget=budget)

    def test_worker_actual_composition_closes_sampler_and_budget_before_result(self):
        value = self.worker_case()
        self.assertIsNone(value.failure)
        self.assertIsNotNone(value.returned)
        self.assertEqual(value.events[-2:], ["sampler-close", "budget-close"])
        self.assertEqual([kind for kind, _ in value.frames], ["component-start"])
        self.assertTrue(value.returned["component_completed"])
        self.assertEqual(value.returned["component"]["counters"], value.returned["counters"]["counters"])
        self.assertTrue(value.budget.closed)

    def test_worker_attempts_all_closes_and_preserves_original_failure_when_reporting_fails(self):
        for case in (
            {"stage_failure": True}, {"stage_failure": True, "sampler_failure": True},
            {"stage_failure": True, "sampler_failure": True, "budget_failure": True, "publication_failure": True},
            {"sampler_failure": True}, {"budget_failure": True},
        ):
            with self.subTest(case=case):
                value = self.worker_case(**case)
                self.assertIsNone(value.returned)
                self.assertIn("sampler-close", value.events)
                self.assertEqual(value.events[-1], "budget-close")
                self.assertTrue(value.budget.closed)
                if case.get("stage_failure") and (case.get("sampler_failure") or case.get("publication_failure")):
                    self.assertIs(value.failure, value.first)
                for kind, record in value.frames:
                    if kind in ("error", "cleanup-error"):
                        self.assertNotIn(b"private", policy.encoded(record))
                        self.assertNotIn(b"frames", policy.encoded(record))

    def test_sampler_failures_are_explicit_and_final_counters_survive_closed_session(self):
        sampler = worker.Sampler("inert")
        sampler.budget = self.budget()
        sampler.session = self.session(sampler.budget)
        sampler.budget.charge("control", 10)
        first = OSError("inert sampler output")
        with mock.patch.object(sampler.stop, "wait", return_value=False), \
             mock.patch.object(kernel, "emit", side_effect=first):
            sampler.run()
        self.assertIs(sampler.failure, first)
        with mock.patch.object(sampler.thread, "join"), mock.patch.object(sampler.thread, "is_alive", return_value=False):
            with self.assertRaises(OSError) as caught:
                sampler.close()
            self.assertIs(caught.exception, first)
        sampler.budget.close()
        value = sampler.snapshot()
        self.assertEqual(value["counters"]["budget"]["categories"]["control"]["charged"], 10)
        self.assertEqual(value["counters"]["session"]["memory_peak"], sampler.session.memory_peak)
        self.assertTrue(value["counters"]["budget"]["closed"])

    def supervisor_case(self, fault=None):
        arguments = SimpleNamespace(
            operation="run", harness="/owned/harness", candidate="/owned/candidate",
            output="/owned/" + policy.OUTPUT_PREFIX + "12345", event="/owned/event",
            sha="a" * 40, event_name="push", run_id="12345", attempt="1", run_number="1",
            runner_environment="github-hosted", runner_os="Linux",
        )
        event = {
            "ref": "refs/heads/" + policy.BRANCH, "before": "0" * 40, "after": "a" * 40,
            "created": True, "deleted": False, "repository": {"full_name": policy.REPOSITORY, "private": False},
            "sender": {"login": "laqieer"},
        }
        scope = policy.validate_event(
            event, sha=arguments.sha, run_id="12345", attempt="1", run_number="1",
            environment="github-hosted", operating_system="Linux", event_name="push",
        )
        scope.update(
            component_launch_requested=False, component_attempted=False, component_completed=False,
            planned_at_monotonic=100.0,
        )
        stored, modes, cleanup, volumes = {}, [], [], []
        facts = {
            "memory_total": 16 * policy.GIB, "memory_available": 12 * policy.GIB,
            "disk_available": 24 * policy.GIB, "cpus": 4, "threads_max": 100000, "threads_current": 500,
            "cgroup_ancestors": [{"memory_max": None, "memory_current": 0, "pids_max": None, "pids_current": 0}],
        }
        output = SimpleNamespace(root=Path(arguments.output), write=lambda name, value: stored.__setitem__(name, copy.deepcopy(value)))

        class Owner:
            def __init__(self, args, actual_scope, artifacts):
                self.scope = actual_scope
            def prepare(self):
                if fault == "prepare":
                    raise OSError(errno.EIO, "actual-owned-prepare")
            def volume(self, name, size):
                volumes.append((name, size))
                if name == "graph-volume" and fault == "volume":
                    raise OSError(errno.EIO, "actual-owned-volume")
                return SimpleNamespace(close=lambda: cleanup.append("probe-volume-close"))
            def cleanup(self):
                cleanup.append("owner-cleanup")
                if fault == "cleanup":
                    raise OSError(errno.EIO, "actual-owned-cleanup")
            def source_status(self, label):
                cleanup.append("source-" + label)

        def proc_proof():
            return {
                "pid": 1, "proc_pid": 1, "oom_score_adj_after": "0", "proc_mount_flags": 14,
                "denied_writes": {
                    path: {"opened": True, "errno": errno.EPERM, "before": "0", "after": "0"}
                    for path in ("/proc/self/oom_score_adj", "/proc/self/oom_adj", "/proc/sys/kernel/kptr_restrict",
                                 "/proc/sys/vm/overcommit_memory", "/proc/sys/kernel/overflowuid", "/proc/sys/kernel/overflowgid")
                },
                "sysrq_write_open": {"exposed": False, "errno": errno.ENOENT},
            }
        def phase(owner, mode, volume, *, memory, pids, seconds):
            modes.append((mode, memory, pids, seconds))
            if mode == "component":
                if fault == "prereturn":
                    raise OSError(errno.EIO, "actual-component-prereturn")
                value = self.component_phase()
                if fault in ("component", "retained"):
                    value["first_cause"] = {"type": "worker-error", "error": {
                        "component_cleanup": copy.deepcopy(value["worker"]["component"]["cleanup"]),
                        "reason": "actual-component",
                    }}
                    if fault == "retained":
                        value["worker"]["component"]["cleanup"]["retained_owners"] = 1
                owner.scope["component_attempted"] = True
                return value
            value = {
                "mode": mode, "identity": {"uid": 999, "gid": 998, "cgroup": "/owned", "namespaces": {"user": 1}},
                "empty": True, "empty_before_outer_cleanup": True, "watchdog_reaped": True,
                "returncode": 0, "first_cause": None, "probe": {},
            }
            if mode == "identity":
                proof = proc_proof()
                value["probe"] = {
                    "proc_protection": proof,
                    "nested": {"uid": 0, "uid_map": ["0", "999", "1"], "gid_map": ["0", "998", "1"],
                               "no_new_privs": "1", "cgroup": "/owned", "namespaces": {"user": 2},
                               "cgroup_control_probe": {"namespace_denied": errno.EPERM}, "proc_protection": proof},
                }
            elif mode == "memory":
                value["kernel"] = {"memory_events": {"oom_kill": 1}}
            elif mode == "output":
                value.update(first_cause={"type": "output-bound"}, output_exceeded=True, output_bytes=65537)
            elif mode in ("deadline", "lifetime"):
                value.update(
                    first_cause={"type": "deadline" if mode == "deadline" else "lifetime-eof"},
                    escaped={"pid": 9}, held_descendants_terminal=2, caller_lifetime_control_exercised=True,
                )
            if mode == "pids" and fault == "preflight":
                value.update(first_cause={"type": "worker-error", "reason": "actual-preflight"}, returncode=1)
            return value

        local_sys = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), dont_write_bytecode=True)
        with mock.patch.object(supervisor, "arguments", return_value=arguments), \
             mock.patch.object(supervisor, "sys", local_sys), \
             mock.patch.object(kernel, "read", side_effect=lambda path, *a: policy.encoded(
                 event if str(path) == arguments.event else scope)), \
             mock.patch.object(supervisor, "Artifacts", return_value=output), \
             mock.patch.object(supervisor, "Owner", Owner), \
             mock.patch.object(supervisor, "capacity_facts", return_value=facts), \
             mock.patch.object(supervisor, "phase", side_effect=phase), \
             mock.patch.object(Path, "exists", return_value=False), \
             mock.patch.object(Path, "is_symlink", return_value=False), \
             mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(os, "chown"):
            code = supervisor.main()
        return SimpleNamespace(code=code, stored=stored, modes=modes, cleanup=cleanup, volumes=volumes)

    def test_supervisor_composes_seven_fresh_controls_then_one_component_only(self):
        value = self.supervisor_case()
        self.assertEqual(value.code, 0)
        self.assertEqual([row[0] for row in value.modes],
                         ["identity", "memory", "pids", "disk", "output", "deadline", "lifetime", "component"])
        self.assertEqual(value.modes[1][1], 64 * policy.MIB)
        self.assertEqual(value.modes[2][2], 8)
        self.assertEqual(value.modes[5][3], 2)
        self.assertEqual(value.modes[-1][3], 3600)
        self.assertEqual(value.cleanup, ["probe-volume-close", "owner-cleanup", "source-after"])
        self.assertEqual(value.stored["result.json"]["status"], "completed-component-diagnostic-only")
        self.assertFalse(value.stored["result.json"]["production_acceptance"])
        self.assertTrue(value.stored["scope.json"]["component_completed"])

    def test_supervisor_setup_and_current_failure_never_borrow_a_qualified_negative(self):
        for fault, expected in (("prepare", "actual-owned-prepare"), ("volume", "actual-owned-volume"),
                                ("prereturn", "actual-component-prereturn")):
            with self.subTest(fault=fault):
                value = self.supervisor_case(fault)
                self.assertEqual(value.code, 1)
                first = value.stored["result.json"]["first_error"]
                self.assertEqual(first["chain"][0]["message"], str(OSError(errno.EIO, expected)))
                self.assertNotEqual(first.get("type"), "lifetime-eof")
        for fault in ("preflight", "component", "retained", "cleanup"):
            with self.subTest(fault=fault):
                value = self.supervisor_case(fault)
                self.assertEqual(value.code, 1)
                if fault == "preflight":
                    self.assertEqual([row[0] for row in value.modes], ["identity", "memory", "pids"])
                    self.assertEqual(value.stored["result.json"]["first_error"]["reason"], "actual-preflight")
                elif fault in ("component", "retained"):
                    self.assertEqual(value.stored["result.json"]["first_error"]["error"]["reason"], "actual-component")
                if fault == "retained":
                    self.assertNotIn("owner-cleanup", value.cleanup)


if __name__ == "__main__":
    unittest.main()
