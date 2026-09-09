from io import BytesIO
import copy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tarfile
import unittest
from unittest.mock import patch

from scripts.validation_ownership import ci_verifier, reporter
from scripts.validation_ownership.coordinator_capture import (
    CHECK_ID,
    VerifierExpectation,
    capture,
    qualify_reviewed_evolution,
    reviewed_evolution_context,
    reviewed_evolution_scope,
    trusted_executor,
)
from scripts.validation_ownership.budget import MakeProbeError
from scripts.workflow_pilot import adaptive_gate as gate, agent_handoff as handoff
from scripts.workflow_pilot import candidate_evidence, coordinator_observations as observations
from scripts.workflow_pilot import pr_metadata as github, raw_diff_check as raw, review_family as review
from scripts.workflow_pilot.tests.coordinator_support import at_offset, decisions, model_control
from scripts.workflow_pilot.tests.review_support import Runtime
from scripts.workflow_pilot.trusted_review_gate import GitTree, ReviewTools
from .report_fixture import ReportFixture, reviewed_evolution_case


class CoordinatorCaptureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ReportFixture()
        self.addCleanup(self.fixture.close)
        self.base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        self.trusted = self.fixture.directory / "trusted"
        self.trusted.mkdir()
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", self.base))) as archive:
            archive.extractall(self.trusted, filter="data")
        self.entry = {
            "assignment": {
                "allowed_worktree": str(self.fixture.root), "assigned_parent_sha": self.base,
                "max_lifetime_seconds": 300,
                "required_checks": {
                    CHECK_ID: {"contract": "coordinator-check", "evidence_id": "ownership", "inputs": []},
                },
            },
            "checks": [],
        }

    def expectation(self, head=None, **changes):
        values = {
            "repository_root": self.fixture.root, "trusted_root": self.trusted,
            "base_sha": self.base, "candidate_sha": head or self.base,
            "trusted_sha": self.base, "mode": "exact-base-pinned",
        }
        values.update(changes)
        return VerifierExpectation(**values)

    def test_actual_standalone_verifier_is_captured_independently(self):
        result = capture(self.entry, self.expectation())
        self.assertEqual(result["exit_code"], 0, result)
        self.assertGreater(result["pid"], 0)
        self.assertGreater(result["peak_rss_bytes"], 0)
        self.assertEqual(result["result_sha"], self.base)
        self.assertEqual(result["contract"], "coordinator-check")
        self.assertEqual(self.entry["checks"], [result])

    def test_candidate_checker_replacement_cannot_supply_the_capture(self):
        marker = self.fixture.root / "candidate-checker-ran"
        self.fixture.add("scripts/validation_ownership/ci_verifier.py",
                         f"open({str(marker)!r},'w').write('ran')\nprint('{{\"mode\":\"exact-base-pinned\"}}')\n")
        head = self.fixture.commit("Replace candidate checker")
        result = capture(self.entry, self.expectation(head))
        self.assertEqual(result["result_sha"], head)
        self.assertFalse(marker.exists())
        self.assertGreater(result["pid"], 0)

    def test_removed_candidate_invocation_does_not_suppress_independent_verifier(self):
        path = self.fixture.root / ".github/workflows/build.yml"
        text = path.read_text()
        start = text.index("    - name: Validate ownership with exact PR-base verifier\n")
        end = text.find("\n    - name:", start + 1)
        path.write_text(text[:start] + (text[end + 1:] if end >= 0 else ""))
        head = self.fixture.commit("Remove candidate verifier invocation")
        result = capture(self.entry, self.expectation(head))
        self.assertNotEqual(result["exit_code"], 0)
        self.assertGreater(result["pid"], 0)
        self.assertEqual(result["result_sha"], head)

    def test_wrong_base_and_head_cannot_supply_a_successful_capture(self):
        self.fixture.add("unrelated.txt", "later head\n")
        head = self.fixture.commit("Advance candidate")
        result = capture(self.entry, self.expectation(head, base_sha=head, trusted_sha=head))
        self.assertNotEqual(result["exit_code"], 0)
        with self.assertRaises(ValueError):
            capture(self.entry, self.expectation(self.base))

    def test_missing_definition_wrong_worktree_and_non_authoritative_mode_reject(self):
        del self.entry["assignment"]["required_checks"][CHECK_ID]
        with self.assertRaisesRegex(MakeProbeError, "check assignment"):
            capture(self.entry, self.expectation())
        with self.assertRaisesRegex(MakeProbeError, "non-authoritative"):
            self.expectation(mode="bootstrap-not-authoritative").validate()
        with self.assertRaisesRegex(MakeProbeError, "separate"):
            self.expectation(trusted_root=self.fixture.root).validate()
        self.assertEqual(self.entry["checks"], [])


class IntroductionCaptureTests(unittest.TestCase):
    def test_real_foundation_introduction_is_explicit_not_exact_base(self):
        fixture = ReportFixture(foundation_base=True)
        self.addCleanup(fixture.close)
        head = fixture.git("rev-parse", "HEAD").decode().strip()
        trusted = fixture.directory / "trusted"
        trusted.mkdir()
        with tarfile.open(fileobj=BytesIO(fixture.git("archive", head))) as archive:
            archive.extractall(trusted, filter="data")
        entry = {
            "assignment": {
                "allowed_worktree": str(fixture.root), "assigned_parent_sha": fixture.foundation_base,
                "max_lifetime_seconds": 300,
                "required_checks": {
                    CHECK_ID: {"contract": "coordinator-check", "evidence_id": "ownership", "inputs": []},
                },
            }, "checks": [],
        }
        expected = VerifierExpectation(fixture.root, trusted, fixture.foundation_base,
                                       head, head, "foundation-introduction")
        actual = capture(entry, expected)
        self.assertEqual(actual["exit_code"], 0, actual)
        self.assertEqual(actual["parent_sha"], fixture.foundation_base)


class ReviewedEvolutionCaptureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ReportFixture()
        self.addCleanup(self.fixture.close)
        self.case = reviewed_evolution_case(self.fixture)
        self.fixture.git("checkout", "-B", "candidate", self.case["head"])
        self.trusted = self.fixture.directory / "trusted-reviewed"
        self.trusted.mkdir()
        self.addCleanup(lambda: self.trusted.exists() and shutil.rmtree(self.trusted))
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", self.case["base"]))) as archive:
            archive.extractall(self.trusted, filter="data")

    def coordinator(self, *, head=None, paths=None, edges=None, consumers=None, session_changes=None):
        head = head or self.case["head"]
        paths = tuple(paths or self.case["reviewed_paths"])
        edges = tuple(edges or self.case["reviewed_edges"])
        consumers = tuple(consumers or self.case["affected_consumers"])
        state = handoff.new_state("owner/repository", "coordinator-one", {
            "mode": "plan", "observed_at": at_offset(-10),
            "valid_until": at_offset(300),
            "autostop_enabled": None, "stop_on_disconnect": None,
            "plan": "Exact reviewed evolution local check",
        })
        pr = type("PR", (), {
            "repository": "owner/repository",
            "repository_id": 1,
            "number": 186,
            "head_sha": head,
            "head_ref": "candidate",
            "base_sha": self.case["base"],
            "base_ref": "master",
        })()
        decision = gate.select_mode(
            decisions(number=186, risks=("lifecycle",), mode="review-first"),
            number=186,
            head_sha=pr.head_sha,
            decision_oid="a" * 40,
            changed_lines=10,
        )
        decision = model_control(decision, pr)
        record = gate.begin_candidate(state, pr, pr.base_sha, decision, runs=())
        checker_revision = self.case["base"]
        scope = reviewed_evolution_scope(checker_revision, paths, edges, consumers)
        owners = review.ReviewOwnership()
        session = review.ReviewSession(
            state["coordinator_id"],
            state["coordinator_id"],
            scope,
            head,
            identity=(pr.repository, pr.number, pr.base_sha),
            owners=owners,
        )
        runtime = Runtime(head, scope)
        runtime.result.task = "ownership-review-" + head[:12]
        runtime.result.owner = "independent-reviewer"
        runtime.result.files = len(paths) + 2
        for key, value in (session_changes or {}).items():
            setattr(runtime.result, key, value)
        context = reviewed_evolution_context(
            checker_revision, paths, edges, consumers, repository=pr.repository,
            pull_request=pr.number, base_sha=pr.base_sha, candidate_sha=head, worktree=self.fixture.root,
        )
        session.begin(runtime, "independent-reviewer", context=context)
        session.finish(runtime)
        tools = ReviewTools(GitTree(self.fixture.root, checker_revision), self.fixture.root)
        qualification = qualify_reviewed_evolution(
            state,
            record,
            pr,
            self.fixture.root,
            session,
            tools,
            checker_revision=checker_revision,
            changed_paths=paths,
            changed_edge_ids=edges,
            affected_consumers=consumers,
        )
        expected = VerifierExpectation(
            self.fixture.root,
            self.trusted,
            self.case["base"],
            head,
            checker_revision,
            "reviewed-evolution",
            qualification,
        )
        return state, record, pr, decision, session, qualification, expected

    def build_run(self, pr, number, mode):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        preflight = mode == "review-first"
        jobs = []
        for index, key in enumerate(sorted(candidate_evidence.KNOWN_JOB_IDS), 1):
            name = gate.PREFLIGHT_CLASSIFIER if preflight and key == "event-classifier" else key
            skipped = preflight and key in {"extended-host-tests", "legacy"}
            failure = preflight and key == "summary"
            jobs.append(github.JobState(
                number * 100 + index,
                number,
                name,
                "completed",
                "skipped" if skipped else "failure" if failure else "success",
                None if skipped else "fixture-runner",
                now,
                None if skipped else now,
                now,
            ))
        return github.RunState(
            number,
            77,
            number,
            1,
            pr.head_ref,
            now,
            now,
            now,
            "completed",
            "failure" if preflight else "success",
            "explicit-same",
            mode,
            tuple(jobs),
            event="pull_request" if preflight else "workflow_dispatch",
            head_sha=pr.head_sha,
            candidate_binding=(pr.number, pr.head_sha, pr.base_sha),
            candidate_base_ref=pr.base_ref,
        )

    def security(self, head):
        now = observations.utc_now()
        return tuple(
            gate.SecurityCheck(index, name, app, slug, head, "completed", "success", now, now)
            for index, (name, app, slug) in enumerate(sorted(gate.SECURITY_CHECKS), 1)
        )

    def test_actual_reviewed_evolution_capture_passes_and_defines_local_check(self):
        state, record, pr, _, _, qualification, expected = self.coordinator()
        qualification_record = qualification.record()
        self.assertEqual(qualification_record["checker_revision"], self.case["base"])
        self.assertNotIn("checker_objects", qualification_record)
        entry = {
            "assignment": {
                "repository": pr.repository,
                "pull_request": pr.number,
                "allowed_worktree": str(self.fixture.root),
                "assigned_parent_sha": self.case["base"],
                "max_lifetime_seconds": 300,
                "required_checks": {CHECK_ID: expected.check_definition()},
                "review_qualification": qualification_record,
            },
            "checks": [],
        }
        captured = capture(entry, expected)
        self.assertEqual(captured["exit_code"], 0, captured)
        self.assertEqual(captured["evidence_id"], expected.evidence_id())
        self.assertGreater(captured["pid"], 0)
        self.assertEqual(captured["parent_sha"], record["base_sha"])

    def test_explicit_review_context_is_delivered_before_qualification(self):
        started = []
        original = Runtime.start

        def observe(runtime, **arguments):
            started.append(copy.deepcopy(arguments))
            return original(runtime, **arguments)

        with patch.object(Runtime, "start", new=observe):
            state, record, pr, _, session, qualification, _ = self.coordinator()
        self.assertIn("context", started[0])
        context = started[0]["context"]
        self.assertEqual(context["changed_paths"], list(qualification.changed_paths))
        self.assertEqual(context["changed_edge_ids"], list(qualification.changed_edge_ids))
        self.assertEqual(context["affected_consumers"], list(qualification.affected_consumers))
        self.assertEqual(context["checker_revision"], qualification.checker_revision)
        self.assertEqual(context["repository"], pr.repository)
        self.assertEqual(context["candidate_sha"], pr.head_sha)
        self.assertEqual(context["worktree"], str(self.fixture.root))
        self.assertEqual(json.loads(session.lease.context), context)
        self.assertEqual(json.loads(session.report.context), context)
        qualification.validate_binding(state, record, pr)
        session.report = replace(session.report, context=None)
        with self.assertRaisesRegex(MakeProbeError, "explicit review context"):
            qualification.validate_binding(state, record, pr)

    def test_reviewed_capture_requires_exact_assignment_record(self):
        _, _, pr, _, _, qualification, expected = self.coordinator()
        for marker in ("missing", None, {}):
            entry = {"assignment": {
                "repository": pr.repository, "pull_request": pr.number,
                "allowed_worktree": str(self.fixture.root),
                "assigned_parent_sha": self.case["base"],
                "max_lifetime_seconds": 300,
                "required_checks": {CHECK_ID: expected.check_definition()},
            }, "checks": []}
            if marker != "missing":
                entry["assignment"]["review_qualification"] = marker
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(MakeProbeError, "assignment"):
                    capture(entry, expected)
                self.assertEqual(entry["checks"], [])

    def test_review_context_rejects_missing_or_changed_runtime_observation(self):
        original = Runtime.read
        for missing in (True, False):
            def observe(runtime, task):
                result = copy.copy(original(runtime, task))
                if missing:
                    del result.context
                else:
                    result.context = {**result.context, "changed_paths": ["wrong/path"]}
                return result
            with self.subTest(missing=missing), patch.object(Runtime, "read", new=observe):
                with self.assertRaisesRegex(ValueError, "context"):
                    self.coordinator()
        runtime = Runtime(self.case["head"], frozenset({"scope"}))
        session = review.ReviewSession("coordinator", "implementer", frozenset({"scope"}), self.case["head"])
        with self.assertRaisesRegex(ValueError, "request bound"):
            session.begin(runtime, "reviewer", context={"oversized": "x" * review.MAX_REQUEST_BYTES})
        self.assertEqual(runtime.calls, [])

    def test_local_validation_consumes_the_shared_reviewed_evolution_executor(self):
        state, record, pr, _, session, qualification, expected = self.coordinator()
        gate.register_local_validation(state, record, pr, self.fixture.root, {
            "raw": {"contract": "git-diff-check", "evidence_id": "raw", "inputs": []},
            CHECK_ID: expected.check_definition(),
        }, review_qualification=qualification.record())
        gate.capture_local_check(state, record, pr, "raw")
        check = gate.capture_local_check(state, record, pr, CHECK_ID, trusted_executor(expected))
        self.assertEqual(check["exit_code"], 0, check)
        self.assertFalse(gate.coordinator_local_ready(state, record, pr))
        self.assertTrue(gate.coordinator_local_ready(state, record, pr, qualification))
        session.advance("f" * 40)
        self.assertFalse(gate.coordinator_local_ready(state, record, pr, qualification))
        session.advance(pr.head_sha)
        record["local_validation"]["required_checks"][CHECK_ID]["evidence_id"] = "ownership-reviewed-stale"
        self.assertFalse(gate.coordinator_local_ready(state, record, pr, qualification))

    def test_missing_wrong_identity_partial_scope_and_unqualified_expectations_reject(self):
        state, record, pr, _, session, qualification, expected = self.coordinator()
        with self.assertRaisesRegex(MakeProbeError, "actual independent qualification"):
            VerifierExpectation(
                self.fixture.root,
                self.trusted,
                self.case["base"],
                self.case["head"],
                self.case["base"],
                "reviewed-evolution",
            ).validate()
        wrong_pr = type("PR", (), {
            "repository": "other/repository",
            "repository_id": pr.repository_id,
            "number": 999,
            "head_sha": pr.head_sha,
            "head_ref": pr.head_ref,
            "base_sha": pr.base_sha,
            "base_ref": pr.base_ref,
        })()
        with self.assertRaises((MakeProbeError, ValueError)):
            qualification.validate_binding(state, record, wrong_pr)
        partial = replace(
            qualification,
            affected_consumers=tuple(self.case["affected_consumers"][:-1]),
        )
        with self.assertRaisesRegex(MakeProbeError, "review observation"):
            partial.validate_binding(state, record, pr)
        self.assertEqual(expected.reviewed_evolution()["repository"], pr.repository)
        self.assertEqual(session.report.owner, "independent-reviewer")

    def test_reviewed_evolution_capture_remains_independent_of_candidate_pr_step_removal(self):
        path = self.fixture.root / ".github/workflows/build.yml"
        text = path.read_text()
        start = text.index("    - name: Validate ownership with exact PR-base verifier\n")
        end = text.find("\n    - name:", start + 1)
        path.write_text(text[:start] + (text[end + 1:] if end >= 0 else ""))
        head = self.fixture.commit("Remove candidate verifier invocation for reviewed evolution")
        trusted = self.fixture.directory / "trusted-reviewed-removed-step"
        trusted.mkdir()
        self.addCleanup(lambda: trusted.exists() and shutil.rmtree(trusted))
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", head))) as archive:
            archive.extractall(trusted, filter="data")
        paths = tuple(sorted((*self.case["reviewed_paths"], ".github/workflows/build.yml")))
        state, record, pr, _, _, qualification, _ = self.coordinator(head=head, paths=paths)
        updated = VerifierExpectation(
            self.fixture.root, self.trusted, self.case["base"], head, self.case["base"],
            "reviewed-evolution", qualification,
        )
        entry = {
            "assignment": {
                "repository": pr.repository,
                "pull_request": pr.number,
                "allowed_worktree": str(self.fixture.root),
                "assigned_parent_sha": self.case["base"],
                "max_lifetime_seconds": 300,
                "required_checks": {CHECK_ID: updated.check_definition()},
                "review_qualification": qualification.record(),
            },
            "checks": [],
        }
        captured = capture(entry, updated)
        self.assertNotEqual(captured["exit_code"], 0, captured)
        self.assertGreater(captured["pid"], 0)

    def test_candidate_counterfeit_checker_cannot_become_the_trusted_source(self):
        marker = self.fixture.root / "candidate-counterfeit-ran"
        self.fixture.add(
            "scripts/validation_ownership/ci_verifier.py",
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        )
        head = self.fixture.commit("Counterfeit candidate checker")
        paths = tuple(sorted((*self.case["reviewed_paths"], "scripts/validation_ownership/ci_verifier.py")))
        state, record, pr, _, _, qualification, expected = self.coordinator(head=head, paths=paths)
        entry = {
            "assignment": {
                "repository": pr.repository,
                "pull_request": pr.number,
                "allowed_worktree": str(self.fixture.root),
                "assigned_parent_sha": self.case["base"],
                "max_lifetime_seconds": 300,
                "required_checks": {CHECK_ID: expected.check_definition()},
                "review_qualification": qualification.record(),
            },
            "checks": [],
        }
        captured = capture(entry, expected)
        self.assertEqual(captured["exit_code"], 0, captured)
        self.assertFalse(marker.exists())

        copied = self.fixture.directory / "counterfeit-copy"
        copied.mkdir()
        self.addCleanup(lambda: copied.exists() and shutil.rmtree(copied))
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", head))) as archive:
            archive.extractall(copied, filter="data")
        with self.assertRaisesRegex(MakeProbeError, "differs from its qualification"):
            VerifierExpectation(
                self.fixture.root,
                copied,
                self.case["base"],
                head,
                head,
                "reviewed-evolution",
                qualification,
            ).validate()

    def test_actual_qualified_capture_controls_dispatch_and_final_admission(self):
        state, record, pr, decision, session, qualification, expected = self.coordinator()
        workflow = (self.fixture.root / reporter.BUILD_WORKFLOW_PATH).read_text()
        job_fields, role, step_fields = ci_verifier._base_step(workflow)
        job = dict(job_fields)
        step = dict(step_fields)
        self.assertEqual(role, "setup")
        self.assertIn("workflow_dispatch", job["if"])
        self.assertIn("classification == 'full'", step["if"])
        verifier_commands = [
            command
            for command in step["run"]
            if "/usr/bin/python3" in command and "--trusted-root" in command
        ]
        self.assertEqual(len(verifier_commands), 1)
        self.assertNotIn("--expected-mode", verifier_commands[0])
        self.assertNotIn("--trusted-sha", verifier_commands[0])
        self.assertFalse(any(str(argument).startswith("--reviewed-")
                             for argument in verifier_commands[0]))
        self.assertEqual(
            step["run"][1:5],
            (
                ("if", "[", "$BUILD_EVENT_NAME", "!=", "pull_request", "];", "then"),
                ("printf", "validation-ownership: exact-base verifier not applicable to %s\\n",
                 "$BUILD_EVENT_NAME"),
                ("exit", "0"),
                ("fi",),
            ),
        )

        strict_root = self.fixture.directory / "strict-pr-event-checker"
        strict_root.mkdir()
        self.addCleanup(lambda: strict_root.exists() and shutil.rmtree(strict_root))
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", self.case["base"]))) as archive:
            archive.extractall(strict_root, filter="data")
        strict = raw.run_process(
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                str(strict_root / "scripts/validation_ownership/ci_verifier.py"),
                "--trusted-root",
                str(strict_root),
                "--repository-root",
                str(self.fixture.root),
                "--base-sha",
                self.case["base"],
                "--candidate-sha",
                self.case["head"],
            ],
            cwd=strict_root,
            env=raw.git_environment(),
            timeout=180,
        )
        self.assertNotEqual(strict.returncode, 0)
        self.assertGreater(strict.pid, 0)
        self.assertGreater(strict.peak_rss_bytes, 0)
        self.assertIn(b"leaves graph surfaces unprobed", strict.stderr)

        gate.register_local_validation(state, record, pr, self.fixture.root, {
            "raw": {"contract": "git-diff-check", "evidence_id": "raw", "inputs": []},
            CHECK_ID: expected.check_definition(),
        }, review_qualification=qualification.record())
        gate.capture_local_check(state, record, pr, "raw")
        captured = gate.capture_local_check(
            state, record, pr, CHECK_ID, trusted_executor(expected),
        )
        self.assertEqual(captured["exit_code"], 0, captured)
        fact = review.ReviewFact(
            "review-clean",
            pr.head_sha,
            "BOT_kgDOCnlnWA",
            "APPROVED",
            at_offset(-1),
            "Complete exact-head review",
            (),
        )
        session.triage(review.Triage(fact, "clean"))
        checks = self.security(pr.head_sha)
        preflight = self.build_run(pr, 1, "review-first")
        tools = qualification.review_tools

        def observed(saved, runs, selected_qualification):
            with (
                patch.object(gate, "fetch_candidate", return_value=(pr, 10)),
                patch.object(gate, "fetch_decision", return_value=decision),
                patch.object(gate, "_review_snapshot",
                             return_value=((pr.base_sha, pr.head_sha), (fact,))),
                patch.object(gate, "security_checks", return_value=checks),
                patch.object(github, "list_candidate_runs", return_value=runs),
                patch.object(gate, "fetch_pilot_control", return_value=decision.control),
            ):
                return gate.assess_observed(
                    object(),
                    saved,
                    gate.find_candidate(saved, gate.candidate_identity(record)),
                    session,
                    tuple(session.rounds.events),
                    tools,
                    criteria_ready=True,
                    local_qualification=selected_qualification,
                )

        ready, actual_preflight = observed(state, (preflight,), qualification)
        self.assertTrue(ready["dispatchable"], ready)
        self.assertEqual(actual_preflight, (preflight,))
        without, _ = observed(state, (preflight,), None)
        self.assertFalse(without["dispatchable"])
        self.assertIn("exact-local-handoff", without["missing"])

        state_path = self.fixture.directory / "reviewed-evolution-state.json"
        state_path.write_bytes(observations.json_bytes(state))
        posts = []

        class Client:
            def request(self, method, endpoint, **kwargs):
                posts.append((method, endpoint, kwargs))

        def assess(saved):
            selected = gate.find_candidate(saved, gate.candidate_identity(record))
            current, actual_runs = observed(saved, (preflight,), qualification)
            return selected, current, actual_runs

        with (
            patch.object(gate, "frozen_base", return_value=pr.base_sha),
            patch.object(github, "fetch_pull_request", return_value=pr),
            patch.object(gate, "fetch_pilot_control", return_value=decision.control),
        ):
            dispatched = gate.dispatch_full(Client(), state_path, pr, assess)
        self.assertEqual(dispatched["state"], "dispatch-observation-pending")
        self.assertEqual(len(posts), 1)
        saved = observations.load_json(state_path)
        selected = gate.find_candidate(saved, gate.candidate_identity(record))
        full = self.build_run(pr, 2, "full")
        self.assertEqual(full.event, "workflow_dispatch")
        admitted, actual_full = observed(saved, (preflight, full), qualification)
        self.assertEqual(actual_full, (preflight, full))
        self.assertTrue(admitted["merge_eligible"], admitted)
        unqualified, _ = observed(saved, (preflight, full), None)
        self.assertFalse(unqualified["merge_eligible"])


if __name__ == "__main__":
    unittest.main()
