from io import BytesIO
import json
from pathlib import Path
import tarfile
import unittest

from scripts.validation_ownership.coordinator_capture import CHECK_ID, VerifierExpectation, capture
from scripts.validation_ownership.budget import MakeProbeError
from .report_fixture import ReportFixture


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


if __name__ == "__main__":
    unittest.main()
