"""Focused reducer, role and independent public-schema regressions for #179."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import copy
from dataclasses import replace
import json
import unittest

from scripts.workflow_pilot import review_family as model
from scripts.workflow_pilot.tests.review_support import ROOT, Runtime, request


class RequestTests(unittest.TestCase):
    def test_public_request_positive_and_closed_negative_controls(self):
        valid = request()
        self.assertEqual(model.validate_request(model.parse_json(json.dumps(valid).encode())), valid)
        for key in ("pass", "program", "module", "members", "trusted", "receipt"):
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
        for extra in ("program", "expected_members", "pass", "trusted"):
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
        for action in ("write", "bash", "push", "comment", "request-review", "dispatch-CI", "merge"):
            with self.subTest(action=action), self.assertRaises(model.ReviewError):
                self.session.read_action(action)
        self.assertEqual(self.effects, ["read"])
        self.assertEqual(self.runtime.calls[0][1]["role"], "code-review")
        self.assertEqual(set(self.runtime.calls[0][1]["actions"]),
                         {"read-candidate", "read-evidence", "emit-report"})
        self.assertEqual(self.session.finish(self.runtime), self.runtime.result)
        self.assertEqual([row[0] for row in self.runtime.calls], ["start", "read"])
        with self.assertRaises(model.ReviewError):
            self.session.read_action("read-candidate")

    def test_overlap_bounds_and_no_waiting(self):
        for owner in ("implementer", "coordinator"):
            with self.assertRaises(model.ReviewError):
                self.session.begin(self.runtime, owner)
        self.session.begin(self.runtime, "reviewer", duration=10)
        with self.assertRaises(model.ReviewError):
            self.session.begin(self.runtime, "second-reviewer")
        self.assertEqual(len(self.runtime.calls), 1)
        self.time = 11
        with self.assertRaises(model.ReviewError):
            self.session.finish(self.runtime)
        self.assertEqual(len(self.runtime.calls), 1)

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
        second = model.ReviewSession("coordinator", "other-implementer", self.scope, "c" * 40,
                                     identity=identity, owners=owners)
        first.begin(self.runtime, "reviewer")
        with self.assertRaisesRegex(model.ReviewError, "overlapping"):
            second.begin(Runtime("c" * 40, self.scope), "second-reviewer")
        first.finish(self.runtime)
        second.begin(Runtime("c" * 40, self.scope), "second-reviewer")


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
