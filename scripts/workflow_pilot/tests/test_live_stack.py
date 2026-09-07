"""Actual Git stack ancestry and immutable decision blobs through production routing."""

import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.workflow_pilot import adaptive_gate as gate, event_classifier, pr_metadata as github, reporter
from scripts.workflow_pilot.tests.test_adaptive_gate import decisions
from scripts.workflow_pilot.tests.test_agent_handoff import git, write_json
from scripts.workflow_pilot.tests import test_pr_metadata as api


ROOT = Path(__file__).resolve().parents[3]


class LiveStackTests(unittest.TestCase):
    def scenario(self, depth, *, fault=None):
        artifacts = ROOT / "build/test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="live-stack-", dir=artifacts)
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        git(root, "init", "-b", "master")
        git(root, "config", "user.name", "Stack regression")
        git(root, "config", "user.email", "stack@example.invalid")
        (root / "README.md").write_text("Actual default tree\n")
        git(root, "add", ".")
        git(root, "commit", "-m", "Default base")
        default = git(root, "rev-parse", "HEAD")
        records, pull_requests, heads = {}, {}, []
        decision_path = root / reporter.DECISION_RECORD_PATH
        decision_path.parent.mkdir(parents=True)
        for level in range(depth + 1):
            number = api.PR_NUMBER + level
            parent = number - 1 if level else None
            raw = decisions(number, ("protocol",), "review-first")
            raw["pull_requests"][0]["stack"] = {
                "depth": level, "parent_pr": parent,
                "exception_reason": "Bounded foundation/protocol/feature stack" if level == 3 else None}
            if fault == "cycle" and level == 0:
                raw["pull_requests"][0]["stack"].update(depth=1, parent_pr=api.PR_NUMBER + 1)
            if fault == "parent-decision-missing" and level == 0:
                raw["pull_requests"][0]["pull_request"] = api.PR_NUMBER + 100
            records[number] = raw
            base = heads[-1] if heads else default
            branch = f"feature/stack-{level}"
            git(root, "checkout", "-b", branch)
            write_json(decision_path, raw)
            (root / "README.md").write_text(f"Actual layer {level}\n")
            git(root, "add", ".")
            git(root, "commit", "-m", f"Stack layer {level}")
            head = git(root, "rev-parse", "HEAD")
            heads.append(head)
            pr = api._pr(head=head, base=base)
            pr.update(number=number, url=github._api_url(api._endpoint(f"pulls/{number}")),
                      additions=10, deletions=0)
            pr["head"]["ref"] = branch
            pr["base"]["ref"] = f"feature/stack-{level - 1}" if level else "master"
            pull_requests[number] = pr
        child = api.PR_NUMBER + depth
        if fault == "cycle":
            pull_requests[api.PR_NUMBER]["base"].update(
                ref=pull_requests[child]["head"]["ref"], sha=pull_requests[child]["head"]["sha"])
        stack = records[child]["pull_requests"][0]["stack"]
        if fault == "missing-parent":
            stack.update(depth=1, parent_pr=None)
        elif fault == "missing-exception":
            stack["exception_reason"] = None
        elif fault == "wrong-depth":
            stack["depth"] = 1
        elif fault == "self-cycle":
            stack["parent_pr"] = child
        elif fault == "root-parent":
            stack.update(parent_pr=child - 1)
        elif fault == "wrong-branch":
            pull_requests[child]["base"]["ref"] = "unrelated"
        if fault in {"missing-parent", "missing-exception", "wrong-depth", "self-cycle", "root-parent"}:
            write_json(decision_path, records[child])
            git(root, "add", ".")
            git(root, "commit", "-m", "Actual invalid stack declaration")
            pull_requests[child]["head"]["sha"] = git(root, "rev-parse", "HEAD")
        if fault == "unsynced-parent":
            parent = child - 1
            git(root, "checkout", pull_requests[parent]["head"]["ref"])
            (root / "later.md").write_text("Real parent movement without a child merge\n")
            git(root, "add", ".")
            git(root, "commit", "-m", "Parent advanced")
            moved = git(root, "rev-parse", "HEAD")
            pull_requests[parent]["head"]["sha"] = moved
            pull_requests[child]["base"]["sha"] = moved
        pr = pull_requests[child]
        payload = {"action": "synchronize", "number": child, "pull_request": copy.deepcopy(pr)}
        event = event_classifier.classify_event(
            "pull_request", payload, github_ref=f"refs/pull/{child}/merge", github_sha="f" * 40,
            pr_head_sha=pr["head"]["sha"], pr_base_sha=pr["base"]["sha"], push_sha="")
        calls = []
        parent_reads = 0

        def transport(argv, **kwargs):
            nonlocal parent_reads
            method = argv[argv.index("--method") + 1]
            endpoint = argv[argv.index("X-GitHub-Api-Version: 2022-11-28") + 1]
            self.assertEqual(method, "GET")
            calls.append(endpoint)
            status = 200
            if endpoint == api._endpoint("").rstrip("/"):
                response = {"id": api.REPOSITORY_ID, "full_name": api.REPOSITORY, "default_branch": "master"}
            elif "/pulls/" in endpoint:
                number = int(endpoint.rsplit("/", 1)[1])
                if fault == "unavailable-parent" and number != child:
                    status, response = 404, {"message": "Parent unavailable"}
                else:
                    response = copy.deepcopy(pull_requests[number])
                    if number != child:
                        parent_reads += 1
                        if fault == "moving-parent" and parent_reads > depth:
                            git(root, "checkout", response["head"]["ref"])
                            (root / "moving.md").write_text("Parent advanced during its observation\n")
                            git(root, "add", ".")
                            git(root, "commit", "-m", "Parent moved during validation")
                            response["head"]["sha"] = git(root, "rev-parse", "HEAD")
                            pull_requests[number] = copy.deepcopy(response)
                            pull_requests[child]["base"]["sha"] = response["head"]["sha"]
            elif "/contents/" in endpoint:
                revision = endpoint.rsplit("=", 1)[1]
                content = reporter.run_git(root, "show", revision + ":" + str(reporter.DECISION_RECORD_PATH))
                response = {"type": "file", "path": str(reporter.DECISION_RECORD_PATH), "encoding": "base64",
                            "sha": git(root, "rev-parse", revision + ":" + str(reporter.DECISION_RECORD_PATH)),
                            "content": base64.b64encode(content).decode()}
            elif "/compare/" in endpoint:
                base, head = endpoint.rsplit("/", 1)[1].split("...")
                response = {"base_commit": {"sha": base},
                            "merge_base_commit": {"sha": git(root, "merge-base", "--all", base, head)}}
            else:
                raise AssertionError("Unexpected API request: " + endpoint)
            wire = f"HTTP/2.0 {status} Result\r\nContent-Type: application/json\r\n\r\n" + json.dumps(response)
            return subprocess.CompletedProcess(argv, 0 if status == 200 else 1, wire.encode(), b"")

        result, selected, _ = gate.route_event(
            github.GitHubClient("/usr/bin/gh", runner=transport), event, payload, api.REPOSITORY)
        self.stack_evidence = {"depth": depth, "fault": fault, "pull_requests": pull_requests,
                               "calls": calls, "mode": selected.mode, "known": selected.known,
                               "reason": selected.reason}
        return result, selected

    def test_real_root_child_depth_two_and_exceptional_depth_three(self):
        for depth in range(4):
            with self.subTest(depth=depth):
                result, selected = self.scenario(depth)
                self.assertTrue(selected.known, self.stack_evidence)
                self.assertEqual(result.classification, "review-first")

    def test_actual_invalid_cross_fields_or_parent_authority_remain_unknown(self):
        for depth, fault in (
            (0, "missing-parent"), (3, "missing-exception"), (2, "wrong-depth"),
            (1, "self-cycle"), (0, "root-parent"), (1, "wrong-branch"),
            (1, "unsynced-parent"), (1, "unavailable-parent"), (1, "moving-parent"),
            (1, "cycle"), (1, "parent-decision-missing"),
        ):
            with self.subTest(depth=depth, fault=fault):
                result, selected = self.scenario(depth, fault=fault)
                self.assertFalse(selected.known, self.stack_evidence)
                self.assertEqual(result.classification, "full")

    def test_unknown_live_stack_cannot_reach_candidate_admission(self):
        _, decision = self.scenario(1, fault="unavailable-parent")
        number = api.PR_NUMBER + 1
        pr = github._parse_pull_request_payload(self.stack_evidence["pull_requests"][number],
                                                api.REPOSITORY, number)
        with patch.object(gate, "fetch_candidate", return_value=(pr, 10)), \
             patch.object(gate, "fetch_decision", return_value=decision):
            with self.assertRaisesRegex(ValueError, "live decision authority"):
                gate.assess_observed(None, {"repository": api.REPOSITORY},
                                     {"pr_number": number}, None, (), None)
