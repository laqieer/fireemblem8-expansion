import copy
import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import github_review, reporter, review_family


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT_PATH = FIXTURES / "review_family_default.json"
ADAPTER_PATH = FIXTURES / "review_family_github_adapter.json"
CHECKER_PARENT = "e88cd6215c625a7df2fafd51732a04c15f11c62d"
KEY = b"test-only-external-receipt-key-material-32-bytes"
KEY_ID = "test-review-root"
KEY_EPOCH = 7


def git(root, *arguments, environment=None):
    env = reporter.git_environment(offline=True)
    if environment:
        env.update(environment)
    return subprocess.run(
        reporter.git_command(root, *arguments),
        env=env,
        check=True,
        capture_output=True,
    )


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


class IsolatedGitHubGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifacts = ROOT / "build" / "test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        cls.repo = artifacts / f"isolated-review-gate-{os.getpid()}"
        cls.replay = artifacts / f"isolated-review-replay-{os.getpid()}"
        cls.repo.mkdir()
        cls.replay.mkdir()
        git(cls.repo, "init", "-q")
        git(cls.repo, "config", "user.email", "test@example.com")
        git(cls.repo, "config", "user.name", "Review Gate Test")
        git(
            cls.repo,
            "remote",
            "add",
            "origin",
            "https://github.com/laqieer/fireemblem8-expansion.git",
        )
        checker = reporter.run_git(
            ROOT,
            "show",
            f"{CHECKER_PARENT}:{github_review.BASE_CHECKER_PATH}",
        )
        checker_path = cls.repo / github_review.BASE_CHECKER_PATH
        checker_path.parent.mkdir(parents=True)
        checker_path.write_bytes(checker)
        result_path = (
            cls.repo / "scripts/workflow_pilot/tests/test_review_family.py"
        )
        result_path.parent.mkdir(parents=True)
        result_path.write_text("RESULT_SOURCE = True\n", encoding="utf-8")
        (cls.repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        (cls.repo / "changed.txt").write_text("base\n", encoding="utf-8")
        git(cls.repo, "add", ".")
        git(cls.repo, "commit", "-q", "-m", "base checker")
        cls.base_sha = git(cls.repo, "rev-parse", "HEAD").stdout.decode().strip()
        (cls.repo / "changed.txt").write_text("candidate\n", encoding="utf-8")
        git(cls.repo, "add", "changed.txt")
        git(cls.repo, "commit", "-q", "-m", "candidate")
        cls.candidate_sha = (
            git(cls.repo, "rev-parse", "HEAD").stdout.decode().strip()
        )
        cls.committed_at = reporter._load_git_commit_objects(
            cls.repo, (cls.candidate_sha,)
        )[cls.candidate_sha]["committed_at"]
        cls.now = cls.committed_at + timedelta(seconds=20)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo)
        shutil.rmtree(cls.replay)

    def setUp(self):
        for path in self.replay.iterdir():
            path.unlink()

    def contract(self):
        contract = reporter.load_json(CONTRACT_PATH)
        contract["candidate_sha"] = self.candidate_sha
        return contract

    def adapter(self, *, state="COMMENTED", body="", comments=None):
        payload = reporter.load_json(ADAPTER_PATH)
        pr = payload["data"]["repository"]["pullRequest"]
        pr["headRefOid"] = self.candidate_sha
        pr["createdAt"] = iso(self.committed_at + timedelta(seconds=2))
        commit = pr["commits"]["nodes"][0]["commit"]
        commit["id"] = "LIVE_CANDIDATE_ADVANCE"
        commit["oid"] = self.candidate_sha
        commit["committedDate"] = iso(self.committed_at)
        commit["pushedDate"] = iso(self.committed_at + timedelta(seconds=1))
        review = pr["reviews"]["nodes"][0]
        review["commit"]["oid"] = self.candidate_sha
        review["submittedAt"] = iso(
            self.committed_at + timedelta(seconds=10)
        )
        review["state"] = state
        review["body"] = body
        review["comments"]["nodes"] = comments or []
        return payload

    def review_report(self, finding_ids=None):
        return {
            "schema_version": 1,
            "report_id": "INDEPENDENT_REPORT_001",
            "repository": "laqieer/fireemblem8-expansion",
            "pull_request": 179,
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "reviewer_actor_id": "ACTOR_COLLECTOR_001",
            "reviewer_login": "fresh-collector",
            "implementer_actor_id": "ACTOR_IMPLEMENTER_001",
            "implementer_login": "implementation-agent",
            "started_at": iso(self.committed_at + timedelta(seconds=3)),
            "completed_at": iso(self.committed_at + timedelta(seconds=4)),
            "permissions": ["contents:read"],
            "actions": ["read-candidate", "emit-local-report"],
            "reviewed_files": ["changed.txt"],
            "finding_ids": finding_ids or [],
        }

    def signed_report(self, report=None, **overrides):
        report = report or self.review_report()
        arguments = {
            "repository": "laqieer/fireemblem8-expansion",
            "pull_request": 179,
            "candidate_sha": self.candidate_sha,
            "issued_at": iso(self.committed_at + timedelta(seconds=5)),
            "expires_at": iso(self.committed_at + timedelta(seconds=305)),
            "nonce": "review-receipt-nonce-0001",
            "key_id": KEY_ID,
            "key_epoch": KEY_EPOCH,
            "key": KEY,
        }
        arguments.update(overrides)
        return github_review.make_signed_receipt_bytes(
            reporter.normalized_json(report),
            **arguments,
        )

    def verify(self, receipt, *, replay_store=None, now=None, **overrides):
        arguments = {
            "repository": "laqieer/fireemblem8-expansion",
            "pull_request": 179,
            "candidate_sha": self.candidate_sha,
            "trusted_key_id": KEY_ID,
            "trusted_key_epoch": KEY_EPOCH,
            "trusted_key": KEY,
            "current_time": now or self.now,
            "replay_store": replay_store,
            "consume_nonce": replay_store is not None,
        }
        arguments.update(overrides)
        return github_review.verify_signed_receipt_bytes(receipt, **arguments)

    def test_receipt_binds_fresh_scope_epoch_purpose_and_canonical_bytes(self):
        receipt = self.signed_report()
        payload = self.verify(receipt, replay_store=self.replay)
        self.assertEqual(payload, reporter.normalized_json(self.review_report()))

        mutations = (
            ("repository", {"repository": "other/project"}),
            ("pull request", {"pull_request": 180}),
            ("candidate", {"candidate_sha": "f" * 40}),
            ("key ID", {"trusted_key_id": "other-root"}),
            ("key epoch", {"trusted_key_epoch": 8}),
        )
        for label, overrides in mutations:
            self.setUp()
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    reporter.PilotDataError, "outside trusted scope"
                ):
                    self.verify(
                        receipt,
                        replay_store=self.replay,
                        **overrides,
                    )

        noncanonical = receipt[:-1] + b" \n"
        with self.assertRaisesRegex(
            reporter.PilotDataError, "not canonical immutable bytes"
        ):
            self.verify(noncanonical)

        malformed_nonce = self.signed_report(nonce="short")
        with self.assertRaisesRegex(
            reporter.PilotDataError, "nonce is malformed"
        ):
            self.verify(malformed_nonce)

        wrong_purpose = json.loads(receipt)
        wrong_purpose["purpose"] = "other-purpose"
        with self.assertRaisesRegex(
            reporter.PilotDataError, "purpose is outside trusted scope"
        ):
            self.verify(reporter.normalized_json(wrong_purpose))

    def test_receipt_rejects_stale_future_long_lived_and_reused_nonce(self):
        stale = self.signed_report(
            issued_at="2020-01-01T00:00:00Z",
            expires_at="2020-01-01T00:05:00Z",
        )
        with self.assertRaisesRegex(reporter.PilotDataError, "stale"):
            self.verify(stale)

        future = self.signed_report(
            issued_at=iso(self.now + timedelta(seconds=10)),
            expires_at=iso(self.now + timedelta(seconds=100)),
        )
        with self.assertRaisesRegex(reporter.PilotDataError, "not yet valid"):
            self.verify(future)

        long_lived = self.signed_report(
            expires_at=iso(self.committed_at + timedelta(seconds=700))
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError, "lifetime exceeds"
        ):
            self.verify(long_lived)

        receipt = self.signed_report()
        self.verify(receipt, replay_store=self.replay)
        with self.assertRaisesRegex(reporter.PilotDataError, "already consumed"):
            self.verify(receipt, replay_store=self.replay)

    def test_receipt_requires_external_replay_and_rejects_mutation(self):
        receipt = self.signed_report()
        with self.assertRaisesRegex(
            reporter.PilotDataError, "requires replay authority"
        ):
            self.verify(
                receipt,
                replay_store=None,
                consume_nonce=True,
            )

        parsed = json.loads(receipt)
        parsed["candidate_sha"] = "f" * 40
        mutated = reporter.normalized_json(parsed)
        with self.assertRaisesRegex(
            reporter.PilotDataError, "outside trusted scope|signature"
        ):
            self.verify(mutated)

    def test_base_pinned_checker_binds_source_argv_diff_and_cleanliness(self):
        report_bytes = reporter.normalized_json(self.review_report())
        times = iter((self.now, self.now + timedelta(seconds=1)))
        receipt = github_review.run_base_pinned_checker(
            self.repo,
            repository="laqieer/fireemblem8-expansion",
            pull_request=179,
            base_sha=self.base_sha,
            candidate_sha=self.candidate_sha,
            github_finding_ids=[],
            review_report_bytes=report_bytes,
            clock=lambda: next(times),
        )
        self.assertEqual(receipt["result"], "pass")
        self.assertEqual(receipt["checker_path"], github_review.BASE_CHECKER_PATH)
        self.assertEqual(receipt["argv"], list(github_review.BASE_CHECKER_ARGV))
        self.assertEqual(receipt["changed_files"], ["changed.txt"])
        self.assertTrue(receipt["read_only"])
        self.assertTrue(receipt["pre_clean"])
        self.assertTrue(receipt["post_clean"])
        expected_blob = git(
            self.repo,
            "rev-parse",
            f"{self.base_sha}:{github_review.BASE_CHECKER_PATH}",
        ).stdout.decode().strip()
        self.assertEqual(receipt["checker_blob_oid"], expected_blob)

    def test_base_checker_rejects_incomplete_report_and_dirty_candidate(self):
        report = self.review_report()
        report["reviewed_files"] = []
        receipt = github_review.run_base_pinned_checker(
            self.repo,
            repository="laqieer/fireemblem8-expansion",
            pull_request=179,
            base_sha=self.base_sha,
            candidate_sha=self.candidate_sha,
            github_finding_ids=[],
            review_report_bytes=reporter.normalized_json(report),
        )
        self.assertEqual(receipt["result"], "fail")

        dirty = self.repo / "dirty.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        try:
            with self.assertRaisesRegex(
                reporter.PilotDataError, "clean candidate worktree"
            ):
                github_review.run_base_pinned_checker(
                    self.repo,
                    repository="laqieer/fireemblem8-expansion",
                    pull_request=179,
                    base_sha=self.base_sha,
                    candidate_sha=self.candidate_sha,
                    github_finding_ids=[],
                    review_report_bytes=reporter.normalized_json(
                        self.review_report()
                    ),
                )
        finally:
            dirty.unlink()

        artifact_root = self.repo / "build" / "test-artifacts"
        target = self.repo / "build" / "review-target"
        artifact_root.rmdir()
        target.mkdir()
        artifact_root.symlink_to(target, target_is_directory=True)
        try:
            with self.assertRaisesRegex(
                reporter.PilotDataError, "artifact root escapes"
            ):
                github_review.run_base_pinned_checker(
                    self.repo,
                    repository="laqieer/fireemblem8-expansion",
                    pull_request=179,
                    base_sha=self.base_sha,
                    candidate_sha=self.candidate_sha,
                    github_finding_ids=[],
                    review_report_bytes=reporter.normalized_json(
                        self.review_report()
                    ),
                )
        finally:
            artifact_root.unlink()
            target.rmdir()
            artifact_root.mkdir()

    def test_core_rejects_stale_wrong_result_check_sha_and_checker_receipts(self):
        report = self.review_report()
        execution = github_review.run_base_pinned_checker(
            self.repo,
            repository="laqieer/fireemblem8-expansion",
            pull_request=179,
            base_sha=self.base_sha,
            candidate_sha=self.candidate_sha,
            github_finding_ids=[],
            review_report_bytes=reporter.normalized_json(report),
            clock=lambda: self.now,
        )
        contract = self.contract()
        with mock.patch.object(
            github_review.GhApiAdapter,
            "fetch",
            return_value=self.adapter(),
        ):
            evidence_bytes = github_review.collect_live_evidence_bytes(
                contract,
                self.repo,
                self.candidate_sha,
                report,
                execution,
                clock=lambda: self.now,
            )
        evidence = json.loads(evidence_bytes)
        valid = review_family.build_report(
            contract, evidence_bytes, self.repo, self.candidate_sha
        )
        self.assertEqual(len(valid["provenance"]["execution_receipt_seals"]), 1)
        self.assertFalse(valid["gates"]["merge_allowed"])

        missing = copy.deepcopy(evidence)
        missing["execution_receipts"] = []
        missing_result = review_family.build_report(
            contract, missing, self.repo, self.candidate_sha
        )
        self.assertFalse(missing_result["gates"]["merge_allowed"])
        self.assertEqual(
            missing_result["provenance"]["execution_receipt_seals"], []
        )

        mutations = []
        wrong_check = copy.deepcopy(evidence)
        wrong_check["execution_receipts"][0]["check_id"] = "wrong-check"
        wrong_check["execution_receipts"][0]["seal"] = (
            github_review.receipt_seal(wrong_check["execution_receipts"][0])
        )
        mutations.append(("check_id", wrong_check, "must be one of"))

        wrong_result = copy.deepcopy(evidence)
        wrong_result["execution_receipts"][0]["exit_code"] = 1
        wrong_result["execution_receipts"][0]["result"] = "fail"
        wrong_result["execution_receipts"][0]["seal"] = (
            github_review.receipt_seal(wrong_result["execution_receipts"][0])
        )
        mutations.append(("result", wrong_result, "lacks a passing"))

        stale_sha = copy.deepcopy(evidence)
        stale_sha["execution_receipts"][0]["candidate_sha"] = "f" * 40
        stale_sha["execution_receipts"][0]["seal"] = (
            github_review.receipt_seal(stale_sha["execution_receipts"][0])
        )
        mutations.append(("candidate_sha", stale_sha, "does not exist"))

        wrong_checker = copy.deepcopy(evidence)
        wrong_checker["execution_receipts"][0]["checker_blob_oid"] = "f" * 40
        wrong_checker["execution_receipts"][0]["seal"] = (
            github_review.receipt_seal(wrong_checker["execution_receipts"][0])
        )
        mutations.append(
            ("checker_blob_oid", wrong_checker, "does not match")
        )

        for field, mutated, message in mutations:
            with self.subTest(field=field):
                with self.assertRaisesRegex(reporter.PilotDataError, message):
                    review_family.build_report(
                        contract,
                        mutated,
                        self.repo,
                        self.candidate_sha,
                    )

    def test_live_gate_core_revalidates_state_but_cannot_authorize_in_process(self):
        contract = self.contract()
        receipt = self.signed_report()
        payload = self.adapter()
        with mock.patch.object(
            github_review.GhApiAdapter,
            "fetch",
            side_effect=[copy.deepcopy(payload), copy.deepcopy(payload)],
        ) as fetch:
            result = github_review._run_isolated_live_gate(
                raw_contract=contract,
                repository_root=self.repo,
                expected_candidate=self.candidate_sha,
                base_sha=self.base_sha,
                review_receipt_bytes=receipt,
                replay_store=self.replay,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=self.now,
                clock=lambda: self.now,
            )
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(result["provenance"]["base_pinned_checker"])
        self.assertTrue(
            result["provenance"]["isolated_gate_evidence_complete"]
        )
        self.assertTrue(result["structural_eligibility"]["merge"])
        self.assertFalse(result["gates"]["trusted_push_allowed"])
        self.assertFalse(result["gates"]["merge_allowed"])

    def test_live_gate_rejects_state_change_between_collections(self):
        contract = self.contract()
        receipt = self.signed_report(nonce="review-receipt-nonce-0002")
        first = self.adapter()
        second = self.adapter(state="CHANGES_REQUESTED")
        with mock.patch.object(
            github_review.GhApiAdapter,
            "fetch",
            side_effect=[first, second],
        ):
            with self.assertRaisesRegex(
                reporter.PilotDataError, "state changed during gate"
            ):
                github_review._run_isolated_live_gate(
                    raw_contract=contract,
                    repository_root=self.repo,
                    expected_candidate=self.candidate_sha,
                    base_sha=self.base_sha,
                    review_receipt_bytes=receipt,
                    replay_store=self.replay,
                    trusted_key_id=KEY_ID,
                    trusted_key_epoch=KEY_EPOCH,
                    trusted_key=KEY,
                    current_time=self.now,
                    clock=lambda: self.now,
                )

    def test_changes_requested_and_body_findings_never_classify_clean(self):
        contract = self.contract()
        report = self.review_report()
        execution = {"placeholder": True}
        for state, body in (
            ("CHANGES_REQUESTED", ""),
            ("COMMENTED", "Please fix the lifecycle."),
        ):
            with self.subTest(state=state, body=body):
                with mock.patch.object(
                    github_review.GhApiAdapter,
                    "fetch",
                    return_value=self.adapter(state=state, body=body),
                ):
                    evidence = github_review.collect_live_evidence_bytes(
                        contract,
                        self.repo,
                        self.candidate_sha,
                        report,
                        execution,
                        clock=lambda: self.now,
                    )
                review = json.loads(evidence)["remote_reviews"][0]
                self.assertEqual(review["outcome"], "changes-requested")

    def test_no_module_level_trust_capability_exists(self):
        forbidden = (
            "TrustedEvidence",
            "TrustedCheckReceipt",
            "unwrap_evidence",
            "_TRUST_TOKEN",
        )
        for name in forbidden:
            with self.subTest(name=name):
                self.assertFalse(hasattr(github_review, name))

    def test_gate_launcher_requires_isolated_python_startup(self):
        launcher = (
            ROOT / "scripts/workflow_pilot/isolated_review_gate.py"
        )
        ordinary = subprocess.run(
            (sys.executable, str(launcher), "--help"),
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(ordinary.returncode, 2)
        self.assertIn(b"requires /usr/bin/python3 -I", ordinary.stderr)

        isolated = subprocess.run(
            ("/usr/bin/python3", "-I", str(launcher), "--help"),
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(isolated.returncode, 0)


if __name__ == "__main__":
    unittest.main()
