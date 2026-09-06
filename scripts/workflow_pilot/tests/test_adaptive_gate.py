"""TC-WORKFLOW-REVIEW-FIRST-001: real seams and bounded scheduling controls."""

import copy
import ast
import base64
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from scripts.workflow_pilot import adaptive_gate as gate
from scripts.workflow_pilot import agent_handoff as handoff
from scripts.workflow_pilot import candidate_evidence
from scripts.workflow_pilot import coordinator_observations as observations
from scripts.workflow_pilot import pr_metadata as github
from scripts.workflow_pilot import reporter, review_family as review
from scripts.workflow_pilot import event_classifier
from scripts.workflow_pilot.tests.review_support import Runtime
from scripts.workflow_pilot.tests.test_agent_handoff import GitFixture, at_offset, write_json, git


ROOT = Path(__file__).resolve().parents[3]


def decisions(number=191, risks=("none",), mode="concurrent", *, paused=False):
    return {"schema_version": 1, "artifacts": [], "pull_requests": [{
        "pull_request": number, "risk_boundaries": list(risks), "gate_mode": mode,
        "threshold": {"triggers": ["none"], "override_history": []},
        "stack": {"depth": 0, "parent_pr": None, "exception_reason": None},
        "pilot": {"included": False, "disposition": "paused" if paused else "excluded"},
    }]}


class ModeTests(unittest.TestCase):
    def select(self, raw, lines=100):
        return gate.select_mode(raw, number=191, head_sha="a" * 40,
                                decision_oid="b" * 40, changed_lines=lines)

    def test_named_risks_size_small_unknown_and_pause_reuse_the_record(self):
        for risk in gate.HIGH_RISKS:
            with self.subTest(risk=risk):
                actual = self.select(decisions(risks=(risk,)))
                self.assertEqual(actual.mode, "review-first")
                self.assertTrue(actual.pre_review_required)
        for count, expected in ((0, "concurrent"), (2000, "concurrent"), (2001, "review-first")):
            self.assertEqual(self.select(decisions(), count).mode, expected)
        for raw in (None, {}, decisions(number=192), decisions(risks=("unknown",))):
            result = self.select(raw, 5000)
            self.assertEqual(result.mode, "concurrent")
            self.assertFalse(result.known)
            self.assertIn("unknown-decision", result.reason)
        paused = self.select(decisions(risks=("save",), paused=True), 5000)
        self.assertEqual(paused.mode, "concurrent")
        self.assertTrue(paused.pre_review_required)
        self.assertTrue(paused.paused)

    def test_unavailable_override_provenance_is_visible_and_broader(self):
        raw = decisions(risks=("generated-data",))
        raw["pull_requests"][0]["threshold"]["override_history"] = [
            {"enabled": True, "reason": "Generated-only size is not semantic risk"}]
        selected = self.select(raw, 5000)
        self.assertFalse(selected.known)
        self.assertEqual(selected.mode, "concurrent")
        self.assertIn("override", selected.reason)
        self.assertEqual(self.select(decisions(risks=("save",), mode="concurrent")).mode, "review-first")

    def test_actual_git_pre_review_override_and_late_introduction(self):
        from scripts.workflow_pilot.tests import test_reporter as fixtures
        fixtures.TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)
        for introduction, accepted in (("a", True), ("c", False)):
            with self.subTest(introduction=introduction):
                owner = fixtures.FailClosedDataTests()
                self.addCleanup(owner.doCleanups)
                root, fixture, raw, _, _ = owner.make_override_case(introduction=introduction)
                data = reporter.validate_fixture(fixture)
                data["repository_authority"] = reporter.validate_repository_authority(root, data)
                head = data["pull_requests"][1]["head_sha"]
                oid = reporter.run_git(root, "rev-parse", head + ":" + str(reporter.DECISION_RECORD_PATH))
                selected = gate.select_mode(
                    raw, number=1, head_sha=head, decision_oid=oid.decode().strip(), changed_lines=5000,
                    data=data, repository_root=root)
                self.assertEqual(selected.known, accepted, selected.reason)
                self.assertEqual(selected.mode, "concurrent")
                self.assertTrue(selected.pre_review_required, "timing override must not waive local review")
                self.assertEqual(selected.reason == "validated-pre-review-override", accepted)

    def test_safety_pause_uses_existing_events_and_never_counts_fixtures(self):
        raw = decisions()
        record = raw["pull_requests"][0]
        self.assertEqual(gate.pause_for_safety(record, [{"id": "unrelated", "type": "pilot_coordination"}]), ())
        for event_type in ("broken_master", "security_finding", "escaped_defect"):
            self.assertEqual(gate.pause_for_safety(record, [{"id": event_type, "type": event_type}]),
                             (event_type,))
        self.assertEqual(record["pilot"]["disposition"], "paused")
        self.assertFalse(record["pilot"]["included"])

    def test_live_decisions_can_be_projected_without_changing_the_frozen_cohort(self):
        raw = decisions(number=150)
        baseline = copy.deepcopy(raw)
        raw["pull_requests"].extend(decisions(number=191, mode="review-first")["pull_requests"])
        self.assertEqual(reporter.project_cohort_decisions(raw, {150}), baseline)
        self.assertEqual(len(raw["pull_requests"]), 2)
        with self.assertRaises(reporter.PilotDataError):
            reporter.project_cohort_decisions(
                {**raw, "pull_requests": [*raw["pull_requests"], raw["pull_requests"][0]]}, {150})


class GateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)
        result = self.fixture.complete()
        self.assertTrue(self.fixture.validate(result)["handoff_ready"])
        self.state = self.fixture.state
        self.pr = github.PullRequestState(
            "owner/repository", 1, 2, 3, "PR_node", 191, result["result_sha"], "agent/test",
            self.fixture.parent, "master", "Stable contract", "Stable body",
            datetime.now(timezone.utc))
        self.decision = gate.select_mode(
            decisions(risks=("lifecycle",), mode="review-first"), number=191,
            head_sha=self.pr.head_sha, decision_oid="d" * 40, changed_lines=50)
        self.record = gate.begin_candidate(self.state, self.pr, self.fixture.parent, self.decision)
        self.record["created_at"] = at_offset(-120)
        self.scope = frozenset({"TC-WORKFLOW-REVIEW-FAMILY-001/review-session"})
        self.session = review.ReviewSession(
            "coordinator", "implementer", self.scope, self.pr.head_sha,
            identity=(self.pr.repository, self.pr.number, self.fixture.parent),
            owners=review.ReviewOwnership())
        runtime = Runtime(self.pr.head_sha, self.scope)
        runtime.result.started_at, runtime.result.completed_at = at_offset(-100), at_offset(-90)
        self.session.begin(runtime, "reviewer")
        self.session.finish(runtime)
        self.fact = review.ReviewFact(
            "review-1", self.pr.head_sha, "BOT_kgDOCnlnWA", "APPROVED", at_offset(-60),
            "Complete coordinator-triaged content", ())
        self.session.triage(review.Triage(self.fact, "clean"))
        self.checks = tuple(gate.SecurityCheck(
            index, name, app_id, slug, self.pr.head_sha, "completed", "success",
            at_offset(-50), at_offset(-40))
            for index, (name, app_id, slug) in enumerate(sorted(gate.SECURITY_CHECKS), 1))
        self.runs = [self.workflow_run(1, "review-first")]

    def workflow_run(self, run_id, mode="full", *, event=None, conclusion=None, attempt=1):
        created = reporter.parse_time(at_offset(-20), "run")
        classifier = (candidate_evidence.PREFLIGHT_CLASSIFIER if mode == "review-first"
                      else candidate_evidence.FULL_CLASSIFIER)
        jobs = []
        for index, job_id in enumerate(sorted(candidate_evidence.KNOWN_JOB_IDS)):
            name = classifier if job_id == "event-classifier" else job_id
            verdict = ("skipped" if mode == "review-first" and job_id in {"legacy", "extended-host-tests"}
                       else "failure" if mode == "review-first" and job_id == "summary" else "success")
            jobs.append(github.JobState(
                index + 1, run_id, name, "completed", verdict,
                None if verdict == "skipped" else "hosted-runner", created, created, created,
                candidate_binding=(self.pr.number, self.pr.head_sha, self.fixture.parent)
                if job_id == "event-classifier" else None))
        return github.RunState(
            run_id, 10, run_id, attempt, self.pr.head_ref, created, created, created, "completed",
            conclusion or ("failure" if mode == "review-first" else "success"),
            "explicit-same", mode, tuple(jobs), event or ("pull_request" if mode == "review-first"
                                                        else "workflow_dispatch"),
            self.pr.head_sha, (self.pr.number, self.pr.head_sha, self.fixture.parent))

    def parsed_dispatch(self, run_id, *, queued=False, created=None, number=None, branch=None):
        from scripts.workflow_pilot.tests import test_pr_metadata as fixtures
        created = created or datetime.now(timezone.utc).replace(microsecond=0)
        self.assertEqual(created.microsecond, 0, "fixture must retain the provider's actual precision")
        start = created.isoformat().replace("+00:00", "Z")
        end = (created + timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
        raw, jobs = fixtures._run(run_id, number or run_id, mode="full")
        raw.update(event="workflow_dispatch", head_sha=self.pr.head_sha,
                   head_branch=branch or self.pr.head_ref, pull_requests=[],
                   path=".github/workflows/build.yml@refs/heads/" + (branch or self.pr.head_ref),
                   created_at=start, run_started_at=None if queued else start,
                   updated_at=start if queued else end,
                   status="queued" if queued else "completed", conclusion=None if queued else "success")
        raw["url"] = raw["url"].replace(fixtures.REPOSITORY, self.pr.repository)
        jobs = [] if queued else [job for job in jobs if job["name"] != "patch-release"]
        for job in jobs:
            job.update(event="workflow_dispatch", head_sha=raw["head_sha"], head_branch=raw["head_branch"],
                       created_at=start, started_at=start, completed_at=end)
            for field in ("url", "run_url", "check_run_url", "html_url"):
                job[field] = job[field].replace(fixtures.REPOSITORY, self.pr.repository)
            if job["name"] == "event-classifier":
                job["steps"] = [{
                    "name": gate.binding_name(self.pr.number, self.pr.head_sha, self.record["base_sha"]),
                    "status": "completed", "conclusion": "success"}]
        workflow = fixtures._workflow()
        for field in ("url", "html_url", "badge_url"):
            workflow[field] = workflow[field].replace(fixtures.REPOSITORY, self.pr.repository)
        client = fixtures.ScriptedClient()
        client.add("GET", github._endpoint(self.pr.repository, "actions/workflows/build.yml"), workflow)
        client.add("GET", github._query_endpoint(
            self.pr.repository, "actions/workflows/build.yml/runs",
            [("head_sha", self.pr.head_sha), ("per_page", "100"), ("page", "1")]),
            {"total_count": 1, "workflow_runs": [raw]})
        if not queued:
            client.add("GET", github._endpoint(self.pr.repository, f"actions/runs/{run_id}"), raw)
            client.add("GET", github._endpoint(
                self.pr.repository, f"compare/{self.pr.base_sha}...{self.pr.head_sha}"),
                {"base_commit": {"sha": self.pr.base_sha},
                 "merge_base_commit": {"sha": self.record["base_sha"]}})
        client.add("GET", github._query_endpoint(
            self.pr.repository, f"actions/runs/{run_id}/attempts/1/jobs",
            [("per_page", "100"), ("page", "1")]), {"total_count": len(jobs), "jobs": jobs})
        parsed = github.list_candidate_runs(client, self.pr)
        self.assertEqual(len(parsed), 1)
        return parsed[0]

    def assess(self, **changes):
        args = dict(state=self.state, record=self.record, decision=self.decision, pr=self.pr,
                    session=self.session, facts=(self.fact,), triage=tuple(self.session.rounds.events),
                    checks=self.checks, runs=tuple(self.runs), criteria_ready=True)
        args.update(changes)
        return gate.assess_candidate(**args)

    def dispatched(self):
        report = self.assess()
        self.assertTrue(report["dispatchable"], report)
        with patch.object(observations, "utc_now", return_value=at_offset(-30)):
            gate.reserve_full_dispatch(self.state, self.record, report, self.runs)
        self.record["dispatch_sent_at"] = at_offset(-29)
        self.runs.append(self.workflow_run(2))

    def test_clean_exact_review_security_and_handoff_admit_one_complete_run(self):
        self.assertFalse(self.assess()["merge_eligible"])
        self.dispatched()
        report = self.assess()
        self.assertTrue(report["merge_eligible"], report)
        self.assertFalse(report["dispatchable"])
        self.assertEqual((self.record["full_run_id"], self.record["full_attempt"]), (2, 1))
        with self.assertRaises(ValueError):
            gate.reserve_full_dispatch(self.state, self.record, report, self.runs)
        body = gate.evidence_comment(report, "Preserved local/runtime evidence.")
        self.assertEqual(body.count(github.EVIDENCE_MARKER), 1)
        self.assertIn("Preserved local/runtime evidence.", body)
        self.assertTrue(report["final_master_build_required"])

    def test_accepted_finding_abandons_even_if_an_early_or_later_build_passes(self):
        finding = review.Finding(
            "finding", next(iter(self.scope)), "wire", "validators:review-session",
            self.pr.head_sha, "scripts/workflow_pilot/review_family.py", self.fact.id)
        changed = replace(self.fact, body="Actual valid defect", state="CHANGES_REQUESTED")
        self.session.triage(review.Triage(changed, "changes-requested", (finding,)))
        self.fact = changed
        self.runs.append(self.workflow_run(2))
        report = self.assess()
        self.assertEqual(report["state"], "review-abandoned")
        self.assertFalse(report["dispatchable"])
        self.assertFalse(report["merge_eligible"])
        clean = replace(changed, body="Later edit cannot revive the head", state="APPROVED")
        self.session.triage(review.Triage(clean, "clean"))
        self.fact = clean
        self.assertFalse(self.assess()["merge_eligible"])
        self.assertIs(gate.begin_candidate(
            self.state, self.pr, self.fixture.parent, self.decision), self.record)

    def test_false_positive_retriage_unresolved_and_missing_evidence_do_not_abandon(self):
        unresolved = replace(self.fact, unresolved_threads=("thread-1",))
        self.session.triage(review.Triage(unresolved, "untriaged"))
        self.fact = unresolved
        report = self.assess()
        self.assertFalse(report["dispatchable"])
        self.assertIsNone(self.record["abandoned_reason"])
        explained = replace(unresolved, body="False positive: inspected source and observed behavior",
                            unresolved_threads=())
        self.session.triage(review.Triage(explained, "clean"))
        self.fact = explained
        self.assertTrue(self.assess()["dispatchable"])
        for changes in ({"checks": ()}, {"criteria_ready": False}, {"facts": ()}, {"runs": ()}):
            self.assertFalse(self.assess(**changes)["dispatchable"])
        empty_handoff = {**self.state, "assignments": []}
        self.assertFalse(self.assess(state=empty_handoff)["dispatchable"])

    def test_security_failure_requires_triage_and_valid_finding_is_terminal(self):
        failed = replace(self.checks[0], conclusion="failure")
        self.checks = failed, self.checks[1]
        self.assertFalse(self.assess()["dispatchable"])
        self.assertIsNone(self.record["abandoned_reason"])
        self.assertEqual(self.assess(accepted_security=(failed,))["state"], "review-abandoned")
        self.checks = replace(failed, conclusion="success"), self.checks[1]
        self.assertFalse(self.assess()["dispatchable"])

    def test_stale_security_review_run_and_changed_head_base_reject(self):
        stale = replace(self.checks[0], head_sha="a" * 40)
        self.assertFalse(self.assess(checks=(stale, self.checks[1]))["dispatchable"])
        self.assertFalse(self.assess(facts=(replace(self.fact, head="a" * 40),))["dispatchable"])
        self.dispatched()
        self.assertTrue(self.assess()["merge_eligible"])
        for run in (
            replace(self.runs[-1], head_sha="a" * 40),
            replace(self.runs[-1], candidate_binding=(191, self.pr.head_sha, "b" * 40)),
            replace(self.runs[-1], run_attempt=2),
            replace(self.runs[-1], conclusion="cancelled"),
        ):
            self.assertFalse(self.assess(runs=(self.runs[0], run))["merge_eligible"])
        for pr in (replace(self.pr, head_sha="a" * 40), replace(self.pr, base_ref="other-base")):
            with self.assertRaises(ValueError):
                self.assess(pr=pr)
        other = replace(self.pr, head_sha="a" * 40)
        gate.begin_candidate(self.state, other, self.fixture.parent,
                             replace(self.decision, head_sha=other.head_sha))
        self.assertEqual(self.record["abandoned_reason"], "superseded-head-or-base")
        self.assertFalse(self.assess()["merge_eligible"])

    def test_unrelated_live_base_movement_does_not_cancel_a_candidate(self):
        from scripts.workflow_pilot.trusted_review_gate import GitTree, ReviewTools
        root = self.fixture.repository
        (root / "docs/unrelated.txt").write_text("Independent master work\n")
        git(root, "add", ".")
        git(root, "commit", "-m", "Independent upstream change")
        advanced = git(root, "rev-parse", "HEAD")
        tools = SimpleNamespace(model=review, tree=lambda revision: GitTree(self.fixture.worktree, revision))
        request = {"candidate_sha": self.pr.head_sha, "base_sha": self.fixture.parent}
        ReviewTools.validate_base(tools, request, advanced)
        with self.assertRaises(ValueError):
            ReviewTools.validate_base(tools, request, self.pr.head_sha)
        report = self.assess(pr=replace(self.pr, base_sha=advanced))
        self.assertTrue(report["dispatchable"])
        self.assertIsNone(self.record["abandoned_reason"])

    def test_new_head_checks_can_start_before_coordinator_registration_but_base_rebind_cannot_reuse_them(self):
        self.record["created_at"] = observations.utc_now()
        self.assertTrue(self.assess()["dispatchable"], "automatic checks precede coordinator observation")
        rebound_pr = replace(self.pr, base_ref="changed-base")
        rebound = gate.begin_candidate(self.state, rebound_pr, self.fixture.parent, self.decision)
        report = self.assess(record=rebound, pr=rebound_pr)
        self.assertFalse(report["dispatchable"])
        self.assertIn("review-predates-candidate-binding", report["missing"])
        self.assertIn("exact-clean-security", report["missing"])

    def test_early_manual_duplicate_and_abandoned_cancelled_runs_are_inadmissible(self):
        self.runs.append(self.workflow_run(2))
        self.assertIn("early-or-unbound-full-run", self.assess()["missing"])
        self.assertFalse(self.assess()["merge_eligible"])
        self.runs.append(self.workflow_run(3))
        self.assertIn("duplicate-full-run", self.assess()["missing"])
        self.record["abandoned_reason"] = "accepted-review-or-security-finding"
        self.runs = [replace(run, conclusion="cancelled") for run in self.runs]
        self.assertFalse(self.assess()["merge_eligible"])

    def test_real_queued_unbound_dispatch_blocks_both_dispatch_and_merge(self):
        queued = self.parsed_dispatch(3, queued=True)
        self.assertEqual((queued.mode, queued.binding, queued.candidate_binding, queued.jobs),
                         ("active-unknown", "unbound", None, ()))
        self.boundary_evidence = {"queued": asdict(queued), "assessments": []}
        pending = self.assess(runs=(*self.runs, queued))
        self.boundary_evidence["assessments"].append(pending)
        with self.subTest(state="before dispatch"):
            self.assertFalse(pending["dispatchable"])
            self.assertFalse(pending["merge_eligible"])
            self.assertIn(queued.run_id, {item["run_id"] for item in pending["runs"]})
        self.dispatched()
        self.runs[-1] = self.parsed_dispatch(2)
        ready = self.assess()
        self.assertTrue(ready["merge_eligible"], ready)
        pending = self.assess(runs=(*self.runs, queued))
        self.boundary_evidence["assessments"].append(pending)
        with self.subTest(state="beside successful full"):
            self.assertFalse(pending["merge_eligible"])
            self.assertFalse(pending["dispatchable"])
            self.assertIn(queued.run_id, {item["run_id"] for item in pending["runs"]})
        unrelated = self.parsed_dispatch(4, queued=True, branch="other-pr")
        self.assertTrue(self.assess(runs=(*self.runs, unrelated))["merge_eligible"])
        resolved = self.parsed_dispatch(3)
        duplicate = self.assess(runs=(*self.runs, resolved))
        self.assertFalse(duplicate["merge_eligible"])
        self.assertIn("duplicate-full-run", duplicate["missing"])

    def test_base_rebind_accepts_fresh_clean_after_complete_history_without_dropping_holds(self):
        old = self.fact
        self.pr = replace(self.pr, base_ref="retargeted-base")
        with patch.object(observations, "utc_now", return_value=at_offset(-30)):
            self.record = gate.begin_candidate(
                self.state, self.pr, self.fixture.parent, self.decision)
        self.assertFalse(self.assess()["dispatchable"])
        fresh = replace(old, id="review-2", submitted_at=at_offset(-10), body="Fresh complete clean review")
        self.session.triage(review.Triage(fresh, "clean"))
        self.checks = tuple(replace(check, created_at=at_offset(-20), completed_at=at_offset(-5))
                            for check in self.checks)

        def assess():
            return self.assess(facts=tuple(item.fact for item in self.session.rounds.events))

        current = assess()
        self.boundary_evidence = {"fresh_after_history": current}
        with self.subTest(history="fully triaged"):
            self.assertTrue(current["dispatchable"], current["missing"])
        self.assertFalse(self.assess(facts=(fresh,))["dispatchable"])
        changed = replace(old, body="Historical content changed")
        self.session.triage(review.Triage(changed, "untriaged"))
        self.assertFalse(assess()["dispatchable"])
        unresolved = replace(changed, unresolved_threads=("historical-thread",))
        self.session.triage(review.Triage(unresolved, "changes-requested"))
        self.assertFalse(assess()["dispatchable"])
        explained = replace(unresolved, body="Resolved with source-backed explanation", unresolved_threads=())
        self.session.triage(review.Triage(explained, "clean"))
        with self.subTest(history="resolved and fully retriaged"):
            self.assertTrue(assess()["dispatchable"])
        finding = review.Finding(
            "historical-valid", next(iter(self.scope)), "wire", "validators:review-session",
            self.pr.head_sha, "scripts/workflow_pilot/review_family.py", old.id)
        valid = replace(explained, body="Accepted real defect", state="CHANGES_REQUESTED")
        self.session.triage(review.Triage(valid, "changes-requested", (finding,)))
        self.assertEqual(assess()["state"], "review-abandoned")
        self.session.triage(review.Triage(replace(valid, body="Later edit", state="APPROVED"), "clean"))
        self.assertFalse(assess()["dispatchable"])

    def test_native_fractional_reservation_correlates_with_actual_second_precision_runs(self):
        second = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=10)
        reservation = second.replace(microsecond=250000).isoformat().replace("+00:00", "Z")
        with patch.object(observations, "utc_now", return_value=reservation):
            actual = gate.reserve_full_dispatch(self.state, self.record, self.assess(), self.runs)
        self.assertEqual(actual, reservation)
        self.record["dispatch_sent_at"] = second.replace(microsecond=750000).isoformat().replace("+00:00", "Z")
        run = self.parsed_dispatch(2, created=second)
        self.assertEqual(run.created_at, second)
        current = self.assess(runs=(*self.runs, run))
        self.boundary_evidence = {"reservation": reservation, "run": asdict(run), "assessment": current}
        with self.subTest(ordering="legitimate same second"):
            self.assertTrue(current["merge_eligible"], current["missing"])
        self.assertEqual(self.record["dispatch_requested_at"], reservation)
        for wrong in (
            self.parsed_dispatch(2, created=second - timedelta(seconds=1)),
            self.parsed_dispatch(2, created=second, number=self.record["watermark"]),
            replace(run, run_attempt=2),
        ):
            with self.subTest(wrong=(wrong.created_at, wrong.run_number, wrong.run_attempt)):
                self.assertFalse(self.assess(runs=(*self.runs, wrong))["merge_eligible"])
        duplicate = self.parsed_dispatch(3, created=second)
        self.assertFalse(self.assess(runs=(*self.runs, run, duplicate))["merge_eligible"])
        with self.assertRaises(ValueError):
            gate.reserve_full_dispatch(self.state, self.record, current, self.runs)
        self.assertEqual(self.record["dispatch_requested_at"], reservation)

    def test_concurrent_and_paused_routes_keep_the_full_graph_and_every_final_gate(self):
        for paused in (False, True):
            with self.subTest(paused=paused):
                self.decision = gate.select_mode(
                    decisions(paused=paused), number=191, head_sha=self.pr.head_sha,
                    decision_oid=self.record["decision_oid"], changed_lines=30)
                self.record["mode"] = "concurrent"
                self.runs = [self.workflow_run(2, event="pull_request")]
                self.assertTrue(self.assess()["merge_eligible"])
                self.assertFalse(self.assess(criteria_ready=False)["merge_eligible"])
                self.assertFalse(self.assess(checks=())["merge_eligible"])
                self.assertFalse(self.assess(runs=())["merge_eligible"])

    def test_schema_preserves_existing_state_and_bounds_optional_candidate_records(self):
        schema = json.loads((ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text())
        validator = Draft202012Validator(schema)
        self.assertTrue(validator.is_valid(self.state))
        handoff.validate_state(self.state)
        for value in (None, {}, self.state["candidates"] * 129):
            bad = {**self.state, "candidates": value}
            self.assertFalse(validator.is_valid(bad))
            with self.assertRaises(ValueError):
                handoff.validate_state(bad)

    def test_watcher_ownership_uses_the_existing_real_process_seam(self):
        first = self.fixture.waiting_process()
        handoff.reserve_watcher(self.state, "watch-one", 2, 1, self.pr.head_sha, first.pid)
        second = self.fixture.waiting_process()
        with self.assertRaises(ValueError):
            handoff.reserve_watcher(self.state, "watch-two", 2, 1, self.pr.head_sha, second.pid)

    def test_dispatch_persists_before_network_and_unknown_delivery_cannot_retry(self):
        path = self.fixture.home / "coordinator.json"
        write_json(path, self.state)
        calls = []
        pr = self.pr

        class Client:
            def request(inner, method, endpoint, **kwargs):
                calls.append((method, endpoint, kwargs))
                saved = observations.load_json(path)
                self.assertIsNotNone(saved["candidates"][0]["dispatch_requested_at"])
                self.assertEqual(kwargs["body"], {"ref": pr.head_ref})
                raise github.MetadataEditError("network outcome unknown")

        def assess(state):
            record = state["candidates"][0]
            report = self.assess(state=state, record=record)
            return record, report, self.runs

        with patch.object(github, "fetch_pull_request", return_value=self.pr):
            with self.assertRaises(github.MetadataEditError):
                gate.dispatch_full(Client(), path, self.pr, assess)
            with self.assertRaises(ValueError):
                gate.dispatch_full(Client(), path, self.pr, assess)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(observations.load_json(path)["candidates"][0]["dispatch_sent_at"])

    def test_observed_adapter_uses_shared_review_git_and_refreshes_all_remote_facts(self):
        from scripts.workflow_pilot import trusted_review_gate
        tools = SimpleNamespace(model=review, tree=lambda revision: trusted_review_gate.GitTree(
            self.fixture.worktree, revision))
        tools.validate_base = lambda request, base: trusted_review_gate.ReviewTools.validate_base(
            tools, request, base)
        payload = {"data": {"repository": {"nameWithOwner": self.pr.repository, "pullRequest": {
            "number": self.pr.number, "baseRefOid": self.pr.base_sha, "headRefOid": self.pr.head_sha,
            "reviews": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{
                "id": self.fact.id, "state": self.fact.state, "submittedAt": self.fact.submitted_at,
                "body": self.fact.body, "commit": {"oid": self.fact.head},
                "author": {"__typename": "Bot", "id": trusted_review_gate.COPILOT[1],
                           "login": trusted_review_gate.COPILOT[2]},
                "comments": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            }]},
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        }}}}
        calls = []

        def request(method, endpoint, **kwargs):
            self.assertEqual((method, endpoint), ("POST", "graphql"))
            calls.append(kwargs["body"])
            return SimpleNamespace(payload=copy.deepcopy(payload))

        client = SimpleNamespace(request=request)
        with patch.object(gate, "fetch_candidate", return_value=(self.pr, 10)), \
             patch.object(gate, "fetch_decision", return_value=self.decision), \
             patch.object(gate, "security_checks", return_value=self.checks), \
             patch.object(github, "list_candidate_runs", return_value=tuple(self.runs)) as run_reader:
            report, actual_runs = gate.assess_observed(
                client, self.state, self.record, self.session, tuple(self.session.rounds.events),
                tools, criteria_ready=True)
            self.assertTrue(report["dispatchable"], report)
            self.assertEqual(actual_runs, tuple(self.runs))
            self.assertEqual(run_reader.call_count, 2)
            self.assertEqual(len(calls), 2)
            run_reader.side_effect = [tuple(self.runs), (*self.runs, self.workflow_run(2))]
            with self.assertRaisesRegex(ValueError, "Build evidence changed"):
                gate.assess_observed(client, self.state, self.record, self.session,
                                     tuple(self.session.rounds.events), tools, criteria_ready=True)

    def test_cancellation_requires_persisted_abandonment_and_exact_actual_run(self):
        path = self.fixture.home / "coordinator.json"
        write_json(path, self.state)
        run = self.workflow_run(2)
        calls = []
        client = SimpleNamespace(request=lambda *args, **kwargs: calls.append((args, kwargs)))
        self.record["abandoned_reason"] = "accepted-review-or-security-finding"
        with self.assertRaisesRegex(ValueError, "recorded abandonment"):
            gate.cancel_abandoned(client, path, self.record, run)
        self.assertEqual(calls, [])
        write_json(path, self.state)
        actual = {"repository": self.pr.repository, "run_id": 2, "attempt": 1,
                  "head_sha": self.pr.head_sha, "workflow_id": run.workflow_id,
                  "status": "in_progress", "conclusion": None}
        with patch.object(observations, "github_run", return_value=actual) as read:
            gate.cancel_abandoned(client, path, self.record, run)
            read.assert_called_once_with(self.pr.repository, 2, 1, self.pr.head_sha)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ("POST", github._endpoint(
            self.pr.repository, "actions/runs/2/cancel")))
        with self.assertRaises(ValueError):
            gate.cancel_abandoned(client, path, self.record, replace(run, head_sha="a" * 40))
        self.assertEqual(len(calls), 1)


class SecurityTests(unittest.TestCase):
    def test_actual_client_parser_binds_app_sha_completion_and_pagination(self):
        pr = SimpleNamespace(repository="owner/repository", repository_id=1, head_sha="a" * 40)
        rows = [{"id": index, "name": name, "app": {"id": app, "slug": slug},
                 "head_sha": pr.head_sha, "status": "completed", "conclusion": "success",
                 "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:01:00Z"}
                for index, (name, app, slug) in enumerate(sorted(gate.SECURITY_CHECKS), 1)]
        payload = {"total_count": len(rows), "check_runs": rows}
        client = SimpleNamespace(request=lambda *args, **kwargs: SimpleNamespace(payload=payload, headers={}))
        self.assertEqual(len(gate.security_checks(client, pr)), 2)
        for field, wrong in (("head_sha", "b" * 40), ("app", {"id": 1, "slug": "github-actions"}),
                             ("status", "unknown"), ("id", True)):
            original = rows[0][field]
            rows[0][field] = wrong
            with self.subTest(field=field), self.assertRaises((ValueError, reporter.PilotDataError)):
                gate.security_checks(client, pr)
            rows[0][field] = original
        payload["total_count"] = 3
        with self.assertRaises(ValueError):
            gate.security_checks(client, pr)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        from scripts.workflow_pilot.tests import test_pr_metadata as metadata
        self.m = metadata

    def content(self, raw):
        payload = json.dumps(raw).encode()
        return {
            "type": "file", "path": reporter.DECISION_RECORD_PATH.as_posix(), "encoding": "base64",
            "sha": hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest(),
            "content": base64.b64encode(payload).decode(),
        }

    def route_client(self, raw, *, lines=10):
        m = self.m
        client = m.ScriptedClient()
        pr = {**m._pr(), "additions": lines, "deletions": 0}
        client.add("GET", m._endpoint(f"pulls/{m.PR_NUMBER}"), pr)
        client.add("GET", m._query("contents/" + reporter.DECISION_RECORD_PATH.as_posix(),
                                  [("ref", m.HEAD)]), self.content(raw))
        client.add("GET", m._endpoint(f"compare/{m.BASE}...{m.HEAD}"),
                   {"base_commit": {"sha": m.BASE}, "merge_base_commit": {"sha": m.BASE}})
        payload = {"action": "opened", "number": m.PR_NUMBER, "pull_request": pr}
        decision = event_classifier.classify_event(
            "pull_request", payload, github_ref=f"refs/pull/{m.PR_NUMBER}/merge", github_sha="a" * 40,
            pr_base_sha=m.BASE, pr_head_sha=m.HEAD, push_sha="")
        return client, payload, decision

    def test_route_reads_real_record_bytes_without_candidate_programs_or_pass_flags(self):
        m = self.m
        for raw, lines, expected in (
            (decisions(m.PR_NUMBER, ("protocol",)), 10, "review-first"),
            (decisions(m.PR_NUMBER), 2001, "review-first"),
            (decisions(m.PR_NUMBER), 20, "full"),
            (decisions(m.PR_NUMBER, ("security",), paused=True), 20, "full"),
            (decisions(m.PR_NUMBER + 1), 2001, "full"),
        ):
            with self.subTest(lines=lines, expected=expected):
                client, payload, decision = self.route_client(raw, lines=lines)
                result, selected, binding = gate.route_event(client, decision, payload, m.REPOSITORY)
                self.assertEqual(result.classification, expected)
                self.assertEqual(result.expected_head, m.HEAD)
                self.assertEqual(binding, gate.binding_name(m.PR_NUMBER, m.HEAD, m.BASE))
                self.assertEqual(selected.decision_oid, self.content(raw)["sha"])
                self.assertTrue(all(method == "GET" for method, _, _ in client.calls))
        raw = decisions(m.PR_NUMBER, ("protocol",))
        raw["pull_requests"][0]["program"] = "invented success"
        client, payload, decision = self.route_client(raw)
        result, selected, _ = gate.route_event(client, decision, payload, m.REPOSITORY)
        self.assertEqual(result.classification, "full")
        self.assertFalse(selected.known)

    def test_route_preserves_event_identity_across_real_unrelated_base_advance(self):
        m = self.m
        fixture = GitFixture(assign=False)
        self.addCleanup(fixture.close)
        head = fixture.commit()
        original_base = fixture.parent
        (fixture.repository / "docs/upstream.txt").write_text("Independent upstream work\n")
        git(fixture.repository, "add", ".")
        git(fixture.repository, "commit", "-m", "Independent master change")
        advanced_base = git(fixture.repository, "rev-parse", "HEAD")
        raw_event_pr = m._pr(head=head, base=original_base)
        payload = {"action": "synchronize", "number": m.PR_NUMBER, "pull_request": raw_event_pr}
        classified = event_classifier.classify_event(
            "pull_request", payload, github_ref=f"refs/pull/{m.PR_NUMBER}/merge",
            github_sha="f" * 40, pr_base_sha=original_base, pr_head_sha=head, push_sha="")
        self.boundary_evidence = {"event_base": original_base, "head": head, "cases": []}
        for name, live_base, live_head, base_ref, authentic, accepted in (
            ("unchanged", original_base, head, "master", True, True),
            ("unrelated-tip", advanced_base, head, "master", True, True),
            ("changed-merge-base", head, head, "master", True, False),
            ("changed-head", original_base, original_base, "master", True, False),
            ("changed-base-ref", original_base, head, "other-base", True, False),
            ("changed-head-ref", original_base, head, "master", True, False),
            ("changed-raw-event", original_base, head, "master", False, False),
        ):
            with self.subTest(case=name):
                client = m.ScriptedClient()
                current = {**m._pr(head=live_head, base=live_base), "additions": 10, "deletions": 0}
                current["base"]["ref"] = base_ref
                if name == "changed-head-ref":
                    current["head"]["ref"] = "other-head-ref"
                client.add("GET", m._endpoint(f"pulls/{m.PR_NUMBER}"), current)
                client.add("GET", m._query(
                    "contents/" + reporter.DECISION_RECORD_PATH.as_posix(), [("ref", live_head)]),
                    self.content(decisions(m.PR_NUMBER, ("lifecycle",))))
                bases = {}
                for base in {original_base, live_base}:
                    actual = git(fixture.repository, "merge-base", "--all", base, live_head).splitlines()
                    self.assertEqual(len(actual), 1)
                    bases[base] = actual[0]
                    client.add("GET", m._endpoint(f"compare/{base}...{live_head}"),
                               {"base_commit": {"sha": base}, "merge_base_commit": {"sha": actual[0]}})
                entry = {"case": name, "live_base": live_base, "merge_bases": bases}
                self.boundary_evidence["cases"].append(entry)
                event = copy.deepcopy(payload)
                if not authentic:
                    event["pull_request"]["base"]["sha"] = head

                def route():
                    try:
                        result = gate.route_event(client, classified, event, m.REPOSITORY)
                    except ValueError as error:
                        entry["error"] = str(error)
                        raise
                    entry["decision"] = asdict(result[0])
                    entry["binding"] = result[2]
                    return result

                if accepted:
                    result, _, binding = route()
                    self.assertEqual(result.classification, "review-first")
                    self.assertEqual(result.expected_base, original_base)
                    self.assertEqual(result.expected_head, head)
                    self.assertEqual(binding, gate.binding_name(m.PR_NUMBER, head, original_base))
                else:
                    with self.assertRaises(ValueError):
                        route()

    def test_input_free_dispatch_resolves_actual_pr_branch_and_stays_full(self):
        m = self.m
        client, payload, decision = self.route_client(decisions(m.PR_NUMBER, ("security",)))
        endpoint = m._query("pulls", [("state", "open"), ("head", "owner:" + m.HEAD_REF),
                                      ("per_page", "100")])
        client.add("GET", endpoint, [payload["pull_request"]])
        decision = replace(decision, reason="explicit-final-dispatch")
        result, selected, binding = gate.route_dispatch(
            client, decision, {"inputs": {}}, m.REPOSITORY, "refs/heads/" + m.HEAD_REF)
        self.assertEqual(result.classification, "full")
        self.assertTrue(result.run_expensive)
        self.assertEqual(selected.mode, "review-first")
        self.assertEqual(binding, gate.binding_name(m.PR_NUMBER, m.HEAD, m.BASE))
        for bad in ({"inputs": {"pass": True}}, {"inputs": "success"}):
            with self.assertRaises(ValueError):
                gate.route_dispatch(client, decision, bad, m.REPOSITORY, "refs/heads/" + m.HEAD_REF)

    def test_dispatched_run_uses_existing_job_parser_attempt_and_merge_base_identity(self):
        m = self.m
        pr = github._parse_pull_request_payload(m._pr(), m.REPOSITORY, m.PR_NUMBER)
        for event in ("pull_request", "workflow_dispatch"):
            with self.subTest(event=event):
                raw, jobs = m._run(10, 10, mode="full", attempt=2)
                raw["event"] = event
                if event == "workflow_dispatch":
                    raw["pull_requests"] = []
                for job in jobs:
                    job["event"] = event
                    if job["name"] == "event-classifier":
                        job["steps"] = [{
                            "name": gate.binding_name(m.PR_NUMBER, m.HEAD, "c" * 40),
                            "status": "completed", "conclusion": "success"}]
                client = m.ScriptedClient()
                m._add_snapshot(client, [(raw, jobs)])
                client.add("GET", m._endpoint(f"compare/{m.BASE}...{m.HEAD}"),
                           {"base_commit": {"sha": m.BASE}, "merge_base_commit": {"sha": "c" * 40}})
                observed = github.list_candidate_runs(client, pr)
                self.assertEqual(len(observed), 1)
                run = observed[0]
                self.assertEqual((run.run_id, run.run_attempt, run.event, run.head_sha),
                                 (10, 2, event, m.HEAD))
                self.assertEqual(run.binding, "explicit-same")
                self.assertEqual(run.candidate_binding, (m.PR_NUMBER, m.HEAD, "c" * 40))
                github.require_full_success(run)

    def test_actual_http_client_accepts_only_the_closed_dispatch_and_cancel_statuses(self):
        captured = []

        def runner(argv, **kwargs):
            captured.append((argv, kwargs))
            status = 204 if any(argument.endswith("/dispatches") for argument in argv) else 202
            return subprocess.CompletedProcess(argv, 0, f"HTTP/2.0 {status} Accepted\r\n\r\n".encode(), b"")

        client = github.GitHubClient("/usr/bin/gh", runner=runner)
        client.request("POST", "repos/owner/repo/actions/workflows/build.yml/dispatches",
                       body={"ref": "candidate"}, label="dispatch")
        client.request("POST", "repos/owner/repo/actions/runs/10/cancel", label="cancel")
        self.assertEqual(json.loads(captured[0][1]["input"]), {"ref": "candidate"})
        self.assertEqual(captured[1][1]["input"], None)


def workflow_condition(expression, context):
    expression = expression.removeprefix("${{").removesuffix("}}").strip()
    expression = expression.replace("always()", "True").replace("&&", "and").replace("||", "or")
    expression = re.sub(
        r"'[^']*'|[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+",
        lambda match: match[0] if match[0].startswith("'") else repr(context[match[0]]), expression)

    def value(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [bool(value(item)) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = value(node.left), value(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        raise AssertionError("unsupported workflow expression")

    return bool(value(ast.parse(expression, mode="eval").body))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        from tests.workflows import test_build_ci_topology as topology
        self.topology = topology
        self.text = (ROOT / ".github/workflows/build.yml").read_text()
        self.jobs = topology._job_blocks(self.text)

    def context(self, event="pull_request", classification="full"):
        head, base = "a" * 40, "b" * 40
        values = {
            "github.event_name": event, "github.sha": head,
            "github.event.after": head if event == "push" else "",
            "github.event.pull_request.head.sha": head, "github.event.pull_request.base.sha": base,
            "needs.event-identity.result": "success", "needs.event-classifier.result": "success",
            "needs.event-identity.outputs.fallback_kind": event,
            "needs.event-identity.outputs.fallback_sha": head,
        }
        for name, value in {
            "classification": classification, "head_valid": "true", "identity_valid": "true",
            "full_fallback": "false", "run_expensive": "true" if classification == "full" else "false",
            "expected_head": head, "expected_base": "" if event == "push" else base,
        }.items():
            values["needs.event-classifier.outputs." + name] = value
        return values

    def workers(self, context):
        return {name for name in candidate_evidence.WORKER_JOB_IDS
                if workflow_condition(self.topology._direct_job_if(self.jobs[name]), context)}

    def test_actual_job_guards_keep_full_graph_and_limit_initial_preflight(self):
        self.assertEqual(set(self.jobs), candidate_evidence.KNOWN_JOB_IDS)
        full = set(candidate_evidence.WORKER_JOB_IDS)
        for event in ("pull_request", "push", "workflow_dispatch"):
            self.assertEqual(self.workers(self.context(event)), full)
        for mode in ("metadata-only", "review-first"):
            context = self.context(classification=mode)
            self.assertEqual(self.workers(context), {"host-tests", "build"})
            for field, wrong in (
                ("needs.event-classifier.outputs.expected_head", "c" * 40),
                ("needs.event-classifier.outputs.expected_base", "c" * 40),
                ("needs.event-identity.result", "failure"),
                ("needs.event-classifier.outputs.identity_valid", "false"),
                ("needs.event-identity.outputs.fallback_sha", "c" * 40),
            ):
                with self.subTest(mode=mode, field=field):
                    self.assertEqual(self.workers({**context, field: wrong}), set())

    def test_actual_fast_preflight_scripts_reject_wrong_source_identity(self):
        for name in ("host-tests", "build"):
            script = self.topology._literal_run_script(self.topology._step_blocks(self.jobs[name])[0])
            environment = {
                **os.environ, "PR_NUMBER": "191", "PR_HEAD_SHA": "a" * 40, "PR_BASE_SHA": "b" * 40,
                "CLASSIFIED_HEAD": "a" * 40, "CLASSIFIED_BASE": "b" * 40,
                "DECISION_OID": "c" * 40, "CANDIDATE_BINDING": gate.binding_name(191, "a" * 40, "b" * 40),
            }
            for changes, success in (
                ({}, True), ({"PR_NUMBER": "false"}, False), ({"PR_HEAD_SHA": "HEAD"}, False),
                ({"CLASSIFIED_HEAD": "c" * 40}, False), ({"CLASSIFIED_BASE": "c" * 40}, False),
                ({"DECISION_OID": ""}, False),
                ({"CANDIDATE_BINDING": gate.binding_name(192, "a" * 40, "b" * 40)}, False),
            ):
                with self.subTest(job=name, changes=changes):
                    actual = subprocess.run(["/bin/bash", "-e", "-o", "pipefail", "-c", script],
                                            env={**environment, **changes}, capture_output=True, timeout=10)
                    self.assertEqual(actual.returncode == 0, success, actual.stderr.decode())

    def test_metadata_continuity_observes_real_dispatch_binding_and_current_merge_base(self):
        t = self.topology
        script = t._literal_run_script(t._step_blocks(self.jobs["summary"])[0])
        number, head, base = t.SUMMARY_TEST_PR_NUMBER, t.SUMMARY_TEST_HEAD_SHA, t.SUMMARY_TEST_BASE_SHA
        frozen = "c" * 40
        prior = t._summary_workflow_run(
            10, event="workflow_dispatch", pull_requests=[],
            path=".github/workflows/build.yml@refs/heads/fixture")
        current = t._summary_workflow_run(t.SUMMARY_TEST_RUN_ID)
        for marker, success in (
            (gate.binding_name(number, head, frozen), True),
            (gate.binding_name(number, "d" * 40, frozen), False),
            (gate.binding_name(number, head, "d" * 40), False),
            (gate.binding_name(number + 1, head, frozen), False),
            ("", False),
        ):
            with self.subTest(marker=marker):
                jobs = [job for job in t._summary_full_jobs() if job["name"] != "patch-release"]
                classifier = next(job for job in jobs if job["name"] == "event-classifier")
                classifier["steps"] = [{"name": marker, "status": "completed", "conclusion": "success"}]
                responses = {
                    t._summary_runs_path(): t._summary_response(t._summary_api_payload(
                        "workflow_runs", [current, prior])),
                    t._summary_jobs_path(10): t._summary_response(t._summary_api_payload("jobs", jobs)),
                    f"/repos/{t.SUMMARY_TEST_REPOSITORY}/compare/{base}...{head}": t._summary_response({
                        "base_commit": {"sha": base}, "merge_base_commit": {"sha": frozen}}),
                }
                actual, _ = t._run_summary_with_api(
                    script, environment=t._summary_metadata_env(), routes=responses)
                self.assertEqual(actual.returncode == 0, success, actual.stderr)
