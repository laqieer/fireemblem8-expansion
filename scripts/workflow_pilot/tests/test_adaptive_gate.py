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
import shutil
import subprocess
import tempfile
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


def model_control(decision, pr):
    """Explicit typed observation for reducer-only fixtures, not provider evidence."""
    return replace(decision, control=gate.PilotControl(
        pr.repository, pr.repository_id, "master", pr.base_sha, "c" * 40, False, at_offset(-150)))


def git_scope_files(root, base, head):
    stats = {}
    for row in git(root, "diff", "--numstat", "--no-renames", base, head).splitlines():
        added, deleted, name = row.split("\t")
        stats[name] = (int(added), int(deleted))
    files = []
    for name, (old_mode, new_mode, old_oid, new_oid) in handoff._changes(root, base, head).items():
        added, deleted = stats[name]
        files.append({
            "filename": name, "sha": (old_oid if new_mode == b"000000" else new_oid).decode(),
            "status": "removed" if new_mode == b"000000" else
                      "added" if old_mode == b"000000" else "modified",
            "additions": added, "deletions": deleted, "changes": added + deleted})
    return files


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

    def test_provenance_callback_without_scope_is_not_an_override(self):
        raw = decisions()
        raw["pull_requests"][0]["threshold"]["override_history"] = [
            {"enabled": True, "reason": "A reason cannot supply missing scope evidence"}]
        selected = gate.select_mode(raw, number=191, head_sha="a" * 40, decision_oid="b" * 40,
                                    changed_lines=3000, verify_override=lambda record: None)
        self.assertFalse(selected.known)
        self.assertEqual(selected.mode, "concurrent")
        self.assertIn("scope authority unavailable", selected.reason)

    def test_actual_git_pre_review_override_and_late_introduction(self):
        from scripts.workflow_pilot.tests import test_reporter as fixtures
        fixtures.TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)
        for introduction, accepted in (("a", True), ("c", False)):
            with self.subTest(introduction=introduction):
                owner = fixtures.FailClosedDataTests()
                self.addCleanup(owner.doCleanups)
                root, fixture, raw, _, shas = owner.make_override_case(
                    introduction=introduction, marker_path="docs/override.md", marker_lines=3000)
                data = reporter.validate_fixture(fixture)
                data["repository_authority"] = reporter.validate_repository_authority(root, data)
                head = data["pull_requests"][1]["head_sha"]
                oid = reporter.run_git(root, "rev-parse", head + ":" + str(reporter.DECISION_RECORD_PATH))
                selected = gate.select_mode(
                    raw, number=1, head_sha=head, decision_oid=oid.decode().strip(),
                    changed_lines=sum(item["changes"] for item in git_scope_files(root, shas["0"], head)),
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


class OverrideScopeTests(unittest.TestCase):
    def setUp(self):
        control = patch.object(gate, "fetch_pilot_control", side_effect=lambda client, repository, repository_id=None:
                               gate.PilotControl(repository, repository_id or 1, "master", "a" * 40,
                                                 "b" * 40, False, at_offset(-1)))
        control.start()
        self.addCleanup(control.stop)

    def route(self, category, *, risk="none", fault=None):
        from scripts.workflow_pilot.tests import test_pr_metadata as m
        artifacts = ROOT / "build/test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        owned = tempfile.TemporaryDirectory(prefix="override-scope-", dir=artifacts)
        self.addCleanup(owned.cleanup)
        root = Path(owned.name)
        git(root, "init", "-b", m.HEAD_REF)
        git(root, "config", "user.name", "Scope regression")
        git(root, "config", "user.email", "scope@example.invalid")
        path = {
            "generated": "src/data/supports.json",
            "localization": "texts/expansion/catalog.en.json",
            "docs": "docs/scope.md", "delete": "src/retired.c",
            "runtime": "src/runtime.c", "archival": "asm/archive.s",
            "spoofed": "src/generated/claimed.c", "partial-delete": "src/retired.c",
            "renamed-runtime": "src/runtime.c", "renamed-docs": "docs/old.md",
        }[category]
        if category in {"generated", "localization"}:
            before = (ROOT / path).read_text()
            after = before + "\n" * 2100
            self.assertEqual(json.loads(before), json.loads(after))
        else:
            before = "old\n" * 2100
            after = None if category == "delete" else (
                "old\n" if category == "partial-delete" else "new\n" * 2100)
        if category.startswith("renamed-"):
            before, after = "old\n" * 4200, "old\n" * 4200 + "new\n" * 2100
        source = root / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(before)
        raw = decisions(m.PR_NUMBER, (risk,))
        if fault == "reordered":
            raw["pull_requests"].extend(decisions(m.PR_NUMBER + 1)["pull_requests"])
        decision_path = root / reporter.DECISION_RECORD_PATH
        decision_path.parent.mkdir(parents=True)
        write_json(decision_path, raw)
        git(root, "add", ".")
        git(root, "commit", "-m", "Scope base")
        base = git(root, "rev-parse", "HEAD")
        git(root, "branch", "master", base)
        raw["pull_requests"][0]["threshold"]["override_history"] = [
            {"enabled": True, "reason": "Generated, localized, documented or bulk deletion work"}]
        if fault == "reordered":
            raw["pull_requests"].reverse()
        if fault in {"other-decision", "masked-decision"}:
            raw["pull_requests"].extend(decisions(m.PR_NUMBER + 1)["pull_requests"])
        write_json(decision_path, raw)
        if after is None:
            source.unlink()
        else:
            source.write_text(after)
        if category.startswith("renamed-"):
            destination = root / "docs/moved.md"
            destination.parent.mkdir(exist_ok=True)
            source.rename(destination)
        if fault == "mixed":
            (root / "src").mkdir(exist_ok=True)
            (root / "src/other.c").write_text("int runtime_change;\n")
        git(root, "add", ".")
        git(root, "commit", "-m", "Actual scope delta and timing record")
        head = git(root, "rev-parse", "HEAD")
        if fault in {"advanced-base", "masked-decision"}:
            upstream = reporter.load_decisions_from_commit(root, base)
            upstream["pull_requests"].extend(decisions(m.PR_NUMBER + 1)["pull_requests"])
            git(root, "checkout", "master")
            write_json(decision_path, upstream)
            git(root, "add", ".")
            git(root, "commit", "-m", "Independent integration-base metadata")
            base = git(root, "rev-parse", "HEAD")
        frozen = git(root, "merge-base", "--all", base, head)
        files = git_scope_files(root, frozen, head)
        if category.startswith("renamed-"):
            renames = [row.split("\t") for row in git(root, "diff", "--name-status", "-M", frozen, head).splitlines()
                       if row.startswith("R")]
            self.assertEqual(len(renames), 1)
            _, old, new = renames[0]
            stats = [row.split("\t") for row in git(root, "diff", "--numstat", "-M", frozen, head).splitlines()
                     if not row.endswith(str(reporter.DECISION_RECORD_PATH))]
            self.assertEqual(len(stats), 1)
            added, deleted = map(int, stats[0][:2])
            files = [item for item in files if item["filename"] not in {old, new}]
            files.append({"filename": new, "previous_filename": old, "status": "renamed",
                          "sha": git(root, "rev-parse", head + ":" + new),
                          "additions": added, "deletions": deleted, "changes": added + deleted})
        pr = m._pr(head=head, base=base)
        pr.update(changed_files=len(files), additions=sum(item["additions"] for item in files),
                  deletions=sum(item["deletions"] for item in files))
        payload = {"number": m.PR_NUMBER, "action": "synchronize", "pull_request": copy.deepcopy(pr)}
        event = event_classifier.classify_event(
            "pull_request", payload, github_ref=f"refs/pull/{m.PR_NUMBER}/merge", github_sha="f" * 40,
            pr_base_sha=base, pr_head_sha=head, push_sha="")
        endpoint = m._endpoint(f"compare/{base}...{head}")
        comparison = {"url": github._api_url(endpoint), "base_commit": {"sha": base},
                      "merge_base_commit": {"sha": git(root, "merge-base", "--all", base, head)},
                      "total_commits": 1, "commits": [{"sha": head}], "files": files}
        if fault == "missing":
            comparison.pop("files")
        elif fault == "truncated":
            comparison["files"] = files[:-1]
        elif fault == "stale":
            comparison["commits"] = [{"sha": base}]
        elif fault == "commit-truncated":
            comparison["total_commits"] = 2
        elif fault == "foreign-url":
            comparison["url"] = comparison["url"].replace("owner/repo", "foreign/repo")
        elif fault == "missing-count":
            pr.pop("changed_files")
        elif fault == "bad-status":
            comparison["files"][-1]["status"] = "claimed-generated"
        elif fault == "wrong-count":
            comparison["files"][-1]["changes"] += 1
        committed = git(root, "show", "-s", "--format=%cI", head).replace("+00:00", "Z")
        submitted = (reporter.parse_time(committed, "commit") + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        if fault == "late":
            submitted = committed
        review_data = {"data": {"repository": {"nameWithOwner": m.REPOSITORY, "pullRequest": {
            "number": m.PR_NUMBER, "baseRefOid": base, "headRefOid": head,
            "reviews": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{
                "id": "scope-review", "state": "APPROVED", "submittedAt": submitted,
                "body": "", "commit": {"oid": head},
                "author": {"__typename": "Bot", "id": "BOT_kgDOCnlnWA",
                           "login": "copilot-pull-request-reviewer"},
                "comments": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            }]}, "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        }}}}
        responses = {
            ("GET", m._endpoint("").rstrip("/")): {
                "id": m.REPOSITORY_ID, "full_name": m.REPOSITORY, "default_branch": "master"},
            ("GET", m._endpoint(f"pulls/{m.PR_NUMBER}")): pr,
            ("GET", endpoint): comparison,
            ("GET", m._endpoint(f"compare/{head}...{head}")): {
                "base_commit": {"sha": head}, "merge_base_commit": {"sha": head}},
            ("GET", m._endpoint(f"git/commits/{head}")): {"sha": head, "committer": {"date": committed}},
            ("POST", "graphql"): review_data,
        }
        for revision in {base, frozen, head}:
            content = reporter.run_git(root, "show", revision + ":" + str(reporter.DECISION_RECORD_PATH))
            responses[("GET", m._query("contents/" + str(reporter.DECISION_RECORD_PATH), [("ref", revision)]))] = {
                "path": str(reporter.DECISION_RECORD_PATH), "type": "file", "encoding": "base64",
                "sha": git(root, "rev-parse", revision + ":" + str(reporter.DECISION_RECORD_PATH)),
                "content": base64.b64encode(content).decode()}
        calls = []

        def transport(argv, **kwargs):
            method = argv[argv.index("--method") + 1]
            target = argv[argv.index("X-GitHub-Api-Version: 2022-11-28") + 1]
            calls.append((method, target))
            if fault in {"head-moves", "base-ref-moves"} and calls.count(("GET", endpoint)) >= 2 and target.endswith(
                    f"pulls/{m.PR_NUMBER}"):
                if fault == "head-moves":
                    (root / "later.md").write_text("A genuinely newer local Git candidate\n")
                    git(root, "add", ".")
                    git(root, "commit", "-m", "Candidate advanced during observation")
                    pr["head"]["sha"] = git(root, "rev-parse", "HEAD")
                else:
                    git(root, "branch", "retargeted", base)
                    pr["base"]["ref"] = "retargeted"
            headers = "HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n"
            if fault == "paginated" and target == endpoint:
                headers += f'Link: <{github._api_url(endpoint)}?page=2>; rel="next"\r\n'
            body = json.dumps(responses[(method, target)])
            return subprocess.CompletedProcess(argv, 0, (headers + "\r\n" + body).encode(), b"")

        result, selected, _ = gate.route_event(
            github.GitHubClient("/usr/bin/gh", runner=transport), event, payload, m.REPOSITORY)
        self.observed = {"category": category, "risk": risk, "fault": fault, "base": base, "head": head,
                         "files": files, "calls": calls, "decision": asdict(selected),
                         "classification": result.classification}
        return result, selected

    def test_only_actual_eligible_categories_honor_a_timely_override(self):
        for category, fault in (("generated", None), ("localization", None), ("docs", None),
                                ("delete", None), ("docs", "reordered"), ("renamed-docs", None)):
            with self.subTest(category=category, fault=fault):
                result, selected = self.route(category, fault=fault)
                self.assertTrue(selected.known, self.observed)
                self.assertEqual(selected.reason, "validated-pre-review-override")
                self.assertEqual(result.classification, "full")

    def test_known_ineligible_runtime_archival_and_claims_keep_size_timing(self):
        for category, risk, fault in (
            ("runtime", "runtime", None), ("archival", "archival", None),
            ("runtime", "none", None), ("runtime", "generated-data", None),
            ("spoofed", "generated-data", None), ("partial-delete", "none", None),
            ("renamed-runtime", "none", None),
            ("generated", "generated-data", "mixed"), ("docs", "none", "other-decision"),
        ):
            with self.subTest(category=category, risk=risk, fault=fault):
                result, selected = self.route(category, risk=risk, fault=fault)
                self.assertTrue(selected.known, self.observed)
                self.assertEqual((selected.reason, result.classification),
                                 ("large-change", "review-first"), self.observed)

    def test_bookkeeping_uses_actual_diff_origin_not_an_advanced_live_base(self):
        for fault, expected in (("advanced-base", "full"), ("masked-decision", "review-first")):
            with self.subTest(fault=fault):
                result, selected = self.route("docs", fault=fault)
                self.assertTrue(selected.known, self.observed)
                self.assertEqual(result.classification, expected, self.observed)

    def test_missing_stale_truncated_or_moving_authority_stays_unknown_and_broader(self):
        for fault in ("missing", "truncated", "stale", "commit-truncated", "foreign-url",
                      "missing-count", "bad-status", "wrong-count", "paginated",
                      "head-moves", "base-ref-moves", "late"):
            with self.subTest(fault=fault):
                result, selected = self.route("docs", fault=fault)
                self.assertFalse(selected.known, self.observed)
                self.assertTrue(selected.reason.startswith("unknown-decision:"), selected.reason)
                self.assertEqual(result.classification, "full")

    def test_named_risk_cannot_be_overridden_by_scope_or_missing_scope_facts(self):
        for fault in (None, "missing", "late"):
            with self.subTest(fault=fault):
                result, selected = self.route("docs", risk="security", fault=fault)
                self.assertEqual((selected.reason, result.classification), ("named-risk", "review-first"))


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
        self.decision = model_control(self.decision, self.pr)
        self.control_observation = patch.object(
            gate, "fetch_pilot_control", side_effect=lambda *args: self.decision.control)
        self.control_observation.start()
        self.addCleanup(self.control_observation.stop)
        self.record = gate.begin_candidate(self.state, self.pr, self.fixture.parent, self.decision, runs=())
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
        from scripts.workflow_pilot.tests.test_pr_metadata import WORKFLOW_ID
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
                if job_id == "event-classifier" else None,
                candidate_base_ref=self.pr.base_ref if job_id == "event-classifier" else None))
        return github.RunState(
            run_id, WORKFLOW_ID, run_id, attempt, self.pr.head_ref, created, created, created, "completed",
            conclusion or ("failure" if mode == "review-first" else "success"),
            "explicit-same", mode, tuple(jobs), event or ("pull_request" if mode == "review-first"
                                                        else "workflow_dispatch"),
            self.pr.head_sha, (self.pr.number, self.pr.head_sha, self.fixture.parent), self.pr.base_ref)

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
                    "name": gate.binding_name(self.pr.number, self.pr.head_sha, self.record["base_sha"], self.pr.base_ref),
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

    def test_registered_local_criteria_are_not_hidden_by_an_accepted_delegate(self):
        self.assertTrue(gate._local_ready(self.state, self.pr, self.record))
        gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, {
            "raw": {"contract": "git-diff-check", "evidence_id": "raw", "inputs": []},
            "extra": {"contract": "coordinator-check", "evidence_id": "extra", "inputs": []},
        })
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.assertFalse(self.assess()["dispatchable"])

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
                             replace(self.decision, head_sha=other.head_sha), runs=())
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
        rebound = gate.begin_candidate(self.state, rebound_pr, self.fixture.parent, self.decision, runs=())
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

    def test_queued_pr_run_with_old_base_tip_stays_visible_until_marker(self):
        from scripts.workflow_pilot.tests import test_pr_metadata as fixtures
        root = self.fixture.repository
        (root / "docs/unrelated.txt").write_text("Independent master work\n")
        git(root, "add", ".")
        git(root, "commit", "-m", "Advance unrelated base")
        advanced = git(root, "rev-parse", "HEAD")
        merge_bases = git(root, "merge-base", "--all", advanced, self.pr.head_sha).splitlines()
        self.assertEqual(merge_bases, [self.fixture.parent])
        self.pr = replace(self.pr, base_sha=advanced)
        before_dispatch = copy.deepcopy(self.state)
        self.dispatched()
        preflight = self.runs[0]
        start = datetime.now(timezone.utc).replace(microsecond=0)
        end = (start + timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
        start = start.isoformat().replace("+00:00", "Z")

        def read_pair(queued, *, foreign=False):
            rows = []
            for run_id in (3, 2):
                waiting = run_id == 3 and queued
                number = self.pr.number + int(run_id == 3 and foreign)
                event = "workflow_dispatch" if run_id == 2 else "pull_request"
                raw, jobs = fixtures._run(run_id, run_id, mode="full")
                raw.update(
                    event=event, head_sha=self.pr.head_sha, head_branch=self.pr.head_ref,
                    pull_requests=[] if run_id == 2 else [{
                        "number": number, "head": {"sha": self.pr.head_sha},
                        "base": {"sha": self.fixture.parent}}],
                    path=".github/workflows/build.yml@" + (
                        "refs/heads/" + self.pr.head_ref if run_id == 2
                        else f"refs/pull/{number}/merge"),
                    created_at=start, run_started_at=None if waiting else start,
                    updated_at=start if waiting else end,
                    status="queued" if waiting else "completed",
                    conclusion=None if waiting else "success")
                raw["url"] = raw["url"].replace(fixtures.REPOSITORY, self.pr.repository)
                jobs = [] if waiting else [job for job in jobs if job["name"] != "patch-release"]
                for job in jobs:
                    job.update(event=event, head_sha=self.pr.head_sha, head_branch=self.pr.head_ref,
                               created_at=start, started_at=start, completed_at=end)
                    for field in ("url", "run_url", "check_run_url", "html_url"):
                        job[field] = job[field].replace(fixtures.REPOSITORY, self.pr.repository)
                    if job["name"] == "event-classifier":
                        job["steps"] = [{
                            "name": gate.binding_name(number, self.pr.head_sha, merge_bases[0], self.pr.base_ref),
                            "status": "completed", "conclusion": "success"}]
                rows.append((raw, jobs))
            workflow = fixtures._workflow()
            for field in ("url", "html_url", "badge_url"):
                workflow[field] = workflow[field].replace(fixtures.REPOSITORY, self.pr.repository)
            client = fixtures.ScriptedClient()
            client.add("GET", github._endpoint(self.pr.repository, "actions/workflows/build.yml"), workflow)
            client.add("GET", github._query_endpoint(
                self.pr.repository, "actions/workflows/build.yml/runs",
                [("head_sha", self.pr.head_sha), ("per_page", "100"), ("page", "1")]),
                {"total_count": len(rows), "workflow_runs": [raw for raw, _ in rows]})
            for raw, jobs in rows:
                if raw["status"] == "completed":
                    client.add("GET", github._endpoint(
                        self.pr.repository, f"actions/runs/{raw['id']}"), raw)
                client.add("GET", github._query_endpoint(
                    self.pr.repository, f"actions/runs/{raw['id']}/attempts/1/jobs",
                    [("per_page", "100"), ("page", "1")]),
                    {"total_count": len(jobs), "jobs": jobs})
            client.add("GET", github._endpoint(
                self.pr.repository, f"compare/{advanced}...{self.pr.head_sha}"),
                *({"base_commit": {"sha": advanced}, "merge_base_commit": {"sha": merge_bases[0]}}
                  for _ in rows))
            return github.list_candidate_runs(client, self.pr)

        parsed = read_pair(True)
        self.assertEqual({run.run_id for run in parsed}, {2, 3})
        full = next(run for run in parsed if run.run_id == 2)
        queued = next(run for run in parsed if run.run_id == 3)
        self.assertEqual((full.binding, full.mode), ("explicit-same", "full"))
        self.assertEqual((queued.binding, queued.mode, queued.candidate_binding),
                         ("unbound", "active-unknown", None))
        ready = self.assess(runs=(preflight, full))
        self.assertTrue(ready["merge_eligible"], ready)
        dispatch = self.assess(state=before_dispatch, record=before_dispatch["candidates"][0],
                               runs=(preflight, queued))
        merge = self.assess(runs=(preflight, full, queued))
        classified = read_pair(False)
        duplicate = self.assess(runs=(preflight, *classified))
        unrelated = self.assess(runs=(preflight, *read_pair(False, foreign=True)))
        self.boundary_evidence = {
            "live_base": advanced, "merge_bases": merge_bases,
            "parsed_runs": [asdict(full), asdict(queued)],
            "dispatch": dispatch, "merge": merge, "duplicate": duplicate, "unrelated": unrelated,
        }
        for phase, report in (("dispatch", dispatch), ("merge", merge)):
            with self.subTest(phase=phase):
                self.assertFalse(report["dispatchable"])
                self.assertFalse(report["merge_eligible"])
                self.assertIn(queued.run_id, {item["run_id"] for item in report["runs"]})
        self.assertIn("duplicate-full-run", duplicate["missing"])
        self.assertFalse(duplicate["merge_eligible"])
        self.assertTrue(unrelated["merge_eligible"], unrelated)

    def test_base_rebind_accepts_fresh_clean_after_complete_history_without_dropping_holds(self):
        old = self.fact
        self.pr = replace(self.pr, base_ref="retargeted-base")
        with patch.object(observations, "utc_now", return_value=at_offset(-30)):
            self.record = gate.begin_candidate(
                self.state, self.pr, self.fixture.parent, self.decision, runs=())
        self.runs.append(self.workflow_run(2, "review-first"))
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
                self.decision = model_control(self.decision, self.pr)
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
                if method == "GET":
                    self.assertEqual(endpoint, github._endpoint(
                        pr.repository, f"compare/{pr.base_sha}...{pr.head_sha}"))
                    return SimpleNamespace(payload={
                        "base_commit": {"sha": pr.base_sha},
                        "merge_base_commit": {"sha": git(self.fixture.worktree, "merge-base",
                                                        pr.base_sha, pr.head_sha)}})
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

    def test_observed_run_recovers_lost_post_ack_without_another_post(self):
        from scripts.workflow_pilot.tests import test_pr_metadata as fixtures
        self.runs[0] = replace(self.runs[0], workflow_id=fixtures.WORKFLOW_ID)
        path = self.fixture.home / "lost-ack.json"
        write_json(path, self.state)
        calls = []
        def accept_post(argv, **kwargs):
            if argv[argv.index("--method") + 1] == "GET":
                self.assertEqual(argv[-1], github._endpoint(
                    self.pr.repository, f"compare/{self.pr.base_sha}...{self.pr.head_sha}"))
                payload = {"base_commit": {"sha": self.pr.base_sha}, "merge_base_commit": {
                    "sha": git(self.fixture.worktree, "merge-base", self.pr.base_sha, self.pr.head_sha)}}
                wire = "HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps(payload)
                return subprocess.CompletedProcess(argv, 0, wire.encode(), b"")
            calls.append((argv, kwargs))
            self.assertEqual(json.loads(kwargs["input"]), {"ref": self.pr.head_ref})
            return subprocess.CompletedProcess(argv, 0, b"HTTP/2.0 204 No Content\r\n\r\n", b"")
        client = github.GitHubClient("/usr/bin/gh", runner=accept_post)
        locked = observations.locked_state
        attempts = []

        def fail_second_lock(target):
            attempts.append(str(target))
            if len(attempts) == 2:
                raise observations.ObservationError("accepted POST, second lock unavailable")
            return locked(target)

        def assess(state):
            record = state["candidates"][0]
            return record, self.assess(state=state, record=record), self.runs

        with patch.object(github, "fetch_pull_request", return_value=self.pr), \
             patch.object(observations, "locked_state", side_effect=fail_second_lock):
            with self.assertRaises(observations.ObservationError):
                gate.dispatch_full(client, path, self.pr, assess)
        self.assertEqual(len(calls), 1)
        saved = observations.load_json(path)
        record = saved["candidates"][0]
        self.assertIsNotNone(record["dispatch_requested_at"])
        self.assertIsNone(record["dispatch_sent_at"])
        run = self.parsed_dispatch(2)
        before = self.assess(state=saved, record=record, runs=(*self.runs, run))
        self.assertFalse(before["merge_eligible"])
        self.boundary_evidence = {"requested": record["dispatch_requested_at"], "before": before}
        with patch.object(github, "list_candidate_runs", return_value=(*self.runs, run)), \
             patch.object(github, "fetch_pull_request", return_value=self.pr), \
             patch.object(gate, "frozen_base", return_value=self.fixture.parent):
            gate.reconcile_full_dispatch(client, path, self.pr)
        recovered = observations.load_json(path)
        record = recovered["candidates"][0]
        self.assertIsNone(record["dispatch_sent_at"], "missing HTTP acknowledgement must not be invented")
        self.assertEqual(record["dispatch_requested_at"], self.boundary_evidence["requested"])
        self.assertEqual((record["full_run_id"], record["full_attempt"]), (2, 1))
        after = self.assess(state=recovered, record=record, runs=(*self.runs, run))
        self.assertTrue(after["merge_eligible"], after)
        self.assertFalse(self.assess(state=recovered, record=record, runs=(*self.runs, run),
                                    checks=())["merge_eligible"])
        self.boundary_evidence["after"] = after
        self.assertEqual(len(calls), 1, "reconciliation must never dispatch again")

    def test_dispatch_reconciliation_keeps_ambiguity_stale_runs_and_abandonment_closed(self):
        from scripts.workflow_pilot.tests import test_pr_metadata as fixtures
        self.runs[0] = replace(self.runs[0], workflow_id=fixtures.WORKFLOW_ID)
        with patch.object(observations, "utc_now", return_value=at_offset(-30)):
            gate.reserve_full_dispatch(self.state, self.record, self.assess(), self.runs)
        good = self.parsed_dispatch(2)
        other = self.parsed_dispatch(3)
        queued = self.parsed_dispatch(3, queued=True)
        path = self.fixture.home / "reconciliation.json"
        def read_compare(method, endpoint, **kwargs):
            self.assertEqual((method, endpoint), ("GET", github._endpoint(
                self.pr.repository, f"compare/{self.pr.base_sha}...{self.pr.head_sha}")))
            return SimpleNamespace(payload={"base_commit": {"sha": self.pr.base_sha}, "merge_base_commit": {
                "sha": git(self.fixture.worktree, "merge-base", self.pr.base_sha, self.pr.head_sha)}})

        client = SimpleNamespace(request=read_compare)
        cases = (
            (), (good, other), (good, queued),
            (replace(good, created_at=reporter.parse_time(at_offset(-60), "earlier")),),
            (replace(good, run_number=self.record["watermark"]),),
            (replace(good, head_sha="a" * 40),),
            (replace(good, head_branch="other"),),
            (replace(good, candidate_binding=(191, self.pr.head_sha, "a" * 40)),),
            (replace(good, workflow_id=good.workflow_id + 1),),
            (replace(good, event="pull_request"),),
        )
        for index, extra in enumerate(cases):
            with self.subTest(case=index):
                write_json(path, self.state)
                with patch.object(github, "list_candidate_runs", return_value=(*self.runs, *extra)):
                    result = gate.reconcile_full_dispatch(client, path, self.pr)
                self.assertEqual(result["state"], "dispatch-uncertain")
                record = observations.load_json(path)["candidates"][0]
                self.assertIsNone(record["full_run_id"])
                self.assertIsNone(record["dispatch_sent_at"])
                self.assertNotIn("dispatch_observed_at", record)
        for reason in ("accepted-review-or-security-finding", "superseded-head-or-base"):
            with self.subTest(abandoned=reason):
                state = copy.deepcopy(self.state)
                state["candidates"][0]["abandoned_reason"] = reason
                write_json(path, state)
                with patch.object(github, "list_candidate_runs", return_value=(*self.runs, good)), \
                     patch.object(github, "fetch_pull_request", return_value=self.pr), \
                     patch.object(gate, "frozen_base", return_value=self.fixture.parent):
                    result = gate.reconcile_full_dispatch(client, path, self.pr)
                self.assertEqual(result["state"], "observed-abandoned")
                state = observations.load_json(path)
                record = state["candidates"][0]
                self.assertEqual(record["full_run_id"], good.run_id)
                self.assertIsNone(record["dispatch_sent_at"])
                self.assertFalse(self.assess(state=state, record=record,
                                             runs=(*self.runs, good))["merge_eligible"])
        state = copy.deepcopy(self.state)
        state["candidates"][0].update(full_run_id=2, full_attempt=2)
        write_json(path, state)
        with patch.object(github, "list_candidate_runs", return_value=(*self.runs, good)):
            self.assertEqual(gate.reconcile_full_dispatch(client, path, self.pr)["state"], "dispatch-uncertain")
        write_json(path, self.state)
        with patch.object(github, "list_candidate_runs", return_value=(*self.runs, good)), \
             patch.object(github, "fetch_pull_request", return_value=replace(self.pr, head_sha="a" * 40)):
            self.assertEqual(gate.reconcile_full_dispatch(client, path, self.pr)["state"], "observed-abandoned")
        observed = observations.load_json(path)
        self.assertEqual(observed["candidates"][0]["abandoned_reason"], "superseded-head-or-base")
        schema = Draft202012Validator(json.loads(
            (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()))
        self.assertTrue(schema.is_valid(observed))
        observed["candidates"][0]["dispatch_observed_at"] = None
        self.assertFalse(schema.is_valid(observed))
        with self.assertRaises(ValueError):
            handoff.validate_state(observed)

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
        control = patch.object(gate, "fetch_pilot_control", side_effect=lambda client, repository, repository_id=None:
                               gate.PilotControl(repository, repository_id or metadata.REPOSITORY_ID, "master",
                                                 "a" * 40, "b" * 40, False, at_offset(-1)))
        control.start()
        self.addCleanup(control.stop)

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
        client.add("GET", m._endpoint(f"pulls/{m.PR_NUMBER}"), pr, pr)
        repository = {"id": m.REPOSITORY_ID, "full_name": m.REPOSITORY, "default_branch": "master"}
        client.add("GET", m._endpoint("").rstrip("/"), repository, repository)
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
                self.assertEqual(binding, gate.binding_name(m.PR_NUMBER, m.HEAD, m.BASE, "master"))
                self.assertEqual(selected.decision_oid, self.content(raw)["sha"])
                self.assertTrue(all(method == "GET" for method, _, _ in client.calls))
        raw = decisions(m.PR_NUMBER, ("protocol",))
        raw["pull_requests"][0]["program"] = "invented success"
        client, payload, decision = self.route_client(raw)
        result, selected, _ = gate.route_event(client, decision, payload, m.REPOSITORY)
        self.assertEqual(result.classification, "full")
        self.assertFalse(selected.known)

    def test_production_route_observes_immutable_pre_review_override(self):
        from scripts.workflow_pilot.tests import test_reporter as fixtures
        fixtures.TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)
        m = self.m
        self.boundary_evidence = []
        for variant, accepted, count in (
            ("exact", True, 1), ("before-first-review", True, 1), ("late", False, 1),
            ("missing-entry", False, 1),
            ("missing-file", False, 1), ("changed-entry", False, 1),
            ("unavailable", False, 1), ("no-override", True, 0),
        ):
            with self.subTest(variant=variant):
                owner = fixtures.FailClosedDataTests()
                self.addCleanup(owner.doCleanups)
                root, fixture, raw, _, shas = owner.make_override_case(
                    first_tree="missing-entry" if variant == "late" else
                    variant if variant in {"missing-entry", "missing-file", "changed-entry"} else "exact",
                    override_count=count, marker_path="docs/override.md", marker_lines=3000)
                head, base = fixture["pull_requests"][0]["head_sha"], shas["0"]
                first = min((item for item in fixture["reviews"] if item["author"] == reporter.REVIEW_BOT),
                            key=lambda item: item["submitted_at"])
                if variant == "late":
                    first = {**first, "submitted_at": "2026-01-01T02:00:00Z"}
                files = git_scope_files(root, base, head)
                pr = {**m._pr(head=head, base=base), "number": 1, "changed_files": len(files),
                      "additions": sum(item["additions"] for item in files),
                      "deletions": sum(item["deletions"] for item in files)}
                pr["url"] = f"https://api.github.com/repos/{m.REPOSITORY}/pulls/1"
                payload = {"number": 1, "action": "synchronize", "pull_request": pr}
                event = event_classifier.classify_event(
                    "pull_request", payload, github_ref="refs/pull/1/merge", github_sha="f" * 40,
                    pr_base_sha=base, pr_head_sha=head, push_sha="")
                client = m.ScriptedClient()
                client.add("GET", m._endpoint("pulls/1"), pr, pr, pr, pr)
                repository = {"id": m.REPOSITORY_ID, "full_name": m.REPOSITORY, "default_branch": "master"}
                client.add("GET", m._endpoint("").rstrip("/"), repository, repository)
                for revision in {head, first["commit_sha"]}:
                    endpoint = m._query("contents/" + str(reporter.DECISION_RECORD_PATH), [("ref", revision)])
                    try:
                        content = reporter.run_git(root, "show", revision + ":" + str(reporter.DECISION_RECORD_PATH))
                    except reporter.PilotDataError:
                        client.add("GET", endpoint, github.MetadataEditError("immutable record missing"))
                    else:
                        client.add("GET", endpoint, {
                            "path": str(reporter.DECISION_RECORD_PATH), "type": "file", "encoding": "base64",
                            "sha": hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest(),
                            "content": base64.b64encode(content).decode(),
                        })
                    committed = fixtures.git_run(
                        root, "show", "-s", "--format=%cI", revision).stdout.decode().strip().replace("+00:00", "Z")
                    client.add("GET", m._endpoint(f"git/commits/{revision}"),
                               {"sha": revision, "committer": {"date": committed}})
                for anchor in {base, first["commit_sha"]}:
                    merge_base = fixtures.git_run(root, "merge-base", "--all", anchor, head).stdout.decode().strip()
                    endpoint = m._endpoint(f"compare/{anchor}...{head}")
                    commits = git(root, "rev-list", "--reverse", f"{anchor}..{head}").splitlines()
                    comparison = {
                        "url": github._api_url(endpoint), "base_commit": {"sha": anchor},
                        "merge_base_commit": {"sha": merge_base}, "total_commits": len(commits),
                        "commits": [{"sha": sha} for sha in commits], "files": git_scope_files(root, anchor, head)}
                    client.add("GET", endpoint, comparison, comparison)
                reviews = {"data": {"repository": {"nameWithOwner": m.REPOSITORY, "pullRequest": {
                    "number": 1, "baseRefOid": base, "headRefOid": head,
                    "reviews": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{
                        "id": str(first["id"]), "state": first["state"], "submittedAt": first["submitted_at"],
                        "body": "", "commit": {"oid": first["commit_sha"]},
                        "author": {"__typename": "Bot", "id": "BOT_kgDOCnlnWA",
                                   "login": "copilot-pull-request-reviewer"},
                        "comments": {"pageInfo": {"hasNextPage": False}, "nodes": []},
                    }]},
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []},
                }}}}
                if variant == "before-first-review":
                    reviews["data"]["repository"]["pullRequest"]["reviews"]["nodes"] = []
                client.add("POST", "graphql", github.MetadataEditError("review authority unavailable")
                           if variant == "unavailable" else reviews)
                result, selected, _ = gate.route_event(client, event, payload, m.REPOSITORY)
                self.boundary_evidence.append({"variant": variant, "decision": asdict(selected),
                                               "classification": result.classification})
                self.assertEqual(selected.known, accepted, selected.reason)
                self.assertEqual(result.classification, "review-first" if count == 0 else "full")
                if variant == "exact":
                    self.assertEqual(selected.reason, "validated-pre-review-override")

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
                client.add("GET", m._endpoint(f"pulls/{m.PR_NUMBER}"), current, current)
                repository = {"id": m.REPOSITORY_ID, "full_name": m.REPOSITORY, "default_branch": "master"}
                client.add("GET", m._endpoint("").rstrip("/"), repository, repository)
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
                    self.assertEqual(binding, gate.binding_name(m.PR_NUMBER, head, original_base, "master"))
                else:
                    with self.assertRaises(ValueError):
                        route()

    def test_input_free_dispatch_resolves_actual_pr_branch_and_stays_full(self):
        m = self.m
        client, payload, decision = self.route_client(decisions(m.PR_NUMBER, ("security",)))
        payload["pull_request"]["head"]["repo"] = copy.deepcopy(payload["pull_request"]["base"]["repo"])
        endpoint = m._query("pulls", [("state", "open"), ("head", "owner:" + m.HEAD_REF),
                                      ("per_page", "100")])
        client.add("GET", endpoint, [payload["pull_request"]])
        decision = replace(decision, reason="explicit-final-dispatch")
        result, selected, binding = gate.route_dispatch(
            client, decision, {"inputs": {}}, m.REPOSITORY, "refs/heads/" + m.HEAD_REF,
            expected_candidate=(m.PR_NUMBER, m.BASE, "master"))
        self.assertEqual(result.classification, "full")
        self.assertTrue(result.run_expensive)
        self.assertEqual(selected.mode, "review-first")
        self.assertEqual(binding, gate.binding_name(m.PR_NUMBER, m.HEAD, m.BASE, "master"))
        for bad in ({"inputs": {"pass": True}}, {"inputs": "success"}):
            with self.assertRaises(ValueError):
                gate.route_dispatch(client, decision, bad, m.REPOSITORY, "refs/heads/" + m.HEAD_REF,
                                    expected_candidate=(m.PR_NUMBER, m.BASE, "master"))

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
                            "name": gate.binding_name(m.PR_NUMBER, m.HEAD, "c" * 40, "master"),
                            "status": "completed", "conclusion": "success"}]
                client = m.ScriptedClient()
                m._add_snapshot(client, [(raw, jobs)], merge_base="c" * 40)
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
            (gate.binding_name(number, head, frozen), False),
            (gate.binding_name(number, head, frozen, "master"), True),
            (gate.binding_name(number, head, frozen, "different/base"), False),
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


class DispatchBootstrapTests(unittest.TestCase):
    def setUp(self):
        from tests.workflows import test_build_ci_topology as topology
        from scripts.workflow_pilot.tests import test_pr_metadata as metadata
        self.t, self.m = topology, metadata
        artifacts = ROOT / "build" / "test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        owned = tempfile.TemporaryDirectory(prefix="dispatch-bootstrap-", dir=artifacts)
        self.addCleanup(owned.cleanup)
        self.owned = Path(owned.name)
        self.root = self.owned / "checkout"
        sources = self.root / "scripts/workflow_pilot"
        sources.mkdir(parents=True)
        for name in ("__init__.py", "event_classifier.py", "isolated_launcher.py"):
            shutil.copyfile(ROOT / "scripts/workflow_pilot" / name, sources / name)
        baseline = reporter.load_json(ROOT / reporter.BASELINE_FIXTURE_PATH)
        baseline_path = self.root / reporter.BASELINE_FIXTURE_PATH
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / reporter.BASELINE_FIXTURE_PATH, baseline_path)
        control_path = self.root / reporter.DECISION_RECORD_PATH
        control_path.parent.mkdir(parents=True)
        write_json(control_path, reporter.project_cohort_decisions(
            reporter.load_json(ROOT / reporter.DECISION_RECORD_PATH),
            (record["number"] for record in baseline["pull_requests"])))
        git(self.root, "init", "-b", "master")
        git(self.root, "config", "user.email", "bootstrap@example.invalid")
        git(self.root, "config", "user.name", "Bootstrap regression")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Pre-adaptive default classifier")
        self.default = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-b", "integration")
        for source in (ROOT / "scripts/workflow_pilot").glob("*.py"):
            shutil.copyfile(source, sources / source.name)
        self.parent_number = metadata.PR_NUMBER - 1
        self.decision_path = self.root / reporter.DECISION_RECORD_PATH
        self.decision_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.decision_path, decisions(self.parent_number))
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Feature-containing integration base")
        self.base = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-b", metadata.HEAD_REF)
        (sources / "isolated_launcher.py").write_text("raise AssertionError('candidate executed')\n")
        child = decisions(metadata.PR_NUMBER, ("lifecycle",), "review-first")
        child["pull_requests"][0]["stack"].update(depth=1, parent_pr=self.parent_number)
        write_json(self.decision_path, child)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Candidate must not supply classifier programs")
        self.head = git(self.root, "rev-parse", "HEAD")
        self.pr = metadata._pr(head=self.head, base=self.base)
        self.pr["base"]["ref"] = "integration"
        self.pr["head"]["repo"] = copy.deepcopy(self.pr["base"]["repo"])
        self.pr.update(additions=1, deletions=1)
        self.parent_pr = metadata._pr(head=self.base, base=self.default)
        self.parent_pr.update(number=self.parent_number, id=9000, node_id="PR_parent",
                              url=github._api_url(metadata._endpoint(f"pulls/{self.parent_number}")))
        self.parent_pr["head"]["ref"] = "integration"
        self.query = {"data": {"repository": {
            "nameWithOwner": metadata.REPOSITORY, "pullRequests": {
                "totalCount": 1, "pageInfo": {"hasNextPage": False}, "nodes": [{
                    "number": metadata.PR_NUMBER, "state": "OPEN",
                    "headRefName": metadata.HEAD_REF, "headRefOid": self.head,
                    "baseRefName": "integration", "baseRefOid": self.base,
                    "headRepository": {"nameWithOwner": metadata.REPOSITORY},
                    "baseRepository": {"nameWithOwner": metadata.REPOSITORY},
                }]}}}}
        self.gh = self.owned / "gh"
        self.gh.write_text("#!/bin/sh\n"
                           "test \"${BOOTSTRAP_UNAVAILABLE:-0}\" = 0 || exit 1\n"
                           "exec /usr/bin/cat \"$BOOTSTRAP_RESPONSE\"\n")
        self.gh.chmod(0o755)
        self.launch = self.owned / "launch.py"
        self.launch.write_text(
            "import json, os, runpy, subprocess, sys\n"
            "from pathlib import Path\n"
            "native = subprocess.run\n"
            "def transport(argv, **kwargs):\n"
            "    if argv[0] != '/usr/bin/gh':\n"
            "        return native(argv, **kwargs)\n"
            "    assert argv[1:5] == ['api', '--hostname', 'github.com', '--include']\n"
            "    assert argv[argv.index('--method') + 1] == 'GET'\n"
            "    payload = json.loads(Path(os.environ['API_RESPONSES']).read_text())[argv[-1]]\n"
            "    raw = 'HTTP/2.0 ' + os.environ.get('API_STATUS', '200')\n"
            "    raw += ' Response\\r\\nContent-Type: application/json\\r\\n\\r\\n'\n"
            "    return subprocess.CompletedProcess(argv, 0, (raw + json.dumps(payload)).encode(), b'')\n"
            "subprocess.run = transport\n"
            "sys.argv[0] = 'scripts/workflow_pilot/isolated_launcher.py'\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n")
        self.jobs = topology._job_blocks((ROOT / ".github/workflows/build.yml").read_text())
        self.environment = {
            **os.environ, "DEFAULT_BRANCH": "master", "EVENT_NAME": "workflow_dispatch",
            "EVENT_REF": "refs/heads/" + metadata.HEAD_REF, "RAW_SHA": self.head,
            "RAW_SHA_JSON": json.dumps(self.head), "GITHUB_REPOSITORY": metadata.REPOSITORY,
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/" + metadata.HEAD_REF,
            "GITHUB_SHA": self.head, "PR_BASE_SHA": "", "PR_HEAD_SHA": "", "PUSH_SHA": "",
            "GITHUB_EVENT_PATH": str(self.owned / "event.json"),
            "BOOTSTRAP_RESPONSE": str(self.owned / "bootstrap.json"),
            "API_RESPONSES": str(self.owned / "api.json"),
        }
        write_json(self.owned / "event.json", {"inputs": {}})

    def identity(self, **environment):
        (self.owned / "bootstrap.json").write_text(json.dumps(self.query))
        self.environment.update(environment)
        return self.script("event-identity", 0)[1]

    def script(self, job, index, **environment):
        script = self.t._literal_run_script(self.t._step_blocks(self.jobs[job])[index])
        script = script.replace("/usr/bin/gh", str(self.gh))
        script = script.replace("scripts/workflow_pilot/isolated_launcher.py", str(self.launch))
        output = self.owned / "outputs"
        output.unlink(missing_ok=True)
        result = subprocess.run(
            ["/bin/bash", "-e", "-o", "pipefail", "-c", script], cwd=self.root,
            env={**self.environment, "GITHUB_OUTPUT": str(output), **environment},
            capture_output=True, text=True, timeout=30)
        values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
        return result, values

    def classify(self, identity, responses=None):
        from scripts.workflow_pilot.tests.test_live_pause import control_routes
        m = self.m
        git(self.root, "checkout", "--detach", identity["classifier_ref"])
        self.environment.update(
            CLASSIFIER_REF=identity["classifier_ref"],
            CLASSIFIER_EXPECTED_SHA=identity["classifier_expected_sha"],
            DISPATCH_PR_NUMBER=identity.get("dispatch_pr_number", ""),
            DISPATCH_BASE_REF=identity.get("dispatch_base_ref", ""),
            VALIDATED_FALLBACK_KIND=identity["fallback_kind"],
            VALIDATED_FALLBACK_SHA=identity["fallback_sha"])
        verified, _ = self.script("event-router", 2)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        routes = {
            m._endpoint("").rstrip("/"): {
                "id": m.REPOSITORY_ID, "full_name": m.REPOSITORY, "default_branch": "master"},
            m._query("pulls", [("state", "open"), ("head", "owner:" + m.HEAD_REF),
                               ("per_page", "100")]): [self.pr],
            m._endpoint(f"pulls/{m.PR_NUMBER}"): self.pr,
            m._endpoint(f"pulls/{self.parent_number}"): self.parent_pr,
            m._endpoint(f"compare/{self.pr['base']['sha']}...{self.head}"): {
                "base_commit": {"sha": self.pr["base"]["sha"]},
                "merge_base_commit": {"sha": git(self.root, "merge-base", self.pr["base"]["sha"], self.head)}},
        }
        routes.update(control_routes(
            self.root, m.REPOSITORY, m.REPOSITORY_ID, (git(self.root, "rev-parse", "master"),)))
        for revision in (self.head, self.base):
            content = reporter.run_git(self.root, "show", revision + ":" + str(reporter.DECISION_RECORD_PATH))
            routes[m._query("contents/" + str(reporter.DECISION_RECORD_PATH), [("ref", revision)])] = {
                "path": str(reporter.DECISION_RECORD_PATH), "type": "file", "encoding": "base64",
                "sha": git(self.root, "rev-parse", revision + ":" + str(reporter.DECISION_RECORD_PATH)),
                "content": base64.b64encode(content).decode()}
        routes.update(responses or {})
        write_json(self.owned / "api.json", routes)
        return self.script("event-router", 3)

    def test_pre_feature_default_executes_integration_base_and_emits_parsed_binding(self):
        identity = self.identity()
        result, values = self.classify(identity)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = github._candidate_step({"steps": [{
            "name": values.get("candidate_binding", ""), "status": "completed", "conclusion": "success"}]})
        self.assertEqual(parsed, (self.m.PR_NUMBER, self.head, self.base), (identity, values))
        self.assertEqual(identity["classifier_ref"], self.base)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)
        self.assertEqual((values["classification"], values["gate_mode"], values["run_expensive"]),
                         ("full", "review-first", "true"))
        m = self.m
        raw, jobs = m._run(10, 10, mode="full")
        raw.update(event="workflow_dispatch", head_sha=self.head, pull_requests=[])
        jobs = [job for job in jobs if job["name"] != "patch-release"]
        for job in jobs:
            job.update(event="workflow_dispatch", head_sha=self.head)
            if job["name"] == "event-classifier":
                job["steps"] = [{"name": values["candidate_binding"],
                                 "status": "completed", "conclusion": "success"}]
        client = m.ScriptedClient()
        m._add_snapshot(client, [(raw, jobs)])
        client.add("GET", m._endpoint(f"compare/{self.base}...{self.head}"),
                   {"base_commit": {"sha": self.base}, "merge_base_commit": {"sha": self.base}})
        pr = github._parse_pull_request_payload(self.pr, m.REPOSITORY, m.PR_NUMBER)
        _, _, observed = github._parse_run(client, pr, github._workflow_authority(client, pr), raw)
        self.assertEqual((observed.binding, observed.candidate_binding), ("explicit-same", parsed))
        github.require_full_success(observed)

    def test_dispatch_discovery_counts_repository_qualified_candidates(self):
        original = copy.deepcopy(self.query)
        own = copy.deepcopy(original["data"]["repository"]["pullRequests"]["nodes"][0])
        foreign = copy.deepcopy(own)
        foreign["number"] += 1
        foreign["headRepository"]["nameWithOwner"] = "foreign/repository"
        other = copy.deepcopy(own)
        other.update(number=own["number"] + 2, baseRefName="master", baseRefOid=self.default)
        malformed = copy.deepcopy(own)
        malformed["baseRefName"] = True
        for label, nodes, count, next_page, accepted in (
            ("own-only", [own], 1, False, True),
            ("foreign-first", [foreign, own], 2, False, True),
            ("foreign-last", [own, foreign], 2, False, True),
            ("foreign-only", [foreign], 1, False, False),
            ("two-eligible", [own, other], 2, False, False),
            ("two-eligible-reversed", [other, own], 2, False, False),
            ("malformed-eligible", [own, malformed], 2, False, False),
            ("unknown-node", [own, None], 2, False, False),
            ("count-mismatch", [own, foreign], 3, False, False),
            ("incomplete-page", [own, foreign], 2, True, False),
            ("over-response-bound", [own] + [foreign] * 100, 101, False, False),
        ):
            with self.subTest(case=label):
                self.query = copy.deepcopy(original)
                self.query["data"]["repository"]["pullRequests"].update(
                    totalCount=count, nodes=nodes, pageInfo={"hasNextPage": next_page})
                identity = self.identity()
                result, values = self.classify(identity)
                self.assertEqual(result.returncode, 0, result.stderr)
                if accepted:
                    self.assertEqual(identity["classifier_ref"], self.base)
                    self.assertEqual(identity["dispatch_pr_number"], str(self.m.PR_NUMBER))
                    self.assertEqual(
                        github._candidate_step({"steps": [{
                            "name": values["candidate_binding"], "status": "completed",
                            "conclusion": "success",
                        }]}),
                        (self.m.PR_NUMBER, self.head, self.base),
                    )
                else:
                    self.assertEqual(identity["classifier_ref"], "refs/heads/master")
                    self.assertEqual(identity["dispatch_pr_number"], "")
                    self.assertNotIn("candidate_binding", values)

    def test_missing_ambiguous_or_invalid_bootstrap_metadata_remains_unbound(self):
        original = copy.deepcopy(self.query)
        for field, value in (
            ("nodes", []), ("nodes", [None]), ("totalCount", 2), ("hasNextPage", True),
            ("headRefOid", self.default), ("headRefName", "other"), ("state", "CLOSED"),
            ("headRepository", {"nameWithOwner": "foreign/repo"}),
            ("baseRepository", {"nameWithOwner": "foreign/repo"}),
            ("baseRefOid", "HEAD"), ("baseRefName", "bad\nref"), ("baseRefName", "integration\n"),
            ("baseRefName", "x" * 1025), ("number", True),
            ("repository", None), ("errors", [{"message": "unavailable"}]),
        ):
            with self.subTest(field=field):
                self.query = copy.deepcopy(original)
                repository = self.query["data"]["repository"]
                prs = repository["pullRequests"]
                target = (prs if field in {"nodes", "totalCount"} else prs["pageInfo"]
                          if field == "hasNextPage" else self.query["data"]
                          if field == "repository" else self.query if field == "errors"
                          else prs["nodes"][0])
                target[field] = value
                identity = self.identity()
                result, values = self.classify(identity)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(identity["classifier_ref"], "refs/heads/master")
                self.assertNotIn("candidate_binding", values)

    def test_unavailable_oversized_and_foreign_metadata_cannot_select_authority(self):
        for change in ("unavailable", "oversized", "foreign"):
            with self.subTest(change=change):
                if change == "oversized":
                    self.query["padding"] = "x" * github.MAX_API_BYTES
                elif change == "foreign":
                    self.query.pop("padding")
                    self.query["data"]["repository"]["nameWithOwner"] = "foreign/repo"
                identity = self.identity(BOOTSTRAP_UNAVAILABLE="1" if change == "unavailable" else "0")
                self.assertEqual(identity["classifier_ref"], "refs/heads/master")
                self.assertEqual(identity["dispatch_pr_number"], "")

    def test_deployed_root_base_and_exact_checkout_verification(self):
        git(self.root, "update-ref", "refs/heads/master", self.base)
        root_decision = decisions(self.m.PR_NUMBER, ("lifecycle",), "review-first")
        write_json(self.decision_path, root_decision)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Actual root decision after base retarget")
        self.head = git(self.root, "rev-parse", "HEAD")
        self.pr["head"]["sha"] = self.head
        self.environment.update(RAW_SHA=self.head, RAW_SHA_JSON=json.dumps(self.head), GITHUB_SHA=self.head)
        self.query["data"]["repository"]["pullRequests"]["nodes"][0]["headRefOid"] = self.head
        self.query["data"]["repository"]["pullRequests"]["nodes"][0]["baseRefName"] = "master"
        self.pr["base"]["ref"] = "master"
        identity = self.identity(DEFAULT_BRANCH="")
        result, values = self.classify(identity)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["candidate_binding"], gate.binding_name(self.m.PR_NUMBER, self.head, self.base, "master"))
        wrong, _ = self.script("event-router", 2, CLASSIFIER_EXPECTED_SHA=self.head)
        self.assertNotEqual(wrong.returncode, 0)

    def test_changed_candidate_base_or_ref_after_bootstrap_cannot_bind(self):
        original = copy.deepcopy(self.pr)
        for side, field, value in (
            ("base", "sha", self.default), ("base", "ref", "retargeted"),
            ("head", "sha", self.base), ("head", "ref", "moved"),
            ("head", "repo", {**self.pr["head"]["repo"], "full_name": "foreign/repo"}),
        ):
            with self.subTest(side=side, field=field):
                self.pr = copy.deepcopy(original)
                identity = self.identity()
                self.pr[side][field] = value
                result, values = self.classify(identity)
                self.assertNotEqual(result.returncode, 0, (identity, values))
                self.assertNotIn("candidate_binding", values)

    def test_refresh_ambiguity_or_http_failure_cannot_emit_binding(self):
        endpoint = self.m._query("pulls", [("state", "open"), ("head", "owner:" + self.m.HEAD_REF),
                                          ("per_page", "100")])
        for rows, status in (([], "200"), ([self.pr, self.pr], "200"), ([self.pr], "503")):
            with self.subTest(count=len(rows), status=status):
                self.environment["API_STATUS"] = status
                result, values = self.classify(self.identity(), {endpoint: rows})
                self.assertNotEqual(result.returncode, 0, (result.stderr, values))
                self.assertNotIn("candidate_binding", values)

    def test_old_base_and_plain_deployed_manual_dispatch_keep_unbound_full_route(self):
        for deployed in (False, True):
            with self.subTest(deployed=deployed):
                if deployed:
                    git(self.root, "update-ref", "refs/heads/master", self.base)
                    self.query["data"]["repository"]["pullRequests"]["nodes"] = []
                    self.query["data"]["repository"]["pullRequests"]["totalCount"] = 0
                else:
                    node = self.query["data"]["repository"]["pullRequests"]["nodes"][0]
                    node.update(baseRefOid=self.default, baseRefName="master")
                    self.pr["base"].update(sha=self.default, ref="master")
                result, values = self.classify(self.identity())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((values["classification"], values["run_expensive"]), ("full", "true"))
                self.assertNotIn("candidate_binding", values)
