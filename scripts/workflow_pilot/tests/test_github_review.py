"""Exact GitHub facts, coordinator triage and real launcher/test integration."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import copy
from dataclasses import replace
import io
import json
import subprocess
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

from scripts.workflow_pilot import trusted_review_gate as gate
from scripts.workflow_pilot.tests.review_support import ENV, Runtime, request, snapshot


def response(base, head, body="Complete review content", *, actor=None):
    return {"data": {"repository": {"nameWithOwner": "owner/repo", "pullRequest": {
        "number": 1, "baseRefOid": base, "headRefOid": head,
        "reviews": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{
            "id": "review-1", "state": "COMMENTED", "submittedAt": "2026-01-01T00:00:10Z",
            "body": body, "commit": {"oid": head},
            "author": actor or {"__typename": "Bot", "id": gate.COPILOT[1],
                               "login": gate.COPILOT[2]},
            "comments": {"pageInfo": {"hasNextPage": False},
                         "nodes": [{"id": "comment-1", "path": "src/expansion_aoe.c",
                                    "body": "An inline finding"}]},
        }]},
        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []},
    }}}}


class ObservedGitHub(gate.GitHub):
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def query(self, *arguments):
        self.calls += 1
        return copy.deepcopy(self.payload)


class GitHubReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = snapshot()
        cls.repo = cls.workspace.__enter__()
        cls.tools = gate.ReviewTools(gate.GitTree(cls.repo.root, cls.repo.base), cls.repo.root)
        cls.model = cls.tools.model

    @classmethod
    def tearDownClass(cls):
        cls.workspace.__exit__(None, None, None)

    def setup_review(self, body="Complete review including suppressed findings"):
        data = request(base=self.repo.base, head=self.repo.base)
        scope = frozenset({self.model.subject_key(data["subjects"][0])})
        session = self.model.ReviewSession(
            "coordinator", "implementer", scope, self.repo.base,
            identity=("owner/repo", 1, self.repo.base), owners=self.model.ReviewOwnership())
        runtime = Runtime(self.repo.base, scope)
        session.begin(runtime, "reviewer")
        session.finish(runtime)
        github = ObservedGitHub(response(self.repo.base, self.repo.base, body))
        _, facts = github.snapshot("owner/repo", 1, self.model)
        return data, session, runtime, github, facts

    def test_real_execution_requires_actual_task_and_complete_coordinator_triage(self):
        data, session, runtime, github, facts = self.setup_review()
        triage = self.model.Triage(facts[0], "clean")
        session.triage(triage)
        result = self.tools.assess(data, session, github, (triage,), pre_review_required=True)
        self.assertTrue(result["handoff_eligible"])
        self.assertTrue(result["exact_head_review_clean"])
        self.assertFalse(result["merge_permission"])
        self.assertIn("security", result["required_final_gates"])
        self.assertIn("master-Build", result["required_final_gates"])
        self.assertEqual([item[0] for item in runtime.calls], ["start", "read"])
        self.assertEqual(github.calls, 3)

    def test_commented_and_zero_inline_do_not_mean_clean(self):
        for body in ("No issues found.", "### 🟢 Approval recommended", "Needs a closer look"):
            data, session, _, github, facts = self.setup_review(body)
            triage = self.model.Triage(facts[0], "untriaged")
            session.triage(triage)
            result = self.tools.assess(data, session, github, (triage,), pre_review_required=True)
            self.assertFalse(result["exact_head_review_clean"])
            self.assertFalse(result["handoff_eligible"])

    def test_changed_content_wrong_identity_and_missing_task_reject(self):
        data, session, _, github, facts = self.setup_review()
        triage = self.model.Triage(facts[0], "clean")
        session.triage(triage)
        pr = github.payload["data"]["repository"]["pullRequest"]
        pr["reviews"]["nodes"][0]["body"] += " additional suppressed finding"
        with self.assertRaisesRegex(ValueError, "triage|content"):
            self.tools.assess(data, session, github, (triage,), pre_review_required=True)
        pr["reviews"]["nodes"][0]["body"] = facts[0].body
        session.report = None
        with self.assertRaisesRegex(ValueError, "task observation"):
            self.tools.assess(data, session, github, (triage,), pre_review_required=True)
        for key, value in (("repository", "other/repo"), ("pull_request", 2)):
            changed = {**data, key: value}
            with self.assertRaises(ValueError):
                self.tools.assess(changed, session, github, (triage,), pre_review_required=False)

    def test_read_only_api_command_and_exact_actor_before_content(self):
        github = gate.GitHub()
        payload = response(self.repo.base, self.repo.base)
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
        with patch.object(gate.subprocess, "run", return_value=completed) as invoke:
            github.snapshot("owner/repo", 1, self.model)
        args = invoke.call_args.args[0]
        self.assertEqual(args[:3], ["/usr/bin/gh", "api", "graphql"])
        self.assertEqual(args[args.index("--hostname") + 1], "github.com")
        query = next(value.partition("=")[2] for value in args if value.startswith("query="))
        self.assertIn("query(", query)
        self.assertNotIn("mutation", query)
        for author in (None, {"__typename": "User", "id": gate.COPILOT[1],
                              "login": gate.COPILOT[2]},
                       {"__typename": "Bot", "id": "wrong", "login": gate.COPILOT[2]}):
            altered = copy.deepcopy(payload)
            item = altered["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]
            item["author"] = author
            item["body"] = None
            _, facts = ObservedGitHub(altered).snapshot("owner/repo", 1, self.model)
            self.assertEqual(facts, ())

    def test_incomplete_duplicate_stale_and_api_errors_fail(self):
        base = response(self.repo.base, self.repo.base)
        bad = []
        altered = copy.deepcopy(base)
        altered["errors"] = [{"message": "incomplete response"}]
        bad.append(altered)
        for node in ("reviews", "reviewThreads"):
            altered = copy.deepcopy(base)
            target = altered["data"]["repository"]["pullRequest"][node]
            target["pageInfo"]["hasNextPage"] = True
            bad.append(altered)
        altered = copy.deepcopy(base)
        altered["data"]["repository"]["pullRequest"]["reviews"]["nodes"] *= 2
        bad.append(altered)
        for payload in bad:
            with self.assertRaises(ValueError):
                ObservedGitHub(payload).snapshot("owner/repo", 1, self.model)
        data, session, _, github, facts = self.setup_review()
        triage = self.model.Triage(facts[0], "clean")
        session.triage(triage)
        github.payload["data"]["repository"]["pullRequest"]["headRefOid"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "stale"):
            self.tools.assess(data, session, github, (triage,), pre_review_required=True)

    def test_same_source_bytes_and_no_candidate_success_import_cli(self):
        data = request(base=self.repo.base, head=self.repo.base)
        path = self.repo.root / "request.json"
        path.write_text(json.dumps(data))
        launcher = self.repo.root / "scripts/workflow_pilot/isolated_launcher.py"
        command = [
            sys.executable, "-I", "-B", str(launcher), "review-family",
            "--repository-root", str(self.repo.root), "--subject-root", str(self.repo.root),
            "--tool-revision", self.repo.base, "--candidate", self.repo.base,
            "--request", str(path), "--mode", "plan",
        ]
        files = [self.repo.root / ("scripts/workflow_pilot/" + name + ".py")
                 for name in ("trusted_review_gate", "review_family", "review_subjects",
                              "reporter", "__init__")]
        intended = [item.read_bytes() for item in files]
        try:
            for item in files:
                item.write_text("raise RuntimeError('wrong working copy executed')\n")
            result = subprocess.run(command, env=ENV, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            report = json.loads(result.stdout)
            self.assertEqual(report["tool_revision"], self.repo.base)
            self.assertEqual(len(report["obligations"]), 9)
            self.assertFalse(report["merge_permission"])
        finally:
            for item, source in zip(files, intended):
                item.write_bytes(source)
        data["trusted"] = True
        path.write_text(json.dumps(data))
        result = subprocess.run(command, env=ENV, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)

    def test_check_cli_runs_probes_but_cannot_authenticate_task_from_json(self):
        data = request(base=self.repo.base, head=self.repo.base)
        path = self.repo.root / "request.json"
        path.write_text(json.dumps(data))
        output = io.StringIO()
        with patch.object(gate, "GitHub", return_value=ObservedGitHub(
                response(self.repo.base, self.repo.base))), redirect_stdout(output):
            code = gate.main([
                "--repository-root", str(self.repo.root), "--subject-root", str(self.repo.root),
                "--tool-revision", self.repo.base, "--candidate", self.repo.base,
                "--request", str(path), "--mode", "check"])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["source_audit_complete"])
        self.assertEqual(report["untriaged_review_ids"], ["review-1"])
        self.assertFalse(report["handoff_eligible"])
        self.assertTrue(report["coordinator_observations_required"])


if __name__ == "__main__":
    unittest.main()
