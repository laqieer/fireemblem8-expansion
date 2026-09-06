"""Exact GitHub facts, coordinator triage and real launcher/test integration."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import copy
from dataclasses import replace
import io
import json
import os
import subprocess
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

from scripts.workflow_pilot import trusted_review_gate as gate
from scripts.workflow_pilot.tests.review_support import ENV, Runtime, git, request, snapshot


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

    def test_dismissed_review_is_retained_but_never_clean(self):
        data, session, _, github, _ = self.setup_review()
        github.payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["state"] = "DISMISSED"
        _, facts = github.snapshot("owner/repo", 1, self.model)
        self.assertEqual(facts[0].state, "DISMISSED")
        with self.assertRaisesRegex(ValueError, "clean triage"):
            self.model.Triage(facts[0], "clean").validate()
        triage = self.model.Triage(facts[0], "untriaged")
        session.triage(triage)
        result = self.tools.assess(data, session, github, (triage,), pre_review_required=True)
        self.assertFalse(result["exact_head_review_clean"])
        self.assertFalse(result["handoff_eligible"])

    def test_exact_merge_base_allows_only_unrelated_base_fast_forward(self):
        common = self.repo.commit({"lineage-common": "branch point"})
        head = self.repo.commit({"lineage-candidate": "candidate"}, parent=common)
        advanced = self.repo.commit({"lineage-live-base": "unrelated base advance"}, parent=common)
        for frozen, live, accepted in (
            (common, common, True), (common, advanced, True),
            (self.repo.base, advanced, False), (common, self.repo.base, False),
        ):
            with self.subTest(frozen=frozen, live=live):
                data = request(base=frozen, head=head)
                scope = frozenset({self.model.subject_key(data["subjects"][0])})
                session = self.model.ReviewSession(
                    "coordinator", "implementer", scope, head,
                    identity=("owner/repo", 1, frozen))
                github = ObservedGitHub(response(live, head))
                _, facts = github.snapshot("owner/repo", 1, self.model)
                triage = self.model.Triage(facts[0], "clean")
                session.triage(triage)
                if accepted:
                    result = self.tools.assess(
                        data, session, github, (triage,), pre_review_required=False)
                    self.assertTrue(result["exact_head_review_clean"])
                else:
                    with self.assertRaises(ValueError):
                        self.tools.assess(data, session, github, (triage,), pre_review_required=False)

    def local_source_remediation(self, *, generated=False, reported_member=None):
        path = ("scripts/generated_data/eventlists/generate.py" if generated
                else "scripts/workflow_pilot/review_family.py")
        source = (self.repo.root / path).read_text()
        broken = (source.replace(
            'return "".join(parts)',
            'return "".join(parts).replace("FACTION_ID_BLUE", "FACTION_ID_RED")') if generated
            else source.replace('require(request["candidate_sha"] == session.head,', 'require(True,'))
        self.assertNotEqual(broken, source)
        before = self.repo.commit({path: broken})
        after = self.repo.commit({path: source}, parent=before)
        data = (request("TC-CORE-004", "generated-eventlists", self.repo.base, after) if generated
                else request(base=self.repo.base, head=after))
        key = self.model.subject_key(data["subjects"][0])
        finding = self.model.Finding(
            "local-finding", key, "generated" if generated else "wire",
            reported_member or ("outputs:eventlists" if generated else "stale-bindings:review-session"),
            before, path, "local:task-1")
        session = self.model.ReviewSession(
            "coordinator", "implementer", frozenset({key}), before,
            identity=("owner/repo", 1, self.repo.base), owners=self.model.ReviewOwnership())
        runtime = Runtime(before, session.scope)
        runtime.result.findings = (finding,)
        session.begin(runtime, "reviewer")
        session.finish(runtime)
        session.advance(after)
        github = ObservedGitHub(response(self.repo.base, after))
        _, facts = github.snapshot("owner/repo", 1, self.model)
        triage = self.model.Triage(facts[0], "clean")
        session.triage(triage)
        return data, session, github, triage, finding

    def test_omitted_local_report_finding_cannot_admit_handoff(self):
        data, session, github, triage, finding = self.local_source_remediation()
        members = self.tools.members(data, (finding.origin,))
        before = self.tools.run_obligations(members, finding.origin)
        self.assertEqual(next(item.verdict for item in before
                              if item.obligation.member == finding.member), "contract-violation")
        with self.assertRaisesRegex(ValueError, "local.*triage"):
            self.tools.assess(data, session, github, (triage,), pre_review_required=True)

    def test_reported_member_cannot_borrow_a_real_sibling_origin_failure(self):
        data, session, github, triage, finding = self.local_source_remediation(
            reported_member="validators:review-session")
        session.triage_local(finding.id, accepted=True, reason="Reported validator defect")
        data["findings"] = [{
            "finding_id": finding.id, **data["subjects"][0],
            "family": finding.family, "reported_member": finding.member,
        }]
        members = self.tools.members(data, (finding.origin,))
        before = self.tools.run_obligations(
            tuple(item for item in members if item.family == finding.family), finding.origin)
        verdicts = {item.obligation.member: item.verdict for item in before}
        self.assertEqual(verdicts[finding.member], "satisfied")
        self.assertEqual(verdicts["stale-bindings:review-session"], "contract-violation")
        current = self.tools.run_obligations(members, data["candidate_sha"])
        self.assertTrue(all(item.verdict == "satisfied" for item in current))
        for entrypoint in ("direct", "adapter"):
            with self.subTest(entrypoint=entrypoint), self.assertRaisesRegex(ValueError, "reported member"):
                if entrypoint == "direct":
                    self.model.assess_handoff(
                        data, members, (*before, *current), session, tool_revision=self.repo.base,
                        remote_reviews=(triage.fact,), triage=(triage,), pre_review_required=True)
                else:
                    self.tools.assess(data, session, github, (triage,), pre_review_required=True)

    def test_earlier_head_conversation_blocks_clean_until_observed_resolution(self):
        head = self.repo.commit({"earlier-thread-candidate": "new candidate"})
        data = request(base=self.repo.base, head=head)
        scope = frozenset({self.model.subject_key(data["subjects"][0])})
        session = self.model.ReviewSession(
            "coordinator", "implementer", scope, head,
            identity=("owner/repo", 1, self.repo.base), owners=self.model.ReviewOwnership())
        runtime = Runtime(head, scope)
        session.begin(runtime, "reviewer")
        session.finish(runtime)
        payload = response(self.repo.base, head)
        pr = payload["data"]["repository"]["pullRequest"]
        earlier = pr["reviews"]["nodes"][0]
        earlier.update(state="CHANGES_REQUESTED", body="Earlier conversation remains unresolved")
        earlier["commit"]["oid"] = self.repo.base
        latest = copy.deepcopy(earlier)
        latest.update(id="review-2", state="APPROVED", submittedAt="2026-01-01T00:00:20Z",
                      body="Complete clean review of the new head")
        latest["commit"]["oid"] = head
        latest["comments"]["nodes"] = []
        pr["reviews"]["nodes"].append(latest)
        pr["reviewThreads"]["nodes"] = [{
            "id": "earlier-thread", "isResolved": False,
            "comments": {"nodes": [{"pullRequestReview": {"id": earlier["id"]}}]},
        }]
        github = ObservedGitHub(payload)
        _, facts = github.snapshot("owner/repo", 1, self.model)
        self.assertEqual(facts[0].head, self.repo.base)
        self.assertEqual(facts[0].unresolved_threads, ("earlier-thread",))
        session.triage(self.model.Triage(facts[0], "changes-requested"))
        session.triage(self.model.Triage(facts[1], "clean"))
        report = self.tools.assess(
            data, session, github, tuple(session.rounds.events), pre_review_required=True)
        with self.subTest(admission="unresolved conversation"):
            self.assertFalse(report["exact_head_review_clean"])
            self.assertFalse(report["handoff_eligible"])
        pr["reviewThreads"]["nodes"][0]["isResolved"] = True
        with self.assertRaises(ValueError):
            self.tools.assess(
                data, session, github, tuple(session.rounds.events), pre_review_required=True)
        self.assertEqual(session.rounds.events[0].outcome, "untriaged")
        _, resolved = github.snapshot("owner/repo", 1, self.model)
        self.assertTrue(all(not item.unresolved_threads for item in resolved))
        report = self.tools.assess(
            data, session, github, tuple(session.rounds.events), pre_review_required=True)
        with self.subTest(admission="resolved but untriaged"):
            self.assertFalse(report["handoff_eligible"])
            self.assertFalse(report["exact_head_review_clean"])
        session.triage(self.model.Triage(resolved[0], "changes-requested"))
        report = self.tools.assess(
            data, session, github, tuple(session.rounds.events),
            pre_review_required=True)
        self.assertTrue(report["exact_head_review_clean"])
        self.assertTrue(report["handoff_eligible"])

    def test_local_report_accepted_rejected_and_remediated_decisions(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                data, session, github, triage, finding = self.local_source_remediation()
                session.triage_local(finding.id, accepted=accepted, reason="Coordinator source review")
                if accepted:
                    self.assertEqual(session.accepted[finding.id], finding)
                    data["findings"] = [{
                        "finding_id": finding.id, **data["subjects"][0],
                        "family": finding.family, "reported_member": finding.member,
                    }]
                else:
                    self.assertNotIn(finding.id, session.accepted)
                result = self.tools.assess(data, session, github, (triage,), pre_review_required=True)
                self.assertTrue(result["handoff_eligible"])
                self.assertEqual(bool(result["outcomes"]), accepted)
                if accepted:
                    self.assertTrue(any(row["outcome"] == "affected-fixed" for row in result["outcomes"]))
                    session.accepted.clear()
                    data["findings"] = []
                    with self.assertRaisesRegex(ValueError, "local.*triage"):
                        self.tools.assess(data, session, github, (triage,), pre_review_required=True)
                with self.assertRaises(ValueError):
                    session.triage_local(finding.id, accepted=accepted, reason="Duplicate decision")

    def test_accepted_local_generated_finding_drives_the_actual_sibling_sweep(self):
        data, session, github, triage, finding = self.local_source_remediation(generated=True)
        session.triage_local(finding.id, accepted=True, reason="Generated output changed faction")
        data["findings"] = [{
            "finding_id": finding.id, **data["subjects"][0],
            "family": finding.family, "reported_member": finding.member,
        }]
        result = self.tools.assess(data, session, github, (triage,), pre_review_required=True)
        self.assertTrue(result["handoff_eligible"])
        self.assertTrue(any(row["member"] == finding.member and row["outcome"] == "affected-fixed"
                            for row in result["outcomes"]))
        self.assertEqual({row["member"] for row in result["outcomes"]},
                         {member.member for member in self.tools.members(data, (finding.origin,))})

    def test_unattached_tool_tree_is_rejected_before_initializer_execution(self):
        data = request(base=self.repo.base, head=self.repo.base)
        request_path = self.repo.root / "request.json"
        request_path.write_text(json.dumps(data))
        sentinel = self.repo.root / "build" / "unattached-tree-executed"
        payload = self.repo.root / "build" / "tree-initializer.py"
        payload.parent.mkdir(exist_ok=True)
        payload.write_text("from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('ran')\n")
        git(self.repo.root, "read-tree", self.repo.base)
        oid = git(self.repo.root, "hash-object", "-w", str(payload))
        git(self.repo.root, "update-index", "--add", "--cacheinfo",
            "100644," + oid + ",scripts/__init__.py")
        tree = git(self.repo.root, "write-tree")
        git(self.repo.root, "read-tree", self.repo.base)
        self.assertEqual(git(self.repo.root, "cat-file", "-t", tree), "tree")
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-B",
                 str(self.repo.root / "scripts/workflow_pilot/isolated_launcher.py"), "review-family",
                 "--repository-root", str(self.repo.root), "--subject-root", str(self.repo.root),
                 "--tool-revision", tree, "--candidate", self.repo.base,
                 "--request", str(request_path), "--mode", "plan"],
                env=ENV, capture_output=True, timeout=30)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(sentinel.exists(), "unattached tree code executed before rejection")
        finally:
            sentinel.unlink(missing_ok=True)

    def test_changed_content_wrong_identity_and_missing_task_reject(self):
        data, session, _, github, facts = self.setup_review()
        triage = self.model.Triage(facts[0], "clean")
        session.triage(triage)
        pr = github.payload["data"]["repository"]["pullRequest"]
        pr["reviews"]["nodes"][0]["body"] += " additional suppressed finding"
        with self.assertRaisesRegex(ValueError, "triage|content"):
            self.tools.assess(data, session, github, (triage,), pre_review_required=True)
        pr["reviews"]["nodes"][0]["body"] = facts[0].body
        session.triage(triage)
        session.report = None
        with self.assertRaisesRegex(ValueError, "task observation"):
            self.tools.assess(data, session, github, (triage,), pre_review_required=True)
        for key, value in (("repository", "other/repo"), ("pull_request", 2)):
            changed = {**data, key: value}
            with self.assertRaises(ValueError):
                self.tools.assess(changed, session, github, (triage,), pre_review_required=False)

    def test_actual_clean_review_edit_invalidates_then_accepts_fresh_triage(self):
        data, session, _, github, facts = self.setup_review()
        clean = self.model.Triage(facts[0], "clean")
        session.triage(clean)
        self.assertTrue(self.tools.assess(
            data, session, github, (clean,), pre_review_required=True)["exact_head_review_clean"])
        github.payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["body"] += " edited"
        with self.assertRaises(ValueError):
            self.tools.assess(data, session, github, (clean,), pre_review_required=True)
        self.assertEqual(session.rounds.events[0].outcome, "untriaged")
        self.assertEqual(len(session.rounds.events), 1)
        _, refreshed = github.snapshot("owner/repo", 1, self.model)
        final = self.model.Triage(refreshed[0], "clean")
        session.triage(final)
        self.assertTrue(self.tools.assess(
            data, session, github, (final,), pre_review_required=True)["exact_head_review_clean"])
        with self.assertRaises(ValueError):
            session.triage(final)
        query = github.query
        change_after = github.calls + 1

        def edit_during_probe(*arguments):
            if github.calls == change_after:
                github.payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["body"] += " during probe"
            return query(*arguments)

        with patch.object(github, "query", side_effect=edit_during_probe):
            with self.assertRaisesRegex(ValueError, "changed during execution"):
                self.tools.assess(data, session, github, (final,), pre_review_required=True)
        self.assertEqual(session.rounds.events[0].outcome, "untriaged")

    def test_actual_formal_review_retriage_dismissal_and_later_handoff(self):
        data, _, github, _, local = self.local_source_remediation()
        session = self.model.ReviewSession(
            "coordinator", "implementer", frozenset({local.subject}), data["candidate_sha"],
            identity=("owner/repo", 1, self.repo.base))
        node = github.payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]
        node["state"] = "CHANGES_REQUESTED"
        node["commit"]["oid"] = local.origin
        _, facts = github.snapshot("owner/repo", 1, self.model)
        session.triage(self.model.Triage(facts[0], "untriaged"))
        finding = replace(local, id="remote-finding", review_id=facts[0].id)
        complete = self.model.Triage(facts[0], "changes-requested", (finding,))
        session.triage(complete)
        data["findings"] = [{
            "finding_id": finding.id, **data["subjects"][0],
            "family": finding.family, "reported_member": finding.member,
        }]
        result = self.tools.assess(data, session, github, (complete,), pre_review_required=False)
        self.assertTrue(result["handoff_eligible"])
        self.assertEqual(session.rounds.consecutive, 1)
        self.assertEqual(session.rounds.handoffs[0]["findings"], [finding.id])
        node["body"] += " edited"
        with self.assertRaises(ValueError):
            self.tools.assess(data, session, github, (complete,), pre_review_required=False)
        self.assertEqual(session.rounds.handoffs, [])
        self.assertEqual(session.accepted[finding.id], finding)
        _, facts = github.snapshot("owner/repo", 1, self.model)
        complete = self.model.Triage(facts[0], "changes-requested", (finding,))
        session.triage(complete)
        self.assertEqual(session.rounds.consecutive, 1)
        self.assertEqual(session.rounds.handoffs[0]["findings"], [finding.id])
        node["body"] += " further context"
        _, facts = github.snapshot("owner/repo", 1, self.model)
        complete = self.model.Triage(facts[0], "changes-requested")
        session.triage(complete)
        self.assertEqual(session.rounds.handoffs[0]["findings"], [finding.id])
        node["state"] = "DISMISSED"
        with self.assertRaises(ValueError):
            self.tools.assess(data, session, github, (complete,), pre_review_required=False)
        _, facts = github.snapshot("owner/repo", 1, self.model)
        dismissed = self.model.Triage(facts[0], "dismissed")
        session.triage(dismissed)
        with self.assertRaises(ValueError):
            self.tools.assess({**data, "findings": []}, session, github,
                              (dismissed,), pre_review_required=False)
        later = copy.deepcopy(node)
        later.update(id="review-2", state="APPROVED", submittedAt="2026-01-01T00:00:20Z")
        later["commit"]["oid"] = data["candidate_sha"]
        github.payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"].append(later)
        _, facts = github.snapshot("owner/repo", 1, self.model)
        session.triage(self.model.Triage(facts[-1], "clean"))
        result = self.tools.assess(
            data, session, github, tuple(session.rounds.events), pre_review_required=False)
        self.assertTrue(result["exact_head_review_clean"])
        self.assertTrue(result["handoff_eligible"])
        self.assertEqual(len(session.rounds.seen), 2)

    def test_real_direct_and_isolated_cli_bound_expected_git_errors(self):
        descendant = self.repo.commit({"lineage-error-control": "descendant"})
        launcher = self.repo.root / "scripts/workflow_pilot/isolated_launcher.py"
        bootstrap = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from scripts.workflow_pilot.trusted_review_gate import main; "
            "raise SystemExit(main(sys.argv[2:]))"
        )
        for base, head in ((self.repo.base, "f" * 40), ("e" * 40, self.repo.base),
                           (descendant, self.repo.base)):
            path = self.repo.root / "request.json"
            path.write_text(json.dumps(request(base=base, head=head)))
            arguments = [
                "--repository-root", str(self.repo.root), "--subject-root", str(self.repo.root),
                "--tool-revision", self.repo.base, "--candidate", head,
                "--request", str(path), "--mode", "plan",
            ]
            for entrypoint in (
                [sys.executable, "-I", "-c", bootstrap, str(self.repo.root)],
                [sys.executable, "-I", str(launcher), "review-family"],
            ):
                with self.subTest(base=base, head=head, entrypoint=entrypoint[2]):
                    completed = subprocess.run(entrypoint + arguments, env=ENV,
                                               capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, "")
                    self.assertTrue(completed.stderr.startswith("review-family:"), completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertLessEqual(len(completed.stderr), 1200)

    def test_real_direct_and_isolated_cli_translate_expected_tool_failures(self):
        path = self.repo.root / "request.json"
        path.write_text(json.dumps(request(base=self.repo.base, head=self.repo.base)))
        bootstrap = """
import runpy, subprocess, sys
from pathlib import Path
fault, entry, root = sys.argv[1:4]
arguments = sys.argv[4:]
run = subprocess.run
def bounded(command, **kwargs):
    if Path(command[0]).name == ("git" if fault == "git-timeout" else "gh"):
        if fault == "git-timeout":
            kwargs["timeout"] = 0
        elif fault == "gh-timeout":
            command = [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
            kwargs["timeout"] = 0.05
        else:
            command = [str(Path(root) / "build/not-installed-gh")]
    return run(command, **kwargs)
subprocess.run = bounded
if entry == "direct":
    sys.path.insert(0, root)
    if fault == "git-timeout":
        from scripts.workflow_pilot.isolated_launcher import main
        arguments = ["review-family", *arguments]
    else:
        from scripts.workflow_pilot.trusted_review_gate import main
    raise SystemExit(main(arguments))
sys.argv = [str(Path(root) / "scripts/workflow_pilot/isolated_launcher.py"),
            "review-family", *arguments]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
        for fault in ("git-timeout", "gh-timeout", "gh-launch"):
            for entry in ("direct", "isolated"):
                with self.subTest(fault=fault, entry=entry):
                    result = subprocess.run(
                        [sys.executable, "-I", "-c", bootstrap, fault, entry, str(self.repo.root),
                         "--repository-root", str(self.repo.root), "--subject-root", str(self.repo.root),
                         "--tool-revision", self.repo.base, "--candidate", self.repo.base,
                         "--request", str(path), "--mode", "plan" if fault == "git-timeout" else "check"],
                        env=ENV, capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertTrue(result.stderr.startswith(
                        "workflow-pilot-launcher:" if fault == "git-timeout" else "review-family:"),
                        result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertLessEqual(len(result.stderr), 1200)

    def test_github_api_translates_real_os_failures_but_not_programming_errors(self):
        run = subprocess.run
        for fault in ("timeout", "launch"):
            with self.subTest(fault=fault):
                def unavailable(command, **kwargs):
                    if fault == "timeout":
                        command = [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
                        kwargs["timeout"] = 0.05
                    else:
                        command = [str(self.repo.root / "build/not-installed-gh")]
                    return run(command, **kwargs)

                with patch.object(gate.subprocess, "run", side_effect=unavailable):
                    with self.assertRaisesRegex(ValueError, "GitHub observation unavailable") as caught:
                        gate.GitHub().query("owner/repo", 1)
                self.assertLessEqual(len(str(caught.exception)), 1200)
                self.assertIsInstance(caught.exception.__cause__,
                                      subprocess.TimeoutExpired if fault == "timeout" else OSError)
        with patch.object(gate.subprocess, "run", side_effect=RuntimeError("programming defect")):
            with self.assertRaisesRegex(RuntimeError, "programming defect"):
                gate.GitHub().query("owner/repo", 1)

    def request_cli(self, path, *, isolated, timeout=30):
        bootstrap = (
            "import resource, sys; "
            "resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024,) * 2); "
        )
        if isolated:
            bootstrap += (
                "import runpy; sys.argv = sys.argv[1:]; "
                "runpy.run_path(sys.argv[0], run_name='__main__')"
            )
            entry = [str(self.repo.root / "scripts/workflow_pilot/isolated_launcher.py"),
                     "review-family"]
        else:
            bootstrap += (
                "sys.path.insert(0, sys.argv[1]); "
                "from scripts.workflow_pilot.trusted_review_gate import main; "
                "raise SystemExit(main(sys.argv[2:]))"
            )
            entry = [str(self.repo.root)]
        return subprocess.run(
            [sys.executable, "-I", "-c", bootstrap, *entry,
             "--repository-root", str(self.repo.root), "--subject-root", str(self.repo.root),
             "--tool-revision", self.repo.base, "--candidate", self.repo.base,
             "--request", str(path), "--mode", "plan"],
            env=ENV, capture_output=True, text=True, timeout=timeout)

    def assert_request_rejected(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("review-family:"), result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertLessEqual(len(result.stderr), 1200)

    def test_real_cli_request_size_bound_precedes_allocation(self):
        root = self.repo.root / "build/intake-size"
        root.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(request(base=self.repo.base, head=self.repo.base)).encode()
        exact = root / "exact.json"
        exact.write_bytes(raw.ljust(1024 * 1024, b" "))
        over = root / "over.json"
        over.write_bytes(raw.ljust(1024 * 1024 + 1, b" "))
        huge = root / "huge.json"
        with huge.open("wb") as file:
            file.truncate(256 * 1024 * 1024)
        for isolated in (False, True):
            with self.subTest(isolated=isolated, case="exact-bound"):
                result = self.request_cli(exact, isolated=isolated)
                self.assertEqual(result.returncode, 0, result.stderr)
                parsed = json.loads(result.stdout)
                self.assertEqual(len(parsed["obligations"]), 9)
                self.assertFalse(parsed["merge_permission"])
            for path in (over, huge):
                with self.subTest(isolated=isolated, case=path.name):
                    result = self.request_cli(path, isolated=isolated)
                    self.assert_request_rejected(result)
                    self.assertIn("size bound", result.stderr)

    def test_real_cli_invalid_and_nonregular_request_ingress_is_bounded(self):
        root = self.repo.root / "build/intake-invalid"
        root.mkdir(parents=True, exist_ok=True)
        payloads = [b"", b"{", b"\xff", b"[" * 2000 + b"]" * 2000]
        key = json.dumps("x" * 5000).encode()
        payloads.append(b"{" + key + b":1," + key + b":2}")
        for family in ([], {}, None):
            data = request(base=self.repo.base, head=self.repo.base)
            data["findings"] = [{
                "finding_id": "finding", **data["subjects"][0],
                "family": family, "reported_member": "validators:review-session",
            }]
            payloads.append(json.dumps(data).encode())
        paths = []
        for number, payload in enumerate(payloads):
            path = root / f"malformed-{number}.json"
            path.write_bytes(payload)
            paths.append(path)
        valid = root / "valid.json"
        valid.write_text(json.dumps(request(base=self.repo.base, head=self.repo.base)))
        symlink = root / "symlink.json"
        symlink.symlink_to(valid)
        fifo = root / "fifo.json"
        os.mkfifo(fifo)
        paths.extend((root, root / "missing.json", symlink, fifo))
        for path in paths:
            for isolated in (False, True):
                with self.subTest(path=path.name, isolated=isolated):
                    self.assert_request_rejected(
                        self.request_cli(path, isolated=isolated, timeout=5))

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
