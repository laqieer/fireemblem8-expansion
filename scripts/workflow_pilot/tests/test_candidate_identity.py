"""Native Git/local checks with synthetic provider review/Build observations."""

import copy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.workflow_pilot import adaptive_gate as gate, agent_handoff as handoff
from scripts.workflow_pilot import candidate_evidence, coordinator_observations as observations
from scripts.workflow_pilot import pr_metadata as github, review_family as review
from scripts.workflow_pilot.tests import test_pr_metadata as api
from scripts.workflow_pilot.tests.review_support import Runtime
from scripts.workflow_pilot.tests.test_adaptive_gate import decisions
from scripts.workflow_pilot.tests.test_agent_handoff import GitFixture, at_offset, git, write_json
from scripts.workflow_pilot.tests import test_coordinator_local as local_tests


class CandidateIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture(assign=False)
        self.addCleanup(self.fixture.close)
        self.middle = self.fixture.commit("Intermediate candidate work\n", name="docs/intermediate.md")
        self.head = self.fixture.commit('{"value":7}\n', name="docs/value.json")
        self.pr = github._parse_pull_request_payload(self.payload(self.fixture.parent),
                                                     api.REPOSITORY, api.PR_NUMBER)
        self.live = self.pr
        self.state = handoff.new_state(api.REPOSITORY, "coordinator-one", {
            "mode": "plan", "observed_at": at_offset(-10), "valid_until": at_offset(300),
            "autostop_enabled": None, "stop_on_disconnect": None,
            "plan": "Bounded test-owned native checks, without assumed host settings.",
        })
        self.path = self.fixture.home / "candidate-identities.json"
        self.decision = gate.select_mode(
            decisions(api.PR_NUMBER, ("lifecycle",), "review-first"),
            number=api.PR_NUMBER, head_sha=self.head, decision_oid="d" * 40, changed_lines=2)
        self.old = gate.begin_candidate(self.state, self.pr, self.fixture.parent, self.decision)
        self.owners = review.ReviewOwnership()
        # Both immutable Git bases exist and are reviewed before the first remote fixture review.
        self.old_session = self.session(self.fixture.parent)
        self.new_session = self.session(self.middle)
        self.complete_local(self.old, self.pr)
        self.old_fact = self.fact("review-old")
        self.old_session.triage(review.Triage(self.old_fact, "clean"))
        self.new_session.triage(review.Triage(self.old_fact, "clean"))
        self.old_checks = self.security()
        self.rows = []
        self.calls = []
        self.posts = []
        self.allow_post = False
        self.client = github.GitHubClient("/usr/bin/gh", runner=self.transport)
        self.add_run(1, self.fixture.parent, preflight=True)
        runs = github.list_candidate_runs(self.client, self.pr)
        gate.reserve_full_dispatch(self.state, self.old, self.assess(self.old, self.pr,
                                   self.old_session, self.old_checks, runs), runs)
        self.add_run(2, self.fixture.parent)

    def payload(self, base):
        raw = api._pr(head=self.head, base=base)
        raw["head"]["ref"] = "agent/test"
        return raw

    def session(self, base):
        scope = frozenset({"TC-WORKFLOW-REVIEW-FAMILY-001/review-session"})
        owner = self.state["coordinator_id"]
        session = review.ReviewSession(owner, owner, scope, self.head,
                                       identity=(api.REPOSITORY, api.PR_NUMBER, base), owners=self.owners)
        runtime = Runtime(self.head, scope)
        runtime.result.task = "review-" + base[:12]
        runtime.result.started_at = observations.utc_now()
        session.begin(runtime, "reviewer")
        runtime.result.completed_at = observations.utc_now()
        session.finish(runtime)
        return session

    def fact(self, name):
        return review.ReviewFact(name, self.head, "BOT_kgDOCnlnWA", "APPROVED",
                                 observations.utc_now(), "Complete fixture review content", ())

    def security(self):
        return tuple(gate.SecurityCheck(
            index, name, app, slug, self.head, "completed", "success",
            observations.utc_now(), observations.utc_now())
            for index, (name, app, slug) in enumerate(sorted(gate.SECURITY_CHECKS), 1))

    def complete_local(self, record, pr):
        gate.register_local_validation(self.state, record, pr, self.fixture.worktree, local_tests.CHECKS)
        gate.capture_local_check(self.state, record, pr, "raw")
        gate.capture_local_check(self.state, record, pr, "config",
                                lambda context, head: local_tests.CoordinatorLocalTests.executor(self, context, head))
        self.assertTrue(gate.coordinator_local_ready(self.state, record, pr))
        self.assertEqual(self.state["assignments"], [])

    def assess(self, record, pr, session, checks, runs, *, state=None, criteria=True):
        return gate.assess_candidate(
            self.state if state is None else state, record, self.decision, pr, session,
            tuple(item.fact for item in session.rounds.events), tuple(session.rounds.events),
            checks, runs, criteria_ready=criteria)

    def add_run(self, number, base, *, preflight=False, queued=False):
        raw, _ = api._run(number, number, mode="full")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        event = "pull_request" if preflight else "workflow_dispatch"
        raw.update(event=event, head_sha=self.head, head_branch="agent/test", created_at=now,
                   run_started_at=None if queued else now, updated_at=now,
                   status="queued" if queued else "completed",
                   conclusion=None if queued else "failure" if preflight else "success",
                   pull_requests=[{"number": api.PR_NUMBER, "head": {"sha": self.head},
                                   "base": {"sha": base}}] if preflight else [])
        jobs = []
        for index, key in enumerate(sorted(candidate_evidence.KNOWN_JOB_IDS), 1):
            skipped = preflight and key in {"extended-host-tests", "legacy"}
            name = gate.PREFLIGHT_CLASSIFIER if preflight and key == "event-classifier" else key
            job = api._job(
                name, job_id=number * 100 + index, run_id=number,
                head_sha=self.head, head_branch="agent/test", created_at=now,
                started_at=now, completed_at=now,
                runner_name=None if skipped else "fixture-runner",
                conclusion="skipped" if skipped else "failure" if preflight and key == "summary" else "success")
            job["event"] = event
            if key == "event-classifier":
                job["steps"] = [{"name": gate.binding_name(api.PR_NUMBER, self.head, base),
                                 "status": "completed", "conclusion": "success"}]
            jobs.append(job)
        self.rows.append((raw, [] if queued else jobs))

    def transport(self, argv, **kwargs):
        method = argv[argv.index("--method") + 1]
        endpoint = argv[argv.index("X-GitHub-Api-Version: 2022-11-28") + 1]
        self.calls.append((method, endpoint))
        if method == "POST":
            self.assertTrue(self.allow_post, "unexpected mutation in reconciliation")
            self.assertEqual(endpoint, api._endpoint("actions/workflows/build.yml/dispatches"))
            self.posts.append(json.loads(kwargs["input"]))
            return subprocess.CompletedProcess(argv, 0, b"HTTP/2.0 204 No Content\r\n\r\n", b"")
        self.assertEqual(method, "GET")
        routes = {
            api._endpoint(f"pulls/{api.PR_NUMBER}"): self.payload(self.live.base_sha),
            api._endpoint("actions/workflows/build.yml"): api._workflow(),
            api._query("actions/workflows/build.yml/runs",
                       [("head_sha", self.head), ("per_page", "100"), ("page", "1")]):
                {"total_count": len(self.rows), "workflow_runs": [raw for raw, _ in reversed(self.rows)]},
        }
        routes[api._endpoint(f"pulls/{api.PR_NUMBER}")]["base"]["ref"] = self.live.base_ref
        for raw, jobs in self.rows:
            routes[api._endpoint(f"actions/runs/{raw['id']}")] = raw
            routes[api._query(f"actions/runs/{raw['id']}/attempts/{raw['run_attempt']}/jobs",
                              [("per_page", "100"), ("page", "1")])] = {"total_count": len(jobs), "jobs": jobs}
        if endpoint.startswith(api._endpoint("compare/")):
            base, head = endpoint.rsplit("/", 1)[1].split("...")
            bases = git(self.fixture.worktree, "merge-base", "--all", base, head).splitlines()
            self.assertEqual(len(bases), 1)
            payload = {"base_commit": {"sha": base}, "merge_base_commit": {"sha": bases[0]}}
        else:
            payload = routes[endpoint]
        response = "HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps(payload)
        return subprocess.CompletedProcess(argv, 0, response.encode(), b"")

    def rebind(self, *, reserve=True):
        git(self.fixture.repository, "merge", "--ff-only", self.middle)
        self.live = replace(self.pr, base_sha=git(self.fixture.repository, "rev-parse", "HEAD"))
        self.new = gate.begin_candidate(self.state, self.live, self.middle, self.decision)
        self.assertEqual(len(self.state["candidates"]), 2)
        self.assertEqual(self.old["abandoned_reason"], "superseded-head-or-base")
        self.assertFalse(gate.coordinator_local_ready(self.state, self.new, self.live))
        self.complete_local(self.new, self.live)
        self.new_session.triage(review.Triage(self.fact("review-new"), "clean"))
        self.new_checks = self.security()
        self.add_run(3, self.middle, preflight=True)
        runs = github.list_candidate_runs(self.client, self.live)
        assessment = self.assess(self.new, self.live, self.new_session, self.new_checks, runs)
        self.assertTrue(assessment["dispatchable"], assessment)
        if reserve:
            gate.reserve_full_dispatch(self.state, self.new, assessment, runs)
            self.add_run(4, self.middle)
        write_json(self.path, self.state)

    def test_same_head_ref_rebind_uses_exact_reservation_and_preserves_old_cleanup(self):
        self.rebind()
        old = copy.deepcopy(self.old)
        runs = github.list_candidate_runs(self.client, self.live)
        before = self.assess(self.new, self.live, self.new_session, self.new_checks, runs)
        self.assertIn("early-or-unbound-full-run", before["missing"])
        current = gate.reconcile_full_dispatch(self.client, self.path, self.live)
        self.assertEqual((current["state"], current["run_id"]), ("dispatch-observed", 4))
        state = observations.load_json(self.path)
        self.assertEqual(state["candidates"][0], old)
        new = state["candidates"][1]
        self.assertIsNone(new["dispatch_sent_at"])
        self.assertTrue(self.assess(new, self.live, self.new_session, self.new_checks, runs,
                                   state=state)["merge_eligible"])
        self.assertFalse(self.assess(new, self.live, self.new_session, self.new_checks, runs,
                                    state=state, criteria=False)["merge_eligible"])
        cleanup = gate.reconcile_full_dispatch(self.client, self.path, self.pr)
        self.assertEqual((cleanup["state"], cleanup["run_id"]), ("observed-abandoned", 2))
        final = observations.load_json(self.path)
        self.assertEqual(final["candidates"][1], new)
        old_runs = github.list_candidate_runs(self.client, self.pr)
        self.assertFalse(self.assess(final["candidates"][0], self.pr, self.old_session,
                                    self.old_checks, old_runs, state=final)["merge_eligible"])
        self.assertEqual(self.posts, [])
        self.family_evidence = {"old": old, "new": new, "before": before, "cleanup": cleanup,
                                "final": final, "runs": [asdict(run) for run in runs]}

    def test_reserved_run_predicate_binds_every_candidate_identity_field(self):
        self.rebind()
        runs = github.list_candidate_runs(self.client, self.live)
        run = next(item for item in runs if item.run_id == 4)
        self.assertTrue(gate._reserved_dispatch(self.new, self.live, run, runs))
        for field, wrong in (("pr_number", api.PR_NUMBER + 1), ("head_sha", self.middle),
                             ("base_sha", self.fixture.parent), ("base_ref", "other")):
            with self.subTest(field=field):
                self.assertFalse(gate._reserved_dispatch({**self.new, field: wrong}, self.live, run, runs))
        self.assertFalse(gate._reserved_dispatch(self.new, replace(self.live, base_ref="other"), run, runs))

    def test_full_identity_lookup_keeps_history_and_is_order_independent(self):
        self.rebind()
        for records in (self.state["candidates"], list(reversed(self.state["candidates"]))):
            state = {**self.state, "candidates": records}
            self.assertIs(gate.find_candidate(state, gate.candidate_identity(self.old)), self.old)
            self.assertIs(gate.find_candidate(state, gate.candidate_identity(self.new)), self.new)
            before = copy.deepcopy(records)
            self.assertIs(gate.begin_candidate(state, self.pr, self.fixture.parent, self.decision), self.old)
            self.assertEqual(state["candidates"], before)
        key = gate.candidate_identity(self.new)
        for incomplete in (key[:3], (True, *key[1:]), (*key[:3], "other")):
            with self.assertRaises(ValueError):
                gate.find_candidate(self.state, incomplete)
        with self.assertRaises(ValueError):
            gate.find_candidate({**self.state, "candidates": [self.new, self.new]}, key)

    def test_base_ref_rebind_uses_observed_run_ownership_when_marker_is_unchanged(self):
        write_json(self.path, self.state)
        self.assertEqual(gate.reconcile_full_dispatch(self.client, self.path, self.pr)["run_id"], 2)
        self.state = observations.load_json(self.path)
        self.old = self.state["candidates"][0]
        git(self.fixture.repository, "branch", "alternate-base", self.fixture.parent)
        self.live = replace(self.pr, base_ref="alternate-base")
        new = gate.begin_candidate(self.state, self.live, self.fixture.parent, self.decision)
        self.complete_local(new, self.live)
        self.old_session.triage(review.Triage(self.fact("review-retargeted"), "clean"))
        checks = self.security()
        self.add_run(3, self.fixture.parent, preflight=True)
        runs = github.list_candidate_runs(self.client, self.live)
        ready = self.assess(new, self.live, self.old_session, checks, runs)
        self.assertTrue(ready["dispatchable"], ready)
        gate.reserve_full_dispatch(self.state, new, ready, runs)
        self.add_run(4, self.fixture.parent)
        write_json(self.path, self.state)
        self.assertEqual(gate.reconcile_full_dispatch(self.client, self.path, self.live)["run_id"], 4)
        saved = observations.load_json(self.path)
        runs = github.list_candidate_runs(self.client, self.live)
        self.assertTrue(self.assess(saved["candidates"][1], self.live, self.old_session, checks,
                                   runs, state=saved)["merge_eligible"])
        self.assertEqual(saved["candidates"][0]["full_run_id"], 2)
        old_run = next(run for run in runs if run.run_id == 2)
        new_run = next(run for run in runs if run.run_id == 4)
        with patch.object(observations, "github_run", side_effect=AssertionError("unexpected run lookup")):
            for wrong in (new_run, replace(old_run, run_attempt=2)):
                with self.assertRaises(ValueError):
                    gate.cancel_abandoned(self.client, self.path, saved["candidates"][0], wrong)
        unknown = copy.deepcopy(saved)
        unknown["candidates"][0].update(full_run_id=None, full_attempt=None)
        unknown["candidates"][0].pop("dispatch_observed_at")
        write_json(self.path, unknown)
        self.assertEqual(gate.reconcile_full_dispatch(self.client, self.path, self.live)["state"],
                         "dispatch-uncertain")
        self.assertFalse(self.assess(unknown["candidates"][1], self.live, self.old_session, checks,
                                    runs, state=unknown)["merge_eligible"])
        with patch.object(observations, "github_run", side_effect=AssertionError("unexpected run lookup")):
            with self.assertRaises(ValueError):
                gate.cancel_abandoned(self.client, self.path, unknown["candidates"][0], old_run)
        self.ref_evidence = {"observed": saved, "unknown_ownership": unknown,
                             "runs": [asdict(run) for run in runs]}

    def test_dispatch_accepts_unrelated_tip_advance_but_not_an_old_frozen_identity(self):
        self.rebind(reserve=False)
        selected = self.live
        self.fixture.repository.joinpath("docs/unrelated.md").write_text("Independent upstream work\n")
        git(self.fixture.repository, "add", ".")
        git(self.fixture.repository, "commit", "-m", "Unrelated base tip advance")
        self.live = replace(self.live, base_sha=git(self.fixture.repository, "rev-parse", "HEAD"))
        self.assertEqual(git(self.fixture.worktree, "merge-base", "--all",
                             self.live.base_sha, self.head), self.middle)

        def assess(state):
            record = next(item for item in state["candidates"] if (
                item["pr_number"], item["head_sha"], item["base_sha"], item["base_ref"]) ==
                (api.PR_NUMBER, self.head, self.middle, self.live.base_ref))
            runs = github.list_candidate_runs(self.client, self.live)
            return record, self.assess(record, self.live, self.new_session, self.new_checks,
                                      runs, state=state), runs

        self.allow_post = True
        with self.assertRaises(ValueError):
            gate.dispatch_full(self.client, self.path, self.pr, assess)
        self.assertEqual(self.posts, [])
        result = gate.dispatch_full(self.client, self.path, selected, assess)
        self.assertEqual(result["state"], "dispatch-observation-pending")
        self.assertEqual(self.posts, [{"ref": "agent/test"}])
        saved = observations.load_json(self.path)
        self.assertIsNotNone(saved["candidates"][1]["dispatch_sent_at"])
        self.assertIsNone(saved["candidates"][0]["dispatch_sent_at"])

    def test_zero_multiple_unknown_and_wrong_bound_runs_cannot_reconcile(self):
        self.rebind()
        original_rows, original_state = copy.deepcopy(self.rows), copy.deepcopy(self.state)
        self.negative_evidence = []
        for case in ("zero", "duplicate", "queued", "base", "head", "branch", "workflow",
                     "attempt", "candidate-ref", "candidate-head", "candidate-repository"):
            with self.subTest(case=case):
                self.rows = copy.deepcopy(original_rows)
                state = copy.deepcopy(original_state)
                selected = self.live
                raw, jobs = self.rows[-1]
                if case == "zero":
                    self.rows.pop()
                elif case == "duplicate":
                    self.add_run(5, self.middle)
                elif case == "queued":
                    self.add_run(5, self.middle, queued=True)
                elif case == "base":
                    next(job for job in jobs if job["name"] == "event-classifier")["steps"][0]["name"] = (
                        gate.binding_name(api.PR_NUMBER, self.head, self.fixture.parent))
                elif case == "head":
                    raw["head_sha"] = self.middle
                elif case == "branch":
                    raw["head_branch"] = "other"
                elif case == "workflow":
                    raw["workflow_id"] += 1
                elif case == "attempt":
                    state["candidates"][1].update(full_run_id=4, full_attempt=2)
                elif case == "candidate-ref":
                    selected = replace(selected, base_ref="other")
                elif case == "candidate-head":
                    selected = replace(selected, head_sha=self.middle)
                elif case == "candidate-repository":
                    selected = replace(selected, repository="other/repo")
                write_json(self.path, state)
                try:
                    result = gate.reconcile_full_dispatch(self.client, self.path, selected)
                except ValueError as error:
                    result = {"state": "held", "detail": str(error)}
                self.assertIn(result["state"], {"held", "dispatch-uncertain"}, result)
                saved = observations.load_json(self.path)
                self.assertEqual(saved["candidates"], state["candidates"])
                self.assertEqual(self.posts, [])
                self.negative_evidence.append({"case": case, "result": result})

    def test_reconciliation_and_later_clean_review_do_not_release_a_third_round_hold(self):
        self.rebind()
        for number in (1, 2, 3):
            self.new_session.triage(review.Triage(self.fact(f"change-{number}"), "changes-requested"))
        held = self.new_session.rounds.hold
        self.assertEqual(held, ("change-3", self.head))
        self.new_session.triage(review.Triage(self.fact("later-clean"), "clean"))
        self.assertEqual(gate.reconcile_full_dispatch(self.client, self.path, self.live)["run_id"], 4)
        state = observations.load_json(self.path)
        report = self.assess(state["candidates"][1], self.live, self.new_session, self.new_checks,
                             github.list_candidate_runs(self.client, self.live), state=state)
        self.assertFalse(report["merge_eligible"])
        self.assertIn("architecture-hold", report["missing"])
        self.assertEqual(self.new_session.rounds.hold, held)
        self.hold_evidence = {"held": held, "assessment": report}

    def test_unbound_terminal_full_run_holds_both_assessment_and_reconciliation(self):
        self.rebind()
        gate.reconcile_full_dispatch(self.client, self.path, self.live)
        self.add_run(5, self.middle)
        next(job for job in self.rows[-1][1] if job["name"] == "event-classifier")["steps"] = []
        state = observations.load_json(self.path)
        runs = github.list_candidate_runs(self.client, self.live)
        report = self.assess(state["candidates"][1], self.live, self.new_session,
                             self.new_checks, runs, state=state)
        self.assertFalse(report["merge_eligible"], report)
        self.assertIn("duplicate-full-run", report["missing"])
        self.assertIn(5, {item["run_id"] for item in report["runs"]})
        self.assertEqual(gate.reconcile_full_dispatch(self.client, self.path, self.live)["state"],
                         "dispatch-uncertain")
