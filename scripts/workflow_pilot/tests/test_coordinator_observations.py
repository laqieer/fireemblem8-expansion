import copy
import fcntl
import json
import os
import shlex
import subprocess
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from scripts.workflow_pilot import agent_handoff as handoff
from scripts.workflow_pilot import coordinator_observations as observations
from scripts.workflow_pilot import raw_diff_check as raw
from scripts.workflow_pilot import reporter
from scripts.workflow_pilot.tests.test_agent_handoff import (
    GitFixture, ROOT, SESSION, at_offset, gh_stub_environment, git, native_event, write_json,
)


def owner_events(fixture, result, prefix):
    return [
        native_event("user.message", prefix + "-receipt", content=f"[handoff:{fixture.assignment['id']}]",
                     parentAgentTaskId=fixture.assignment["dispatch_id"]),
        native_event("assistant.turn_start", prefix + "-turn"),
        native_event("tool.execution_start", prefix + "-progress", toolCallId=prefix + "-edit", toolName="bash"),
        native_event("assistant.message", prefix + "-delivery", content=json.dumps({"handoff_result": result})),
    ]


def task_event(kind, event_id, owner=None, **data):
    event = native_event(kind, event_id, **data)
    if owner is not None:
        event["agentId"] = owner
    return event


def reserve_task(fixture, *, state=None, log=None):
    log = log or fixture.parent_log
    root = "coordinator-session"
    if not log.exists():
        fixture.append(log, native_event("session.start", "root-start", sessionId=root))
    prompt = f"[handoff:{fixture.assignment['id']}] Implement the assigned change."
    fixture.assignment.update({
        "owner_id": None, "session_id": None, "dispatch_id": None,
        "native": {"root_session_id": root, "events_path": str(log), "prompt": prompt},
    })
    fixture.entry = handoff.assign(state or fixture.state, fixture.assignment)
    return log, root, prompt


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

    def test_shared_native_task_binds_real_lifecycle_without_inventing_process_exit(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        log, root, prompt = reserve_task(f)
        assignment = f.entry["assignment"]
        schema = json.loads((ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text())
        validator = Draft202012Validator(schema)
        validator.validate(assignment)
        validator.validate(f.state)
        invalid = copy.deepcopy(assignment)
        invalid["owner_id"] = "unobserved-owner"
        self.assertFalse(validator.is_valid(invalid))
        with self.assertRaises(handoff.HandoffDataError):
            handoff.validate_assignment(invalid)
        self.assertIsNone(assignment["dispatch_id"])
        self.assertIsNone(assignment["owner_id"])
        self.assertIsNone(assignment["session_id"])

        f.append(log, task_event("tool.execution_start", "task-dispatch", toolName="task",
                                 toolCallId="call-one", arguments={"prompt": prompt}))
        handoff.observe_cli(f.entry, log)
        self.assertEqual([item["state"] for item in f.entry["events"]], ["assignment_sent"])
        self.assertEqual(assignment["dispatch_id"], "call-one")

        f.append(log, task_event("subagent.started", "task-started", owner="owner-one",
                                 toolCallId="call-one", interactionId="shared-interaction"))
        handoff.observe_cli(f.entry, log)
        self.assertEqual(assignment["owner_id"], "owner-one")
        self.assertEqual(len(f.entry["events"]), 1)

        f.append(log, task_event("user.message", "task-receipt", owner="owner-one", content=prompt,
                                 source=f"agent-{root}", interactionId="shared-interaction",
                                 parentAgentTaskId="separate-parent-task"))
        handoff.observe_cli(f.entry, log)
        self.assertEqual([item["state"] for item in f.entry["events"]],
                         ["assignment_sent", "assignment_received"])
        self.assertIsNone(assignment["session_id"])

        f.append(log, task_event("tool.execution_start", "other-progress", owner="other-owner",
                                 toolName="bash", toolCallId="other-call",
                                 ownerNativeSessionId="other-session"))
        handoff.observe_cli(f.entry, log)
        self.assertEqual(len(f.entry["events"]), 2)
        f.append(log, task_event("tool.execution_start", "task-progress", owner="owner-one",
                                 toolName="bash", toolCallId="owner-call", ownerNativeSessionId=SESSION))
        handoff.observe_cli(f.entry, log)
        self.assertEqual(assignment["session_id"], SESSION)
        self.assertEqual([item["state"] for item in f.entry["events"]],
                         ["assignment_sent", "assignment_received", "progressing"])
        validator.validate(f.state)

        revision = f.commit()
        result = {"schema_version": 3, "assignment_id": assignment["id"],
                  "assigned_parent_sha": f.parent, "result_sha": revision,
                  "evidence_refs": ["raw-evidence"]}
        f.append(log, task_event("assistant.message", "task-delivery", owner="owner-one",
                                 interactionId="shared-interaction",
                                 content=json.dumps({"handoff_result": result})))
        handoff.observe_cli(f.entry, log)
        verdict = f.validate(result)
        self.assertFalse(verdict["handoff_ready"])
        self.assertIn("owner-exit-unknown", verdict["rejection_codes"])
        self.assertIn("owner-rss-unknown", verdict["rejection_codes"])
        self.assertEqual([item["state"] for item in f.entry["events"]], list(handoff.STATES))

    def test_terminal_task_event_does_not_claim_logical_ownership_release(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        log, root, prompt = reserve_task(f)
        f.append(log,
                 task_event("tool.execution_start", "dispatch", toolName="task",
                            toolCallId="call-one", arguments={"prompt": prompt}),
                 task_event("subagent.started", "started", owner="owner-one",
                            toolCallId="call-one", interactionId="interaction-one"),
                 task_event("user.message", "receipt", owner="owner-one", content=prompt,
                            source=f"agent-{root}", interactionId="interaction-one",
                            parentAgentTaskId="receipt-task"),
                 task_event("tool.execution_start", "progress", owner="owner-one", toolName="bash",
                            toolCallId="owner-call", ownerNativeSessionId=SESSION))
        handoff.observe_cli(f.entry, log)
        revision = f.commit()
        result = {"schema_version": 3, "assignment_id": f.assignment["id"],
                  "assigned_parent_sha": f.parent, "result_sha": revision,
                  "evidence_refs": ["raw-evidence"]}
        f.append(log,
                 task_event("assistant.message", "delivery", owner="owner-one",
                            interactionId="interaction-one",
                            content=json.dumps({"handoff_result": result})),
                 native_event("tool.execution_complete", "task-terminal",
                              toolCallId="call-one", success=True))
        handoff.observe_cli(f.entry, log)

        terminal = f.entry["terminal_task"]
        self.assertEqual(terminal["task_id"], f.assignment["dispatch_id"])
        self.assertEqual(terminal["owner_id"], f.assignment["owner_id"])
        self.assertEqual(terminal["session_id"], SESSION)
        self.assertEqual(terminal["head_sha"], revision)
        self.assertEqual(terminal["worktree"], f.assignment["allowed_worktree"])
        self.assertEqual(terminal["allowed_scope"], f.assignment["allowed_scope"])
        self.assertIsNone(terminal["rss_peak_bytes"])
        self.assertIn("does not expose per-owner RSS", terminal["rss_unavailable_reason"])
        self.assertIsNone(f.entry["process"])
        self.assertIsNone(f.entry["closed_at"])
        self.assertIn("owner-worktree-release-unavailable",
                      handoff.owner_completion_errors(f.entry))
        handoff.validate_state(f.state)
        Draft202012Validator(json.loads(
            (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()
        )).validate(f.state)
        premature_release = copy.deepcopy(f.state)
        premature_release["assignments"][0]["closed_at"] = terminal["observed_at"]
        with self.assertRaisesRegex(
                handoff.HandoffDataError, "logical ownership release requires coordinator validation"):
            handoff.validate_state(premature_release)

        delayed_validation = (
            handoff.timestamp(terminal["observed_at"])
            + timedelta(seconds=f.assignment["max_lifetime_seconds"] + 1)
        ).isoformat().replace("+00:00", "Z")
        self.assertEqual(
            handoff.owner_lifetime_ms(f.entry, delayed_validation),
            handoff.owner_lifetime_ms(f.entry, terminal["observed_at"]),
        )
        self.assertNotIn(
            "owner-lifetime-exceeded",
            handoff.owner_acceptance_errors(f.entry, delayed_validation),
        )

        verdict = f.validate(result)
        self.assertFalse(verdict["handoff_ready"], verdict)
        self.assertEqual(verdict["local_outcome"], "rejected")
        self.assertIn("owner-worktree-release-unavailable", verdict["rejection_codes"])
        self.assertIsNone(f.entry["closed_at"])
        self.assertEqual(f.entry["validation"], verdict)
        repeated_verdict = f.validate(result)
        self.assertFalse(repeated_verdict["handoff_ready"], repeated_verdict)
        self.assertIn("owner-worktree-release-unavailable", repeated_verdict["rejection_codes"])
        self.assertIsNone(f.entry["closed_at"])
        self.assertEqual(handoff.summarize_handoffs(f.state)["rejected"], 1)
        handoff.validate_state(f.state)
        Draft202012Validator(json.loads(
            (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()
        )).validate(f.state)

        for field, value in (("task_id", "other-task"), ("owner_id", "other-owner"),
                             ("session_id", "other-session"), ("session_id", None), ("head_sha", f.parent),
                             ("worktree", str(f.repository)), ("allowed_scope", ["src/"]),
                             ("ownership_released", True)):
            with self.subTest(field=field):
                changed = copy.deepcopy(f.state)
                changed["assignments"][0]["terminal_task"][field] = value
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.validate_state(changed)

    def test_owner_tagged_task_completion_cannot_establish_coordinator_terminal(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        log, root, prompt = reserve_task(f)
        f.append(log,
                 task_event("tool.execution_start", "dispatch", toolName="task",
                            toolCallId="call-one", arguments={"prompt": prompt}),
                 task_event("subagent.started", "started", owner="owner-one",
                            toolCallId="call-one", interactionId="interaction-one"),
                 task_event("user.message", "receipt", owner="owner-one", content=prompt,
                            source=f"agent-{root}", interactionId="interaction-one",
                            parentAgentTaskId="receipt-task"),
                 task_event("tool.execution_start", "progress", owner="owner-one", toolName="bash",
                            toolCallId="owner-call", ownerNativeSessionId=SESSION))
        handoff.observe_cli(f.entry, log)
        offset = f.entry["cursors"][0]["offset"]

        f.append(log, task_event("tool.execution_complete", "owner-terminal", owner="owner-one",
                                 sessionId=root, toolCallId="call-one", success=True))
        with self.assertRaisesRegex(handoff.HandoffDataError, "coordinator"):
            handoff.observe_cli(f.entry, log)
        self.assertEqual(f.entry["cursors"][0]["offset"], offset)
        self.assertIsNone(f.entry["terminal_task"])

    def test_failed_logical_task_does_not_close_or_preserve_wip_without_release(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        log, root, prompt = reserve_task(f)
        f.append(log,
                 task_event("tool.execution_start", "dispatch", toolName="task",
                            toolCallId="call-one", arguments={"prompt": prompt}),
                 task_event("subagent.started", "started", owner="owner-one",
                            toolCallId="call-one", interactionId="interaction-one"),
                 task_event("user.message", "receipt", owner="owner-one", content=prompt,
                            source=f"agent-{root}", interactionId="interaction-one",
                            parentAgentTaskId="receipt-task"),
                 task_event("tool.execution_start", "progress", owner="owner-one", toolName="bash",
                            toolCallId="owner-call", ownerNativeSessionId=SESSION))
        handoff.observe_cli(f.entry, log)
        f.commit()
        f.append(log, native_event("tool.execution_complete", "task-failed",
                                   toolCallId="call-one", success=False))
        handoff.observe_cli(f.entry, log)

        self.assertFalse(f.entry["terminal_task"]["success"])
        self.assertIsNone(f.entry["interruption"])
        self.assertIsNone(f.entry["closed_at"])
        self.assertIn("owner-task-failed", handoff.owner_completion_errors(f.entry))
        self.assertNotIn("owner-not-retired", handoff.owner_completion_errors(f.entry))
        self.assertFalse((Path(f.entry["git_identity"]["git_dir"]) / "locked").exists())
        self.assertEqual(handoff.summarize_handoffs(f.state)["in_progress"], 1)

    def test_failed_logical_task_cannot_be_preserved_without_release(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        log, root, prompt = reserve_task(f)
        f.append(log,
                 task_event("tool.execution_start", "dispatch", toolName="task",
                            toolCallId="call-one", arguments={"prompt": prompt}),
                 task_event("subagent.started", "started", owner="owner-one",
                            toolCallId="call-one", interactionId="interaction-one"),
                 task_event("user.message", "receipt", owner="owner-one", content=prompt,
                            source=f"agent-{root}", interactionId="interaction-one",
                            parentAgentTaskId="receipt-task"),
                 task_event("tool.execution_start", "progress", owner="owner-one", toolName="bash",
                            toolCallId="owner-call", ownerNativeSessionId=SESSION))
        handoff.observe_cli(f.entry, log)
        f.append(log, native_event("tool.execution_complete", "task-failed",
                                   toolCallId="call-one", success=False))
        handoff.observe_cli(f.entry, log)

        before = handoff.observe_git(f.assignment)
        lock = Path(f.entry["git_identity"]["git_dir"]) / "locked"
        with self.assertRaisesRegex(handoff.HandoffDataError, "invalid enum"):
            handoff.preserve_interruption(f.entry, "task-failed")

        self.assertEqual(handoff.observe_git(f.assignment), before)
        self.assertFalse(lock.exists())
        self.assertIsNone(f.entry["closed_at"])
        self.assertIsNone(f.entry["interruption"])

    def test_shared_native_receipt_rejects_spliced_or_missing_observations(self):
        for variant in ("missing-start", "wrong-owner", "wrong-root", "wrong-interaction",
                        "wrong-prompt", "missing-parent", "stale-receipt"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                events = [task_event("tool.execution_start", "dispatch", toolName="task",
                                     toolCallId="call-one", arguments={"prompt": prompt})]
                if variant != "missing-start":
                    events.append(task_event("subagent.started", "started", owner="owner-one",
                                             toolCallId="call-one", interactionId="interaction-one"))
                receipt = task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                     source=f"agent-{root}", interactionId="interaction-one",
                                     parentAgentTaskId="receipt-task")
                if variant == "wrong-owner":
                    receipt["agentId"] = "other-owner"
                elif variant == "wrong-root":
                    receipt["data"]["source"] = "agent-other-root"
                elif variant == "wrong-interaction":
                    receipt["data"]["interactionId"] = "other-interaction"
                elif variant == "wrong-prompt":
                    receipt["data"]["content"] += " altered"
                elif variant == "missing-parent":
                    del receipt["data"]["parentAgentTaskId"]
                elif variant == "stale-receipt":
                    receipt["timestamp"] = at_offset(-3600)
                events.append(receipt)
                events.append(task_event("tool.execution_start", "progress", owner="owner-one",
                                         toolName="bash", toolCallId="owner-call",
                                         ownerNativeSessionId=SESSION))
                f.append(log, *events)
                handoff.observe_cli(f.entry, log)
                self.assertEqual([item["state"] for item in f.entry["events"]], ["assignment_sent"])
                self.assertIsNone(f.entry["assignment"]["session_id"])

    def test_native_followup_cannot_rebind_the_received_parent_task(self):
        for variant in ("same-interaction", "changed-interaction", "changed-interaction-only"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                f.append(log,
                         task_event("tool.execution_start", "dispatch", toolName="task",
                                    toolCallId="call-one", arguments={"prompt": prompt}),
                         task_event("subagent.started", "started", owner="owner-one",
                                    toolCallId="call-one", interactionId="interaction-one"),
                         task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                    source=f"agent-{root}", interactionId="interaction-one",
                                    parentAgentTaskId="receipt-task"))
                handoff.observe_cli(f.entry, log)
                f.append(log, task_event("user.message", "followup", owner="owner-one",
                                         content="Additional instructions", source=f"agent-{root}",
                                         interactionId="interaction-one",
                                         parentAgentTaskId="receipt-task"))
                handoff.observe_cli(f.entry, log)
                self.assertEqual([item["state"] for item in f.entry["events"]],
                                 ["assignment_sent", "assignment_received"])
                interaction = ("interaction-one" if variant == "same-interaction"
                               else "different-interaction")
                parent = ("receipt-task" if variant == "changed-interaction-only"
                          else "other-task" if variant == "same-interaction" else "different-task")
                f.append(log, task_event("user.message", "contradiction", owner="owner-one",
                                         content="Additional instructions", source=f"agent-{root}",
                                         interactionId=interaction, parentAgentTaskId=parent))
                message = ("receipt interaction changed" if variant == "changed-interaction-only"
                           else "receipt task changed")
                with self.assertRaisesRegex(handoff.HandoffDataError, message):
                    handoff.observe_cli(f.entry, log)

    def test_native_receipt_replay_or_conflicting_interaction_is_rejected(self):
        for variant in ("replay", "new-id", "changed-interaction"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                dispatch = task_event("tool.execution_start", "dispatch", toolName="task",
                                      toolCallId="call-one", arguments={"prompt": prompt})
                started = task_event("subagent.started", "started", owner="owner-one",
                                     toolCallId="call-one", interactionId="interaction-one")
                receipt = task_event(
                    "user.message", "receipt", owner="owner-one", content=prompt,
                    source=f"agent-{root}", interactionId="interaction-one",
                    parentAgentTaskId="receipt-task")
                f.append(log, dispatch, started, receipt)
                handoff.observe_cli(f.entry, log)
                duplicate = copy.deepcopy(receipt)
                if variant != "replay":
                    duplicate["id"] = "another-receipt"
                if variant == "changed-interaction":
                    duplicate["data"]["interactionId"] = "other-interaction"
                    duplicate["data"]["parentAgentTaskId"] = "other-task"
                f.append(log, duplicate)
                expected = ("native owner event source reused" if variant == "replay"
                            else "native receipt")
                with self.assertRaisesRegex(handoff.HandoffDataError, expected):
                    handoff.observe_cli(f.entry, log)

    def test_native_followup_participates_in_progress_chronology(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        log, root, prompt = reserve_task(f)
        dispatch = task_event("tool.execution_start", "dispatch", toolName="task",
                              toolCallId="call-one", arguments={"prompt": prompt})
        started = task_event("subagent.started", "started", owner="owner-one",
                             toolCallId="call-one", interactionId="interaction-one")
        receipt = task_event("user.message", "receipt", owner="owner-one", content=prompt,
                             source=f"agent-{root}", interactionId="interaction-one",
                             parentAgentTaskId="receipt-task")
        progress = task_event("tool.execution_start", "progress", owner="owner-one",
                              toolName="bash", toolCallId="edit", ownerNativeSessionId=SESSION)
        followup = task_event("user.message", "followup", owner="owner-one",
                              content="Additional instructions", source=f"agent-{root}",
                              interactionId="interaction-one", parentAgentTaskId="receipt-task")
        f.append(log, dispatch, started, receipt, followup, progress)
        with self.assertRaisesRegex(handoff.HandoffDataError, "chronology reversed"):
            handoff.observe_cli(f.entry, log)

    def test_native_progress_rejects_replayed_event_id_and_accepts_distinct_start(self):
        for variant in ("replay", "distinct"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                f.append(log,
                         task_event("tool.execution_start", "dispatch", toolName="task",
                                    toolCallId="call-one", arguments={"prompt": prompt}),
                         task_event("subagent.started", "started", owner="owner-one",
                                    toolCallId="call-one", interactionId="interaction-one"),
                         task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                    source=f"agent-{root}", interactionId="interaction-one",
                                    parentAgentTaskId="receipt-task"))
                handoff.observe_cli(f.entry, log)
                progress = task_event("tool.execution_start", "progress", owner="owner-one",
                                      toolName="bash", toolCallId="edit-one",
                                      ownerNativeSessionId=SESSION)
                f.append(log, progress)
                handoff.observe_cli(f.entry, log)
                replay = copy.deepcopy(progress)
                if variant == "distinct":
                    replay["id"] = "distinct-progress"
                    replay["data"]["toolCallId"] = "edit-two"
                f.append(log, replay)
                if variant == "replay":
                    with self.assertRaisesRegex(handoff.HandoffDataError,
                                                "native owner event source reused"):
                        handoff.observe_cli(f.entry, log)
                else:
                    handoff.observe_cli(f.entry, log)
                    self.assertEqual(f.entry["events"][-1]["source_id"], "progress")
                    self.assertEqual(f.entry["cursors"][0]["offset"], log.stat().st_size)

    def test_native_stale_event_cannot_reuse_a_lifecycle_source_id(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        log, root, prompt = reserve_task(f)
        f.append(log,
                 task_event("tool.execution_start", "dispatch", toolName="task",
                            toolCallId="call-one", arguments={"prompt": prompt}),
                 task_event("subagent.started", "started", owner="owner-one",
                            toolCallId="call-one", interactionId="interaction-one"),
                 task_event("user.message", "receipt", owner="owner-one", content=prompt,
                            source=f"agent-{root}", interactionId="interaction-one",
                            parentAgentTaskId="receipt-task"),
                 task_event("tool.execution_start", "progress", owner="owner-one",
                            toolName="bash", toolCallId="owner-call",
                            ownerNativeSessionId=SESSION))
        handoff.observe_cli(f.entry, log)
        offset = f.entry["cursors"][0]["offset"]

        stale = task_event("assistant.turn_start", "progress", owner="owner-one",
                           parentToolCallId="call-one", interactionId="interaction-one")
        stale["timestamp"] = at_offset(-3600)
        f.append(log, stale)
        with self.assertRaisesRegex(handoff.HandoffDataError, "native owner event source reused"):
            handoff.observe_cli(f.entry, log)
        self.assertEqual(f.entry["cursors"][0]["offset"], offset)

    def test_native_lifecycle_source_id_cannot_be_reused_by_owner_turn(self):
        for variant in ("replay", "distinct"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                f.append(log,
                         task_event("tool.execution_start", "dispatch", toolName="task",
                                    toolCallId="call-one", arguments={"prompt": prompt}),
                         task_event("subagent.started", "started", owner="owner-one",
                                    toolCallId="call-one", interactionId="interaction-one"),
                         task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                    source=f"agent-{root}", interactionId="interaction-one",
                                    parentAgentTaskId="receipt-task"))
                handoff.observe_cli(f.entry, log)
                f.append(log, task_event("tool.execution_start", "progress", owner="owner-one",
                                         toolName="bash", toolCallId="edit",
                                         ownerNativeSessionId=SESSION))
                handoff.observe_cli(f.entry, log)
                previous_offset = f.entry["cursors"][0]["offset"]
                event_id = "progress" if variant == "replay" else "distinct-turn"
                f.append(log, task_event("assistant.turn_start", event_id, owner="owner-one",
                                         parentToolCallId="call-one",
                                         interactionId="interaction-one"))
                if variant == "replay":
                    with self.assertRaisesRegex(handoff.HandoffDataError,
                                                "native owner event source reused"):
                        handoff.observe_cli(f.entry, log)
                    self.assertEqual(f.entry["cursors"][0]["offset"], previous_offset)
                    self.assertEqual(f.entry["coordination_turns"], 0)
                else:
                    handoff.observe_cli(f.entry, log)
                    self.assertEqual(f.entry["coordination_turns"], 1)

    def test_closed_native_owner_cannot_advance_cursor_with_late_event(self):
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
        f.append(log, native_event("tool.execution_complete", "task-terminal",
                                   toolCallId="call-one", success=True))
        handoff.observe_cli(f.entry, log)
        self.assertIsNone(f.entry["closed_at"])
        handoff.validate_state(f.state)
        handoff.capture_check(f.entry, "raw", revision)
        f.finish_owner(process)
        verdict = handoff.validate_handoff(f.state, result, worktree=f.worktree, run_checks=False)
        self.assertTrue(verdict["handoff_ready"], verdict)
        self.assertIsNotNone(f.entry["closed_at"])

        f.append(log, task_event("assistant.turn_start", "other-owner-turn", owner="other-owner",
                                 parentToolCallId="other-dispatch", interactionId="other-interaction"))
        handoff.observe_cli(f.entry, log)
        previous_offset = f.entry["cursors"][0]["offset"]
        late_event = task_event("assistant.turn_start", "late-owner-turn", owner="owner-one",
                                parentToolCallId="call-one", interactionId="interaction-one")
        late_event["timestamp"] = at_offset(-60)
        f.append(log, late_event)
        with self.assertRaisesRegex(handoff.HandoffDataError, "closed native owner emitted an event"):
            handoff.observe_cli(f.entry, log)
        self.assertEqual(f.entry["cursors"][0]["offset"], previous_offset)
        self.assertEqual(f.entry["coordination_turns"], 0)

    def test_native_progress_requires_distinct_complete_tool_identity(self):
        for variant in ("missing-id", "opaque-id", "dispatch-id", "missing-name", "opaque-name"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                f.append(log,
                         task_event("tool.execution_start", "dispatch", toolName="task",
                                    toolCallId="call-one", arguments={"prompt": prompt}),
                         task_event("subagent.started", "started", owner="owner-one",
                                    toolCallId="call-one", interactionId="interaction-one"),
                         task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                    source=f"agent-{root}", interactionId="interaction-one",
                                    parentAgentTaskId="receipt-task"))
                handoff.observe_cli(f.entry, log)
                progress = task_event("tool.execution_start", "progress", owner="owner-one",
                                      toolName="bash", toolCallId="edit", ownerNativeSessionId=SESSION)
                if variant == "missing-id":
                    del progress["data"]["toolCallId"]
                elif variant == "opaque-id":
                    progress["data"]["toolCallId"] = {"id": "edit"}
                elif variant == "dispatch-id":
                    progress["data"]["toolCallId"] = "call-one"
                elif variant == "missing-name":
                    del progress["data"]["toolName"]
                else:
                    progress["data"]["toolName"] = {"name": "bash"}
                f.append(log, progress)
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.observe_cli(f.entry, log)

    def test_native_validation_rejects_pending_and_contradictory_log_records(self):
        for variant in ("more-than-one-batch", "partial", "contradiction"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                f.append(log,
                         task_event("tool.execution_start", "dispatch", toolName="task",
                                    toolCallId="call-one", arguments={"prompt": prompt}),
                         task_event("subagent.started", "started", owner="owner-one",
                                    toolCallId="call-one", interactionId="interaction-one"),
                         task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                    source=f"agent-{root}", interactionId="interaction-one",
                                    parentAgentTaskId="receipt-task"),
                         task_event("tool.execution_start", "progress", owner="owner-one",
                                    toolName="bash", toolCallId="edit", ownerNativeSessionId=SESSION))
                handoff.observe_cli(f.entry, log)
                process = f.owner_process()
                revision = f.commit()
                result = {"schema_version": 3, "assignment_id": f.assignment["id"],
                          "assigned_parent_sha": f.parent, "result_sha": revision,
                          "evidence_refs": ["raw-evidence"]}
                delivery = task_event("assistant.message", "delivery", owner="owner-one",
                                      interactionId="interaction-one",
                                      content=json.dumps({"handoff_result": result}))
                f.append(log, delivery)
                handoff.observe_cli(f.entry, log)
                f.finish_owner(process)
                if variant == "partial":
                    with log.open("ab") as output:
                        output.write(b'{"type":')
                else:
                    count = 129 if variant == "more-than-one-batch" else 128
                    f.append(log, *(task_event("assistant.message", f"other-{index}",
                                              owner="other-owner", content="unrelated")
                                    for index in range(count)))
                    if variant == "contradiction":
                        f.append(log, task_event(
                            "user.message", "contradiction", owner="owner-one",
                            content="Additional instructions", source=f"agent-{root}",
                            interactionId="interaction-one", parentAgentTaskId="other-task"))
                verdict = f.validate(result)
                self.assertFalse(verdict["handoff_ready"])
                self.assertIn("native-log-unread", verdict["rejection_codes"])
                self.assertIsNone(f.entry["closed_at"])
                if variant == "contradiction":
                    with self.assertRaisesRegex(handoff.HandoffDataError, "receipt task changed"):
                        f.validate(result)
                elif variant == "more-than-one-batch":
                    self.assertTrue(f.validate(result)["handoff_ready"])

    def test_shared_native_log_correlates_two_tasks_with_the_same_interaction(self):
        first = GitFixture(assign=False)
        second = GitFixture(assign=False)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        log, root, first_prompt = reserve_task(first)
        second.assignment.update({"id": "assignment-two", "issue": 179, "pull_request": 192})
        with mock.patch.object(handoff, "preflight_issue_pr", return_value={}):
            _, _, second_prompt = reserve_task(second, state=first.state, log=log)
        for fixture, prompt, suffix, session in (
            (first, first_prompt, "one", "session-one"),
            (second, second_prompt, "two", "session-two"),
        ):
            f = fixture
            f.append(log,
                     task_event("tool.execution_start", f"dispatch-{suffix}", toolName="task",
                                toolCallId=f"call-{suffix}", arguments={"prompt": prompt}),
                     task_event("subagent.started", f"started-{suffix}", owner=f"owner-{suffix}",
                                toolCallId=f"call-{suffix}", interactionId="shared-interaction"),
                     task_event("user.message", f"receipt-{suffix}", owner=f"owner-{suffix}",
                                content=prompt, source=f"agent-{root}", interactionId="shared-interaction",
                                parentAgentTaskId=f"parent-{suffix}"),
                     task_event("tool.execution_start", f"progress-{suffix}", owner=f"owner-{suffix}",
                                toolName="bash", toolCallId=f"edit-{suffix}", ownerNativeSessionId=session))
        handoff.observe_cli(first.entry, log)
        handoff.observe_cli(second.entry, log)
        handoff.validate_state(first.state)
        for fixture, suffix in ((first, "one"), (second, "two")):
            assignment = fixture.entry["assignment"]
            self.assertEqual(assignment["owner_id"], f"owner-{suffix}")
            self.assertEqual(assignment["dispatch_id"], f"call-{suffix}")
            self.assertEqual(assignment["session_id"], f"session-{suffix}")
            self.assertEqual([item["state"] for item in fixture.entry["events"]],
                             ["assignment_sent", "assignment_received", "progressing"])
        for key in ("owner_id", "session_id", "dispatch_id"):
            duplicate = copy.deepcopy(first.state)
            duplicate["assignments"][1]["assignment"][key] = duplicate["assignments"][0]["assignment"][key]
            with self.subTest(reused=key), self.assertRaises(handoff.HandoffDataError):
                handoff.validate_state(duplicate)
        duplicate = copy.deepcopy(first.state)
        duplicate["assignments"][1]["native_binding"]["parent_task_id"] = (
            duplicate["assignments"][0]["native_binding"]["parent_task_id"]
        )
        with self.assertRaisesRegex(handoff.HandoffDataError, "receipt task reused"):
            handoff.validate_state(duplicate)

    def test_native_reservation_retains_partial_record_and_ignores_stale_dispatch(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        prompt = f"[handoff:{f.assignment['id']}] Implement the assigned change."
        root = native_event("session.start", "root-start", sessionId="coordinator-session")
        stale = task_event("tool.execution_start", "stale-dispatch", toolName="task",
                           toolCallId="old-call", arguments={"prompt": prompt})
        stale["timestamp"] = at_offset(-3600)
        encoded = observations.json_bytes(stale)
        split = len(encoded) // 2
        f.parent_log.write_bytes(observations.json_bytes(root) + encoded[:split])
        log, _, _ = reserve_task(f)
        with log.open("ab") as output:
            output.write(encoded[split:])
        f.append(log, task_event("tool.execution_start", "real-dispatch", toolName="task",
                                 toolCallId="actual-call", arguments={"prompt": prompt}))
        handoff.observe_cli(f.entry, log)
        self.assertEqual(f.entry["assignment"]["dispatch_id"], "actual-call")
        self.assertEqual(f.entry["events"][0]["source_id"], "real-dispatch")
        with self.assertRaisesRegex(handoff.HandoffDataError, "wrong native root log"):
            handoff.observe_cli(f.entry, f.owner_log)

    def test_native_reservation_rejects_duplicate_dispatch_and_reversed_chronology(self):
        for variant in ("duplicate", "reversed"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, _, prompt = reserve_task(f)
                dispatch = task_event("tool.execution_start", "dispatch", toolName="task",
                                      toolCallId="call-one", arguments={"prompt": prompt})
                started = task_event("subagent.started", "started", owner="owner-one",
                                     toolCallId="call-one", interactionId="interaction-one")
                if variant == "duplicate":
                    events = [dispatch, task_event("tool.execution_start", "second-dispatch",
                                                   toolName="task", toolCallId="call-two",
                                                   arguments={"prompt": prompt})]
                else:
                    assigned = observations.timestamp(f.entry["assigned_at"])
                    dispatch["timestamp"] = (assigned + timedelta(microseconds=1)).isoformat().replace("+00:00", "Z")
                    started["timestamp"] = (assigned + timedelta(microseconds=3)).isoformat().replace("+00:00", "Z")
                    receipt = task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                         source="agent-coordinator-session", interactionId="interaction-one",
                                         parentAgentTaskId="receipt-task")
                    receipt["timestamp"] = (assigned + timedelta(microseconds=2)).isoformat().replace("+00:00", "Z")
                    events = [dispatch, started, receipt]
                f.append(log, *events)
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.observe_cli(f.entry, log)

    def test_native_progress_requires_one_owner_session_and_stops_after_handoff(self):
        for variant in ("changed-session", "retired-owner", "retired-missing",
                        "retired-opaque", "retired-invalid", "other-owner"):
            with self.subTest(variant=variant):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                log, root, prompt = reserve_task(f)
                f.append(log,
                         task_event("tool.execution_start", "dispatch", toolName="task",
                                    toolCallId="call-one", arguments={"prompt": prompt}),
                         task_event("subagent.started", "started", owner="owner-one",
                                    toolCallId="call-one", interactionId="interaction-one"),
                         task_event("user.message", "receipt", owner="owner-one", content=prompt,
                                    source=f"agent-{root}", interactionId="interaction-one",
                                    parentAgentTaskId="receipt-task"),
                         task_event("tool.execution_start", "no-session", owner="owner-one",
                                    toolName="bash", toolCallId="owner-call"))
                handoff.observe_cli(f.entry, log)
                self.assertEqual([item["state"] for item in f.entry["events"]],
                                 ["assignment_sent", "assignment_received"])
                f.append(log, task_event("tool.execution_start", "progress", owner="owner-one",
                                         toolName="bash", toolCallId="owner-call-2",
                                         ownerNativeSessionId=SESSION))
                handoff.observe_cli(f.entry, log)
                if variant == "changed-session":
                    f.append(log, task_event("tool.execution_start", "changed-session", owner="owner-one",
                                             toolName="bash", toolCallId="owner-call-3",
                                             ownerNativeSessionId="different-session"))
                    expected = "session changed"
                else:
                    result = {"schema_version": 3, "assignment_id": f.assignment["id"],
                              "assigned_parent_sha": f.parent, "result_sha": f.commit(),
                              "evidence_refs": ["raw-evidence"]}
                    f.append(log, task_event("assistant.message", "delivery", owner="owner-one",
                                             interactionId="interaction-one",
                                             content=json.dumps({"handoff_result": result})))
                    handoff.observe_cli(f.entry, log)
                    late = task_event("tool.execution_start", "after-delivery", owner="owner-one",
                                      toolName="bash", toolCallId="late-call",
                                      ownerNativeSessionId=SESSION)
                    if variant == "retired-missing":
                        del late["data"]["ownerNativeSessionId"]
                    elif variant == "retired-opaque":
                        late["data"]["ownerNativeSessionId"] = {"id": SESSION}
                    elif variant == "retired-invalid":
                        late["data"]["ownerNativeSessionId"] = "invalid session!"
                    elif variant == "other-owner":
                        late["agentId"] = "other-owner"
                    f.append(log, late)
                    expected = "retired owner"
                if variant == "other-owner":
                    handoff.observe_cli(f.entry, log)
                    self.assertEqual(f.entry["events"][-1]["state"], "handed_off")
                else:
                    with self.assertRaisesRegex(handoff.HandoffDataError, expected):
                        handoff.observe_cli(f.entry, log)

    def test_each_event_uses_prior_session_context_within_and_across_batches(self):
        for mode in ("same-batch", "split", "bounded"):
            with self.subTest(mode=mode):
                f = GitFixture()
                self.addCleanup(f.close)
                process = f.owner_process()
                f.append(f.parent_log, native_event("tool.execution_start", "dispatch",
                                                    toolCallId=f.assignment["dispatch_id"], toolName="task"))
                handoff.observe_cli(f.entry, f.parent_log)
                result = {"schema_version": 3, "assignment_id": f.assignment["id"],
                          "assigned_parent_sha": f.parent, "result_sha": f.commit(),
                          "evidence_refs": ["raw-evidence"]}
                early = owner_events(f, result, "early")
                if mode == "bounded":
                    early = [native_event("assistant.message", f"unbound-{index}", content="unbound")
                             for index in range(124)] + early
                f.append(f.owner_log, *early)
                if mode == "split":
                    handoff.observe_cli(f.entry, f.owner_log)
                f.append(f.owner_log, native_event("session.start", "late-start",
                                                  sessionId=f.assignment["session_id"]))
                handoff.observe_cli(f.entry, f.owner_log)
                if mode == "bounded":
                    handoff.observe_cli(f.entry, f.owner_log)
                self.assertEqual([event["state"] for event in f.entry["events"]], ["assignment_sent"])
                self.assertIsNone(f.entry["result"])
                self.assertEqual(f.entry["coordination_turns"], 0)
                f.append(f.owner_log, *owner_events(f, result, "actual"))
                handoff.observe_cli(f.entry, f.owner_log)
                self.assertEqual([event["state"] for event in f.entry["events"]], list(handoff.STATES))
                self.assertEqual(f.entry["events"][1]["source_id"], "actual-receipt")
                self.assertEqual(f.entry["coordination_turns"], 1)
                handoff.observe_cli(f.entry, f.owner_log)
                self.assertEqual(f.entry["coordination_turns"], 1)
                f.finish_owner(process)
                self.assertTrue(f.validate(result)["handoff_ready"])

    def test_stale_events_do_not_advance_assignment_but_old_session_context_can_resume(self):
        f = self.fixture
        process = f.owner_process()
        old = at_offset(-3600)
        dispatch = native_event("tool.execution_start", "old-dispatch",
                                toolCallId=f.assignment["dispatch_id"], toolName="task")
        dispatch["timestamp"] = old
        f.append(f.parent_log, dispatch)
        handoff.observe_cli(f.entry, f.parent_log)
        self.assertEqual(f.entry["events"], [])
        f.append(f.parent_log, native_event("tool.execution_start", "actual-dispatch",
                                            toolCallId=f.assignment["dispatch_id"], toolName="task"))
        handoff.observe_cli(f.entry, f.parent_log)
        result = {"schema_version": 3, "assignment_id": f.assignment["id"],
                  "assigned_parent_sha": f.parent, "result_sha": f.commit(),
                  "evidence_refs": ["raw-evidence"]}
        events = [native_event("session.start", "old-context", sessionId=f.assignment["session_id"]),
                  *owner_events(f, result, "stale")]
        for event in events:
            event["timestamp"] = old
        f.append(f.owner_log, *events)
        handoff.observe_cli(f.entry, f.owner_log)
        self.assertEqual([event["state"] for event in f.entry["events"]], ["assignment_sent"])
        self.assertEqual(f.entry["coordination_turns"], 0)
        self.assertIsNone(f.entry["result"])
        f.append(f.owner_log, *owner_events(f, result, "current"))
        handoff.observe_cli(f.entry, f.owner_log)
        f.finish_owner(process)
        self.assertTrue(f.validate(result)["handoff_ready"])
        self.assertEqual(f.entry["events"][1]["source_id"], "current-receipt")

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


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def test_json_decoder_accepts_state_above_previous_node_limit(self):
        raw = b'{"records":[' + b'{"id":0},' * 11999 + b'{"id":0}]}'
        parsed = observations.parse_bytes(raw)
        self.assertEqual(len(parsed["records"]), 12000)

    def test_real_sigkill_leftovers_never_replace_or_block_canonical_state(self):
        program = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from scripts.workflow_pilot import coordinator_observations as observations
path, phase = sys.argv[2:]
def pause(staging):
    print("ready", flush=True)
    print(os.path.basename(staging), flush=True)
    sys.stdin.readline()
    raise RuntimeError("crash fixture unexpectedly resumed")
fdopen = os.fdopen
class PartialOutput:
    def __init__(self, stream):
        self.stream = stream
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.stream.close()
    def write(self, data):
        self.stream.write(data[:len(data) // 2])
        self.stream.flush()
        os.fsync(self.stream.fileno())
        pause(os.readlink("/proc/self/fd/" + str(self.stream.fileno())))
def open_output(fd, mode):
    stream = fdopen(fd, mode)
    return PartialOutput(stream) if phase == "partial" and mode == "wb" else stream
def replace_output(source, destination, **kwargs):
    pause(source)
os.fdopen, os.replace = open_output, replace_output
with observations.locked_state(path) as state:
    state["coordinator_id"] = "crash-uncommitted"
"""
        for phase in ("partial", "complete"):
            with self.subTest(phase=phase):
                f = GitFixture()
                self.addCleanup(f.close)
                path = f.home / "coordination.json"
                write_json(path, f.state)
                before = path.read_bytes()
                control = f.waiting_process()
                process = subprocess.Popen(
                    [str(ROOT / "build/host-python/bin/python3"), "-I", "-c", program, str(ROOT), str(path), phase],
                    cwd=f.home, env=raw.git_environment(), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                )
                f.processes.append(process)
                f.assert_ready(process)
                staging = f.home / process.stdout.readline().decode().strip()
                identity = observations.process_identity(process.pid, "store-writer")
                process.kill()
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
                exited = observations.observe_owned_exit(process, identity)
                self.assertEqual(exited["exit_code"], -9)
                self.assertTrue(exited["rss_complete"])
                self.assertIsNone(control.poll())
                abandoned = staging.read_bytes()
                if phase == "partial":
                    with self.assertRaises(handoff.HandoffDataError):
                        observations.parse_bytes(abandoned)
                else:
                    self.assertEqual(observations.parse_bytes(abandoned)["coordinator_id"], "crash-uncommitted")
                self.assertEqual(path.read_bytes(), before)
                with self.assertRaisesRegex(ValueError, "aborted"):
                    with observations.locked_state(path) as state:
                        self.assertEqual(state["coordinator_id"], "coordinator-one")
                        raise ValueError("aborted")
                self.assertEqual(path.read_bytes(), before)
                names = set(f.home.iterdir())
                for index in range(3):
                    with observations.locked_state(path) as state:
                        self.assertNotEqual(state["coordinator_id"], "crash-uncommitted")
                        state["coordinator_id"] = f"after-{index}"
                    self.assertEqual(observations.load_json(path)["coordinator_id"], f"after-{index}")
                    self.assertEqual(staging.read_bytes(), abandoned)
                    self.assertEqual(set(f.home.iterdir()), names)

    def test_legacy_staging_is_not_promoted_or_overwritten(self):
        f = self.fixture
        path = f.home / "coordination.json"
        write_json(path, f.state)
        staging = path.with_name(path.name + ".new")
        target = f.home / "user-data"
        target.write_bytes(b"unrelated user data")
        for kind in ("partial", "complete", "symlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "partial":
                    staging.write_bytes(b'{"interrupted":')
                elif kind == "complete":
                    write_json(staging, {**f.state, "coordinator_id": "uncommitted"})
                elif kind == "symlink":
                    staging.symlink_to(target)
                else:
                    os.mkfifo(staging)
                before = staging.lstat()
                try:
                    with observations.locked_state(path) as state:
                        self.assertNotEqual(state["coordinator_id"], "uncommitted")
                        state["coordinator_id"] = "current"
                    after = staging.lstat()
                    self.assertEqual((before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns),
                                     (after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns))
                    self.assertEqual(target.read_bytes(), b"unrelated user data")
                finally:
                    staging.unlink()

    def test_staging_collisions_preserve_existing_file_or_symlink(self):
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                f = GitFixture()
                self.addCleanup(f.close)
                path = f.home / "coordination.json"
                write_json(path, f.state)
                before = path.read_bytes()
                target = f.home / "foreign-data"
                target.write_bytes(b"do not change")
                actual_open = observations.os.open
                collision = []
                def collide(name, flags, *args, **kwargs):
                    if flags & os.O_EXCL:
                        collision.append(f.home / name)
                        if kind == "symlink":
                            os.symlink(target, name, dir_fd=kwargs["dir_fd"])
                        else:
                            fd = actual_open(name, flags, *args, **kwargs)
                            os.write(fd, b"preexisting data")
                            os.close(fd)
                    return actual_open(name, flags, *args, **kwargs)
                with mock.patch.object(observations.os, "open", side_effect=collide):
                    with self.assertRaises(FileExistsError):
                        with observations.locked_state(path) as state:
                            state["coordinator_id"] = "not-committed"
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(len(collision), 1)
                existing = collision[0]
                if kind == "symlink":
                    self.assertTrue(existing.is_symlink())
                    self.assertEqual(target.read_bytes(), b"do not change")
                else:
                    self.assertEqual(existing.read_bytes(), b"preexisting data")
                with observations.locked_state(path) as state:
                    state["coordinator_id"] = "retry"
                self.assertEqual(observations.load_json(path)["coordinator_id"], "retry")
                self.assertTrue(existing.exists())

    def test_failed_replace_removes_only_current_staging_and_keeps_canonical(self):
        f = self.fixture
        path = f.home / "coordination.json"
        write_json(path, f.state)
        with observations.locked_state(path):
            pass
        before = path.read_bytes()
        names = set(f.home.iterdir())
        with mock.patch.object(observations.os, "replace", side_effect=OSError("replace unavailable")):
            with self.assertRaisesRegex(OSError, "replace unavailable"):
                with observations.locked_state(path) as state:
                    state["coordinator_id"] = "not-committed"
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(set(f.home.iterdir()), names)
        with observations.locked_state(path) as state:
            state["coordinator_id"] = "retry"
        self.assertEqual(observations.load_json(path)["coordinator_id"], "retry")
        self.assertEqual(set(f.home.iterdir()), names)


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

    def test_interruption_chronology_cannot_precede_assignment_close_or_follow_replacement(self):
        f = self.fixture
        process = f.owner_process()
        f.receive()
        retained = f.worktree / "docs/retained"
        retained.write_text("original mutable data\n")
        f.finish_owner(process)
        recovery = handoff.preserve_interruption(f.entry, "process-exit")
        replacement = {**copy.deepcopy(f.assignment), "id": "replacement", "owner_id": "replacement-owner",
                       "session_id": "replacement-session", "dispatch_id": "replacement-dispatch",
                       "kind": "replacement", "predecessor_id": f.assignment["id"]}
        baseline = {"schema_version": 1, "snapshot": {"repository": f.state["repository"]}}
        index = Path(f.entry["git_identity"]["git_dir"]) / "index"
        lock = index.parent / "locked"
        before = (retained.read_bytes(), index.read_bytes(), lock.read_bytes())
        for mutation in ("pre-assignment", "after-close", "missing-close", "future", "negative-lifetime"):
            with self.subTest(mutation=mutation):
                state = copy.deepcopy(f.state)
                entry = state["assignments"][0]
                if mutation in ("pre-assignment", "negative-lifetime"):
                    entry["interruption"]["at"] = at_offset(-3600)
                elif mutation == "after-close":
                    entry["interruption"]["at"] = (
                        observations.timestamp(entry["closed_at"]) + timedelta(microseconds=1)
                    ).isoformat().replace("+00:00", "Z")
                elif mutation == "missing-close":
                    entry["closed_at"] = None
                else:
                    entry["interruption"]["at"] = at_offset(3600)
                if mutation in ("future", "negative-lifetime"):
                    entry["closed_at"] = entry["interruption"]["at"]
                with self.subTest(boundary="load"):
                    path = f.home / "chronology.json"
                    write_json(path, state)
                    with self.assertRaises(handoff.HandoffDataError):
                        handoff.validate_state(observations.load_json(path))
                with self.subTest(boundary="report"):
                    with self.assertRaises(reporter.PilotDataError):
                        reporter.with_handoff_metrics(baseline, observations.json_bytes(state))
                with self.subTest(boundary="replacement"):
                    with self.assertRaises(handoff.HandoffDataError):
                        handoff.assign(state, replacement)
                self.assertEqual(before, (retained.read_bytes(), index.read_bytes(), lock.read_bytes()))
        child = handoff.assign(f.state, replacement)
        expected = (observations.timestamp(child["assigned_at"]) - observations.timestamp(recovery["at"]))
        self.assertGreater(expected.total_seconds(), 0)
        self.assertGreater(handoff.summarize_handoffs(f.state)["recovery_ms"], 0)
        state = copy.deepcopy(f.state)
        state["assignments"][0]["interruption"]["at"] = (
            observations.timestamp(child["assigned_at"]) + timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        with self.assertRaises(reporter.PilotDataError):
            reporter.with_handoff_metrics(baseline, observations.json_bytes(state))
        self.assertEqual(before, (retained.read_bytes(), index.read_bytes(), lock.read_bytes()))

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
            with mock.patch.object(handoff, "preflight_issue_pr", return_value={}):
                with self.assertRaisesRegex(handoff.HandoffDataError, "initial"):
                    handoff.assign(copy.deepcopy(f.state), fresh)
        with self.subTest(source="loaded"):
            separate = {**f.state, "assignments": []}
            with mock.patch.object(handoff, "preflight_issue_pr", return_value={}):
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
    def test_native_task_reservation_and_observation_through_isolated_cli(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        root = "coordinator-session"
        prompt = f"[handoff:{f.assignment['id']}] Implement the assigned change."
        f.append(f.parent_log, native_event("session.start", "root-start", sessionId=root))
        f.assignment.update({
            "owner_id": None, "session_id": None, "dispatch_id": None,
            "native": {"root_session_id": root, "events_path": str(f.parent_log), "prompt": prompt},
        })
        state_path, assignment_path = f.home / "state.json", f.home / "assignment.json"
        write_json(state_path, f.state)
        write_json(assignment_path, f.assignment)
        environment = gh_stub_environment(f.home, [{
            "number": 191, "title": "Fix issue #178", "body": "Closes #178",
            "headRefName": "agent/test", "closingIssuesReferences": [],
        }])
        launcher = ROOT / "scripts/workflow_pilot/isolated_launcher.py"
        command = [sys.executable, "-I", str(launcher), "agent-handoff"]
        assigned = raw.run_process(
            [*command, "assign", "--state", str(state_path), "--assignment", str(assignment_path)],
            cwd=ROOT, env=environment,
        )
        self.assertEqual(assigned.returncode, 0, assigned.stderr.decode())
        f.append(f.parent_log,
                 task_event("tool.execution_start", "dispatch", toolName="task",
                            toolCallId="call-one", arguments={"prompt": prompt}),
                 task_event("subagent.started", "started", owner="owner-one",
                            toolCallId="call-one", interactionId="interaction-one"),
                 task_event("user.message", "receipt", owner="owner-one", content=prompt,
                            source=f"agent-{root}", interactionId="interaction-one",
                            parentAgentTaskId="receipt-task"),
                 task_event("tool.execution_start", "progress", owner="owner-one",
                            toolName="bash", toolCallId="owner-call", ownerNativeSessionId=SESSION))
        observe = [*command, "observe", "--state", str(state_path),
                   "--assignment-id", f.assignment["id"], "--runtime-events", str(f.parent_log)]
        captured = raw.run_process(observe, cwd=ROOT, env=environment)
        self.assertEqual(captured.returncode, 0, captured.stderr.decode())
        entry = observations.load_json(state_path)["assignments"][0]
        self.assertEqual(entry["assignment"]["session_id"], SESSION)
        self.assertEqual([event["state"] for event in entry["events"]],
                         ["assignment_sent", "assignment_received", "progressing"])

        before = state_path.read_bytes()
        f.append(f.parent_log, task_event("tool.execution_start", "duplicate", toolName="task",
                                          toolCallId="call-two", arguments={"prompt": prompt}))
        rejected = raw.run_process(observe, cwd=ROOT, env=environment)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(state_path.read_bytes(), before)

    def test_cli_session_context_and_clock_rejection_preserve_canonical_state(self):
        f = GitFixture()
        self.addCleanup(f.close)
        process = f.owner_process()
        result = {"schema_version": 3, "assignment_id": f.assignment["id"],
                  "assigned_parent_sha": f.parent, "result_sha": f.commit(),
                  "evidence_refs": ["raw-evidence"]}
        f.append(f.owner_log, native_event("tool.execution_start", "dispatch",
                                           toolCallId=f.assignment["dispatch_id"], toolName="task"),
                 *owner_events(f, result, "unbound"),
                 native_event("session.start", "late-start", sessionId=f.assignment["session_id"]))
        path = f.home / "state.json"
        write_json(path, f.state)
        command = [str(ROOT / "build/host-python/bin/python3"), "-I",
                   str(ROOT / "scripts/workflow_pilot/isolated_launcher.py"), "agent-handoff", "observe",
                   "--state", str(path), "--assignment-id", f.assignment["id"],
                   "--runtime-events", str(f.owner_log)]
        actual = raw.run_process(command, cwd=ROOT, env=raw.git_environment())
        self.assertEqual(actual.returncode, 0, actual.stderr.decode())
        entry = observations.load_json(path)["assignments"][0]
        with self.subTest(boundary="event-context"):
            self.assertEqual([event["state"] for event in entry["events"]], ["assignment_sent"])
            self.assertIsNone(entry["result"])
        wrong = copy.deepcopy(f.state)
        wrong["clock"]["at"] = at_offset(3600)
        write_json(path, wrong)
        before = path.read_bytes()
        rejected = raw.run_process(command, cwd=ROOT, env=raw.git_environment())
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(path.read_bytes(), before)
        f.finish_owner(process)

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
