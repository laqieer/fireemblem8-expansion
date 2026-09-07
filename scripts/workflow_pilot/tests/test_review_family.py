"""Focused reducer, role and independent public-schema regressions for #179."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import copy
from dataclasses import FrozenInstanceError, replace
import json
import unittest
from unittest.mock import patch

from scripts.workflow_pilot import review_family as model
from scripts.workflow_pilot.tests.review_support import ROOT, Runtime, request


class RequestTests(unittest.TestCase):
    def test_public_request_positive_and_closed_negative_controls(self):
        valid = request()
        self.assertEqual(model.validate_request(model.parse_json(json.dumps(valid).encode())), valid)
        for key in ("pass", "program", "module", "members", "trusted", "receipt",
                    "execution_inputs", "blocked_by", "evidence"):
            with self.subTest(key=key), self.assertRaises(model.ReviewError):
                model.validate_request({**valid, key: True})
        for key in valid:
            changed = copy.deepcopy(valid)
            del changed[key]
            with self.subTest(key=key), self.assertRaises(model.ReviewError):
                model.validate_request(changed)
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.assertRaises(model.ReviewError):
                model.parse_json(raw)
        for value in (True, 0, "1"):
            with self.assertRaises(model.ReviewError):
                model.validate_request({**valid, "pull_request": value})

    def test_schema_parity_uses_inherited_locked_interpreter(self):
        from scripts import host_python
        from jsonschema import Draft202012Validator

        host_python.check_environment()
        schema = json.loads((ROOT / "scripts/workflow_pilot/review_family.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        cases = [(request(), True), ({**request(), "pull_request": 1.0}, True),
                 ({**request(), "schema_version": 1.0}, True)]
        for key, value in (
            ("schema_version", 2), ("schema_version", True), ("pull_request", False),
            ("pull_request", 0), ("candidate_sha", "HEAD"), ("base_sha", "A" * 40),
            ("subjects", []), ("subjects", request()["subjects"] * 2), ("findings", "pass"),
            ("subjects", [{"case_id": "unknown", "subject": "fixture"}]),
            ("candidate_sha", "b" * 40 + "\n"), ("repository", "owner/repo\n"),
        ):
            cases.append(({**request(), key: value}, False))
        for extra in ("program", "expected_members", "pass", "trusted",
                      "execution_inputs", "blocked_by", "evidence"):
            cases.append(({**request(), extra: "injected"}, False))
        for data, expected in cases:
            with self.subTest(data=data):
                try:
                    model.validate_request(data)
                    accepted = True
                except (ValueError, TypeError):
                    accepted = False
                self.assertEqual(accepted, expected)
                self.assertEqual(validator.is_valid(data), expected)

    def test_finding_and_scope_semantic_joins(self):
        valid = request()
        valid["findings"] = [{
            "finding_id": "finding-1", **valid["subjects"][0],
            "family": "wire", "reported_member": "validators:review-session",
        }]
        model.validate_request(valid)
        for field, value in (("subject", "other"), ("family", "other"),
                             ("family", []), ("family", {}), ("family", None),
                             ("reported_member", ""), ("case_id", "TC-OTHER-001")):
            data = copy.deepcopy(valid)
            data["findings"][0][field] = value
            with self.assertRaises(model.ReviewError):
                model.validate_request(data)
        valid["findings"] *= 2
        with self.assertRaisesRegex(model.ReviewError, "duplicate"):
            model.validate_request(valid)


def fact(number, head="b" * 40):
    return model.ReviewFact(str(number), head, "actual-bot", "COMMENTED",
                            f"2026-01-01T00:00:{number + 10:02d}Z",
                            "Complete review, including suppressed findings.", ())


class RoundTests(unittest.TestCase):
    def test_formal_provisional_review_can_finish_once_without_an_extra_round(self):
        state = model.RoundState()
        observed = replace(fact(1), state="CHANGES_REQUESTED")
        finding = model.Finding(
            "finding", "case/subject", "wire", "validators:member",
            observed.head, "source.py", observed.id)
        state.observe(model.Triage(observed, "untriaged"))
        self.assertEqual(state.consecutive, 1)
        final = model.Triage(observed, "changes-requested", (finding,))
        state.observe(final)
        self.assertEqual((len(state.events), len(state.seen), state.consecutive), (1, 1, 1))
        self.assertEqual(state.handoffs[0]["findings"], ["finding"])
        with self.assertRaises(ValueError):
            state.observe(final)
        for changed in (replace(observed, head="c" * 40), replace(observed, actor="other"),
                        replace(observed, submitted_at="2026-01-01T00:00:59Z")):
            with self.assertRaises(ValueError):
                state.observe(model.Triage(changed, "untriaged"))

    def test_refresh_preserves_hold_and_disposition_count_boundary(self):
        state = model.RoundState()
        for number in (1, 2, 3):
            state.observe(model.Triage(fact(number), "changes-requested"))
        state.observe(model.Triage(replace(fact(1), body="edited"), "untriaged"))
        self.assertEqual(state.hold, ("3", "b" * 40))
        self.assertNotIn("1", {item["review_id"] for item in state.handoffs})
        state.observe(model.Triage(replace(fact(3), state="DISMISSED"), "dismissed"))
        self.assertEqual(state.hold, ("3", "b" * 40))
        state.observe(model.Triage(fact(4), "clean"))
        state.dispose(model.Disposition("3", "b" * 40, "coordinator", "redesign", "new plan"),
                      "coordinator")
        state.observe(model.Triage(fact(5), "changes-requested"))
        changed = replace(fact(2), body="historical edit")
        state.observe(model.Triage(changed, "untriaged"))
        state.observe(model.Triage(changed, "changes-requested"))
        self.assertEqual(state.consecutive, 1)
        self.assertIsNone(state.hold)

    def test_formal_change_requests_hold_even_before_full_content_triage(self):
        state = model.RoundState()
        for number in (1, 2, 3):
            state.observe(model.Triage(replace(fact(number), state="CHANGES_REQUESTED"), "untriaged"))
        self.assertEqual(state.hold, ("3", "b" * 40))

    def test_first_second_handoffs_and_sticky_third_hold(self):
        state = model.RoundState()
        for number in (1, 2, 3):
            state.observe(model.Triage(fact(number), "changes-requested"))
        self.assertEqual([item["consecutive"] for item in state.handoffs], [1, 2])
        self.assertEqual(state.hold, ("3", "b" * 40))
        state.observe(model.Triage(fact(4, "c" * 40), "clean"))
        state.observe(model.Triage(fact(5, "d" * 40), "changes-requested"))
        self.assertEqual(state.hold, ("3", "b" * 40))
        good = model.Disposition("3", "b" * 40, "coordinator", "redesign", "bounded replacement")
        for bad in (
            replace(good, held_head="c" * 40), replace(good, review_id="2"),
            replace(good, coordinator="implementer"), replace(good, action="patch-again"),
            replace(good, reason=""),
        ):
            with self.assertRaises(model.ReviewError):
                state.dispose(bad, "coordinator")
        state.dispose(good, "coordinator")
        self.assertIsNone(state.hold)
        with self.assertRaises(model.ReviewError):
            state.dispose(good, "coordinator")

    def test_clean_before_hold_resets_but_untriaged_does_not(self):
        state = model.RoundState()
        state.observe(model.Triage(fact(1), "changes-requested"))
        state.observe(model.Triage(fact(2), "untriaged"))
        self.assertEqual(state.consecutive, 1)
        state.observe(model.Triage(fact(3), "clean"))
        self.assertEqual(state.consecutive, 0)
        with self.assertRaises(model.ReviewError):
            state.observe(model.Triage(fact(3), "clean"))

    def test_no_natural_language_approval_inference(self):
        for state in ("COMMENTED", "APPROVED"):
            model.Triage(replace(fact(1), state=state), "clean").validate()
        for body in ("No issues found.", "### 🟢 Approval recommended",
                     "### 🔵 Needs a closer look", "", "zero new inline comments"):
            review = replace(fact(1), body=body)
            state = model.RoundState()
            state.observe(model.Triage(review, "untriaged"))
            self.assertEqual(state.consecutive, 0)
        with self.assertRaises(model.ReviewError):
            model.Triage(replace(fact(1), state="CHANGES_REQUESTED"), "clean").validate()
        with self.assertRaises(model.ReviewError):
            model.Triage(replace(fact(1), unresolved_threads=("thread",)), "clean").validate()


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.scope = frozenset({"TC-WORKFLOW-REVIEW-FAMILY-001/review-session"})
        self.runtime = Runtime("b" * 40, self.scope)
        self.time = 0
        self.effects = []
        self.session = model.ReviewSession(
            "coordinator", "implementer", self.scope, "b" * 40,
            clock=lambda: self.time, readers={"read-candidate": lambda: self.effects.append("read")})

    def test_existing_runtime_task_and_read_tool_boundary(self):
        self.session.begin(self.runtime, "reviewer", duration=30)
        self.session.read_action("read-candidate")
        for action in ("write", "bash", "push", "comment", "request-review", "dispatch-CI",
                       "merge", "stop", "abort", None, [], {}):
            with self.subTest(action=action), self.assertRaises(model.ReviewError):
                self.session.read_action(action)
        self.assertEqual(self.effects, ["read"])
        self.assertEqual(self.runtime.calls[0][1]["role"], "code-review")
        self.assertEqual(set(self.runtime.calls[0][1]["actions"]),
                         {"read-candidate", "read-evidence", "emit-report"})
        report = self.session.finish(self.runtime)
        self.assertIs(report, self.session.report)
        self.assertIsNot(report, self.runtime.result)
        self.assertEqual((report.task, report.head, report.owner),
                         (self.runtime.result.task, self.runtime.result.head, self.runtime.result.owner))
        self.assertEqual(self.session.lease.outcome, "completed")
        self.assertEqual([row[0] for row in self.runtime.calls], ["start", "read"])
        with self.assertRaises(model.ReviewError):
            self.session.read_action("read-candidate")

    def test_overlap_bounds_and_no_waiting(self):
        for coordinator, implementer, scope in (
            (None, "implementer", self.scope), ("coordinator", [], self.scope),
            (" ", "implementer", self.scope),
            *(("coordinator", "implementer", scope)
              for scope in (None, {}, "scope", ["scope"], frozenset({1}))),
        ):
            with self.subTest(scope=scope), self.assertRaises(model.ReviewError):
                model.ReviewSession(coordinator, implementer, scope, "b" * 40)
        for owner in ("implementer", "coordinator", None, True, 1, [], {}, "", " "):
            with self.assertRaises(model.ReviewError):
                self.session.begin(self.runtime, owner)
        self.session.begin(self.runtime, "reviewer", duration=10)
        with self.assertRaises(model.ReviewError):
            self.session.begin(self.runtime, "second-reviewer")
        self.assertEqual(len(self.runtime.calls), 1)
        self.time = 11
        with self.assertRaises(model.ReviewError):
            self.session.finish(self.runtime)
        self.assertEqual(len(self.runtime.calls), 2)
        self.assertTrue(self.session.lease.finished)
        self.assertEqual(self.session.lease.outcome, "timed-out")
        self.assertIsNone(self.session.report)

    def test_invalid_started_task_identity_unwinds_without_admitting_a_local_report(self):
        for task in (None, "", " \t\n", False, True, 0, 1, 1.0,
                     b"task", [], ["task"], {}, {"task": "id"}, ("task",)):
            with self.subTest(task=task):
                owners = model.ReviewOwnership()
                identity = ("owner/repo", 1, "a" * 40)
                session = model.ReviewSession(
                    "coordinator", "implementer", self.scope, "b" * 40,
                    identity=identity, owners=owners, clock=lambda: self.time)
                runtime = Runtime(session.head, session.scope)
                runtime.result.task = task
                finding = model.Finding(
                    "finding", next(iter(self.scope)), "wire", "validators:review-session",
                    session.head, "scripts/workflow_pilot/review_family.py", "local:" + str(task))
                runtime.result.findings = (finding,)
                try:
                    session.begin(runtime, "reviewer")
                except model.ReviewError:
                    pass
                else:
                    session.finish(runtime)
                    session.triage_local(finding.id, accepted=True, reason="Returned task finding")
                    self.fail("invalid task identity admitted " + repr(session.accepted[finding.id].review_id))
                self.assertEqual([call[0] for call in runtime.calls], ["start"])
                self.assertIsNone(session.lease)
                self.assertIsNone(session.report)
                self.assertEqual(session.accepted, {})
                self.assertEqual(owners.records, {})
                actual_task = "actual-runtime-task:42 "
                runtime.result.task = actual_task
                runtime.result.findings = (replace(finding, review_id="local:" + actual_task),)
                self.assertEqual(session.begin(runtime, "reviewer"), actual_task)
                self.assertEqual(session.lease.task, actual_task)
                self.assertEqual(session.finish(runtime).task, actual_task)
                session.triage_local(finding.id, accepted=True, reason="Actual task finding")
                session.validate_local_triage()
                fresh = model.ReviewSession(
                    "coordinator", "next-implementer", self.scope, session.head,
                    identity=identity, owners=owners)
                replacement = Runtime(fresh.head, fresh.scope)
                fresh.begin(replacement, "reviewer")
                fresh.finish(replacement)

    def owned_runtime(self, *, completed):
        self.time = 0
        owners = model.ReviewOwnership()
        identity = ("owner/repo", 1, "a" * 40)
        session = model.ReviewSession(
            "coordinator", "implementer", self.scope, "b" * 40,
            identity=identity, owners=owners, clock=lambda: self.time)
        runtime = Runtime(session.head, self.scope)
        runtime.result.completed = completed
        session.begin(runtime, "reviewer", duration=10)
        fresh = model.ReviewSession(
            "coordinator", "next-implementer", self.scope, session.head,
            identity=identity, owners=owners, clock=lambda: self.time)
        return session, runtime, fresh

    def test_finished_report_snapshots_runtime_values_and_nested_collections(self):
        for mutate_on_release in (False, True):
            with self.subTest(mutate_on_release=mutate_on_release):
                session, runtime, fresh = self.owned_runtime(completed=True)
                finding = model.Finding(
                    "finding", next(iter(self.scope)), "wire", "validators:review-session",
                    session.head, "scripts/workflow_pilot/review_family.py", "local:task-1")
                runtime.result.subjects = set(self.scope)
                runtime.result.actions = ["read-candidate", "emit-report"]
                runtime.result.findings = [finding]
                original = copy.deepcopy(runtime.result)

                def mutate():
                    runtime.result.subjects.clear()
                    runtime.result.actions[:] = ["push"]
                    runtime.result.findings[:] = [replace(finding, id="invented")]
                    runtime.result.completed_at = "2026-01-01T00:00:59Z"
                    runtime.result.started_at = "2026-01-01T00:00:58Z"
                    runtime.result.head = "c" * 40
                    runtime.result.owner = "implementer"
                    runtime.result.completed = False
                    runtime.result.read_only = False
                    runtime.result.files = 999

                release = session.owners.finish

                def finish_owner(owner):
                    release(owner)
                    if mutate_on_release:
                        mutate()

                with patch.object(session.owners, "finish", side_effect=finish_owner):
                    report = session.finish(runtime)
                if not mutate_on_release:
                    mutate()
                self.assertEqual(report.completed_at, original.completed_at)
                self.assertEqual(report.started_at, original.started_at)
                self.assertEqual(report.head, original.head)
                self.assertEqual(report.owner, original.owner)
                self.assertIs(report.completed, True)
                self.assertIs(report.read_only, True)
                self.assertEqual(report.files, original.files)
                self.assertEqual(report.subjects, frozenset(original.subjects))
                self.assertEqual(report.actions, frozenset(original.actions))
                self.assertEqual(report.findings, (finding,))
                self.assertIsNot(report, runtime.result)
                self.assertIs(report, session.report)
                session.triage_local(finding.id, accepted=True, reason="Observed original finding")
                session.validate_local_triage()
                self.assertEqual(session.accepted, {finding.id: finding})
                with self.assertRaises(FrozenInstanceError):
                    report.completed_at = runtime.result.completed_at
                with self.assertRaises(AttributeError):
                    report.actions.add("write")
                with self.assertRaises(AttributeError):
                    report.subjects.clear()
                with self.assertRaises(AttributeError):
                    report.findings.append(finding)
                with self.assertRaises(FrozenInstanceError):
                    report.findings[0].id = "changed"
                replacement = Runtime(fresh.head, fresh.scope)
                fresh.begin(replacement, "reviewer")
                fresh.finish(replacement)

    def test_head_advance_requires_actual_terminal_lease_release(self):
        for terminal in ("completed", "aborted", "timed-out"):
            with self.subTest(terminal=terminal):
                session, runtime, fresh = self.owned_runtime(completed=False)
                old_head = session.head
                for expired in (False, True):
                    self.time = 11 if expired else 1
                    with self.assertRaises(model.ReviewError):
                        session.advance("c" * 40)
                    self.assertEqual((session.head, session.lease.head), (old_head, old_head))
                    with self.assertRaises(model.ReviewError):
                        fresh.begin(Runtime(fresh.head, fresh.scope), "reviewer")
                self.time = 11 if terminal == "timed-out" else 1
                if terminal == "aborted":
                    stop = runtime.stop
                    runtime.stop = lambda task: runtime.calls.append(("stop", task)) or True
                    with self.assertRaises(model.ReviewError):
                        session.abort(runtime)
                    with self.assertRaises(model.ReviewError):
                        session.advance("c" * 40)
                    runtime.stop = stop
                    session.abort(runtime)
                else:
                    runtime.result.completed = True
                    if terminal == "timed-out":
                        with self.assertRaises(model.ReviewError):
                            session.finish(runtime)
                    else:
                        session.finish(runtime)
                self.assertEqual(session.lease.outcome, terminal)
                session.advance("c" * 40)
                self.assertEqual(session.head, "c" * 40)
                self.assertEqual(session.lease.head, old_head)
                replacement = Runtime(fresh.head, fresh.scope)
                fresh.begin(replacement, "reviewer")
                fresh.finish(replacement)

    def test_completed_review_requires_candidate_read_and_report_observations(self):
        for actions, accepted in (
            ((), False), (("read-evidence",), False),
            (("read-candidate",), False), (("emit-report",), False),
            (("read-candidate", "read-evidence"), False),
            (("emit-report", "read-evidence"), False),
            (("read-candidate", "emit-report"), True),
            (("emit-report", "read-evidence", "read-candidate"), True),
        ):
            with self.subTest(actions=actions):
                session, runtime, fresh = self.owned_runtime(completed=True)
                runtime.result.actions = actions
                if accepted:
                    self.assertIs(session.finish(runtime), session.report)
                    self.assertEqual(session.report.actions, frozenset(actions))
                    self.assertEqual(session.lease.outcome, "completed")
                else:
                    with self.assertRaises(model.ReviewError):
                        session.finish(runtime)
                    self.assertFalse(session.lease.finished)
                    self.assertIsNone(session.report)
                    self.assertEqual(session.local_findings, {})
                    session.abort(runtime)
                    self.assertEqual(session.lease.outcome, "aborted")
                    self.assertIsNone(session.report)
                    replacement = Runtime(session.head, self.scope)
                    fresh.begin(replacement, "reviewer")
                    fresh.finish(replacement)

    def test_expired_lease_retires_only_after_observed_terminal_completion(self):
        session, runtime, fresh = self.owned_runtime(completed=False)
        self.time = 11
        with self.assertRaises(model.ReviewError):
            session.finish(runtime)
        self.assertFalse(session.lease.finished)
        self.assertIsNone(session.report)
        with self.assertRaises(model.ReviewError):
            fresh.begin(runtime, "reviewer")
        runtime.result.completed = True
        with self.assertRaisesRegex(model.ReviewError, "duration bound"):
            session.finish(runtime)
        self.assertTrue(session.lease.finished)
        self.assertEqual(session.lease.outcome, "timed-out")
        self.assertIsNone(session.report)
        self.assertEqual(session.local_findings, {})
        self.assertEqual([row[0] for row in runtime.calls], ["start", "read", "read"])
        replacement = Runtime(session.head, self.scope)
        fresh.begin(replacement, "reviewer")
        fresh.finish(replacement)
        self.assertEqual(fresh.lease.outcome, "completed")

    def test_deadline_crossing_during_runtime_read_cannot_accept_a_report(self):
        session, runtime, _ = self.owned_runtime(completed=True)
        self.time = 9
        read = runtime.read

        def late_read(task):
            self.time = 11
            return read(task)

        runtime.read = late_read
        with self.assertRaisesRegex(model.ReviewError, "duration bound"):
            session.finish(runtime)
        self.assertTrue(session.lease.finished)
        self.assertEqual(session.lease.outcome, "timed-out")
        self.assertIsNone(session.report)

    def test_abort_requires_terminal_evidence_and_runtime_failures_retain_ownership(self):
        for case in ("delayed-stop", "stop-failure", "read-failure", "missing-stop", "unknown",
                     "wrong-head", "malformed-completion", "terminal-chronology", "failed-report"):
            with self.subTest(case=case):
                session, runtime, fresh = self.owned_runtime(completed=case == "failed-report")
                original = copy.copy(runtime.result)
                read, stop = runtime.read, runtime.stop
                self.time = 11 if case != "failed-report" else 1
                if case == "delayed-stop":
                    runtime.stop = lambda task: runtime.calls.append(("stop", task)) or True
                elif case == "stop-failure":
                    def fail_stop(task):
                        raise OSError("runtime stop failed")
                    runtime.stop = fail_stop
                elif case == "read-failure":
                    def fail_read(task):
                        raise OSError("runtime observation failed")
                    runtime.read = fail_read
                elif case == "missing-stop":
                    runtime.stop = None
                elif case == "unknown":
                    runtime.result = None
                elif case == "wrong-head":
                    runtime.result.head = "c" * 40
                    runtime.result.completed = True
                elif case == "malformed-completion":
                    runtime.result.completed = "stopped"
                elif case == "terminal-chronology":
                    runtime.result.completed = True
                    runtime.result.completed_at = None
                else:
                    runtime.result.read_only = False
                expected = OSError if case in {"stop-failure", "read-failure"} else model.ReviewError
                with self.assertRaises(expected):
                    session.finish(runtime) if case == "failed-report" else session.abort(runtime)
                self.assertFalse(session.lease.finished)
                self.assertIsNone(session.report)
                with self.assertRaises(model.ReviewError):
                    fresh.begin(Runtime(session.head, self.scope), "reviewer")
                runtime.read, runtime.stop = read, stop
                if case != "failed-report":
                    runtime.result = original
                    runtime.result.completed = case != "delayed-stop"
                session.abort(runtime)
                self.assertTrue(session.lease.finished)
                self.assertEqual(session.lease.outcome, "aborted")
                self.assertIsNone(session.report)
                self.assertEqual(session.local_findings, {})
                if case == "delayed-stop":
                    self.assertEqual([row[0] for row in runtime.calls[-3:]], ["read", "stop", "read"])
                if case == "failed-report":
                    self.assertNotIn("stop", [row[0] for row in runtime.calls])
                with self.assertRaises(model.ReviewError):
                    session.abort(runtime)
                with self.assertRaises(model.ReviewError):
                    session.finish(runtime)
                fresh.begin(Runtime(session.head, self.scope), "reviewer")

    def test_wrong_actual_task_results_are_rejected(self):
        for field, wrong in (("head", "c" * 40), ("task", "other"), ("owner", "implementer"),
                             ("role", "general-purpose"), ("completed", False),
                             ("subjects", ("other",)), ("actions", ("push",))):
            session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
            runtime = Runtime("b" * 40, self.scope)
            session.begin(runtime, "reviewer")
            setattr(runtime.result, field, wrong)
            with self.subTest(field=field), self.assertRaises(model.ReviewError):
                session.finish(runtime)

    def test_runtime_completion_requires_actual_true_flags(self):
        missing = object()
        for field in ("completed", "read_only"):
            for value in (False, None, 0, 1, "", "incomplete", "true", [], {}, [True], missing):
                with self.subTest(field=field, value=value):
                    session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                    runtime = Runtime(session.head, self.scope)
                    session.begin(runtime, "reviewer")
                    if value is missing:
                        delattr(runtime.result, field)
                    else:
                        setattr(runtime.result, field, value)
                    with self.assertRaises(model.ReviewError):
                        session.finish(runtime)
                    self.assertFalse(session.lease.finished)
                    self.assertIsNone(session.report)
                    setattr(runtime.result, field, True)
                    session.finish(runtime)
                    self.assertTrue(session.lease.finished)

    def test_requested_and_returned_file_bounds_are_strict_and_retained(self):
        for keyword, ceiling in (("duration", 3600), ("max_files", 200)):
            for value in (-1, 0, ceiling + 1, True, False, 1.0, 1.5, "10", None, [], {}):
                with self.subTest(requested=keyword, value=value):
                    session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                    runtime = Runtime(session.head, self.scope)
                    with self.assertRaises(model.ReviewError):
                        session.begin(runtime, "reviewer", **{keyword: value})
                    self.assertEqual(runtime.calls, [])
                    self.assertIsNone(session.lease)
        for value in (-1, 11, 200, 201, True, False, 0.0, 3.0, 3.5, "3", None, [], {}):
            with self.subTest(returned=value):
                session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                runtime = Runtime(session.head, self.scope)
                session.begin(runtime, "reviewer", max_files=10)
                self.assertEqual(runtime.calls[0][1]["max_files"], 10)
                runtime.result.files = value
                with self.assertRaises(model.ReviewError):
                    session.finish(runtime)
                self.assertFalse(session.lease.finished)
        for bound, files in ((1, 0), (1, 1), (10, 10), (200, 200)):
            with self.subTest(bound=bound, files=files):
                session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                runtime = Runtime(session.head, self.scope)
                session.begin(runtime, "reviewer", max_files=bound)
                runtime.result.files = files
                session.finish(runtime)
                self.assertEqual(session.lease.max_files, bound)

    def test_malformed_runtime_reports_do_not_release_ownership(self):
        finding = model.Finding(
            "finding", next(iter(self.scope)), "wire", "validators:review-session",
            "b" * 40, "scripts/workflow_pilot/review_family.py", "local:task-1")
        malformed = [
            ("subjects", None), ("subjects", next(iter(self.scope))), ("subjects", ([],)),
            ("actions", None), ("actions", ""), ("actions", ([],)),
            ("findings", None), ("findings", {}), ("findings", "none"),
            ("findings", (replace(finding, family=[]),)),
            ("findings", (replace(finding, subject=[]),)),
            ("started_at", []), ("completed_at", "2025-01-01T00:00:00Z"),
        ]
        missing = object()
        malformed.extend((field, missing) for field in vars(self.runtime.result))
        malformed.extend((None, value) for value in (None, {}, "incomplete"))
        for field, value in malformed:
            with self.subTest(field=field, value=value):
                owners = model.ReviewOwnership()
                identity = ("owner/repo", 1, "a" * 40)
                session = model.ReviewSession(
                    "coordinator", "implementer", self.scope, "b" * 40,
                    identity=identity, owners=owners)
                other = model.ReviewSession(
                    "coordinator", "implementer", self.scope, "b" * 40,
                    identity=identity, owners=owners)
                runtime = Runtime(session.head, self.scope)
                valid = copy.copy(runtime.result)
                session.begin(runtime, "reviewer")
                if field is None:
                    runtime.result = value
                elif value is missing:
                    delattr(runtime.result, field)
                else:
                    setattr(runtime.result, field, value)
                with self.assertRaises(model.ReviewError):
                    session.finish(runtime)
                self.assertFalse(session.lease.finished)
                self.assertIsNone(session.report)
                self.assertEqual(session.local_findings, {})
                with self.assertRaises(model.ReviewError):
                    other.begin(runtime, "other-reviewer")
                runtime.result = valid
                session.finish(runtime)
                other.begin(runtime, "other-reviewer")

    def test_local_report_findings_are_typed_unique_and_task_bound(self):
        finding = model.Finding(
            "finding-1", next(iter(self.scope)), "wire", "validators:review-session",
            "b" * 40, "scripts/workflow_pilot/review_family.py", "local:task-1")
        for findings in (
            ({},), (replace(finding, id=""),), (finding, finding),
            (replace(finding, origin="c" * 40),),
            (replace(finding, subject="other"),),
            (replace(finding, review_id="other-task"),),
        ):
            with self.subTest(findings=findings):
                session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                runtime = Runtime("b" * 40, self.scope)
                runtime.result.findings = findings
                session.begin(runtime, "reviewer")
                with self.assertRaises(ValueError):
                    session.finish(runtime)

    def test_local_decisions_are_complete_reasoned_and_preserve_observed_findings(self):
        finding = model.Finding(
            "finding-1", next(iter(self.scope)), "wire", "validators:review-session",
            "b" * 40, "scripts/workflow_pilot/review_family.py", "local:task-1")
        self.runtime.result.findings = (finding,)
        self.session.begin(self.runtime, "reviewer")
        self.session.finish(self.runtime)
        self.runtime.result.findings = ()
        with self.assertRaisesRegex(ValueError, "local.*triage"):
            self.session.validate_local_triage()
        for finding_id, accepted, reason in (
            ("other", False, "unknown finding"), (finding.id, "yes", "not a boolean"),
            (finding.id, False, " "),
        ):
            with self.assertRaises(ValueError):
                self.session.triage_local(finding_id, accepted=accepted, reason=reason)
        self.session.triage_local(finding.id, accepted=False, reason="Coordinator rejected the finding")
        self.session.validate_local_triage()
        self.assertEqual(self.session.accepted, {})

    def test_existing_commit_publication_does_not_clear_hold(self):
        self.session.rounds.hold = ("3", "b" * 40)
        self.session.advance("c" * 40)
        self.assertEqual(self.session.head, "c" * 40)
        self.assertEqual(self.session.rounds.hold, ("3", "b" * 40))

    def test_shared_coordinator_index_prevents_overlapping_sessions(self):
        owners = model.ReviewOwnership()
        identity = ("owner/repo", 1, "a" * 40)
        first = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40,
                                    identity=identity, owners=owners)
        second = model.ReviewSession("coordinator", "other-implementer", self.scope, "b" * 40,
                                     identity=identity, owners=owners)
        first.begin(self.runtime, "reviewer")
        with self.assertRaisesRegex(model.ReviewError, "overlapping"):
            second.begin(Runtime("b" * 40, self.scope), "second-reviewer")
        first.finish(self.runtime)
        second.begin(Runtime("b" * 40, self.scope), "second-reviewer")

    def test_one_active_pr_head_owner_ignores_scope_but_releases_completed_work(self):
        identity = ("owner/repo", 1, "a" * 40)
        disjoint = frozenset({"TC-CORE-004/generated-eventlists"})
        for next_identity, head in (
            (identity, "b" * 40), (identity, "c" * 40),
            (("owner/repo", 2, "a" * 40), "b" * 40),
            (("other/repo", 1, "a" * 40), "b" * 40),
            (("owner/repo", 1, "d" * 40), "c" * 40),
        ):
            with self.subTest(identity=next_identity, head=head):
                owners = model.ReviewOwnership()
                first = model.ReviewSession(
                    "coordinator", "implementer", self.scope, "b" * 40,
                    identity=identity, owners=owners)
                runtime = Runtime(first.head, self.scope)
                first.begin(runtime, "reviewer")
                second = model.ReviewSession(
                    "coordinator", "other-implementer", disjoint, head,
                    identity=next_identity, owners=owners)
                next_runtime = Runtime(head, disjoint)
                with self.assertRaises(model.ReviewError):
                    second.begin(next_runtime, "reviewer")
                self.assertEqual(next_runtime.calls, [])
                self.assertIsNone(second.lease)
                first.finish(runtime)
                second.begin(next_runtime, "reviewer")
                second.finish(next_runtime)
        owners = model.ReviewOwnership()
        first = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40,
                                    identity=identity, owners=owners)
        first.begin(self.runtime, "reviewer")
        for other_identity, head in ((("owner/repo", 2, "a" * 40), "c" * 40),
                                     (("other/repo", 1, "a" * 40), "d" * 40)):
            independent = model.ReviewSession(
                "coordinator", "other-implementer", self.scope, head,
                identity=other_identity, owners=owners)
            runtime = Runtime(head, self.scope)
            independent.begin(runtime, "reviewer")
            independent.finish(runtime)
        first.finish(self.runtime)


class MemberTests(unittest.TestCase):
    def test_every_family_requires_all_roles(self):
        for family, roles in model.FAMILIES.items():
            members = tuple(model.Obligation(
                "case/subject", family, role + ":member", role, "producer", "consumer",
                "typed representation", "revalidate", "probe-" + role, "host",
                ("positive", "adversarial"), ("source.c",)) for role in roles)
            model.validate_members(members)
            for index in range(len(members)):
                with self.subTest(family=family, missing=roles[index]), self.assertRaises(model.ReviewError):
                    model.validate_members(members[:index] + members[index + 1:])
            with self.assertRaises(model.ReviewError):
                model.validate_members(members + members[:1])


class ExistingMetricTests(unittest.TestCase):
    def test_v1_baseline_metrics_and_actual_coordination_event_delta(self):
        from scripts.workflow_pilot import reporter

        fixture = reporter.load_json(ROOT / reporter.BASELINE_FIXTURE_PATH)
        self.assertEqual(fixture["schema_version"], 1)
        before = reporter.validate_fixture(fixture)
        original = reporter.report_efficiency(before)
        start, end = (reporter.parse_time(fixture["window"][key], key) for key in ("start", "end"))
        observed = next(item for item in fixture["events"] if "pr_number" in item
                        and start <= reporter.parse_time(item["occurred_at"], "event") <= end)
        event = {"id": "synthetic:coordination-observation", "type": "pilot_coordination",
                 "pr_number": observed["pr_number"], "occurred_at": observed["occurred_at"],
                 "minutes": 7}
        events = reporter.validate_events({"events": [event]}, before["pull_requests"])
        after = {**before, "events": {**before["events"], **events}}
        measured = reporter.report_efficiency(after)
        self.assertEqual(measured["pilot_coordination_minutes"],
                         original["pilot_coordination_minutes"] + 7)
        self.assertEqual(measured["net_saved_minutes"], original["net_saved_minutes"] - 7)
        self.assertEqual(reporter.report_reviews(before), reporter.report_reviews(after))
        event["minutes"] = -1
        with self.assertRaises(reporter.PilotDataError):
            reporter.validate_events({"events": [event]}, before["pull_requests"])


if __name__ == "__main__":
    unittest.main()
