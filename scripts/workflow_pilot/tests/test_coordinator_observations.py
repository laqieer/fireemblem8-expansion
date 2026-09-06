import copy
import fcntl
import json
import os
import shlex
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import agent_handoff as handoff
from scripts.workflow_pilot import coordinator_observations as observations
from scripts.workflow_pilot import raw_diff_check as raw
from scripts.workflow_pilot import reporter
from scripts.workflow_pilot.tests.test_agent_handoff import (
    GitFixture, ROOT, git, native_event, write_json,
)


class NativeObservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def test_dispatch_receipt_progress_and_handoff_are_distinct_native_inputs(self):
        f = self.fixture
        f.receive(progressing=False)
        self.assertEqual([item["state"] for item in f.entry["events"]],
                         ["assignment_sent", "assignment_received"])
        f.append(f.owner_log, native_event("subagent.started", "not-progress", toolCallId="other"),
                 native_event("tool.execution_complete", "not-exit", toolCallId="other",
                              success=True, result={"content": "exit 0"}))
        handoff.observe_cli(f.entry, f.owner_log)
        self.assertEqual(len(f.entry["events"]), 2)
        self.assertEqual(f.entry["checks"], [])
        f.append(f.owner_log, native_event("tool.execution_start", "progress",
                                         toolCallId="actual-tool", toolName="bash"))
        handoff.observe_cli(f.entry, f.owner_log)
        self.assertEqual(f.entry["events"][-1]["state"], "progressing")
        handoff.observe_cli(f.entry, f.owner_log)
        self.assertEqual(len(f.entry["events"]), 3)
        self.assertEqual(f.entry["coordination_turns"], 1)

    def test_native_receipt_needs_exact_session_and_correlation_marker(self):
        f = self.fixture
        f.append(f.parent_log,
                 native_event("session.start", "session", sessionId="unrelated"),
                 native_event("tool.execution_start", "dispatch", toolCallId="dispatch-one", toolName="task"),
                 native_event("user.message", "wrong-session", content="[handoff:assignment-one]",
                              parentAgentTaskId=f.assignment["dispatch_id"]))
        handoff.observe_cli(f.entry, f.parent_log)
        self.assertEqual(len(f.entry["events"]), 1)
        f.append(f.owner_log, native_event("session.start", "owner-session", sessionId=f.assignment["session_id"]),
                 native_event("user.message", "wrong-assignment", content="[handoff:different]",
                              parentAgentTaskId=f.assignment["dispatch_id"]))
        handoff.observe_cli(f.entry, f.owner_log)
        self.assertEqual(len(f.entry["events"]), 1)

    def test_native_receipt_requires_the_actual_dispatched_task_identity(self):
        f = self.fixture
        f.append(f.parent_log, native_event("tool.execution_start", "dispatch",
                                           toolCallId=f.assignment["dispatch_id"], toolName="task"))
        handoff.observe_cli(f.entry, f.parent_log)
        marker = f"[handoff:{f.assignment['id']}]"
        for event_id, data in (
            ("wrong-task", {"content": marker, "parentAgentTaskId": "another-dispatch"}),
            ("missing-task", {"content": marker}),
            ("null-task", {"content": marker, "parentAgentTaskId": None}),
            ("opaque-task", {"content": marker, "parentAgentTaskId": {"id": f.assignment["dispatch_id"]}}),
            ("unrelated", {"content": "Different work", "parentAgentTaskId": f.assignment["dispatch_id"]}),
            ("opaque-content", {"content": None, "parentAgentTaskId": f.assignment["dispatch_id"]}),
        ):
            with self.subTest(event=event_id):
                candidate = copy.deepcopy(f.entry)
                path = f.home / (event_id + ".jsonl")
                f.append(path, native_event("session.start", "owner-session",
                                            sessionId=f.assignment["session_id"]),
                         native_event("user.message", event_id, **data))
                handoff.observe_cli(candidate, path)
                self.assertEqual([item["state"] for item in candidate["events"]], ["assignment_sent"])
        f.append(f.owner_log, native_event("session.start", "owner-session",
                                          sessionId=f.assignment["session_id"]),
                 native_event("user.message", "actual-receipt", content=marker,
                                          parentAgentTaskId=f.assignment["dispatch_id"]))
        handoff.observe_cli(f.entry, f.owner_log)
        self.assertEqual([item["state"] for item in f.entry["events"]],
                         ["assignment_sent", "assignment_received"])
        self.assertEqual(f.entry["events"][-1]["source_id"], "actual-receipt")
        handoff.observe_cli(f.entry, f.owner_log)
        self.assertEqual(len(f.entry["events"]), 2)

    def test_native_delivery_does_not_authorize_a_second_owner_cycle(self):
        f = self.fixture
        result = f.complete()
        f.append(f.owner_log, native_event("assistant.message", "duplicate-delivery",
                                         content=json.dumps({"handoff_result": result})))
        with self.assertRaisesRegex(handoff.HandoffDataError, "second handoff"):
            handoff.observe_cli(f.entry, f.owner_log)

    def test_streaming_logs_retain_partial_record_and_reject_rotation_or_fifo(self):
        f = self.fixture
        f.owner_log.write_bytes(observations.json_bytes(native_event(
            "session.start", "owner", sessionId="session")) + b'{"id":')
        events, cursor = observations.cli_event_batch(f.owner_log)
        self.assertEqual(len(events), 1)
        self.assertLess(cursor["offset"], f.owner_log.stat().st_size)
        events, same = observations.cli_event_batch(f.owner_log, cursor)
        self.assertEqual((events, same), ([], cursor))
        f.owner_log.unlink()
        os.mkfifo(f.owner_log)
        with self.assertRaises(handoff.HandoffDataError):
            observations.cli_event_batch(f.owner_log, cursor)

    def test_native_tool_output_is_bounded_without_becoming_process_evidence(self):
        f = self.fixture
        f.receive()
        f.append(f.owner_log, native_event(
            "tool.execution_complete", "large-native-output", toolCallId="actual",
            success=True, result={"content": "x" * 32768 + " exit 0"},
        ))
        handoff.observe_cli(f.entry, f.owner_log)
        self.assertEqual(f.entry["checks"], [])
        self.assertEqual(len(f.entry["events"]), 3)
        f.owner_log.write_bytes(b"x" * (observations.MAX_JSON_BYTES + 1))
        with self.assertRaises(handoff.HandoffDataError):
            observations.cli_event_batch(f.owner_log)

    def test_actual_os_exit_and_rss_are_not_tool_transport_success(self):
        captured = raw.run_process(
            ["/usr/bin/python3", "-I", "-c", "print('exit 0'); raise SystemExit(7)"],
            cwd=ROOT, env=raw.git_environment(),
        )
        self.assertEqual(captured.returncode, 7)
        self.assertEqual(captured.stdout, b"exit 0\n")
        self.assertGreater(captured.pid, 0)
        self.assertGreater(captured.peak_rss_bytes, 0)

    def test_actual_pid_identity_rejects_reuse_and_opaque_completion(self):
        f = self.fixture
        process = f.owner_process()
        current = f.entry["process"]
        forged = {**current, "start_ticks": current["start_ticks"] + 1}
        with self.assertRaisesRegex(handoff.HandoffDataError, "identity"):
            observations.observe_owned_exit(process, forged)
        self.assertIsNone(process.poll())
        sampled = observations.sample_process(forged)
        self.assertEqual(sampled["state"], "exited")
        self.assertIsNone(sampled["exit_code"])
        self.assertFalse(sampled["rss_complete"])
        completed = f.finish_owner(process)
        self.assertEqual(observations.sample_process(completed), completed)

    def test_joint_pipe_limit_and_timeout_reap_only_owned_child(self):
        f = self.fixture
        unrelated = f.owner_process()
        for code, kwargs, error in (
            ("import os; os.write(1,b'x'*4096); os.write(2,b'y'*4096)",
             {"max_bytes": 4096}, "output"),
            ("import time; time.sleep(5)", {"timeout": 0.05}, "timed out"),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, error):
                    raw.run_process(["/usr/bin/python3", "-I", "-c", code], cwd=ROOT,
                                    env=raw.git_environment(), **kwargs)
        self.assertIsNone(unrelated.poll())
        f.finish_owner(unrelated)

    def test_state_lock_prevents_duplicate_writers_and_partial_replacement(self):
        f = self.fixture
        path = f.home / "coordination.json"
        write_json(path, f.state)
        with observations.locked_state(path):
            with self.assertRaisesRegex(handoff.HandoffDataError, "busy"):
                with observations.locked_state(path):
                    self.fail("second writer entered")
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            with observations.locked_state(path) as state:
                state["coordinator_id"] = "not-committed"
                raise ValueError("abandon transaction")
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(path.with_name(path.name + ".new").exists())


class WatcherObservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def response(self, head, conclusion="success", status="completed", *, run_id=91, attempt=2):
        return {"id": run_id, "run_attempt": attempt, "head_sha": head, "workflow_id": 17,
                "repository": {"full_name": "owner/repository"},
                "status": status, "conclusion": conclusion}

    def test_real_query_adapter_binds_run_attempt_head_and_preserves_failure(self):
        f = self.fixture
        result = f.complete()
        process = subprocess.Popen(
            ["/usr/bin/timeout", "0.25", "/bin/sleep", "5"], cwd=f.worktree,
            env=raw.git_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        f.processes.append(process)
        watcher = handoff.reserve_watcher(f.state, "watch-one", 91, 2, result["result_sha"], process.pid)
        for conclusion, expected in (("success", "success"), ("failure", "failure"),
                                     ("cancelled", "failure")):
            response = observations.json_bytes(self.response(result["result_sha"], conclusion))
            completed = raw.ProcessResult(0, response, b"", 1, 0.1, 1)
            with mock.patch.object(raw, "run_process", return_value=completed) as run:
                handoff.reconcile_run(f.state, 91)
                argv = run.call_args.args[0]
                self.assertEqual(argv[:2], ["/usr/bin/gh", "api"])
                self.assertEqual(argv[2], "repos/owner/repository/actions/runs/91/attempts/2")
            self.assertEqual(handoff.ci_state(f.state, result["result_sha"]), expected)
        # A watcher process error/timeout is orthogonal to exact GitHub success.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            observed = observations.observe_owned_exit(process, watcher["process"])
            if observed["rss_complete"]:
                self.assertEqual(observed["exit_code"], 124)
                watcher.update(process=observed, ended_at=observations.utc_now(), exit_code=observed["exit_code"])
                break
            time.sleep(0.01)
        completed = raw.ProcessResult(0, observations.json_bytes(self.response(result["result_sha"])), b"", 1, 0.1, 1)
        with mock.patch.object(raw, "run_process", return_value=completed):
            handoff.reconcile_run(f.state, 91)
        self.assertEqual(handoff.ci_state(f.state, result["result_sha"]), "success")

    def test_api_error_wrong_identity_and_pending_never_become_success(self):
        f = self.fixture
        process = f.owner_process({**f.entry, "process": None})
        watcher = handoff.reserve_watcher(f.state, "watch", 91, 2, f.parent, process.pid)
        for mutation in ({"id": 92}, {"run_attempt": 3}, {"head_sha": "f" * 40},
                         {"repository": {"full_name": "wrong/repo"}}):
            response = {**self.response(f.parent), **mutation}
            capture = raw.ProcessResult(0, observations.json_bytes(response), b"", 1, .1, 1)
            with mock.patch.object(raw, "run_process", return_value=capture):
                handoff.reconcile_run(f.state, 91)
            self.assertEqual(handoff.ci_state(f.state, f.parent), "unknown")
        pending = self.response(f.parent, None, "in_progress")
        with mock.patch.object(raw, "run_process", return_value=raw.ProcessResult(
                0, observations.json_bytes(pending), b"", 1, .1, 1)):
            handoff.reconcile_run(f.state, 91)
        self.assertEqual(handoff.ci_state(f.state, f.parent), "pending")
        with mock.patch.object(raw, "run_process", side_effect=ValueError("timeout")):
            handoff.reconcile_run(f.state, 91)
        self.assertEqual(handoff.ci_state(f.state, f.parent), "unknown")
        self.assertIn("timeout", watcher["query_error"])

    def test_duplicate_watchers_and_claimed_death_cannot_overlap(self):
        f = self.fixture
        process = f.owner_process({**f.entry, "process": None})
        watcher = handoff.reserve_watcher(f.state, "watch", 91, 2, f.parent, process.pid)
        with self.assertRaises(handoff.HandoffDataError):
            handoff.reserve_watcher(f.state, "duplicate", 91, 2, f.parent, process.pid)
        watcher["ended_at"] = observations.utc_now()
        with self.assertRaises(handoff.HandoffDataError):
            handoff.reserve_watcher(f.state, "duplicate", 91, 2, f.parent, process.pid)
        watcher["coordinator_id"] = "other-coordinator"
        with self.assertRaisesRegex(handoff.HandoffDataError, "coordinator"):
            handoff.validate_state(f.state)

    def test_process_identity_is_unique_across_watcher_runs_and_loaded_state(self):
        f = self.fixture
        owner = f.owner_process()
        process, control = f.waiting_process(), f.waiting_process()
        watcher = handoff.reserve_watcher(f.state, "watch-one", 91, 1, f.parent, process.pid)
        with self.subTest(source="reservation"):
            candidate = copy.deepcopy(f.state)
            with self.assertRaisesRegex(handoff.HandoffDataError, "process reused"):
                handoff.reserve_watcher(candidate, "watch-alias", 92, 1, f.parent, process.pid)
            self.assertEqual(len(candidate["watchers"]), 1)
        for stage in ("active", "completed"):
            with self.subTest(source="loaded", stage=stage):
                alias = {**copy.deepcopy(watcher), "id": "watch-alias", "run_id": 92}
                alias["process"]["runtime_handle"] = alias["id"]
                path = f.home / "duplicate-process.json"
                write_json(path, {**f.state, "watchers": [watcher, alias]})
                with self.assertRaisesRegex(handoff.HandoffDataError, "process reused"):
                    handoff.validate_state(observations.load_json(path))
            if stage == "active":
                process.stdin.write(b"done\n")
                process.stdin.flush()
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
                handoff.finish_watcher(f.state, watcher["id"], process)
        handoff.reserve_watcher(f.state, "watch-control", 92, 1, f.parent, control.pid)
        with self.assertRaisesRegex(handoff.HandoffDataError, "implementation owner"):
            handoff.reserve_watcher(f.state, "watch-owner", 93, 1, f.parent, owner.pid)
        self.assertIsNone(control.poll())
        self.assertIsNone(owner.poll())
        handoff.validate_state(f.state)

    def test_exited_zombie_cannot_be_reserved_but_completed_watchers_reconcile(self):
        f = self.fixture
        process, control = f.waiting_process(), f.waiting_process()
        identity = observations.process_identity(process.pid, "unreserved")
        process.stdin.write(b"done\n")
        process.stdin.flush()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        zombie = observations.process_identity(process.pid, "unreserved")
        self.assertEqual(zombie["state"], "exited")
        self.assertIsNone(process.returncode)
        self.assertIsNone(zombie["exit_code"])
        with self.subTest(state="zombie"):
            with self.assertRaisesRegex(handoff.HandoffDataError, "running"):
                handoff.reserve_watcher(copy.deepcopy(f.state), "dead-watch", 91, 1, f.parent, process.pid)
        owned = observations.observe_owned_exit(process, identity)
        self.assertEqual(owned["exit_code"], 0)
        self.assertTrue(owned["rss_complete"])
        self.assertIsNone(control.poll())
        watcher = handoff.reserve_watcher(f.state, "control", 91, 2, f.parent, control.pid)
        control.stdin.write(b"done\n")
        control.stdin.flush()
        os.waitid(os.P_PID, control.pid, os.WEXITED | os.WNOWAIT)
        handoff.finish_watcher(f.state, watcher["id"], control)
        self.assertEqual(watcher["exit_code"], 0)
        self.assertTrue(watcher["process"]["rss_complete"])
        capture = raw.ProcessResult(0, observations.json_bytes(self.response(f.parent)), b"", 1, .1, 1)
        with mock.patch.object(raw, "run_process", return_value=capture):
            handoff.reconcile_run(f.state, 91)
        self.assertEqual(handoff.ci_state(f.state, f.parent), "success")
        handoff.validate_state(f.state)

    def test_report_rows_preserve_each_run_attempt_and_query_error(self):
        f = self.fixture
        result = f.complete()
        self.assertTrue(f.validate(result)["handoff_ready"])
        expected = []
        for run_id, attempt, conclusion, status, query_error, ci in (
            (91, 1, "failure", "completed", False, "failure"),
            (91, 2, "success", "completed", False, "success"),
            (92, 1, "cancelled", "completed", False, "failure"),
            (93, 1, None, "in_progress", False, "pending"),
            (94, 1, "success", "completed", True, "unknown"),
            (95, 1, None, None, True, "unknown"),
            (96, 1, "success", "completed", False, "success"),
        ):
            process = f.waiting_process()
            handoff.reserve_watcher(f.state, f"watch-{run_id}-{attempt}", run_id, attempt,
                                    result["result_sha"], process.pid)
            if status is not None:
                response = self.response(result["result_sha"], conclusion, status, run_id=run_id, attempt=attempt)
                capture = raw.ProcessResult(0, observations.json_bytes(response), b"", 1, .1, 1)
                with mock.patch.object(raw, "run_process", return_value=capture):
                    handoff.reconcile_run(f.state, run_id)
            if query_error:
                with mock.patch.object(raw, "run_process", side_effect=ValueError("query unavailable")):
                    handoff.reconcile_run(f.state, run_id)
            self.assertEqual(handoff.ci_state(f.state, result["result_sha"]), ci)
            expected.append({"run_id": run_id, "attempt": attempt, "ci_state": ci})
        baseline = {"schema_version": 1, "snapshot": {"repository": f.state["repository"]}}
        envelope = reporter.with_handoff_metrics(baseline, observations.json_bytes(f.state))
        self.assertEqual(envelope["baseline"], baseline)
        self.assertEqual(envelope["implementation_handoffs"]["runs"], expected)
        self.assertEqual(handoff.ci_state(f.state, result["result_sha"]), "success")

    def test_malformed_query_bodies_are_unknown_not_exceptions_or_success(self):
        f = self.fixture
        process = f.owner_process({**f.entry, "process": None})
        handoff.reserve_watcher(f.state, "watch", 91, 2, f.parent, process.pid)
        for body in ([], {}, {**self.response(f.parent), "workflow_id": True},
                     {**self.response(f.parent), "conclusion": {}}):
            with self.subTest(body=body):
                capture = raw.ProcessResult(0, observations.json_bytes(body), b"", 1, .1, 1)
                with mock.patch.object(raw, "run_process", return_value=capture):
                    handoff.reconcile_run(f.state, 91)
                self.assertEqual(handoff.ci_state(f.state, f.parent), "unknown")


class RecoveryObservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def test_sigkill_preserves_real_dirty_index_untracked_modes_and_one_replacement(self):
        f = self.fixture
        process = f.owner_process()
        f.receive()
        tracked = f.worktree / "docs/base.txt"
        tracked.write_text("staged\n")
        git(f.worktree, "add", "docs/base.txt")
        tracked.write_text("unstaged\n")
        untracked = f.worktree / "docs/recovery.txt"
        untracked.write_text("recover this exact content\n")
        untracked.chmod(0o755)
        index_path = Path(f.entry["git_identity"]["git_dir"]) / "index"
        before = (tracked.read_bytes(), untracked.read_bytes(), untracked.stat().st_mode, index_path.read_bytes())
        handoff.begin_check(f.entry, "raw", f.parent, process)
        f.finish_owner(process, kill=True)
        raw_kernel = {"_BOOT_ID": f.entry["process"]["boot_id"],
                      "__REALTIME_TIMESTAMP": str(int(observations.timestamp(observations.utc_now()).timestamp()
                                                     * 1_000_000)),
                      "MESSAGE": f"Out of memory: Killed process {process.pid} (copilot) total-vm:1000kB"}
        def query(argv, **kwargs):
            if argv[0] == "/usr/bin/journalctl":
                return raw.ProcessResult(0, observations.json_bytes(raw_kernel), b"", 1, .1, 1)
            return actual_run(argv, **kwargs)
        actual_run = raw.run_process
        with mock.patch.object(raw, "run_process", side_effect=query):
            recovery = handoff.preserve_interruption(f.entry, "sigkill")
        self.assertIn(str(process.pid), recovery["oom_evidence"])
        self.assertIsNone(f.entry["checks"][0]["exit_code"])
        self.assertIsNone(f.entry["checks"][0]["completed_at"])
        self.assertEqual(before, (tracked.read_bytes(), untracked.read_bytes(),
                                 untracked.stat().st_mode, index_path.read_bytes()))
        lock = Path(f.entry["git_identity"]["git_dir"]) / "locked"
        self.assertEqual(lock.read_text().strip(), "handoff-recovery:assignment-one")
        replacement = {**copy.deepcopy(f.assignment), "id": "replacement", "owner_id": "new-owner",
                       "session_id": "new-session", "dispatch_id": "new-dispatch", "kind": "replacement",
                       "predecessor_id": f.assignment["id"]}
        handoff.assign(f.state, replacement)
        self.assertEqual(untracked.read_text(), "recover this exact content\n")
        with self.assertRaises(handoff.HandoffDataError):
            handoff.assign(f.state, {**replacement, "id": "second-replacement", "owner_id": "third-owner"})
        self.assertGreaterEqual(handoff.summarize_handoffs(f.state)["recovery_ms"], 0)

    def test_same_paths_do_not_hide_mutated_recovery_bytes_index_or_modes(self):
        f = self.fixture
        process = f.owner_process()
        tracked, untracked = f.worktree / "docs/base.txt", f.worktree / "docs/recovery.txt"
        tracked.write_text("staged\n")
        git(f.worktree, "add", "docs/base.txt")
        tracked.write_text("unstaged\n")
        untracked.write_text("preserved untracked\n")
        untracked.chmod(0o755)
        f.finish_owner(process)
        handoff.preserve_interruption(f.entry, "process-exit")
        index = Path(f.entry["git_identity"]["git_dir"]) / "index"
        original = [(file, file.read_bytes(), file.stat().st_mode & 0o7777)
                    for file in (tracked, untracked, index)]
        directory = f.worktree / "docs"
        directory_mode = directory.stat().st_mode & 0o7777
        observed = handoff.observe_git(f.assignment)
        replacement = {**copy.deepcopy(f.assignment), "id": "replacement", "owner_id": "replacement-owner",
                       "session_id": "replacement-session", "dispatch_id": "replacement-dispatch",
                       "kind": "replacement", "predecessor_id": f.assignment["id"]}
        def change_index():
            blob = git(f.worktree, "hash-object", "-w", "--stdin", input=b"different staged bytes\n")
            git(f.worktree, "update-index", "--cacheinfo", "100644", blob, "docs/base.txt")
        for label, mutate in (
            ("unstaged-bytes", lambda: tracked.write_text("changed unstaged\n")),
            ("untracked-bytes", lambda: untracked.write_text("changed untracked\n")),
            ("staged-index", change_index),
            ("worktree-mode", lambda: untracked.chmod(0o644)),
            ("index-mode", lambda: index.chmod((index.stat().st_mode & 0o7777) ^ 0o100)),
            ("directory-mode", lambda: directory.chmod(directory_mode ^ 0o020)),
        ):
            with self.subTest(mutation=label):
                try:
                    mutate()
                    current = handoff.observe_git(f.assignment)
                    self.assertEqual((current["head"], current["dirty_paths"]),
                                     (observed["head"], observed["dirty_paths"]))
                    with self.assertRaisesRegex(handoff.HandoffDataError, "retained recovery data changed"):
                        handoff.assign(copy.deepcopy(f.state), replacement)
                finally:
                    for file, content, mode in original:
                        file.write_bytes(content)
                        file.chmod(mode)
                    directory.chmod(directory_mode)
        handoff.assign(f.state, replacement)
        self.assertEqual([(file.read_bytes(), file.stat().st_mode & 0o7777) for file, _, _ in original],
                         [(content, mode) for _, content, mode in original])

    def test_unreadable_recovery_holds_without_closing_or_destroying_work(self):
        for unsafe in ("fifo", "oversized"):
            with self.subTest(unsafe=unsafe):
                f = GitFixture()
                self.addCleanup(f.close)
                process = f.owner_process()
                path = f.worktree / "docs/recovery"
                if unsafe == "fifo":
                    os.mkfifo(path)
                else:
                    path.write_bytes(b"x" * (raw.MAX_BYTES + 1))
                f.finish_owner(process)
                with self.assertRaisesRegex((handoff.HandoffDataError, OSError), "recovery|regular|byte"):
                    handoff.preserve_interruption(f.entry, "process-exit")
                self.assertTrue(path.exists())
                self.assertIsNone(f.entry["closed_at"])
                self.assertIsNone(f.entry["interruption"])
                lock = Path(f.entry["git_identity"]["git_dir"]) / "locked"
                self.assertEqual(lock.read_text().strip(), "handoff-recovery:" + f.assignment["id"])
                path.unlink()
                path.write_text("now safely observable\n")
                handoff.preserve_interruption(f.entry, "process-exit")
                handoff.validate_state(f.state)

    def test_recovery_does_not_read_clean_content_or_follow_symlink_targets(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        f.parent = f.commit("x" * (raw.MAX_BYTES + 1), name="docs/committed-clean.txt")
        f.assignment["assigned_parent_sha"] = f.parent
        f.entry = handoff.assign(f.state, f.assignment)
        process = f.owner_process()
        target = f.home / "outside-target"
        os.mkfifo(target)
        link = f.worktree / "docs/recovery-link"
        link.symlink_to(target)
        (f.worktree / "docs/base.txt").unlink()
        f.finish_owner(process)
        handoff.preserve_interruption(f.entry, "process-exit")
        replacement = {**copy.deepcopy(f.assignment), "id": "replacement", "owner_id": "replacement-owner",
                       "session_id": "replacement-session", "dispatch_id": "replacement-dispatch",
                       "kind": "replacement", "predecessor_id": f.assignment["id"]}
        target.unlink()
        target.write_bytes(b"x" * (raw.MAX_BYTES + 1))
        handoff.assign(copy.deepcopy(f.state), replacement)
        link.unlink()
        link.symlink_to(f.home / "different-target")
        with self.assertRaisesRegex(handoff.HandoffDataError, "retained recovery data changed"):
            handoff.assign(copy.deepcopy(f.state), replacement)
        link.unlink()
        link.symlink_to(target)
        handoff.assign(f.state, replacement)
        self.assertTrue(link.is_symlink())
        self.assertGreater(target.stat().st_size, raw.MAX_BYTES)

    def test_unproven_oom_and_live_process_cannot_be_called_confirmed(self):
        f = self.fixture
        process = f.owner_process()
        with self.assertRaisesRegex(handoff.HandoffDataError, "still live"):
            handoff.preserve_interruption(f.entry, "process-exit")
        f.finish_owner(process)
        with self.assertRaisesRegex(handoff.HandoffDataError, "SIGKILL"):
            handoff.preserve_interruption(f.entry, "sigkill")
        with mock.patch.object(raw, "run_process", side_effect=ValueError("permission denied")):
            identity = {**f.entry["process"], "exit_code": -9}
            self.assertIsNone(observations.kernel_oom_evidence(identity, f.entry["assigned_at"], observations.utc_now()))

    def test_initial_cannot_bypass_retained_worktree_replacement(self):
        f = self.fixture
        process = f.owner_process()
        f.finish_owner(process)
        recovery = handoff.preserve_interruption(f.entry, "process-exit")
        lock = Path(f.entry["git_identity"]["git_dir"]) / "locked"
        locked_bytes = lock.read_bytes()
        other = f.home / "fresh-worktree"
        git(f.repository, "worktree", "add", "-b", "agent/fresh", str(other), f.parent)
        fresh = {**copy.deepcopy(f.assignment), "id": "fresh-root", "owner_id": "fresh-owner",
                 "session_id": "fresh-session", "dispatch_id": "fresh-dispatch",
                 "expected_branch": "agent/fresh", "allowed_worktree": str(other)}
        with self.subTest(source="reservation"):
            with self.assertRaisesRegex(handoff.HandoffDataError, "initial"):
                handoff.assign(copy.deepcopy(f.state), fresh)
        with self.subTest(source="loaded"):
            separate = {**f.state, "assignments": []}
            entry = handoff.assign(separate, fresh)
            path = f.home / "bypassed-retention.json"
            write_json(path, {**f.state, "assignments": [*f.state["assignments"], entry]})
            with self.assertRaisesRegex(handoff.HandoffDataError, "initial"):
                handoff.validate_state(observations.load_json(path))
        replacement = {**fresh, "kind": "replacement", "predecessor_id": f.assignment["id"],
                       "expected_branch": f.assignment["expected_branch"],
                       "allowed_worktree": str(f.worktree), "assigned_parent_sha": recovery["head"]}
        handoff.assign(f.state, replacement)
        self.assertEqual(lock.read_bytes(), locked_bytes)
        self.assertEqual(git(f.worktree, "rev-parse", "HEAD"), recovery["head"])
        self.assertEqual(git(other, "status", "--porcelain"), "")


class HandoffCliTests(unittest.TestCase):
    def test_documented_isolated_cli_positive_negative_and_no_publication_entrypoint(self):
        f = GitFixture()
        self.addCleanup(f.close)
        result = f.complete()
        state_path, result_path = f.home / "state.json", f.home / "result.json"
        write_json(state_path, f.state)
        write_json(result_path, result)
        launcher = ROOT / "scripts/workflow_pilot/isolated_launcher.py"
        document = (ROOT / "docs/workflow-pilot.md").read_text()
        blocks = [block.split("```", 1)[0] for block in document.split("```bash\n")[1:]]
        example = next(block for block in blocks if "agent-handoff validate" in block)
        command = shlex.split(example.replace("\\\n", ""))
        self.assertEqual(command[:5], [
            "/usr/bin/python3", "-I", "$REVIEWED_SOURCE/scripts/workflow_pilot/isolated_launcher.py",
            "agent-handoff", "validate",
        ])
        replacements = {"$REVIEWED_SOURCE/scripts/workflow_pilot/isolated_launcher.py": str(launcher),
                        "$COORDINATOR_STATE": str(state_path), "$RESULT": str(result_path),
                        "$WORKTREE": str(f.worktree)}
        command = [replacements.get(part, part) for part in command]
        positive = raw.run_process(command, cwd=f.worktree, env=raw.git_environment())
        self.assertEqual(positive.returncode, 0, positive.stderr.decode())
        self.assertTrue(observations.parse_bytes(positive.stdout)["handoff_ready"])
        (f.worktree / "docs/uncommitted.txt").write_text("not ready\n")
        negative = raw.run_process(command, cwd=f.worktree, env=raw.git_environment())
        self.assertEqual(negative.returncode, 2)
        self.assertIn("dirty-worktree", observations.parse_bytes(negative.stdout)["rejection_codes"])
        rejected = raw.run_process(
            ["/usr/bin/python3", "-I", str(launcher), "agent-handoff", "publish"],
            cwd=ROOT, env=raw.git_environment(),
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(len(observations.load_json(state_path)["assignments"]), 1)


if __name__ == "__main__":
    unittest.main()
