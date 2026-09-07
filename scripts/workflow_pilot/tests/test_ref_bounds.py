"""Real nested Git refs through the existing router/coordinator byte envelope."""

import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import quote

from jsonschema import Draft202012Validator

from scripts.workflow_pilot import adaptive_gate as gate, agent_handoff as handoff
from scripts.workflow_pilot import coordinator_observations as observations, event_classifier
from scripts.workflow_pilot import pr_metadata as github, raw_diff_check as raw, reporter
from scripts.workflow_pilot.tests import test_adaptive_gate as adapters
from scripts.workflow_pilot.tests.test_agent_handoff import at_offset, git, write_json


ROOT = Path(__file__).resolve().parents[3]


def nested_ref(size, unit="a"):
    width = len(unit.encode("utf-8"))
    parts = []
    while size > 120:
        component = unit * (120 // width)
        parts.append(component)
        size -= len(component.encode("utf-8")) + 1
    parts.append(unit * (size // width) + "a" * (size % width))
    return "/".join(parts)


class RefEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = adapters.DispatchBootstrapTests()
        self.bootstrap.setUp()
        self.addCleanup(self.bootstrap.doCleanups)

    def route(self, ref, *, head_ref=None, dispatch=False):
        b = self.bootstrap
        self.assertTrue(all(len(part.encode()) <= 120 for part in ref.split("/")))
        self.assertEqual(subprocess.run(
            ["/usr/bin/git", "check-ref-format", "refs/heads/" + ref],
            capture_output=True, check=False).returncode, 0)
        git(b.root, "branch", ref, b.base)
        if head_ref is not None:
            git(b.root, "branch", "-m", b.pr["head"]["ref"], head_ref)
            b.pr["head"]["ref"] = head_ref
        b.pr["base"]["ref"] = ref
        b.parent_pr["head"]["ref"] = ref
        b.environment.update(
            EVENT_NAME="pull_request", EVENT_REF=f"refs/pull/{b.m.PR_NUMBER}/merge",
            PR_NUMBER=str(b.m.PR_NUMBER), PR_NUMBER_JSON=str(b.m.PR_NUMBER),
            PR_HEAD_SHA=b.head, PR_HEAD_SHA_JSON=json.dumps(b.head),
            PR_BASE_SHA=b.base, PR_BASE_SHA_JSON=json.dumps(b.base),
            PR_BASE_REF=ref, PR_BASE_REF_JSON=json.dumps(ref),
            GITHUB_EVENT_NAME="pull_request", GITHUB_REF=f"refs/pull/{b.m.PR_NUMBER}/merge")
        event = {"action": "synchronize", "number": b.m.PR_NUMBER, "pull_request": b.pr}
        responses = {}
        if dispatch:
            event = {"inputs": {}}
            b.environment.update(EVENT_NAME="workflow_dispatch", GITHUB_EVENT_NAME="workflow_dispatch",
                                 EVENT_REF="refs/heads/" + b.pr["head"]["ref"],
                                 GITHUB_REF="refs/heads/" + b.pr["head"]["ref"])
            b.query["data"]["repository"]["pullRequests"]["nodes"][0].update(
                headRefName=b.pr["head"]["ref"], baseRefName=ref)
            responses[b.m._query("pulls", [("state", "open"), ("head", "owner:" + b.pr["head"]["ref"]),
                                           ("per_page", "100")])] = [b.pr]
        write_json(b.owned / "event.json", event)
        identity = b.identity()
        completed, values = b.classify(identity, responses)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.last_identity = identity
        return values

    def register(self, values):
        b = self.bootstrap
        pr = github._parse_pull_request_payload(b.pr, b.m.REPOSITORY, b.m.PR_NUMBER)
        decision = gate.select_mode(
            reporter.load_decisions_from_commit(b.root, b.head), number=pr.number,
            head_sha=b.head, decision_oid=values["decision_oid"], changed_lines=2)
        self.assertEqual(decision.mode, values["gate_mode"])
        state = handoff.new_state(pr.repository, "actual-coordinator", {
            "mode": "plan", "observed_at": at_offset(-1), "valid_until": at_offset(300),
            "autostop_enabled": None, "stop_on_disconnect": None,
            "plan": "Bounded native ref-envelope test; no host availability fiction.",
        })
        record = gate.begin_candidate(state, pr, b.base, decision)
        return state, record, pr

    def test_valid_257_byte_ref_can_register_after_actual_review_first_routing(self):
        ref = nested_ref(257)
        values = self.route(ref)
        self.assertEqual(len(ref.encode()), 257)
        self.assertEqual(values["classification"], "review-first")
        state, record, pr = self.register(values)
        self.assertIs(gate.find_candidate(state, gate.candidate_identity(record)), record)
        self.assertEqual(record["base_ref"], ref)
        self.assertEqual(pr.base_ref, ref)

    def test_ascii_and_multibyte_router_state_and_schema_share_byte_boundaries(self):
        schema = json.loads((ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=handoff.schema_format_checker())
        structural = Draft202012Validator(schema)
        self.boundary_evidence = []
        good = None
        for unit in ("a", "界"):
            for size in (256, 257, 1024, 1025):
                with self.subTest(unit=unit, bytes=size):
                    ref = nested_ref(size, unit)
                    self.assertEqual(len(ref.encode("utf-8")), size)
                    values = self.route(ref)
                    self.boundary_evidence.append({
                        "bytes": size, "characters": len(ref), "unit": unit,
                        "classification": values["classification"], "outputs": values})
                    if size <= event_classifier.MAX_BRANCH_REF_BYTES:
                        self.assertEqual(values["classification"], "review-first")
                        state, record, _ = self.register(values)
                        validator.validate(state)
                        restored = observations.parse_bytes(observations.json_bytes(state))
                        handoff.validate_state(restored)
                        self.assertEqual(restored["candidates"][0]["base_ref"], ref)
                        self.assertIs(gate.find_candidate(state, gate.candidate_identity(record)), record)
                        good = state
                    else:
                        self.assertEqual(values["classification"], "full")
                        self.assertNotIn("candidate_binding", values)
                        bad = copy.deepcopy(good)
                        bad["candidates"][0]["base_ref"] = ref
                        self.assertFalse(validator.is_valid(bad))
                        with self.assertRaises(ValueError):
                            handoff.validate_state(bad)
                        if unit != "a":
                            self.assertTrue(structural.is_valid(bad), "maxLength alone is not a byte assertion")

    def test_exact_unicode_limit_captures_native_local_proof_and_preserves_ref_identity(self):
        b = self.bootstrap
        ref = nested_ref(1024, "界")
        outputs = self.route(ref, head_ref=nested_ref(1024, "é"))
        state, record, pr = self.register(outputs)
        git(b.root, "checkout", pr.head_ref)
        checks = {
            "raw": {"contract": "git-diff-check", "evidence_id": "raw", "inputs": []},
            "decision": {"contract": "coordinator-check", "evidence_id": "decision", "inputs": []},
        }
        gate.register_local_validation(state, record, pr, b.root, checks)
        gate.capture_local_check(state, record, pr, "raw")

        def execute(context, head):
            self.assertEqual(head, b.head)
            program = (
                "import json,sys; from pathlib import Path; "
                "data=json.loads((Path(sys.argv[1])/'.github/workflow-pilot-decisions.json').read_text()); "
                "assert any(r['pull_request']==199 and r['stack']['depth']==1 for r in data['pull_requests'])")
            result = raw.run_process(["/usr/bin/python3", "-I", "-B", "-c", program,
                                      context["allowed_worktree"]], cwd=ROOT, env=raw.git_environment())
            return result, dict.fromkeys(handoff.METRICS)

        captured = gate.capture_local_check(state, record, pr, "decision", execute)
        self.assertEqual(captured["exit_code"], 0)
        self.assertGreater(captured["pid"], 0)
        self.assertGreater(captured["peak_rss_bytes"], 0)
        self.assertTrue(gate.coordinator_local_ready(state, record, pr))
        self.assertEqual(state["assignments"], [])
        wrong_ref = nested_ref(1024, "ø")
        wrong_state = copy.deepcopy(state)
        wrong_state["candidates"][0]["local_validation"]["base_ref"] = wrong_ref
        self.assertFalse(gate.coordinator_local_ready(wrong_state, wrong_state["candidates"][0], pr))
        validator = Draft202012Validator(
            json.loads((ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()),
            format_checker=handoff.schema_format_checker())
        validator.validate(state)
        run, jobs = b.m._run(10, 10, mode="full")
        run.update(head_sha=pr.head_sha, head_branch=pr.head_ref,
                   pull_requests=[{"number": pr.number, "head": {"sha": pr.head_sha},
                                   "base": {"sha": pr.base_sha}}])
        for job in jobs:
            job.update(head_sha=pr.head_sha, head_branch=pr.head_ref)
            if job["name"] == "event-classifier":
                job["steps"] = [{"name": outputs["candidate_binding"],
                                 "status": "completed", "conclusion": "success"}]
        client = b.m.ScriptedClient()
        b.m._add_snapshot(client, [(run, jobs)], copies=2)
        client.add("GET", b.m._endpoint(f"compare/{pr.base_sha}...{pr.head_sha}"),
                   {"base_commit": {"sha": pr.base_sha},
                    "merge_base_commit": {"sha": git(b.root, "merge-base", "--all", pr.base_sha, pr.head_sha)}})
        workflow = github._workflow_authority(client, pr)
        _, _, parsed = github._parse_run(client, pr, workflow, run)
        github.require_full_success(parsed)
        self.assertEqual(parsed.binding, "explicit-same")
        self.assertEqual((*parsed.candidate_binding, parsed.candidate_base_ref), gate.candidate_identity(record))
        for invalid in (nested_ref(1025), nested_ref(1025, "界"), "bad..ref", "@"):
            with self.assertRaisesRegex(github.MetadataEditError, "head branch"):
                github._parse_run(client, pr, workflow, {**run, "head_branch": invalid})
        git(b.root, "branch", wrong_ref, b.base)
        rebound = replace(pr, base_ref=wrong_ref)
        _, _, historical = github._parse_run(client, rebound, workflow, run)
        self.assertEqual(historical.binding, "explicit-other")
        new = gate.begin_candidate(state, rebound, b.base, gate.select_mode(
            reporter.load_decisions_from_commit(b.root, b.head), number=pr.number,
            head_sha=b.head, decision_oid=outputs["decision_oid"], changed_lines=2))
        self.assertEqual(gate._candidate_runs(state, new, rebound, (historical,)), ())
        self.assertFalse(gate.coordinator_local_ready(state, new, rebound))
        self.assertIs(gate.find_candidate(state, gate.candidate_identity(record)), record)
        self.assertIs(gate.find_candidate(state, gate.candidate_identity(new)), new)
        record.update(full_run_id=parsed.run_id, full_attempt=parsed.run_attempt)
        path = b.owned / "coordinator.json"
        write_json(path, state)
        with patch.object(observations, "github_run", side_effect=AssertionError("unrelated run lookup")):
            for wrong in (replace(parsed, candidate_base_ref=wrong_ref),
                          replace(parsed, run_attempt=parsed.run_attempt + 1)):
                with self.assertRaisesRegex(ValueError, "unrelated work"):
                    gate.cancel_abandoned(client, path, record, wrong)
        endpoint = b.m._endpoint(f"actions/runs/{parsed.run_id}/cancel")
        client.add("POST", endpoint, b.m._response(None, status=202))
        active = {"status": "in_progress", "workflow_id": parsed.workflow_id}
        with patch.object(observations, "github_run", return_value=active) as observed:
            self.assertEqual(gate.cancel_abandoned(client, path, record, parsed), active)
            observed.assert_called_once_with(pr.repository, parsed.run_id, parsed.run_attempt, b.head)
        for field in ("base_ref", "branch"):
            changed = copy.deepcopy(state)
            changed["candidates"][0]["local_validation"][field] = nested_ref(1025, "界")
            self.assertFalse(validator.is_valid(changed))
            with self.assertRaises(ValueError):
                handoff.validate_state(changed)
        self.local_evidence = {"record": record, "capture": captured,
                               "parsed_base_ref": parsed.candidate_base_ref, "run_binding": parsed.binding,
                               "rebound": new, "historical_binding": historical.binding}

    def test_dispatch_bootstrap_extracts_byte_bounded_head_and_base_refs(self):
        self.dispatch_evidence = []
        for unit in ("a", "界"):
            for side in ("base", "head"):
                for size in (1024, 1025):
                    with self.subTest(unit=unit, side=side, bytes=size):
                        prefix = side + "/" + ("ascii" if unit == "a" else "unicode") + "/"
                        ref = prefix + nested_ref(size - len(prefix), unit)
                        base_ref = ref if side == "base" else "base/" + prefix + str(size)
                        head_ref = ref if side == "head" else "dispatch/" + prefix + str(size)
                        values = self.route(base_ref, head_ref=head_ref, dispatch=True)
                        identity = self.last_identity
                        self.assertEqual(values["classification"], "full")
                        if size == 1024:
                            self.assertEqual(identity["classifier_ref"], self.bootstrap.base)
                            self.assertEqual(gate.binding_base_ref(values["candidate_binding"]), base_ref)
                            self.register(values)
                        else:
                            self.assertEqual(identity["classifier_ref"], "refs/heads/master")
                            self.assertEqual(identity["dispatch_pr_number"], "")
                            self.assertNotIn("candidate_binding", values)
                        self.dispatch_evidence.append({
                            "unit": unit, "side": side, "bytes": size, "identity": identity, "outputs": values})

    def test_invalid_live_or_event_ref_cannot_enter_review_first(self):
        b = self.bootstrap
        self.route("valid/base")
        original = copy.deepcopy(b.pr)
        for ref in (nested_ref(1025), nested_ref(1025, "界"), "bad..ref", "@"):
            for side in ("base", "head"):
                for source in ("live", "event"):
                    with self.subTest(ref=ref[:20], side=side, source=source):
                        b.pr = copy.deepcopy(original)
                        event_pr = copy.deepcopy(original)
                        (b.pr if source == "live" else event_pr)[side]["ref"] = ref
                        write_json(b.owned / "event.json", {
                            "action": "synchronize", "number": b.m.PR_NUMBER, "pull_request": event_pr})
                        completed, values = b.classify(b.identity())
                        self.assertEqual(completed.returncode == 0, source == "event" and side == "base")
                        self.assertNotIn("candidate_binding", values)
                        self.assertNotEqual(values.get("classification"), "review-first")

    def test_canonical_marker_and_step_envelope_includes_percent_expansion(self):
        b = self.bootstrap
        ref = nested_ref(1024, "é")
        self.route(ref)
        marker = gate.binding_name(2**63 - 1, b.head, b.base, ref)
        self.assertTrue(marker.isascii())
        self.assertEqual(len(marker.encode("ascii")), gate.MAX_BINDING_BYTES)
        self.assertEqual(gate.binding_base_ref(marker), ref)
        self.assertEqual(github._candidate_step_details({"steps": [{
            "name": marker, "status": "completed", "conclusion": "success"}]}),
            ((2**63 - 1, b.head, b.base), ref))
        step = next(step for step in b.t._step_blocks(b.jobs["event-classifier"])
                    if "        CANDIDATE_BINDING:" in step)
        steps = [step] + [b.t._step_blocks(b.jobs[job])[0] for job in ("host-tests", "build")]
        for step in steps:
            script = b.t._literal_run_script(step)
            for value, accepted in ((marker, True), (marker + "a", False)):
                completed = subprocess.run(["/bin/bash", "-e", "-o", "pipefail", "-c", script],
                                           env={"PATH": "/usr/bin:/bin", "CANDIDATE_BINDING": value,
                                                "PR_NUMBER": str(2**63 - 1), "PR_HEAD_SHA": b.head,
                                                "PR_BASE_SHA": b.base, "CLASSIFIED_HEAD": b.head,
                                                "CLASSIFIED_BASE": b.base, "DECISION_OID": "a" * 40},
                                           capture_output=True, timeout=10)
                self.assertEqual(completed.returncode == 0, accepted, completed.stderr)
        legacy = gate.binding_name(199, b.head, b.base)
        for malformed in (marker + "a", legacy + ":" + "x" * 1025,
                          gate.binding_name(199, b.head, b.base, ref) + "%C3%A9"):
            with self.assertRaises(github.MetadataEditError):
                github._candidate_step_details({"steps": [{
                    "name": malformed, "status": "completed", "conclusion": "success"}]})
        self.marker_evidence = {"raw_bytes": len(ref.encode()), "step_bytes": len(marker),
                                "maximum_step_bytes": gate.MAX_BINDING_BYTES}

    def test_rest_refs_and_assignment_schema_reject_overlong_or_malformed_utf8(self):
        from scripts.workflow_pilot.tests.test_agent_handoff import GitFixture
        fixture = GitFixture(assign=False)
        self.addCleanup(fixture.close)
        validator = Draft202012Validator(
            json.loads((ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()),
            format_checker=handoff.schema_format_checker())
        for ref, accepted in ((nested_ref(1024), True), (nested_ref(1024, "界"), True),
                              (nested_ref(1025), False), (nested_ref(1025, "界"), False),
                              ("bad..ref", False), ("@", False)):
            with self.subTest(ref=ref[:20], accepted=accepted):
                assignment = {**fixture.assignment, "expected_branch": ref}
                self.assertEqual(validator.is_valid(assignment), accepted)
                if accepted:
                    handoff.validate_assignment(assignment)
                else:
                    with self.assertRaises(ValueError):
                        handoff.validate_assignment(assignment)
                for part in ("base", "head"):
                    pr = copy.deepcopy(self.bootstrap.pr)
                    pr[part]["ref"] = ref
                    if accepted:
                        github._parse_pull_request_payload(pr, self.bootstrap.m.REPOSITORY,
                                                           self.bootstrap.m.PR_NUMBER)
                    else:
                        with self.assertRaises(github.MetadataEditError):
                            github._parse_pull_request_payload(pr, self.bootstrap.m.REPOSITORY,
                                                               self.bootstrap.m.PR_NUMBER)
                    m = self.bootstrap.m
                    state = github._parse_pull_request_payload(self.bootstrap.pr, m.REPOSITORY, m.PR_NUMBER)
                    client = m.ScriptedClient()
                    client.add("POST", "graphql", m._graphql_payload(pr, m._metadata_version()))
                    if accepted:
                        observed, _ = github._fetch_metadata_observation(client, state)
                        self.assertEqual(getattr(observed, part + "_ref"), ref)
                    else:
                        with self.assertRaisesRegex(github.MetadataEditError, part + " ref"):
                            github._fetch_metadata_observation(client, state)

    def test_inline_summary_reads_exact_utf8_limit_and_rejects_oversize_envelopes(self):
        b, t = self.bootstrap, self.bootstrap.t
        script = t._literal_run_script(t._step_blocks(b.jobs["summary"])[0])
        self.summary_evidence = []
        for raw_size, extra, same_ref, accepted in (
                (1024, "", True, True), (1025, "", True, False),
                (1024, "a", True, False), (1024, "", False, False)):
            with self.subTest(bytes=raw_size, extra=extra, same_ref=same_ref):
                ref = nested_ref(raw_size, "é")
                number = t.SUMMARY_TEST_PR_NUMBER
                marker = gate.binding_name(number, b.head, b.base) + ":" + quote(ref, safe="")
                if extra:
                    marker += "a" * (gate.MAX_BINDING_BYTES - len(marker) + 1)
                prior = t._summary_workflow_run(10, pr_number=number, head_sha=b.head, base_sha=b.base)
                current = t._summary_workflow_run(t.SUMMARY_TEST_RUN_ID, pr_number=number,
                                                 head_sha=b.head, base_sha=b.base)
                jobs = t._summary_full_jobs(pr_number=number, head_sha=b.head,
                                            base_sha=b.base, base_ref=nested_ref(1024, "é"))
                next(job for job in jobs if job["name"] == "event-classifier")["steps"][0]["name"] = marker
                routes = {
                    t._summary_runs_path(head_sha=b.head): t._summary_response(
                        t._summary_api_payload("workflow_runs", [current, prior])),
                    t._summary_jobs_path(10): t._summary_response(t._summary_api_payload("jobs", jobs)),
                    f"/repos/{t.SUMMARY_TEST_REPOSITORY}/compare/{b.base}...{b.head}": t._summary_response({
                        "base_commit": {"sha": b.base},
                        "merge_base_commit": {"sha": git(b.root, "merge-base", "--all", b.base, b.head)}}),
                }
                result, _ = t._run_summary_with_api(script, environment=t._summary_metadata_env(
                    PR_NUMBER=str(number), PR_HEAD_SHA=b.head, CLASSIFIED_BUILD_SHA=b.head,
                    PR_BASE_SHA=b.base, CLASSIFIED_BASE_SHA=b.base,
                    PR_BASE_REF=ref if same_ref else nested_ref(1024, "ø"),
                    FALLBACK_SHA=b.head), routes=routes)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
                if not accepted:
                    detail = ("oversize candidate step" if extra else "oversize candidate ref"
                              if same_ref else "requires a prior successful complete full Build CI run")
                    self.assertIn(detail, result.stderr)
                self.summary_evidence.append({"raw_bytes": raw_size, "step_bytes": len(marker),
                                              "same_ref": same_ref, "exit_code": result.returncode,
                                              "detail": result.stderr})
