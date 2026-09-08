"""Current default-branch control over immutable candidates; no live incidents."""

import base64
import copy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.workflow_pilot import adaptive_gate as gate, agent_handoff as handoff
from scripts.workflow_pilot import coordinator_observations as observations, raw_diff_check as raw_git
from scripts.workflow_pilot import event_classifier, pr_metadata as api, reporter
from scripts.workflow_pilot import review_family as review
from scripts.workflow_pilot.tests import test_pr_metadata as metadata
from scripts.workflow_pilot.tests import test_reporter as reporting
from scripts.workflow_pilot.tests.test_adaptive_gate import GateTests, decisions
from scripts.workflow_pilot.tests.test_agent_handoff import GitFixture, at_offset, git, write_json
from scripts.workflow_pilot.tests.review_support import Runtime


def control_routes(root, repository, repository_id, revisions):
    current = git(root, "rev-parse", "master")
    endpoint = lambda path: api._endpoint(repository, path)
    routes = {
        endpoint("").rstrip("/"): {"id": repository_id, "full_name": repository, "default_branch": "master"},
        endpoint("git/ref/heads/master"): {
            "ref": "refs/heads/master", "object": {"type": "commit", "sha": current}},
    }
    for revision in {*revisions, current}:
        tree = git(root, "rev-parse", revision + "^{tree}")
        routes[endpoint(f"git/commits/{revision}")] = {
            "sha": revision, "tree": {"sha": tree},
            "parents": [{"sha": value} for value in git(root, "show", "-s", "--format=%P", revision).split()]}
        for oid in (tree, git(root, "rev-parse", revision + ":.github")):
            entries = []
            for value in reporter.run_git(root, "ls-tree", "-z", oid).split(b"\0"):
                if value:
                    fields, path = value.split(b"\t", 1)
                    mode, kind, sha = fields.decode().split()
                    entries.append({"mode": mode, "type": kind, "sha": sha, "path": path.decode()})
            routes[endpoint(f"git/trees/{oid}")] = {"sha": oid, "truncated": False, "tree": entries}
        path = reporter.DECISION_RECORD_PATH.as_posix()
        content = reporter.run_git(root, "show", revision + ":" + path)
        routes[api._query_endpoint(repository, "contents/" + path, [("ref", revision)])] = {
            "path": path, "type": "file", "encoding": "base64",
            "sha": git(root, "rev-parse", revision + ":" + path),
            "content": base64.b64encode(content).decode()}
    return routes


class ControlFixture:
    def __init__(self):
        self.git = GitFixture(assign=False)
        self.root = self.git.repository
        self.path = reporter.DECISION_RECORD_PATH
        self.raw = decisions(metadata.PR_NUMBER, ("lifecycle",), "review-first")
        (self.root / self.path).parent.mkdir(parents=True, exist_ok=True)
        write_json(self.root / self.path, self.raw)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Current unpaused test control")
        self.base = git(self.root, "rev-parse", "HEAD")
        git(self.git.worktree, "merge", "--ff-only", self.base)
        self.head = self.git.commit("Candidate\n", name="docs/pause.txt")

    def close(self):
        self.git.close()

    def publish_control(self, raw):
        write_json(self.root / self.path, raw)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "Changed test control; not a real incident")
        return git(self.root, "rev-parse", "HEAD")

    def content(self, revision):
        raw = reporter.run_git(self.root, "show", revision + ":" + self.path.as_posix())
        return {"path": self.path.as_posix(), "type": "file", "encoding": "base64",
                "sha": git(self.root, "rev-parse", revision + ":" + self.path.as_posix()),
                "content": base64.b64encode(raw).decode()}

    def tree(self, oid):
        entries = []
        for raw in reporter.run_git(self.root, "ls-tree", "-z", oid).split(b"\0"):
            if raw:
                metadata_, path = raw.split(b"\t", 1)
                mode, kind, sha = metadata_.decode().split()
                entries.append({"mode": mode, "type": kind, "sha": sha, "path": path.decode()})
        return {"sha": oid, "truncated": False, "tree": entries}

    def client(self):
        current = git(self.root, "rev-parse", "master")
        pr = metadata._pr(head=self.head, base=current)
        pr["head"]["ref"] = "agent/test"
        pr.update(additions=1, deletions=0)
        routes = {
            metadata._endpoint("").rstrip("/"): {
                "id": metadata.REPOSITORY_ID, "full_name": metadata.REPOSITORY, "default_branch": "master"},
            metadata._endpoint("git/ref/heads/master"): {
                "ref": "refs/heads/master", "object": {"type": "commit", "sha": current}},
            metadata._endpoint(f"pulls/{metadata.PR_NUMBER}"): pr,
        }
        for revision in {self.base, self.head, current}:
            tree = git(self.root, "rev-parse", revision + "^{tree}")
            routes[metadata._endpoint(f"git/commits/{revision}")] = {
                "sha": revision, "tree": {"sha": tree},
                "parents": [{"sha": value} for value in git(self.root, "show", "-s", "--format=%P", revision).split()]}
            routes[metadata._endpoint(f"git/trees/{tree}")] = self.tree(tree)
            github_tree = git(self.root, "rev-parse", revision + ":.github")
            routes[metadata._endpoint(f"git/trees/{github_tree}")] = self.tree(github_tree)
            routes[metadata._query("contents/" + self.path.as_posix(), [("ref", revision)])] = self.content(revision)
        for base in {self.base, current}:
            roots = git(self.root, "merge-base", "--all", base, self.head).splitlines()
            assert roots == [self.base], roots
            routes[metadata._endpoint(f"compare/{base}...{self.head}")] = {
                "base_commit": {"sha": base}, "merge_base_commit": {"sha": roots[0]}}

        class Client:
            def __init__(self):
                self.calls = []
                self.routes = routes

            def request(self, method, endpoint, *, label, body=None):
                self.calls.append((method, endpoint, copy.deepcopy(body)))
                if method != "GET" or endpoint not in self.routes:
                    raise AssertionError(f"unplanned request: {method} {endpoint}")
                value = self.routes[endpoint]
                if callable(value):
                    value = value()
                if isinstance(value, Exception):
                    raise value
                return metadata._response(copy.deepcopy(value))

        return Client(), pr

    def route(self, client, pr):
        payload = {"action": "synchronize", "number": metadata.PR_NUMBER,
                   "pull_request": copy.deepcopy(pr)}
        payload["pull_request"]["base"]["sha"] = self.base
        event = event_classifier.classify_event(
            "pull_request", payload, github_ref=f"refs/pull/{metadata.PR_NUMBER}/merge",
            github_sha="f" * 40, pr_base_sha=self.base, pr_head_sha=self.head, push_sha="")
        return gate.route_event(client, event, payload, metadata.REPOSITORY)


class CurrentControlTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ControlFixture()
        self.addCleanup(self.fixture.close)

    def test_unchanged_head_observes_current_pause_without_rebinding_candidate(self):
        f = self.fixture
        client, pr = f.client()
        initial, selected, binding = f.route(client, pr)
        self.assertEqual(initial.classification, "review-first")
        paused = copy.deepcopy(f.raw)
        paused["pull_requests"][0]["pilot"]["disposition"] = "paused"
        current = f.publish_control(paused)
        self.assertEqual(git(f.git.worktree, "rev-parse", "HEAD"), f.head)
        client, pr = f.client()
        actual, after, after_binding = f.route(client, pr)
        self.assertEqual(actual.classification, "full")
        self.assertTrue(after.known)
        self.assertTrue(after.paused)
        self.assertEqual(after.decision_oid, selected.decision_oid)
        self.assertEqual(after.head_sha, selected.head_sha)
        self.assertEqual(after_binding, binding)
        self.assertEqual(after.control.commit_sha, current)
        self.assertEqual(gate.parse_binding(after_binding)[2], f.base)

    def test_global_pause_in_excluded_sibling_and_explicit_unpause(self):
        f = self.fixture
        raw = copy.deepcopy(f.raw)
        sibling = copy.deepcopy(raw["pull_requests"][0])
        sibling["pull_request"] += 1
        sibling["pilot"] = {"included": False, "disposition": "paused"}
        raw["pull_requests"].append(sibling)
        f.publish_control(raw)
        client, pr = f.client()
        _, paused, _ = f.route(client, pr)
        self.assertTrue(paused.paused)
        self.assertTrue(paused.pre_review_required)
        sibling["pilot"]["disposition"] = "excluded"
        f.publish_control(raw)
        client, pr = f.client()
        result, restored, _ = f.route(client, pr)
        self.assertEqual(result.classification, "review-first")
        self.assertFalse(restored.paused)
        self.assertNotEqual(restored.control.commit_sha, paused.control.commit_sha)
        self.assertEqual(restored.decision_oid, paused.decision_oid)

    def test_new_head_after_global_pause_starts_broad_without_changing_its_record(self):
        f = self.fixture
        raw = copy.deepcopy(f.raw)
        raw["pull_requests"][0]["pilot"]["disposition"] = "paused"
        f.publish_control(raw)
        f.head = f.git.commit("New head after pause\n", name="docs/pause.txt")
        client, pr = f.client()
        result, selected, _ = f.route(client, pr)
        self.assertEqual(result.classification, "full")
        self.assertTrue(selected.paused)
        self.assertTrue(selected.known)
        self.assertEqual(reporter.load_decisions_from_commit(f.root, f.head), f.raw)

    def test_every_collection_member_is_validated_even_after_a_paused_record(self):
        f = self.fixture
        for change in ("duplicate", "invalid-risk", "boolean", "artifact", "stack"):
            with self.subTest(change=change):
                raw = copy.deepcopy(f.raw)
                raw["pull_requests"][0]["pilot"]["disposition"] = "paused"
                sibling = copy.deepcopy(raw["pull_requests"][0])
                sibling["pull_request"] += 1
                if change == "duplicate":
                    sibling["pull_request"] -= 1
                elif change == "invalid-risk":
                    sibling["risk_boundaries"] = ["invented"]
                elif change == "boolean":
                    sibling["pilot"]["included"] = 1
                elif change == "artifact":
                    raw["artifacts"] = [{"artifact_id": "incomplete"}]
                else:
                    sibling["stack"] = {"depth": 3, "parent_pr": metadata.PR_NUMBER, "exception_reason": None}
                raw["pull_requests"].append(sibling)
                f.publish_control(raw)
                client, pr = f.client()
                routed, selected, _ = f.route(client, pr)
                self.assertEqual(routed.classification, "full")
                self.assertFalse(selected.known)
                self.assertIsNone(selected.control)

    def test_source_identity_regular_blob_and_complete_tree_are_mandatory(self):
        f = self.fixture
        for change in ("repository", "default-ref", "commit", "tree", "symlink", "blob", "unavailable"):
            with self.subTest(change=change):
                client, pr = f.client()
                current = git(f.root, "rev-parse", "master")
                if change == "repository":
                    client.routes[metadata._endpoint("").rstrip("/")]["id"] += 1
                elif change == "default-ref":
                    client.routes[metadata._endpoint("git/ref/heads/master")]["ref"] = "refs/heads/other"
                elif change == "commit":
                    client.routes[metadata._endpoint(f"git/commits/{current}")]["sha"] = f.head
                elif change in {"tree", "symlink"}:
                    tree = git(f.root, "rev-parse", current + ":.github")
                    value = client.routes[metadata._endpoint(f"git/trees/{tree}")]
                    if change == "tree":
                        value["truncated"] = True
                    else:
                        entry = next(row for row in value["tree"] if row["path"] == f.path.name)
                        entry["mode"] = "120000"
                else:
                    endpoint = metadata._query("contents/" + f.path.as_posix(), [("ref", current)])
                    if change == "blob":
                        client.routes[endpoint]["content"] = base64.b64encode(b"{}").decode()
                    else:
                        client.routes[endpoint] = api.MetadataEditError("current source unavailable")
                routed, selected, _ = f.route(client, pr)
                self.assertEqual(routed.classification, "full")
                self.assertFalse(selected.known)

    def test_actual_default_ref_movement_during_read_is_unknown(self):
        f = self.fixture
        client, pr = f.client()
        reference = metadata._endpoint("git/ref/heads/master")
        original = copy.deepcopy(client.routes[reference])
        changed = copy.deepcopy(f.raw)
        changed["pull_requests"][0]["pilot"]["disposition"] = "paused"
        current = f.publish_control(changed)
        observations = [original, {"ref": "refs/heads/master", "object": {"type": "commit", "sha": current}}]
        client.routes[reference] = lambda: observations.pop(0)
        result, selected, _ = f.route(client, pr)
        self.assertEqual(result.classification, "full")
        self.assertFalse(selected.known)
        self.assertIn("moved", selected.reason)


class PauseTransitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def paused(self):
        original = self.fixture.decision
        return replace(original, mode="concurrent", reason="pilot-paused", paused=True,
                       control=replace(original.control, paused=True))

    def test_old_preflight_can_reserve_one_fallback_with_pending_quality(self):
        f = self.fixture
        state = copy.deepcopy(f.state)
        state["assignments"] = []
        record = state["candidates"][0]
        paused = self.paused()
        pending = f.assess(state=state, record=record, decision=paused, checks=(), criteria_ready=False)
        self.assertTrue(pending["dispatchable"], pending)
        self.assertFalse(pending["merge_eligible"])
        self.assertEqual(record["mode"], "review-first")
        self.assertTrue({"exact-local-handoff", "exact-clean-security", "objective-or-manual-criteria"}
                        <= set(pending["missing"]))
        gate.reserve_full_dispatch(state, record, pending, f.runs)
        self.assertEqual(record["watermark"], 1)
        self.assertIsNotNone(record["dispatch_requested_at"])
        with self.assertRaises(ValueError):
            gate.reserve_full_dispatch(state, record, pending, f.runs)

    def test_reserved_full_ownership_survives_pause_and_unpause(self):
        f = self.fixture
        f.dispatched()
        for decision in (self.paused(), f.decision):
            with self.subTest(paused=decision.paused):
                complete = f.assess(decision=decision)
                self.assertTrue(complete["merge_eligible"], complete)
                active = replace(f.runs[-1], mode="active-full", status="in_progress", conclusion=None)
                pending = f.assess(decision=decision, runs=(*f.runs[:-1], active))
                self.assertEqual(pending["state"], "building")
                self.assertFalse(pending["dispatchable"])
                self.assertEqual((f.record["full_run_id"], f.record["full_attempt"]), (2, 1))
        self.assertEqual(f.record["mode"], "review-first")

    def test_initial_concurrent_pr_full_is_retained_after_unpause(self):
        f = self.fixture
        state = {**copy.deepcopy(f.state), "candidates": []}
        full = f.workflow_run(2, event="pull_request")
        record = gate.begin_candidate(state, f.pr, f.fixture.parent, self.paused(), runs=(full,))
        self.assertEqual(record["mode"], "concurrent")
        for decision in (self.paused(), f.decision):
            result = f.assess(state=state, record=record, decision=decision, runs=(full,))
            self.assertTrue(result["merge_eligible"], result)
            self.assertFalse(result["dispatchable"])
            self.assertEqual(record["mode"], "concurrent")
            self.assertIsNone(record["dispatch_requested_at"])

    def test_abandonment_hold_unknown_control_and_unbound_context_block_fallback(self):
        f = self.fixture
        for kind in ("abandoned", "architecture", "unknown-control", "original-context", "coverage"):
            with self.subTest(kind=kind):
                state = copy.deepcopy(f.state)
                record = state["candidates"][0]
                decision = self.paused()
                old_hold, old_report = f.session.rounds.hold, f.session.report
                try:
                    if kind == "abandoned":
                        record["abandoned_reason"] = "accepted-review-or-security-finding"
                    elif kind == "architecture":
                        f.session.rounds.hold = ("held-review", f.pr.head_sha)
                    elif kind == "unknown-control":
                        decision = replace(decision, known=False, control=None)
                    elif kind == "original-context":
                        f.session.report = None
                    else:
                        state["availability"].update(observed_at=at_offset(-10), valid_until=at_offset(-1))
                    result = f.assess(state=state, record=record, decision=decision, checks=(), criteria_ready=False)
                    self.assertFalse(result["dispatchable"], result)
                    self.assertFalse(result["merge_eligible"])
                finally:
                    f.session.rounds.hold, f.session.report = old_hold, old_report

    def test_unknown_intake_holds_and_old_lane_comes_from_actual_bound_history(self):
        f = self.fixture
        empty = {**copy.deepcopy(f.state), "candidates": []}
        with self.assertRaises(ValueError):
            gate.begin_candidate(empty, f.pr, f.fixture.parent, self.paused())
        unknown = replace(f.runs[0], mode="active-unknown", candidate_binding=None, candidate_base_ref=None)
        with self.assertRaises(ValueError):
            gate.begin_candidate(empty, f.pr, f.fixture.parent, self.paused(), runs=(unknown,))
        record = gate.begin_candidate(empty, f.pr, f.fixture.parent, self.paused(), runs=f.runs)
        self.assertEqual(record["mode"], "review-first")
        self.assertNotIn("intake_control", record)
        self.assertEqual(record["decision_oid"], f.decision.decision_oid)

    def test_empty_intake_cannot_override_the_first_actual_execution_lane(self):
        f = self.fixture
        state = {**copy.deepcopy(f.state), "candidates": []}
        record = gate.begin_candidate(state, f.pr, f.fixture.parent, self.paused(), runs=())
        pending = f.assess(state=state, record=record, decision=self.paused(), runs=())
        self.assertFalse(pending["dispatchable"])
        observed = f.assess(state=state, record=record, decision=self.paused(), runs=f.runs)
        self.assertTrue(observed["dispatchable"], observed)
        self.assertEqual(record["mode"], "review-first")
        self.assertNotIn("intake_control", record)
        contradictory = f.workflow_run(2, event="pull_request")
        refused = f.assess(state=state, record=record, decision=self.paused(), runs=(contradictory,))
        self.assertFalse(refused["merge_eligible"])
        self.assertEqual(record["mode"], "review-first")

    def test_reservation_rechecks_native_coverage_after_assessment(self):
        f = self.fixture
        ready = f.assess(decision=self.paused())
        self.assertTrue(ready["dispatchable"])
        f.state["availability"].update(observed_at=at_offset(-10), valid_until=at_offset(-1))
        with self.assertRaisesRegex(ValueError, "coverage"):
            gate.reserve_full_dispatch(f.state, f.record, ready, f.runs)
        self.assertIsNone(f.record["dispatch_requested_at"])


class ProducerFixture:
    def __init__(self):
        self.owned = tempfile.TemporaryDirectory(prefix="live-pause-producer-", dir=reporting.TEST_ARTIFACTS)
        self.home = Path(self.owned.name)
        self.root = self.home / "source"
        self.root.mkdir()
        self.repository = metadata.REPOSITORY
        git(self.root, "init", "-b", "master")
        git(self.root, "config", "user.email", "pilot@example.invalid")
        git(self.root, "config", "user.name", "Pilot fixture")
        git(self.root, "remote", "add", "origin", "https://github.com/" + self.repository + ".git")
        self.control = decisions(1, ("lifecycle",), "review-first")
        self.control["pull_requests"].extend(decisions(metadata.PR_NUMBER, ("lifecycle",), "review-first")["pull_requests"])
        path = self.root / reporter.DECISION_RECORD_PATH
        path.parent.mkdir()
        write_json(path, self.control)
        trees = {}
        for denominator in (0, 1):
            write_json(self.root / "incident.json", {"denominator": denominator})
            git(self.root, "add", ".")
            trees[denominator] = git(self.root, "write-tree")
        fixture = json.loads(json.dumps(reporting.minimal_fixture()).replace("example/workflow", self.repository))
        fixture["workflow_runs"][3].update(
            head_sha=reporting.sha("d"), head_branch="master", status="completed",
            conclusion="failure", completed_at="2026-01-01T09:20:00Z")
        fixture["events"].append({"id": "test-escaped-defect", "type": "escaped_defect",
                                  "occurred_at": "2026-01-01T09:30:00Z", "pr_number": 1,
                                  "sha": reporting.sha("d")})
        replacement = {}
        pending = {item["sha"]: item for item in fixture["commits"]}
        while pending:
            for old, commit in list(pending.items()):
                if not all(parent in replacement for parent in commit["parents"]):
                    continue
                arguments = ["commit-tree", trees[0 if old in {reporting.sha("c"), reporting.sha("d")} else 1]]
                for parent in commit["parents"]:
                    arguments.extend(("-p", replacement[parent]))
                environment = {**os.environ, "GIT_AUTHOR_DATE": commit["committed_at"],
                               "GIT_COMMITTER_DATE": commit["committed_at"]}
                completed = subprocess.run(
                    ["git", "-C", str(self.root), *arguments], input=(commit["message"] + "\n").encode(),
                    env=environment, capture_output=True, check=True)
                replacement[old] = completed.stdout.decode().strip()
                del pending[old]
        self.data = reporting._replace_commit_identities(fixture, replacement)
        self.cause = self.data["base_sha"]
        git(self.root, "update-ref", "refs/heads/master", self.cause)
        write_json(self.root / "incident.json", {"denominator": 0})
        git(self.root, "add", ".")
        self.bare = self.home / "published.git"
        subprocess.run(["git", "clone", "--bare", str(self.root), str(self.bare)],
                       capture_output=True, check=True)
        git(self.root, "remote", "add", "publication", str(self.bare))
        git(self.root, "checkout", "-b", "publish-pause")
        self.state_path = self.home / "coordinator.json"
        state = handoff.new_state(self.repository, "coordinator", {
            "mode": "plan", "observed_at": at_offset(-10), "valid_until": at_offset(600),
            "autostop_enabled": None, "stop_on_disconnect": None,
            "plan": "Owned Git publication fixture; no real incident or remote mutation.",
        })
        write_json(self.state_path, state)
        self.calls = []
        self.dispatch_handler = None
        self.refresh()

    def close(self):
        self.owned.cleanup()

    def refresh(self):
        revisions = git(self.bare, "rev-list", "--all").splitlines()
        self.routes = control_routes(self.bare, self.repository, metadata.REPOSITORY_ID, revisions)
        for before in revisions:
            for after in revisions:
                bases = git(self.bare, "merge-base", "--all", before, after).splitlines()
                self.routes[api._endpoint(self.repository, f"compare/{before}...{after}")] = {
                    "base_commit": {"sha": before}, "merge_base_commit": {"sha": bases[0]}}

    def request(self, method, endpoint, *, label, body=None):
        self.calls.append((method, endpoint, copy.deepcopy(body)))
        if (method == "POST" and self.dispatch_handler is not None
                and endpoint == api._endpoint(self.repository, "actions/workflows/build.yml/dispatches")):
            return self.dispatch_handler(body)
        if method != "GET" or endpoint not in self.routes:
            raise AssertionError(f"unplanned provider call: {method} {endpoint}")
        value = self.routes[endpoint]
        if isinstance(value, Exception):
            raise value
        return metadata._response(copy.deepcopy(value))

    def executor(self, context, head):
        program = """
import json,sys,subprocess
from pathlib import Path
root=Path(sys.argv[1])
current=json.loads((root/'incident.json').read_text())
old=json.loads(subprocess.check_output(['git','-C',str(root),'show',sys.argv[2]+'^1:incident.json']))
def consume(value):
    return 1 // value['denominator']
assert consume(old)==1
if int(sys.argv[3]):
    assert consume(current)==1
    print('controlled recovery')
else:
    try:
        consume(current)
    except ZeroDivisionError:
        print('controlled reproduced fault')
    else:
        raise SystemExit(7)
"""
        result = raw_git.run_process(
            ["/usr/bin/python3", "-I", "-B", "-c", program, context["allowed_worktree"],
             context["assigned_parent_sha"], "1" if context["recovery"] else "0"],
            cwd=self.root, env=raw_git.git_environment())
        return result, dict.fromkeys(handoff.METRICS)

    def publish(self, branch):
        git(self.root, "add", reporter.DECISION_RECORD_PATH.as_posix())
        git(self.root, "commit", "-m", "Prepared owned control publication")
        git(self.root, "push", "publication", branch)
        git(self.root, "checkout", "master")
        git(self.root, "merge", "--no-ff", branch, "-m", "Integrate owned control publication")
        git(self.root, "push", "publication", "master")
        self.refresh()

    def master_evidence(self, head, *, success, run_id=900):
        at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        endpoint = lambda path: api._endpoint(self.repository, path)
        workflow = json.loads(json.dumps(metadata._workflow()).replace(metadata.REPOSITORY, self.repository))
        self.routes[endpoint("actions/workflows/build.yml")] = workflow
        run = {
            "id": run_id, "run_attempt": 1, "workflow_id": metadata.WORKFLOW_ID,
            "repository": {"full_name": self.repository, "id": metadata.REPOSITORY_ID},
            "event": "push", "head_branch": "master", "head_sha": head,
            "path": api.WORKFLOW_PATH + "@refs/heads/master", "status": "completed",
            "conclusion": "success" if success else "failure",
            "created_at": at, "run_started_at": at, "updated_at": at,
        }
        self.routes[endpoint(f"actions/runs/{run_id}/attempts/1")] = run
        jobs = []
        for index, name in enumerate(sorted(gate.candidate_evidence.KNOWN_JOB_IDS), 1):
            job = metadata._job(
                name, job_id=run_id * 100 + index, run_id=run_id, head_sha=head, head_branch="master",
                created_at=at, started_at=at, completed_at=at)
            job["event"] = "push"
            job = json.loads(json.dumps(job).replace(metadata.REPOSITORY, self.repository))
            jobs.append(job)
        self.routes[api._query_endpoint(self.repository, f"actions/runs/{run_id}/attempts/1/jobs",
                                       [("per_page", "100"), ("page", "1")])] = {
            "total_count": len(jobs), "jobs": jobs}
        checks = [{"id": index, "name": name, "app": {"id": app_id, "slug": slug},
                   "head_sha": head, "status": "completed", "conclusion": "success",
                   "started_at": at, "completed_at": at}
                  for index, (name, app_id, slug) in enumerate(sorted(gate.SECURITY_CHECKS), 1)]
        self.routes[api._query_endpoint(self.repository, f"commits/{head}/check-runs",
                                       [("filter", "latest"), ("per_page", "100"), ("page", "1")])] = {
            "total_count": len(checks), "check_runs": checks}
        return run


class SafetyProducerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ProducerFixture()
        self.addCleanup(self.fixture.close)

    def test_real_attribution_local_hold_and_normal_bare_publication(self):
        f = self.fixture
        prepared = gate.pause_pilot(
            f, f.state_path, f.data, "test-escaped-defect", f.root, f.root, f.executor)
        self.assertTrue(prepared["prepared"], prepared)
        self.assertFalse(prepared["global_visibility_confirmed"])
        state = observations.load_json(f.state_path)
        self.assertIn("safety_publication", state)
        self.assertGreater(state["safety_publication"]["attribution"]["pid"], 0)
        self.assertFalse(gate.fetch_pilot_control(f, f.repository).paused)
        with self.assertRaises(ValueError):
            gate.confirm_safety_publication(f, f.state_path)
        f.publish("publish-pause")
        confirmed = gate.confirm_safety_publication(f, f.state_path)
        self.assertTrue(confirmed["global_visibility_confirmed"])
        self.assertTrue(confirmed["control"]["paused"])
        self.assertNotIn("safety_publication", observations.load_json(f.state_path))
        self.assertTrue(all(method == "GET" for method, _, _ in f.calls))

    def test_unavailable_publication_retains_an_actual_local_hold(self):
        f = self.fixture
        endpoint = api._query_endpoint(
            f.repository, "contents/" + reporter.DECISION_RECORD_PATH.as_posix(), [("ref", f.cause)])
        f.routes[endpoint] = api.MetadataEditError("control publication is unavailable")
        result = gate.pause_pilot(
            f, f.state_path, f.data, "test-escaped-defect", f.root, f.root, f.executor)
        self.assertFalse(result["prepared"])
        self.assertFalse(result["global_visibility_confirmed"])
        pending = observations.load_json(f.state_path)["safety_publication"]
        self.assertIsNone(pending["control"])
        self.assertEqual(pending["attribution"]["exit_code"], 0)

    def test_default_branch_is_never_a_publication_shortcut(self):
        f = self.fixture
        git(f.root, "checkout", "master")
        before = (f.root / reporter.DECISION_RECORD_PATH).read_bytes()
        result = gate.pause_pilot(
            f, f.state_path, f.data, "test-escaped-defect", f.root, f.root, f.executor)
        self.assertFalse(result["prepared"])
        self.assertIn("non-default branch", result["detail"])
        self.assertEqual((f.root / reporter.DECISION_RECORD_PATH).read_bytes(), before)
        self.assertIn("safety_publication", observations.load_json(f.state_path))

    def test_publication_schema_runtime_and_incident_binding(self):
        from jsonschema import Draft202012Validator, FormatChecker
        f = self.fixture
        gate.pause_pilot(f, f.state_path, f.data, "test-escaped-defect", f.root, f.root, f.executor)
        state = observations.load_json(f.state_path)
        schema = json.loads((Path(__file__).resolve().parents[3] /
                             "scripts/workflow_pilot/agent_handoff.schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(state)
        handoff.validate_state(state)
        for field, value in (("operation", "invented"), ("target_oid", "HEAD"),
                             ("disposition", "excluded"), ("extra", True)):
            changed = copy.deepcopy(state)
            changed["safety_publication"][field] = value
            self.assertFalse(validator.is_valid(changed))
            with self.assertRaises(ValueError):
                handoff.validate_state(changed)
        for field, value in (("pid", None), ("completed_at", None), ("exit_code", 7),
                             ("evidence_id", "unrelated"), ("contract", "git-diff-check")):
            changed = copy.deepcopy(state)
            changed["safety_publication"]["attribution"][field] = value
            self.assertFalse(validator.is_valid(changed))
            with self.assertRaises(ValueError):
                handoff.validate_state(changed)
        changed = copy.deepcopy(state)
        changed["safety_publication"]["event"]["sha"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "another incident head"):
            handoff.validate_state(changed)
        for value in ("@", "bad..ref", "/leading", "trailing/", "part/.hidden", "part.lock/child", "bad\nref"):
            changed = copy.deepcopy(state)
            changed["safety_publication"]["control"]["default_ref"] = value
            self.assertFalse(validator.is_valid(changed))
            with self.assertRaises(ValueError):
                handoff.validate_state(changed)

    def test_unpause_requires_real_recovery_and_keeps_visibility_pending_until_merge(self):
        f = self.fixture
        gate.pause_pilot(f, f.state_path, f.data, "test-escaped-defect", f.root, f.root, f.executor)
        f.publish("publish-pause")
        gate.confirm_safety_publication(f, f.state_path)
        git(f.root, "checkout", "-b", "repair")
        write_json(f.root / "incident.json", {"denominator": 1})
        git(f.root, "add", ".")
        git(f.root, "commit", "-m", "Fix the controlled semantic fault")
        git(f.root, "checkout", "master")
        git(f.root, "merge", "--no-ff", "repair", "-m", "Integrate controlled recovery")
        git(f.root, "push", "publication", "master")
        f.refresh()
        head = git(f.root, "rev-parse", "HEAD")
        f.master_evidence(head, success=True)
        git(f.root, "checkout", "-b", "publish-unpause")
        result = gate.unpause_pilot(
            f, f.state_path, f.data, "test-escaped-defect", f.root, f.root, f.executor,
            disposition="excluded", master_run=(900, 1))
        self.assertTrue(result["prepared"], result)
        self.assertTrue(gate.fetch_pilot_control(f, f.repository).paused)
        f.publish("publish-unpause")
        confirmed = gate.confirm_safety_publication(f, f.state_path)
        self.assertFalse(confirmed["control"]["paused"])
        self.assertNotIn("safety_publication", observations.load_json(f.state_path))

    def test_arbitrary_premerge_and_unreproduced_events_do_not_create_a_latch(self):
        f = self.fixture
        for variant in ("ordinary-event", "premerge-security", "failed-attribution"):
            with self.subTest(variant=variant):
                data = copy.deepcopy(f.data)
                event = data["events"][-1]
                executor = f.executor
                if variant == "ordinary-event":
                    event = next(item for item in data["events"] if item["type"] == "pilot_coordination")
                elif variant == "premerge-security":
                    event.update(type="security_finding", occurred_at="2026-01-01T06:30:00Z",
                                 sha=data["pull_requests"][0]["head_sha"])
                else:
                    def executor(context, head):
                        return raw_git.run_process(
                            ["/usr/bin/python3", "-I", "-B", "-c", "raise SystemExit(7)"],
                            cwd=f.root, env=raw_git.git_environment()), dict.fromkeys(handoff.METRICS)
                with self.assertRaises((ValueError, reporter.PilotDataError)):
                    gate.pause_pilot(f, f.state_path, data, event["id"], f.root, f.root, executor)
                self.assertNotIn("safety_publication", observations.load_json(f.state_path))

    def test_broken_master_refresh_rejects_other_event_branch_workflow_head_and_outcome(self):
        f = self.fixture
        data = copy.deepcopy(f.data)
        data["events"][-1]["type"] = "broken_master"
        original = f.master_evidence(f.cause, success=False, run_id=4)
        reported = data["workflow_runs"][3]
        original.update(created_at=reported["created_at"], run_started_at=reported["started_at"],
                        updated_at=reported["completed_at"])
        endpoint = api._endpoint(f.repository, "actions/runs/4/attempts/1")
        for field, value in (("event", "pull_request"), ("head_branch", "other"),
                             ("workflow_id", metadata.WORKFLOW_ID + 1),
                             ("head_sha", data["pull_requests"][0]["head_sha"]),
                             ("conclusion", "cancelled"), ("run_attempt", 2), ("id", True)):
            with self.subTest(field=field):
                f.routes[endpoint] = {**original, field: value}
                with self.assertRaises((ValueError, reporter.PilotDataError)):
                    gate.pause_pilot(
                        f, f.state_path, data, "test-escaped-defect", f.root, f.root, f.executor,
                        master_run=(4, 1))
                self.assertNotIn("safety_publication", observations.load_json(f.state_path))
        f.routes[endpoint] = original
        accepted = gate.pause_pilot(
            f, f.state_path, data, "test-escaped-defect", f.root, f.root, f.executor, master_run=(4, 1))
        self.assertTrue(accepted["prepared"], accepted)

    def test_producer_reader_and_locked_fallback_dispatch_use_the_same_unchanged_head(self):
        f = self.fixture
        git(f.root, "checkout", "-b", "candidate")
        (f.root / "docs").mkdir()
        (f.root / "docs" / "candidate.txt").write_text("Unchanged candidate\n")
        git(f.root, "add", ".")
        git(f.root, "commit", "-m", "Unmerged controlled candidate")
        head = git(f.root, "rev-parse", "HEAD")
        git(f.root, "push", "publication", "candidate")
        git(f.root, "checkout", "publish-pause")
        rows = []

        def run(number, *, preflight):
            raw, jobs = metadata._run(number, number, mode="full")
            jobs = [job for job in jobs if job["name"] != "patch-release"]
            at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            event = "pull_request" if preflight else "workflow_dispatch"
            raw.update(head_sha=head, head_branch="candidate", event=event,
                       path=api.WORKFLOW_PATH + ("@refs/pull/199/merge" if preflight else "@refs/heads/candidate"),
                       created_at=at, run_started_at=at, updated_at=at,
                       conclusion="failure" if preflight else "success",
                       pull_requests=[{"number": metadata.PR_NUMBER, "head": {"sha": head},
                                       "base": {"sha": f.cause}}] if preflight else [])
            for job in jobs:
                job.update(head_sha=head, head_branch="candidate", event=event,
                           created_at=at, started_at=at, completed_at=at)
                if job["name"] == "event-classifier":
                    job["name"] = "review-first-classifier" if preflight else "event-classifier"
                    job["steps"] = [{"name": gate.binding_name(metadata.PR_NUMBER, head, f.cause, "master"),
                                     "status": "completed", "conclusion": "success"}]
                if preflight and job["name"] in {"extended-host-tests", "legacy"}:
                    job.update(conclusion="skipped", runner_name=None, runner_id=None,
                               runner_group_id=None, runner_group_name=None)
                if preflight and job["name"] == "summary":
                    job["conclusion"] = "failure"
            return raw, jobs

        def refresh():
            f.refresh()
            current = git(f.bare, "rev-parse", "master")
            raw_pr = metadata._pr(head=head, base=current)
            raw_pr["head"]["ref"] = "candidate"
            raw_pr.update(additions=1, deletions=0)
            f.routes[metadata._endpoint(f"pulls/{metadata.PR_NUMBER}")] = raw_pr
            f.routes[metadata._endpoint("actions/workflows/build.yml")] = metadata._workflow()
            f.routes[metadata._query("actions/workflows/build.yml/runs",
                                    [("head_sha", head), ("per_page", "100"), ("page", "1")])] = {
                "total_count": len(rows), "workflow_runs": [row for row, _ in reversed(rows)]}
            for raw, jobs in rows:
                f.routes[metadata._endpoint(f"actions/runs/{raw['id']}")] = raw
                f.routes[metadata._query(f"actions/runs/{raw['id']}/attempts/1/jobs",
                                        [("per_page", "100"), ("page", "1")])] = {
                    "total_count": len(jobs), "jobs": jobs}
            return api._parse_pull_request_payload(raw_pr, f.repository, metadata.PR_NUMBER)

        rows.append(run(1, preflight=True))
        pr = refresh()
        initial = gate.fetch_decision(f, pr, 1)
        runs = api.list_candidate_runs(f, pr, include_dispatch=True)
        self.assertEqual((runs[0].mode, len(runs[0].jobs)), ("review-first", 8))
        with observations.locked_state(f.state_path) as state:
            record = gate.begin_observed_candidate(f, state, pr.number)
            self.assertEqual(record["decision_oid"], initial.decision_oid)
            self.assertEqual(record["mode"], "review-first")
        scope = frozenset({"TC-WORKFLOW-REVIEW-FAMILY-001/review-session"})
        session = review.ReviewSession("coordinator", "coordinator", scope, head,
                                       identity=(f.repository, pr.number, f.cause), owners=review.ReviewOwnership())
        runtime = Runtime(head, scope)
        runtime.result.started_at = observations.utc_now()
        session.begin(runtime, "reviewer")
        runtime.result.completed_at = observations.utc_now()
        session.finish(runtime)
        gate.pause_pilot(f, f.state_path, f.data, "test-escaped-defect", f.root, f.root, f.executor)
        f.publish("publish-pause")
        gate.confirm_safety_publication(f, f.state_path)
        pr = refresh()
        posts = []

        def dispatch(body):
            state = observations.load_json(f.state_path)
            record = state["candidates"][0]
            self.assertIsNotNone(record["dispatch_requested_at"])
            self.assertIsNone(record["dispatch_sent_at"])
            self.assertEqual(record["watermark"], 1)
            self.assertEqual(body, {"ref": "candidate"})
            posts.append(body)
            return metadata._response(None, status=204)

        f.dispatch_handler = dispatch

        def assess(state):
            current, lines = gate.fetch_candidate(f, f.repository, pr.number)
            decision = gate.fetch_decision(f, current, lines)
            observed = api.list_candidate_runs(f, current, include_dispatch=True)
            record = gate.find_candidate(state, (pr.number, head, f.cause, "master"))
            result = gate.assess_candidate(
                state, record, decision, current, session, (), (), (), observed, criteria_ready=False)
            self.assertFalse(result["merge_eligible"])
            return record, result, observed

        gate.dispatch_full(f, f.state_path, pr, assess)
        self.assertEqual(posts, [{"ref": "candidate"}])
        with self.assertRaises(ValueError):
            gate.dispatch_full(f, f.state_path, pr, assess)
        self.assertEqual(posts, [{"ref": "candidate"}])
        rows.append(run(2, preflight=False))
        pr = refresh()
        observed = gate.reconcile_full_dispatch(f, f.state_path, pr)
        self.assertEqual((observed["state"], observed["run_id"]), ("dispatch-observed", 2))
        state = observations.load_json(f.state_path)
        _, result, _ = assess(state)
        self.assertEqual(state["candidates"][0]["mode"], "review-first")
        self.assertFalse(result["merge_eligible"])
        self.assertIn("objective-or-manual-criteria", result["missing"])


if __name__ == "__main__":
    unittest.main()
