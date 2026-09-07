"""Historical ref identity through real Git, HTTP parsers and the inline summary."""

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.workflow_pilot import adaptive_gate as gate, pr_metadata as metadata, reporter
from scripts.workflow_pilot.tests import test_pr_metadata as fixtures
from scripts.workflow_pilot.tests.test_agent_handoff import git
from tests.workflows import test_build_ci_topology as topology


ROOT = Path(__file__).resolve().parents[3]


class ReflessRunTests(unittest.TestCase):
    def setUp(self):
        artifacts = ROOT / "build/test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        owned = tempfile.TemporaryDirectory(prefix="historical-ref-", dir=artifacts)
        self.addCleanup(owned.cleanup)
        self.root = Path(owned.name)
        git(self.root, "init", "-b", "master")
        git(self.root, "config", "user.name", "Historical ref regression")
        git(self.root, "config", "user.email", "ref@example.invalid")
        (self.root / "README.md").write_text("Base\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Base")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.ref = "retargeted/base"
        git(self.root, "branch", self.ref, self.base)
        git(self.root, "checkout", "-b", fixtures.HEAD_REF)
        (self.root / "README.md").write_text("Current candidate\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Candidate")
        self.head = git(self.root, "rev-parse", "HEAD")
        raw = fixtures._pr(head=self.head, base=self.base)
        raw["base"]["ref"] = self.ref
        self.pr = metadata._parse_pull_request_payload(raw, fixtures.REPOSITORY, fixtures.PR_NUMBER)
        self.raw_pr = raw
        self.marker = gate.binding_name(fixtures.PR_NUMBER, self.head, self.base, self.ref)
        self.legacy = gate.binding_name(fixtures.PR_NUMBER, self.head, self.base)
        self.calls = []

    def runs(self, markers, *, event="pull_request", mode="full"):
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        rows = []
        for index, marker in enumerate(markers, 1):
            number = 10 * index
            transition = fixtures._metadata_event_payload(
                {**self.raw_pr, "body": "before"}, {**self.raw_pr, "body": "after", "updated_at": now})
            raw, jobs = fixtures._run(number, number, mode=mode, metadata_event_payload=transition)
            raw.update(head_sha=self.head, event=event, created_at=now, updated_at=now, run_started_at=now,
                       pull_requests=[{"number": self.pr.number, "head": {"sha": self.head},
                                       "base": {"sha": self.base, "ref": self.ref}}]
                       if event == "pull_request" else [])
            for job in jobs:
                job.update(head_sha=self.head, event=event, created_at=now, started_at=now, completed_at=now)
                if job["name"] == "event-classifier":
                    job["steps"] = [] if marker is None else [{
                        "name": marker, "status": "completed", "conclusion": "success"}]
                for step in job.get("steps", ()):
                    step.update(started_at=now, completed_at=now)
            rows.append((raw, jobs))
        routes = {
            fixtures._endpoint(f"pulls/{self.pr.number}"): self.raw_pr,
            fixtures._endpoint("actions/workflows/build.yml"): fixtures._workflow(),
            fixtures._query("actions/workflows/build.yml/runs",
                            [("head_sha", self.head), ("per_page", "100"), ("page", "1")]):
                {"total_count": len(rows), "workflow_runs": [raw for raw, _ in reversed(rows)]},
            fixtures._endpoint(f"compare/{self.base}...{self.head}"): {
                "base_commit": {"sha": self.base},
                "merge_base_commit": {"sha": git(self.root, "merge-base", "--all", self.base, self.head)}},
        }
        for raw, jobs in rows:
            routes[fixtures._endpoint(f"actions/runs/{raw['id']}")] = raw
            routes[fixtures._query(f"actions/runs/{raw['id']}/attempts/1/jobs",
                                    [("per_page", "100"), ("page", "1")])] = {"total_count": len(jobs), "jobs": jobs}

        def transport(argv, **kwargs):
            method = argv[argv.index("--method") + 1]
            endpoint = argv[argv.index("X-GitHub-Api-Version: 2022-11-28") + 1]
            self.assertEqual(method, "GET", "no metadata mutation is authorized by these controls")
            self.calls.append(endpoint)
            wire = "HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps(routes[endpoint])
            return subprocess.CompletedProcess(argv, 0, wire.encode(), b"")

        client = metadata.GitHubClient("/usr/bin/gh", runner=transport)
        return client, metadata.list_candidate_runs(client, self.pr)

    def test_ref_less_full_runs_never_authorize_same_sha_metadata_after_retarget(self):
        self.observed = []
        for event in ("pull_request", "workflow_dispatch"):
            for marker in (None, self.legacy):
                with self.subTest(event=event, marker=marker):
                    client, runs = self.runs([marker], event=event)
                    run = runs[0]
                    self.observed.append({"event": event, "marker": marker, "binding": run.binding})
                    self.assertEqual(run.binding, "unbound")
                    self.assertFalse(metadata._current_full_authorization(runs)[1])
                    self.assertIsNone(metadata._latest_full(runs))
                    decision = metadata.edit_metadata(
                        client, repository=self.pr.repository, pr_number=self.pr.number,
                        head_sha=self.head, base_sha=self.base, title=None, body="new body", essential_reason=None)
                    self.assertFalse(decision.mutated)

    def test_canonical_ref_is_exact_and_metadata_only_never_replaces_full(self):
        _, runs = self.runs([self.marker])
        self.assertEqual(runs[0].binding, "explicit-same")
        self.assertTrue(metadata._current_full_authorization(runs)[1])
        other = gate.binding_name(self.pr.number, self.head, self.base, "master")
        _, runs = self.runs([other])
        self.assertEqual(runs[0].binding, "explicit-other")
        self.assertIsNone(metadata._latest_full(runs))
        with self.assertRaises(metadata.MetadataEditError):
            self.runs([self.legacy + ":%72etargeted%2Fbase"])
        _, runs = self.runs([None], mode="metadata-only")
        metadata.require_metadata_success(runs[0])
        with self.assertRaises(metadata.MetadataEditError):
            metadata._current_full_authorization(runs)

    def test_newer_unproven_full_cannot_be_skipped_for_an_older_bound_success(self):
        _, runs = self.runs([self.marker, self.legacy])
        self.assertFalse(metadata._current_full_authorization(runs)[1])
        self.assertIsNone(metadata._latest_full(runs))

    def test_actual_inline_summary_requires_historical_ref_for_pr_and_dispatch_full_runs(self):
        t = topology
        script = t._literal_run_script(t._step_blocks(t._job_blocks(t.WORKFLOW.read_text())["summary"])[0])
        number = t.SUMMARY_TEST_PR_NUMBER
        self.summary_observations = []
        for event in ("pull_request", "workflow_dispatch"):
            for ref, accepted in ((self.ref, True), (None, False), ("master", False),
                                  ("missing-marker", False), ("noncanonical", False), ("invalid-utf8", False),
                                  ("contradictory-pr", False), ("newer-refless", False)):
                with self.subTest(event=event, ref=ref):
                    marker = (None if ref == "missing-marker" else
                              gate.binding_name(number, self.head, self.base, ref))
                    if ref in {"noncanonical", "invalid-utf8"}:
                        marker = gate.binding_name(number, self.head, self.base) + (
                            ":%72etargeted%2Fbase" if ref == "noncanonical" else ":%FF")
                    if ref == "contradictory-pr":
                        marker = gate.binding_name(number, self.head, self.base, self.ref)
                    if ref == "newer-refless":
                        marker = gate.binding_name(number, self.head, self.base)
                    prior = t._summary_workflow_run(
                        10, head_sha=self.head, base_sha=self.base, event=event,
                        path=".github/workflows/build.yml@refs/heads/" + fixtures.HEAD_REF
                        if event == "workflow_dispatch" else None,
                        pull_requests=[] if event == "workflow_dispatch" else None)
                    if ref == "contradictory-pr":
                        prior["pull_requests"] = [{"number": number + 1, "head": {"sha": self.head},
                                                  "base": {"sha": self.base}}]
                    current = t._summary_workflow_run(t.SUMMARY_TEST_RUN_ID, head_sha=self.head, base_sha=self.base)
                    jobs = [job for job in t._summary_full_jobs() if job["name"] != "patch-release"]
                    classifier = next(job for job in jobs if job["name"] == "event-classifier")
                    classifier["steps"] = [] if marker is None else [{
                        "name": marker, "status": "completed", "conclusion": "success"}]
                    routes = {
                        t._summary_runs_path(head_sha=self.head): t._summary_response(
                            t._summary_api_payload("workflow_runs", [current, prior])),
                        t._summary_jobs_path(10): t._summary_response(t._summary_api_payload("jobs", jobs)),
                        f"/repos/{t.SUMMARY_TEST_REPOSITORY}/compare/{self.base}...{self.head}":
                            t._summary_response({"base_commit": {"sha": self.base},
                                                 "merge_base_commit": {"sha": self.base}}),
                    }
                    if ref == "newer-refless":
                        older = t._summary_workflow_run(9, head_sha=self.head, base_sha=self.base)
                        old_jobs = copy.deepcopy(jobs)
                        next(job for job in old_jobs if job["name"] == "event-classifier")["steps"][0]["name"] = (
                            gate.binding_name(number, self.head, self.base, self.ref))
                        routes[t._summary_runs_path(head_sha=self.head)] = t._summary_response(
                            t._summary_api_payload("workflow_runs", [current, prior, older]))
                        routes[t._summary_jobs_path(9)] = t._summary_response(t._summary_api_payload("jobs", old_jobs))
                    result, _ = t._run_summary_with_api(script, environment=t._summary_metadata_env(
                        PR_HEAD_SHA=self.head, CLASSIFIED_BUILD_SHA=self.head, PR_BASE_SHA=self.base,
                        CLASSIFIED_BASE_SHA=self.base, PR_BASE_REF=self.ref, FALLBACK_SHA=self.head), routes=routes)
                    self.summary_observations.append({
                        "event": event, "ref": ref, "marker": marker, "exit_code": result.returncode,
                        "head": self.head, "base": self.base, "detail": result.stderr.strip()[-1000:]})
                    self.assertEqual(result.returncode == 0, accepted, result.stderr)


class BootstrapParentDecisionTests(unittest.TestCase):
    def test_current_parent_decision_is_an_excluded_root_not_historical_override(self):
        raw = reporter.load_json(ROOT / reporter.DECISION_RECORD_PATH)
        record = reporter.historical_decision_record(raw, "working root decision", 221)
        self.assertEqual(set(record["risk_boundaries"]), {"protocol", "lifecycle"})
        self.assertEqual(set(record["threshold"]["triggers"]), {"changed-files", "changed-lines", "risk-boundary"})
        self.assertEqual(record["threshold"]["override_history"], [])
        self.assertEqual(record["gate_mode"], "review-first")
        self.assertEqual(record["stack"], {"depth": 0, "parent_pr": None, "exception_reason": None})
        self.assertEqual(record["pilot"], {"included": False, "disposition": "excluded"})
        reporter.validate_stack_decisions({221: record}, {
            "fixture": {"default_branch": "master"},
            "pull_requests": {221: {"base_ref": "master", "head_branch": "delivery/d581-issue-181"}},
        })
        projected = reporter.project_cohort_decisions(raw, {150})
        self.assertEqual([row["pull_request"] for row in projected["pull_requests"]], [150])
