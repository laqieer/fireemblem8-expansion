"""Real coordinator-owned checks, without a delegated owner or new result commit."""

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from jsonschema import Draft202012Validator

from scripts.workflow_pilot import adaptive_gate as gate, agent_handoff as handoff
from scripts.workflow_pilot import coordinator_observations as observations, raw_diff_check as raw
from scripts.workflow_pilot.tests.test_agent_handoff import (
    GitFixture, SESSION, at_offset, gh_stub_environment, git, native_event,
)
from scripts.workflow_pilot.tests.test_adaptive_gate import decisions, model_control
from scripts.workflow_pilot.tests.test_coordinator_observations import complete_logical_task, reserve_task, task_event


ROOT = Path(__file__).resolve().parents[3]
CHECKS = {
    "raw": {"contract": "git-diff-check", "evidence_id": "raw-v1", "inputs": []},
    "config": {"contract": "coordinator-check", "evidence_id": "config-v1", "inputs": []},
}
CONFIG_CHECK = """
import json, sys
from pathlib import Path
value = json.loads((Path(sys.argv[1]) / 'docs/value.json').read_text())
if value != {'value': 7}:
    raise SystemExit(7)
print(json.dumps({'protocol_changes': 1}))
"""


class CoordinatorLocalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture(assign=False)
        self.addCleanup(self.fixture.close)
        self.head = self.fixture.commit('{"value":7}\n', name="docs/value.json")
        self.state = handoff.new_state("owner/repository", "coordinator-one", {
            "mode": "plan", "observed_at": at_offset(-10), "valid_until": at_offset(300),
            "autostop_enabled": None, "stop_on_disconnect": None,
            "plan": "Bounded test-owned validation; no host settings asserted.",
        })
        self.pr = SimpleNamespace(
            repository=self.state["repository"], repository_id=1, number=191, head_sha=self.head,
            head_ref="agent/test", base_sha=self.fixture.parent, base_ref="master")
        decision = gate.select_mode(decisions(), number=191, head_sha=self.head,
                                    decision_oid="a" * 40, changed_lines=1)
        self.record = gate.begin_candidate(
            self.state, self.pr, self.pr.base_sha, model_control(decision, self.pr), runs=())

    def executor(self, context, head):
        self.assertEqual(head, self.head)
        result = raw.run_process(
            ["/usr/bin/python3", "-I", "-B", "-c", CONFIG_CHECK, context["allowed_worktree"]],
            cwd=ROOT, env=raw.git_environment())
        measurements = dict.fromkeys(handoff.METRICS)
        if result.returncode == 0:
            measurements.update(json.loads(result.stdout), rom_bytes=0, ram_bytes=0)
        return result, measurements

    def complete(self):
        local = gate.register_local_validation(
            self.state, self.record, self.pr, self.fixture.worktree, CHECKS)
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        gate.capture_local_check(self.state, self.record, self.pr, "raw")
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        check = gate.capture_local_check(self.state, self.record, self.pr, "config", self.executor)
        self.assertEqual(check["exit_code"], 0)
        self.assertGreater(check["pid"], 0)
        self.assertGreater(check["peak_rss_bytes"], 0)
        self.assertEqual(check["measurements"]["protocol_changes"], 1)
        self.assertTrue(gate._local_ready(self.state, self.pr, self.record))
        self.assertEqual(self.state["assignments"], [])
        return local

    def test_current_committed_head_can_complete_all_checks_without_owner_or_commit(self):
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.complete()
        self.assertEqual(handoff.observe_git({"allowed_worktree": str(self.fixture.worktree)})["head"],
                         self.head)
        self.assertIsNone(self.record["full_run_id"])
        self.assertIsNone(self.record["dispatch_requested_at"])

    def test_accepted_logical_handoff_uses_released_validation_worktree(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        result = complete_logical_task(f)
        pr = SimpleNamespace(
            repository=f.state["repository"], repository_id=1, number=191,
            head_sha=result["result_sha"], head_ref=f.assignment["expected_branch"],
            base_sha=f.parent, base_ref="master")
        decision = gate.select_mode(decisions(), number=pr.number, head_sha=pr.head_sha,
                                    decision_oid="a" * 40, changed_lines=1)
        record = gate.begin_candidate(f.state, pr, pr.base_sha, model_control(decision, pr), runs=())
        validation_worktree = f.home / "validation"
        fake_bin = Path(gh_stub_environment(
            f.home, {"name": pr.head_ref, "commit": {"sha": pr.head_sha}},
        )["PATH"].split(os.pathsep)[0])
        with mock.patch.dict(os.environ, {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}):
            handoff.release_logical_worktree(f.state, f.assignment["id"], f.repository, validation_worktree)
            verdict = handoff.validate_handoff(f.state, result, worktree=validation_worktree)
        self.assertTrue(verdict["handoff_ready"], verdict)
        self.assertFalse(f.worktree.exists())
        self.assertTrue(gate._local_ready(f.state, pr, record))
        tracked = validation_worktree / "docs/change.txt"
        original = tracked.read_bytes()
        tracked.write_bytes(b"modified after validation\n")
        self.assertFalse(gate._local_ready(f.state, pr, record))
        tracked.write_bytes(original)
        self.assertTrue(gate._local_ready(f.state, pr, record))
        temporarily_missing = f.home / "validation-missing"
        validation_worktree.rename(temporarily_missing)
        try:
            self.assertFalse(gate._local_ready(f.state, pr, record))
        finally:
            temporarily_missing.rename(validation_worktree)

        log = Path(f.entry["cursors"][0]["path"])
        f.append(log, task_event("assistant.turn_start", "unrelated-turn", owner="other-owner",
                                 parentToolCallId="other-call", interactionId="other-interaction"))
        self.assertFalse(gate._local_ready(f.state, pr, record))
        handoff.observe_cli(f.entry, log)
        self.assertTrue(gate._local_ready(f.state, pr, record))
        log.rename(f.home / "missing-log")
        try:
            self.assertFalse(gate._local_ready(f.state, pr, record))
        finally:
            (f.home / "missing-log").rename(log)
        self.assertTrue(gate._local_ready(f.state, pr, record))
        original_log = log.read_bytes()
        log.write_bytes(original_log[:-1])
        self.assertFalse(gate._local_ready(f.state, pr, record))
        log.write_bytes(original_log)
        log.rename(f.home / "original-log")
        try:
            log.write_bytes(original_log)
            self.assertFalse(gate._local_ready(f.state, pr, record))
        finally:
            log.unlink()
            (f.home / "original-log").rename(log)
        self.assertTrue(gate._local_ready(f.state, pr, record))
        with log.open("ab") as output:
            output.write(b'{"id":')
        self.assertFalse(gate._local_ready(f.state, pr, record))
        with log.open("r+b") as output:
            output.truncate(f.entry["cursors"][0]["offset"])
        self.assertTrue(gate._local_ready(f.state, pr, record))

        coordinator = f.home / "coordinator"
        git(f.repository, "worktree", "add", str(coordinator), f.assignment["expected_branch"])
        gate.register_local_validation(f.state, record, pr, coordinator, CHECKS)
        gate.capture_local_check(f.state, record, pr, "raw")

        def executor(context, head):
            check = raw.run_process(
                ["/usr/bin/python3", "-I", "-c",
                 "from pathlib import Path; assert Path('docs/change.txt').read_text() == 'change\\n'"],
                cwd=Path(context["allowed_worktree"]), env=raw.git_environment())
            measurements = dict.fromkeys(handoff.METRICS, 0)
            return check, measurements

        gate.capture_local_check(f.state, record, pr, "config", executor)
        self.assertTrue(gate._local_ready(f.state, pr, record))
        f.append(log, task_event("assistant.turn_start", "late-owner", owner=f.assignment["owner_id"],
                                 parentToolCallId=f.assignment["dispatch_id"],
                                 interactionId="interaction-one"))
        self.assertFalse(gate._local_ready(f.state, pr, record))
        with self.assertRaisesRegex(handoff.HandoffDataError, "closed native owner"):
            handoff.observe_cli(f.entry, log)

    def test_review_successor_cannot_hide_unread_predecessor_activity(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        first_result = complete_logical_task(f)
        first_entry = f.entry
        log = Path(first_entry["cursors"][0]["path"])
        validation = f.home / "validation"
        environment = gh_stub_environment(
            f.home, {"name": f.assignment["expected_branch"],
                     "commit": {"sha": first_result["result_sha"]}},
        )
        with mock.patch.dict(os.environ, {"PATH": environment["PATH"]}):
            handoff.release_logical_worktree(f.state, f.assignment["id"], f.repository, validation)
            self.assertTrue(handoff.validate_handoff(
                f.state, first_result, worktree=validation)["handoff_ready"])

        successor_worktree = f.home / "successor"
        git(f.repository, "worktree", "add", str(successor_worktree), f.assignment["expected_branch"])
        successor = copy.deepcopy(f.assignment)
        del successor["native"]
        successor.update(id="review-two", owner_id="review-owner", session_id="review-session",
                         dispatch_id="review-dispatch", kind="review", predecessor_id=f.assignment["id"],
                         assigned_parent_sha=first_result["result_sha"], allowed_worktree=str(successor_worktree))
        f.entry = handoff.assign(f.state, successor)
        f.assignment, f.worktree, f.parent = successor, successor_worktree, first_result["result_sha"]
        f.parent_log, f.owner_log = f.home / "successor-parent.jsonl", f.home / "successor-owner.jsonl"
        with mock.patch("scripts.workflow_pilot.tests.test_agent_handoff.SESSION", successor["session_id"]):
            result = f.complete(content="successor change\n")
        verdict = f.validate(result)
        self.assertTrue(verdict["handoff_ready"], verdict)
        pr = SimpleNamespace(
            repository=f.state["repository"], repository_id=1, number=191,
            head_sha=result["result_sha"], head_ref=successor["expected_branch"],
            base_sha=first_result["assigned_parent_sha"], base_ref="master")
        decision = gate.select_mode(decisions(), number=pr.number, head_sha=pr.head_sha,
                                    decision_oid="a" * 40, changed_lines=1)
        record = gate.begin_candidate(f.state, pr, pr.base_sha, model_control(decision, pr), runs=())
        self.assertTrue(gate._local_ready(f.state, pr, record))
        f.append(log, task_event("assistant.turn_start", "late-predecessor", owner="owner-one",
                                 parentToolCallId="call-one", interactionId="interaction-one"))
        self.assertFalse(gate._local_ready(f.state, pr, record))
        with self.assertRaisesRegex(handoff.HandoffDataError, "closed native owner"):
            handoff.observe_cli(first_entry, log)

    def test_delayed_process_completion_cannot_attribute_successor_head(self):
        for success in (True, False):
            with self.subTest(success=success):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                f.append(log,
                         task_event("tool.execution_start", "dispatch", toolName="task", toolCallId="call-one",
                                    arguments={"prompt": prompt}),
                         task_event("subagent.started", "started", owner="owner-one", toolCallId="call-one",
                                    interactionId="interaction-one"))
                handoff.observe_cli(f.entry, log)
                process = f.waiting_process()
                handoff.bind_process(f.entry, process.pid)
                f.append(log,
                         task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                    source=f"agent-{root}", interactionId="interaction-one",
                                    parentAgentTaskId="receipt-task"),
                         task_event("tool.execution_start", "progress", owner="owner-one", toolName="bash",
                                    toolCallId="edit", ownerNativeSessionId=SESSION))
                handoff.observe_cli(f.entry, log)
                revision = f.commit()
                result = {"schema_version": 3, "assignment_id": f.assignment["id"],
                          "assigned_parent_sha": f.parent, "result_sha": revision,
                          "evidence_refs": ["raw-evidence"]}
                f.append(log, task_event("assistant.message", "delivery", owner="owner-one",
                                         interactionId="interaction-one",
                                         content=json.dumps({"handoff_result": result})))
                handoff.observe_cli(f.entry, log)
                handoff.capture_check(f.entry, "raw", revision)
                f.finish_owner(process)
                late_completion = native_event("tool.execution_complete", "task-terminal",
                                               toolCallId="call-one", success=success)
                verdict = handoff.validate_handoff(f.state, result, worktree=f.worktree, run_checks=False)
                self.assertTrue(verdict["handoff_ready"], verdict)
                self.assertLess(handoff.timestamp(late_completion["timestamp"]),
                                handoff.timestamp(verdict["observed_at"]))
                first_entry, first_parent, closed_at = f.entry, f.parent, f.entry["closed_at"]

                successor = copy.deepcopy(f.assignment)
                del successor["native"]
                successor.update(id="review-two", owner_id="review-owner", session_id="review-session",
                                 dispatch_id="review-dispatch", kind="review", predecessor_id=f.assignment["id"],
                                 assigned_parent_sha=revision)
                f.entry = handoff.assign(f.state, successor)
                f.assignment, f.parent = successor, revision
                f.parent_log, f.owner_log = f.home / "successor-parent.jsonl", f.home / "successor-owner.jsonl"
                with mock.patch("scripts.workflow_pilot.tests.test_agent_handoff.SESSION", successor["session_id"]):
                    successor_result = f.complete(content="successor change\n")
                verdict = f.validate(successor_result)
                self.assertTrue(verdict["handoff_ready"], verdict)
                pr = SimpleNamespace(
                    repository=f.state["repository"], repository_id=1, number=191,
                    head_sha=successor_result["result_sha"], head_ref=successor["expected_branch"],
                    base_sha=first_parent, base_ref="master")
                decision = gate.select_mode(decisions(), number=pr.number, head_sha=pr.head_sha,
                                            decision_oid="a" * 40, changed_lines=1)
                record = gate.begin_candidate(f.state, pr, pr.base_sha, model_control(decision, pr), runs=())
                self.assertTrue(gate._local_ready(f.state, pr, record))
                f.append(log, late_completion)
                self.assertFalse(gate._local_ready(f.state, pr, record))
                state_path = f.home / "coordinator.json"
                state_path.write_bytes(observations.json_bytes(f.state))
                with observations.locked_state(state_path) as persisted:
                    handoff.observe_cli(handoff.find_entry(persisted, first_entry["assignment"]["id"]), log)
                    handoff.validate_state(persisted)
                persisted = observations.load_json(state_path)
                first_entry = handoff.find_entry(persisted, first_entry["assignment"]["id"])
                record = persisted["candidates"][0]
                handoff.validate_state(persisted)
                validator = Draft202012Validator(json.loads(
                    (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()))
                self.assertTrue(validator.is_valid(persisted))
                self.assertEqual(first_entry["closed_at"], closed_at)
                self.assertEqual(first_entry["terminal_task"]["success"], success)
                self.assertNotIn("head_sha", first_entry["terminal_task"])
                self.assertEqual(gate._local_ready(persisted, pr, record), success)
                if not success:
                    summary = handoff.summarize_handoffs(persisted)
                    self.assertEqual(summary["rejected"], 1)
                    self.assertIn("owner-task-failed", summary["rejection_codes"])
                handoff.observe_cli(first_entry, log)
                self.assertEqual(gate._local_ready(persisted, pr, record), success)

    def test_raw_only_and_candidate_success_or_commands_are_not_registration(self):
        for definitions in (
            {"raw": CHECKS["raw"]},
            {**CHECKS, "config": {**CHECKS["config"], "program": "passed"}},
            {**CHECKS, "config": {**CHECKS["config"], "inputs": ["candidate.py"]}},
        ):
            with self.subTest(definitions=definitions), self.assertRaises(ValueError):
                gate.register_local_validation(
                    self.state, self.record, self.pr, self.fixture.worktree, definitions)
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_missing_failed_incomplete_and_changed_definitions_never_reuse_success(self):
        local = self.complete()
        good = copy.deepcopy(local)
        mutations = [
            lambda value: value["checks"].pop("config"),
            lambda value: value["checks"]["config"]["observation"].update(exit_code=7),
            lambda value: value["checks"]["config"]["observation"].update(completed_at=None, exit_code=None),
            lambda value: value["checks"]["config"]["observation"].update(pid=None),
            lambda value: value["checks"]["config"]["observation"].update(peak_rss_bytes=None),
            lambda value: value["checks"]["config"]["observation"].update(result_sha="b" * 40),
            lambda value: value["required_checks"]["config"].update(evidence_id="config-v2"),
            lambda value: value["required_checks"].update(extra={**CHECKS["config"], "evidence_id": "extra"}),
        ]
        for change in mutations:
            self.record["local_validation"] = copy.deepcopy(good)
            change(self.record["local_validation"])
            self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.record["local_validation"] = good
        missing = gate.capture_local_check(
            self.state, self.record, self.pr, "config", lambda *_: ({"passed": True}, {}))
        self.assertIsNone(missing["exit_code"])
        self.assertIn("unavailable", missing["detail"])
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, CHECKS)
        self.assertEqual(self.record["local_validation"]["checks"], {})

    def test_worktree_base_identity_and_availability_are_reobserved(self):
        local = self.complete()
        good = copy.deepcopy(local)
        for field, value in (("head_sha", "b" * 40), ("base_sha", "b" * 40),
                             ("base_ref", "other"), ("coordinator_id", "other"),
                             ("worktree", str(self.fixture.repository))):
            self.record["local_validation"] = copy.deepcopy(good)
            self.record["local_validation"][field] = value
            self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.record["local_validation"] = copy.deepcopy(good)
        self.record["local_validation"]["git_identity"]["inode"] += 1
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.record["local_validation"] = copy.deepcopy(good)
        changed_base = SimpleNamespace(**{**vars(self.pr), "base_sha": self.head})
        self.assertFalse(gate._local_ready(self.state, changed_base, self.record))
        path = self.fixture.worktree / "docs/value.json"
        path.write_text('{"value":8}\n')
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        path.write_text('{"value":7}\n')
        self.record["local_validation"]["clock"]["boot_id"] = "changed-boot"
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.record["local_validation"] = copy.deepcopy(good)
        self.state["availability"]["valid_until"] = at_offset(-1)
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        with self.assertRaises(ValueError):
            gate.capture_local_check(self.state, self.record, self.pr, "config", self.executor)

    def test_real_failed_native_capture_replaces_prior_success(self):
        self.complete()

        def failed(context, head):
            result = raw.run_process(
                ["/usr/bin/python3", "-I", "-B", "-c", "raise SystemExit(7)"],
                cwd=ROOT, env=raw.git_environment())
            return result, dict.fromkeys(handoff.METRICS)

        check = gate.capture_local_check(self.state, self.record, self.pr, "config", failed)
        self.assertEqual(check["exit_code"], 7)
        self.assertGreater(check["pid"], 0)
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_applicable_incomplete_delegation_cannot_be_hidden(self):
        self.complete()
        assignment = copy.deepcopy(self.fixture.assignment)
        assignment["assigned_parent_sha"] = self.head
        entry = handoff.assign(self.state, assignment)
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        with self.assertRaises(ValueError):
            gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, CHECKS)
        entry["closed_at"] = observations.utc_now()
        with self.assertRaisesRegex(handoff.HandoffDataError, "logical ownership release is unavailable"):
            gate._local_ready(self.state, self.pr, self.record)
        with self.assertRaises(ValueError):
            gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, CHECKS)

    def test_capture_detects_real_worktree_mutation_and_loses_prior_success(self):
        self.complete()

        def changing(context, head):
            result, measurements = self.executor(context, head)
            (self.fixture.worktree / "docs/value.json").write_text('{"value":8}\n')
            return result, measurements

        check = gate.capture_local_check(self.state, self.record, self.pr, "config", changing)
        self.assertIsNone(check["exit_code"])
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        (self.fixture.worktree / "docs/value.json").write_text('{"value":7}\n')
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_registry_change_during_real_capture_invalidates_the_observation(self):
        self.complete()

        def changed(context, head):
            result = self.executor(context, head)
            self.record["local_validation"]["required_checks"]["config"]["evidence_id"] = "config-v2"
            return result

        check = gate.capture_local_check(self.state, self.record, self.pr, "config", changed)
        self.assertIsNone(check["exit_code"])
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_removing_a_criterion_and_its_result_cannot_shrink_registered_coverage(self):
        definitions = {**CHECKS, "second": {**CHECKS["config"], "evidence_id": "second-v1"}}
        local = gate.register_local_validation(
            self.state, self.record, self.pr, self.fixture.worktree, definitions)
        for check_id in definitions:
            gate.capture_local_check(self.state, self.record, self.pr, check_id,
                                     self.executor if check_id != "raw" else None)
        self.assertTrue(gate._local_ready(self.state, self.pr, self.record))
        del local["required_checks"]["second"]
        del local["checks"]["second"]
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_schema_and_runtime_reuse_closed_check_types(self):
        self.complete()
        validator = Draft202012Validator(json.loads(
            (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()))
        self.assertTrue(validator.is_valid(self.state))
        handoff.validate_state(self.state)
        for field, value in (("local_ready", True), ("program", "success"), ("checks", []),
                             ("required_checks", {"raw": CHECKS["raw"]})):
            state = copy.deepcopy(self.state)
            state["candidates"][0]["local_validation"][field] = value
            self.assertFalse(validator.is_valid(state))
            with self.assertRaises(ValueError):
                handoff.validate_state(state)
