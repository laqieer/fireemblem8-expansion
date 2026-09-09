from io import BytesIO
import json
from pathlib import Path
import shutil
import tarfile
import unittest

from scripts.validation_ownership.coordinator_capture import CHECK_ID, VerifierExpectation, capture, trusted_executor
from scripts.validation_ownership.budget import MakeProbeError
from scripts.workflow_pilot import adaptive_gate as gate, agent_handoff as handoff
from scripts.workflow_pilot.tests.test_adaptive_gate import decisions, model_control
from scripts.workflow_pilot.tests.test_agent_handoff import at_offset
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
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", self.case["head"]))) as archive:
            archive.extractall(self.trusted, filter="data")

    def expectation(self, **changes):
        values = {
            "repository_root": self.fixture.root,
            "trusted_root": self.trusted,
            "base_sha": self.case["base"],
            "candidate_sha": self.case["head"],
            "trusted_sha": self.case["head"],
            "mode": "reviewed-evolution",
            "reviewed_repository": "owner/repository",
            "reviewed_pull_request": 186,
            "reviewed_paths": tuple(self.case["reviewed_paths"]),
            "reviewed_edge_ids": tuple(self.case["reviewed_edges"]),
        }
        values.update(changes)
        return VerifierExpectation(**values)

    def test_actual_reviewed_evolution_capture_passes_and_defines_local_check(self):
        expected = self.expectation()
        entry = {
            "assignment": {
                "allowed_worktree": str(self.fixture.root),
                "assigned_parent_sha": self.case["base"],
                "max_lifetime_seconds": 300,
                "required_checks": {CHECK_ID: expected.check_definition()},
            },
            "checks": [],
        }
        captured = capture(entry, expected)
        self.assertEqual(captured["exit_code"], 0, captured)
        self.assertEqual(captured["evidence_id"], expected.evidence_id())
        self.assertGreater(captured["pid"], 0)

    def test_local_validation_consumes_the_shared_reviewed_evolution_executor(self):
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
            "head_sha": self.case["head"],
            "head_ref": "candidate",
            "base_sha": self.case["base"],
            "base_ref": "master",
        })()
        decision = gate.select_mode(
            decisions(number=186), number=186, head_sha=pr.head_sha,
            decision_oid="a" * 40, changed_lines=10,
        )
        record = gate.begin_candidate(state, pr, pr.base_sha, model_control(decision, pr), runs=())
        expected = self.expectation()
        gate.register_local_validation(state, record, pr, self.fixture.root, {
            "raw": {"contract": "git-diff-check", "evidence_id": "raw", "inputs": []},
            CHECK_ID: expected.check_definition(),
        })
        gate.capture_local_check(state, record, pr, "raw")
        check = gate.capture_local_check(state, record, pr, CHECK_ID, trusted_executor(expected))
        self.assertEqual(check["exit_code"], 0, check)
        self.assertTrue(gate.coordinator_local_ready(state, record, pr))
        record["local_validation"]["required_checks"][CHECK_ID]["evidence_id"] = "ownership-reviewed-stale"
        self.assertFalse(gate.coordinator_local_ready(state, record, pr))

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
        updated = VerifierExpectation(
            self.fixture.root,
            trusted,
            self.case["base"],
            head,
            head,
            "reviewed-evolution",
            reviewed_repository="owner/repository",
            reviewed_pull_request=186,
            reviewed_paths=tuple(sorted((*self.case["reviewed_paths"], ".github/workflows/build.yml"))),
            reviewed_edge_ids=tuple(self.case["reviewed_edges"]),
        )
        entry = {
            "assignment": {
                "allowed_worktree": str(self.fixture.root),
                "assigned_parent_sha": self.case["base"],
                "max_lifetime_seconds": 300,
                "required_checks": {CHECK_ID: updated.check_definition()},
            },
            "checks": [],
        }
        captured = capture(entry, updated)
        self.assertNotEqual(captured["exit_code"], 0, captured)
        self.assertGreater(captured["pid"], 0)


if __name__ == "__main__":
    unittest.main()
