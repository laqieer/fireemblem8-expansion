import base64
import copy
import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import subprocess
import unittest
from itertools import count
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.workflow_pilot import reporter, review_family, trusted_review_gate


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT_PATH = FIXTURES / "review_family_default.json"
ADAPTER_PATH = FIXTURES / "review_family_github_adapter.json"
BASE = "853cff1eb7bdb3ecce46f780473e81be73e24315"
CANDIDATE = "a8768e4f467c36f8bec60ee823d7d1735d3fcd45"
KEY = b"test-only-external-receipt-key-material-32-bytes"
KEY_ID = "test-review-root"
KEY_EPOCH = 7


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def git(root, *arguments):
    return subprocess.run(
        reporter.git_command(root, *arguments),
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
    )


def review_report(
    base=BASE,
    candidate=CANDIDATE,
    reviewed_files=None,
    reviewed_changes=None,
):
    return {
        "schema_version": 2,
        "report_id": "INDEPENDENT_REPORT_001",
        "repository": "laqieer/fireemblem8-expansion",
        "pull_request": 179,
        "base_sha": base,
        "candidate_sha": candidate,
        "reviewer_actor_id": "ACTOR_PRE_REVIEWER_001",
        "reviewer_login": "fresh-pre-reviewer",
        "implementer_actor_id": "ACTOR_IMPLEMENTER_001",
        "implementer_login": "implementation-agent",
        "started_at": "2026-08-31T03:09:00Z",
        "completed_at": "2026-08-31T03:10:00Z",
        "permissions": ["contents:read"],
        "actions": ["read-candidate", "emit-local-report"],
        "reviewed_files": reviewed_files or ["changed.txt"],
        "reviewed_changes": reviewed_changes or [],
        "findings": [],
    }


def signed_receipt(payload, *, base=BASE, candidate=CANDIDATE, nonce="nonce-value-000001"):
    envelope = {
        "schema_version": 2,
        "repository": "laqieer/fireemblem8-expansion",
        "pull_request": 179,
        "base_sha": base,
        "candidate_sha": candidate,
        "issued_at": "2026-08-31T03:10:01Z",
        "expires_at": "2026-08-31T03:20:01Z",
        "nonce": nonce,
        "key_id": KEY_ID,
        "key_epoch": KEY_EPOCH,
        "purpose": trusted_review_gate.RECEIPT_PURPOSE,
        "payload_b64": base64.b64encode(payload).decode("ascii"),
    }
    envelope["hmac_sha256"] = hmac.new(
        KEY,
        trusted_review_gate.RECEIPT_DOMAIN
        + reporter.normalized_json(envelope),
        hashlib.sha256,
    ).hexdigest()
    return reporter.normalized_json(envelope)


class StaticAdapter:
    def __init__(self, *payloads):
        self.payloads = list(payloads)

    def fetch(self, repository, pull_request):
        if len(self.payloads) == 1:
            return copy.deepcopy(self.payloads[0])
        return copy.deepcopy(self.payloads.pop(0))


class TrustedGitHubGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        trusted_review_gate.reporter = reporter
        trusted_review_gate.review_family = review_family
        artifacts = ROOT / "build" / "test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        cls.repo = artifacts / f"trusted-review-checker-{os.getpid()}"
        cls.replay = artifacts / f"trusted-review-replay-{os.getpid()}"
        cls.trusted = artifacts / f"trusted-review-base-{os.getpid()}"
        cls.repo.mkdir()
        cls.replay.mkdir()
        cls.trusted.mkdir()
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
        for relative in (
            trusted_review_gate.BASE_CHECKER_PATH,
            trusted_review_gate.ASSERTION_PROGRAM_PATH,
            *trusted_review_gate.ASSERTION_SUBJECT_PATHS,
        ):
            target = cls.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        (cls.repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        (cls.repo / "changed.txt").write_text("base\n", encoding="utf-8")
        git(cls.repo, "add", ".")
        git(cls.repo, "commit", "-q", "-m", "independent trusted base")
        cls.base_sha = git(cls.repo, "rev-parse", "HEAD").stdout.decode().strip()
        (cls.repo / "changed.txt").write_text("candidate\n", encoding="utf-8")
        git(cls.repo, "add", "changed.txt")
        git(cls.repo, "commit", "-q", "-m", "untrusted candidate")
        cls.candidate_sha = (
            git(cls.repo, "rev-parse", "HEAD").stdout.decode().strip()
        )
        git(cls.trusted, "init", "-q")
        git(cls.trusted, "config", "user.email", "test@example.com")
        git(cls.trusted, "config", "user.name", "Trusted Base Test")
        for relative in (
            *trusted_review_gate.TRUSTED_REQUIRED_PATHS,
            *trusted_review_gate.ASSERTION_SUBJECT_PATHS,
        ):
            target = cls.trusted / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        git(cls.trusted, "add", ".")
        git(cls.trusted, "commit", "-q", "-m", "external independent base")
        cls.trusted_sha = (
            git(cls.trusted, "rev-parse", "HEAD").stdout.decode().strip()
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo)
        shutil.rmtree(cls.replay)
        shutil.rmtree(cls.trusted)

    def setUp(self):
        for path in self.replay.iterdir():
            path.unlink()

    def contract(self, *, base=BASE, candidate=CANDIDATE, kind="default"):
        path = (
            CONTRACT_PATH
            if kind == "default"
            else FIXTURES / "review_family_complete.json"
        )
        contract = reporter.load_json(path)
        contract["base_sha"] = base
        contract["original_pre_review_head"] = candidate
        contract["candidate_sha"] = candidate
        return contract

    def adapter(self):
        return reporter.load_json(ADAPTER_PATH)

    def test_candidate_package_has_no_credential_entrypoint_or_signer(self):
        self.assertIsNone(
            importlib.util.find_spec("scripts.workflow_pilot.github_review")
        )
        self.assertIsNone(
            importlib.util.find_spec(
                "scripts.workflow_pilot.isolated_review_gate"
            )
        )
        for public_name in (
            "make_signed_receipt_bytes",
            "sign_receipt",
            "receipt_seal",
        ):
            self.assertFalse(hasattr(trusted_review_gate, public_name))
        source = (
            ROOT / trusted_review_gate.TRUSTED_GATE_PATH
        ).read_text(encoding="utf-8")
        self.assertNotIn("sys.path.insert(0, str(candidate_root))", source)
        self.assertNotIn("--installation-mode", source)
        self.assertNotIn("WORKFLOW_REVIEW_EXTERNAL_INSTALLATION_ID", source)
        checker_source = (
            ROOT / trusted_review_gate.BASE_CHECKER_PATH
        ).read_text(encoding="utf-8")
        self.assertNotIn("def _member_observation", checker_source)
        self.assertNotIn('"callable"', checker_source)
        assertion_source = (
            ROOT / trusted_review_gate.ASSERTION_PROGRAM_PATH
        ).read_text(encoding="utf-8")
        for forbidden_import in (
            "import socket",
            "import subprocess",
            "import urllib",
            "import requests",
        ):
            self.assertNotIn(forbidden_import, assertion_source)
        with self.assertRaisesRegex(
            RuntimeError, "candidate checkout cannot be"
        ):
            trusted_review_gate._bind_trusted_modules(
                ROOT, ROOT, BASE
            )

    def test_graphql_query_uses_valid_actor_fragments_and_exact_base(self):
        query = trusted_review_gate.GRAPHQL_QUERY
        self.assertIn("baseRefOid", query)
        self.assertIn("headRefOid", query)
        self.assertGreaterEqual(query.count("... on Node { id }"), 5)
        self.assertNotIn("author { id login }", query)
        self.assertNotIn("owner { id login }", query)

    def run_trusted_startup(self):
        environment = {
            "HOME": str(self.trusted),
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        }
        return subprocess.run(
            (
                "/usr/bin/python3",
                "-I",
                str(self.trusted / trusted_review_gate.TRUSTED_GATE_PATH),
                "--trusted-root",
                str(self.trusted),
                "--candidate-root",
                str(self.repo),
                "--expected-base",
                self.trusted_sha,
                "--expected-candidate",
                self.candidate_sha,
                "--contract",
                str(CONTRACT_PATH),
                "--review-receipt",
                str(CONTRACT_PATH),
            ),
            cwd=self.trusted,
            env=environment,
            check=False,
            capture_output=True,
        )

    def test_trusted_startup_verifies_clean_full_import_graph_before_secrets(self):
        self.assertEqual(
            trusted_review_gate.TRUSTED_REQUIRED_PATHS,
            {
                "scripts/workflow_pilot/__init__.py",
                "scripts/workflow_pilot/trusted_review_gate.py",
                "scripts/workflow_pilot/reporter.py",
                "scripts/workflow_pilot/review_family.py",
                "scripts/workflow_pilot/review_base_checker.py",
                "scripts/workflow_pilot/review_assertions.py",
            },
        )
        clean = self.run_trusted_startup()
        self.assertEqual(clean.returncode, 2)
        self.assertIn(b"requires external key", clean.stderr)

        untracked = self.trusted / "untracked.py"
        untracked.write_text("raise SystemExit('candidate code ran')\n")
        try:
            dirty = self.run_trusted_startup()
            self.assertEqual(dirty.returncode, 2)
            self.assertIn(b"tracked, index, or untracked changes", dirty.stderr)
            self.assertNotIn(b"requires external key", dirty.stderr)
        finally:
            untracked.unlink()

        reporter_path = self.trusted / "scripts/workflow_pilot/reporter.py"
        original = reporter_path.read_bytes()
        reporter_path.write_bytes(original + b"\n# untrusted mutation\n")
        try:
            dirty = self.run_trusted_startup()
            self.assertEqual(dirty.returncode, 2)
            self.assertIn(b"tracked, index, or untracked changes", dirty.stderr)
            self.assertNotIn(b"requires external key", dirty.stderr)
        finally:
            reporter_path.write_bytes(original)

        reporter_path.write_bytes(original + b"\n# staged mutation\n")
        git(self.trusted, "add", "scripts/workflow_pilot/reporter.py")
        try:
            dirty = self.run_trusted_startup()
            self.assertEqual(dirty.returncode, 2)
            self.assertIn(b"tracked, index, or untracked changes", dirty.stderr)
            self.assertNotIn(b"requires external key", dirty.stderr)
        finally:
            reporter_path.write_bytes(original)
            git(self.trusted, "add", "scripts/workflow_pilot/reporter.py")

    def test_nullable_pushed_date_is_metadata_not_head_authority(self):
        contract = self.contract()
        envelope = json.loads(
            signed_receipt(reporter.normalized_json(review_report()))
        )
        evidence = json.loads(
            trusted_review_gate.collect_live_evidence_bytes(
                contract,
                ROOT,
                CANDIDATE,
                review_report(),
                envelope,
                [],
                adapter=StaticAdapter(self.adapter()),
                clock=lambda: datetime(
                    2026, 8, 31, 3, 13, tzinfo=timezone.utc
                ),
            )
        )
        self.assertEqual(evidence["candidate_advances"], [])
        self.assertEqual(evidence["force_push_events"], [])
        self.assertEqual(evidence["pull_request"]["base_sha"], BASE)

    def test_authoritative_graphql_base_must_match_exact_contract_base(self):
        payload = self.adapter()
        payload["data"]["repository"]["pullRequest"]["baseRefOid"] = "f" * 40
        contract = self.contract()
        envelope = json.loads(
            signed_receipt(reporter.normalized_json(review_report()))
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError, "authoritative PR base OID"
        ):
            trusted_review_gate.collect_live_evidence_bytes(
                contract,
                ROOT,
                CANDIDATE,
                review_report(),
                envelope,
                [],
                adapter=StaticAdapter(payload),
            )

    def test_incomplete_collection_and_changed_second_snapshot_fail(self):
        payload = self.adapter()
        payload["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"][
            "hasNextPage"
        ] = True
        contract = self.contract()
        envelope = json.loads(
            signed_receipt(reporter.normalized_json(review_report()))
        )
        with self.assertRaisesRegex(reporter.PilotDataError, "bounded complete"):
            trusted_review_gate.collect_live_evidence_bytes(
                contract,
                ROOT,
                CANDIDATE,
                review_report(),
                envelope,
                [],
                adapter=StaticAdapter(payload),
            )

        first = self.adapter()
        second = self.adapter()
        second["data"]["repository"]["pullRequest"]["headRefOid"] = "f" * 40
        first_bytes = reporter.normalized_json(first)
        second_bytes = reporter.normalized_json(second)
        self.assertNotEqual(
            hashlib.sha256(first_bytes).digest(),
            hashlib.sha256(second_bytes).digest(),
        )

    def test_external_receipt_scope_freshness_and_atomic_replay(self):
        payload = reporter.normalized_json(review_report())
        receipt = signed_receipt(payload)
        now = datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc)
        verified, envelope = trusted_review_gate._verify_signed_receipt_bytes(
            receipt,
            repository="laqieer/fireemblem8-expansion",
            pull_request=179,
            base_sha=BASE,
            candidate_sha=CANDIDATE,
            trusted_key_id=KEY_ID,
            trusted_key_epoch=KEY_EPOCH,
            trusted_key=KEY,
            current_time=now,
            replay_store=self.replay,
            consume_nonce=True,
        )
        self.assertEqual(verified, payload)
        self.assertEqual(envelope["base_sha"], BASE)
        preserved_payload, _ = (
            trusted_review_gate._verify_signed_receipt_bytes(
                receipt,
                repository="laqieer/fireemblem8-expansion",
                pull_request=179,
                base_sha=BASE,
                candidate_sha=CANDIDATE,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=now + timedelta(days=1),
                replay_store=self.replay,
                consume_nonce=False,
                require_current_time=False,
                require_preserved=True,
            )
        )
        self.assertEqual(preserved_payload, payload)
        with self.assertRaisesRegex(
            reporter.PilotDataError, "already consumed"
        ):
            trusted_review_gate._verify_signed_receipt_bytes(
                receipt,
                repository="laqieer/fireemblem8-expansion",
                pull_request=179,
                base_sha=BASE,
                candidate_sha=CANDIDATE,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=now,
                replay_store=self.replay,
                consume_nonce=True,
            )
        resigned = signed_receipt(
            payload, nonce="different-nonce-0002"
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError, "re-signed"
        ):
            trusted_review_gate._verify_signed_receipt_bytes(
                resigned,
                repository="laqieer/fireemblem8-expansion",
                pull_request=179,
                base_sha=BASE,
                candidate_sha=CANDIDATE,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=now,
                replay_store=self.replay,
                consume_nonce=True,
            )

        for label, overrides in (
            ("base", {"base_sha": "f" * 40}),
            ("head", {"candidate_sha": "f" * 40}),
            ("epoch", {"trusted_key_epoch": 8}),
        ):
            arguments = {
                "repository": "laqieer/fireemblem8-expansion",
                "pull_request": 179,
                "base_sha": BASE,
                "candidate_sha": CANDIDATE,
                "trusted_key_id": KEY_ID,
                "trusted_key_epoch": KEY_EPOCH,
                "trusted_key": KEY,
                "current_time": now,
                "replay_store": None,
                "consume_nonce": False,
            }
            arguments.update(overrides)
            with self.subTest(label=label), self.assertRaisesRegex(
                reporter.PilotDataError, "outside trusted scope"
            ):
                trusted_review_gate._verify_signed_receipt_bytes(
                    receipt, **arguments
                )

    def test_base_checker_executes_closed_registry_and_binds_receipt(self):
        contract = review_family.validate_contract(
            self.contract(base=self.base_sha, candidate=self.candidate_sha)
        )
        contract["trust_mode"] = "base-pinned"
        report = review_report(
            self.base_sha,
            self.candidate_sha,
            ["changed.txt"],
            review_family.derive_change_records(
                self.repo, self.base_sha, self.candidate_sha
            ),
        )
        remote_review = {
            "id": 1001,
            "node_id": "REMOTE_SYNTHETIC_1",
            "round": 1,
            "reviewer_actor_id": "COPILOT",
            "candidate_sha": self.candidate_sha,
            "submitted_at": "2026-08-31T04:02:00Z",
            "state": "COMMENTED",
            "body": "### 🟢 Approval recommended",
            "body_classification": "clean-approval",
            "body_has_findings": False,
            "outcome": "clean",
            "finding_ids": [],
        }
        evidence = {
            "remote_reviews": [remote_review],
            "findings": [],
            "pre_review_findings": [],
        }
        requests = review_family.build_assertion_requests(
            contract, evidence, self.candidate_sha, 1
        )
        times = iter(
            (
                datetime.now(timezone.utc),
                datetime.now(timezone.utc) + timedelta(seconds=1),
            )
        )
        receipt = trusted_review_gate.run_base_pinned_checker(
            self.repo,
            contract=contract,
            candidate_sha=self.candidate_sha,
            review_round=1,
            review_context=remote_review,
            all_remote_reviews=[remote_review],
            remote_findings=[],
            remote_finding_ids=[],
            original_review_report_bytes=reporter.normalized_json(report),
            original_receipt_sha256="9" * 64,
            assertion_requests=requests,
            trusted_key=KEY,
            clock=lambda: next(times),
        )
        self.assertEqual(receipt["result"], "pass")
        self.assertEqual(receipt["base_sha"], self.base_sha)
        self.assertEqual(receipt["candidate_sha"], self.candidate_sha)
        self.assertEqual(receipt["changed_files"], ["changed.txt"])
        expected_program_blob = git(
            self.repo,
            "rev-parse",
            f"{self.base_sha}:{trusted_review_gate.ASSERTION_PROGRAM_PATH}",
        ).stdout.decode().strip()
        self.assertEqual(
            receipt["assertion_program_blob_oid"], expected_program_blob
        )
        self.assertEqual(
            receipt["assertion_program_argv"],
            list(trusted_review_gate.ASSERTION_PROGRAM_ARGV),
        )
        expected_head_tree = git(
            self.repo, "rev-parse", f"{self.candidate_sha}^{{tree}}"
        ).stdout.decode().strip()
        self.assertEqual(
            len(receipt["assertion_results"]),
            len(review_family.REQUIRED_BEHAVIOR_ROWS)
            * len(review_family.EVIDENCE_CLASSES),
        )
        self.assertEqual(
            len(
                {
                    result["program_case"]
                    for result in receipt["assertion_results"]
                }
            ),
            len(receipt["assertion_results"]),
        )
        self.assertTrue(
            all(
                result["output"].get("rejection_observed")
                for result in receipt["assertion_results"]
                if ":adversarial:" in result["assertion_id"]
            )
        )
        self.assertTrue(
            all(
                result["program_blob_oid"] == expected_program_blob
                and result["program_exit_code"] == 0
                and result["authority_binding"]["finding_id"] is None
                and result["authority_binding"]["head_sha"] == self.candidate_sha
                and result["authority_binding"]["head_tree"] == expected_head_tree
                for result in receipt["assertion_results"]
            )
        )
        self.assertEqual(
            receipt["seal"],
            trusted_review_gate._execution_receipt_seal(receipt, KEY),
        )

    def test_checker_rejects_fabricated_result_id_and_dirty_candidate(self):
        contract = review_family.validate_contract(
            self.contract(base=self.base_sha, candidate=self.candidate_sha)
        )
        contract["trust_mode"] = "base-pinned"
        report = review_report(
            self.base_sha,
            self.candidate_sha,
            ["changed.txt"],
            review_family.derive_change_records(
                self.repo, self.base_sha, self.candidate_sha
            ),
        )
        remote_review = {
            "id": 1001,
            "node_id": "REMOTE_SYNTHETIC_1",
            "round": 1,
            "reviewer_actor_id": "COPILOT",
            "candidate_sha": self.candidate_sha,
            "submitted_at": "2026-08-31T04:02:00Z",
            "state": "COMMENTED",
            "body": "### 🟢 Approval recommended",
            "body_classification": "clean-approval",
            "body_has_findings": False,
            "outcome": "clean",
            "finding_ids": [],
        }
        evidence = {
            "remote_reviews": [remote_review],
            "findings": [],
            "pre_review_findings": [],
        }
        requests = review_family.build_assertion_requests(
            contract, evidence, self.candidate_sha, 1
        )
        requests[0]["assertion_id"] = "candidate-fabricated-pass"
        receipt = trusted_review_gate.run_base_pinned_checker(
            self.repo,
            contract=contract,
            candidate_sha=self.candidate_sha,
            review_round=1,
            review_context=remote_review,
            all_remote_reviews=[remote_review],
            remote_findings=[],
            remote_finding_ids=[],
            original_review_report_bytes=reporter.normalized_json(report),
            original_receipt_sha256="9" * 64,
            assertion_requests=requests,
            trusted_key=KEY,
        )
        self.assertEqual(receipt["result"], "fail")
        self.assertEqual(receipt["assertion_results"], [])

        dirty = self.repo / "dirty.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        try:
            with self.assertRaisesRegex(
                reporter.PilotDataError, "clean candidate worktree"
            ):
                trusted_review_gate.run_base_pinned_checker(
                    self.repo,
                    contract=contract,
                    candidate_sha=self.candidate_sha,
                    review_round=1,
                    review_context=remote_review,
                    all_remote_reviews=[remote_review],
                    remote_findings=[],
                    remote_finding_ids=[],
                    original_review_report_bytes=reporter.normalized_json(
                        report
                    ),
                    original_receipt_sha256="9" * 64,
                    assertion_requests=review_family.build_assertion_requests(
                        contract, evidence, self.candidate_sha, 1
                    ),
                    trusted_key=KEY,
                )
        finally:
            dirty.unlink()

    def test_introducing_pr_uses_actual_base_and_cannot_self_attest(self):
        self.assertFalse(
            trusted_review_gate._base_contains_gate(ROOT, BASE)
        )
        contract = self.contract()
        result = trusted_review_gate._bootstrap_result(
            contract, BASE, CANDIDATE
        )
        self.assertEqual(result["bootstrap"]["mode"], "introduction")
        self.assertTrue(
            result["bootstrap"]["external_coordinator_review_required"]
        )
        self.assertFalse(result["gates"]["trusted_push_allowed"])
        self.assertFalse(result["gates"]["merge_allowed"])

    def test_integrated_multi_head_lifecycle_preserves_original_receipt(self):
        repository = (
            ROOT
            / "build"
            / "test-artifacts"
            / f"multi-head-review-{os.getpid()}"
        )
        repository.mkdir()
        try:
            git(repository, "init", "-q")
            git(repository, "config", "user.email", "test@example.com")
            git(repository, "config", "user.name", "Multi Head Test")
            git(
                repository,
                "remote",
                "add",
                "origin",
                "https://github.com/laqieer/fireemblem8-expansion.git",
            )
            for relative in (
                *trusted_review_gate.TRUSTED_REQUIRED_PATHS,
                *trusted_review_gate.ASSERTION_SUBJECT_PATHS,
            ):
                target = repository / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            (repository / ".gitignore").write_text(
                "build/\n", encoding="utf-8"
            )
            (repository / "feature.txt").write_text("base\n", encoding="utf-8")

            def commit(message, timestamp):
                environment = reporter.git_environment(offline=True)
                environment.update(
                    {
                        "GIT_AUTHOR_DATE": timestamp,
                        "GIT_COMMITTER_DATE": timestamp,
                    }
                )
                subprocess.run(
                    reporter.git_command(repository, "add", "-A"),
                    env=environment,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    reporter.git_command(
                        repository, "commit", "-q", "-m", message
                    ),
                    env=environment,
                    check=True,
                    capture_output=True,
                )
                return git(repository, "rev-parse", "HEAD").stdout.decode().strip()

            base = commit("trusted base", "2026-08-31T03:00:00Z")
            commit_times = (
                "2026-08-31T03:01:00Z",
                "2026-08-31T03:12:00Z",
                "2026-08-31T03:14:00Z",
                "2026-08-31T03:16:00Z",
                "2026-08-31T03:18:00Z",
                "2026-08-31T03:20:00Z",
                "2026-08-31T03:22:00Z",
            )
            affected_sequence = (
                ("action", "actions"),
                ("generated", "owners"),
                ("lifecycle", "entries"),
                ("resource", "enabled"),
                ("wire", "producers"),
                ("action", "items"),
            )

            def set_subject_health(family, member, healthy):
                path = (
                    repository
                    / "scripts/workflow_pilot/assertion_subjects"
                    / f"{family}_{member.replace('-', '_')}.json"
                )
                subject = json.loads(
                    (
                        ROOT
                        / "scripts/workflow_pilot/assertion_subjects"
                        / f"{family}_{member.replace('-', '_')}.json"
                    ).read_text(encoding="utf-8")
                )
                if not healthy:
                    field = next(iter(subject["payload"]))
                    subject["payload"][field] = None
                path.write_bytes(reporter.normalized_json(subject))

            heads = []
            for index, timestamp in enumerate(commit_times, 1):
                if index > 1:
                    previous_family, previous_member = affected_sequence[
                        index - 2
                    ]
                    set_subject_health(
                        previous_family, previous_member, True
                    )
                if index <= len(affected_sequence):
                    family, member = affected_sequence[index - 1]
                    set_subject_health(family, member, False)
                (repository / "feature.txt").write_text(
                    f"head-{index}\n", encoding="utf-8"
                )
                heads.append(commit(f"head {index}", timestamp))

            contract = reporter.load_json(
                FIXTURES / "review_family_complete.json"
            )
            contract["base_sha"] = base
            contract["original_pre_review_head"] = heads[0]
            contract["candidate_sha"] = heads[-1]
            contract["trust_mode"] = "base-pinned"
            source_sweeps = {}
            for sweep in contract["family_sweeps"]:
                members = {item["member"] for item in sweep["siblings"]}
                family = next(
                    family
                    for family, registered in review_family.FAMILY_MEMBERS.items()
                    if members == set(registered)
                )
                source_sweeps[family] = sweep
            contract["family_sweeps"] = []
            for round_number, (family, affected_member) in enumerate(
                affected_sequence, 1
            ):
                sweep = copy.deepcopy(source_sweeps[family])
                sweep["finding_id"] = f"FINDING_MULTI_{round_number}"
                for sibling in sweep["siblings"]:
                    outcome = (
                        "affected-fixed"
                        if sibling["member"] == affected_member
                        else "verified-unaffected"
                    )
                    sibling["result"] = outcome
                    sibling["assertion_id"] = (
                        review_family.member_assertion_id(
                            family, sibling["member"], outcome
                        )
                    )
                contract["family_sweeps"].append(sweep)

            original_changes = review_family.derive_change_records(
                repository, base, heads[0]
            )
            original_files = sorted(
                {
                    path
                    for change in original_changes
                    for path in (change["old_path"], change["new_path"])
                    if path is not None
                }
            )
            report = review_report(
                base,
                heads[0],
                original_files,
                original_changes,
            )
            payload = self.adapter()
            pr = payload["data"]["repository"]["pullRequest"]
            pr["createdAt"] = "2026-08-31T03:05:00Z"
            pr["baseRefOid"] = base
            pr["headRefOid"] = heads[-1]
            pr["commits"]["nodes"] = [
                {
                    "commit": {
                        "id": f"COMMIT_MULTI_{index}",
                        "oid": head,
                        "pushedDate": None,
                        "committedDate": commit_times[index - 1],
                    }
                }
                for index, head in enumerate(heads, 1)
            ]
            reviews = []
            threads = []
            review_times = (
                "2026-08-31T03:11:00Z",
                "2026-08-31T03:13:00Z",
                "2026-08-31T03:15:00Z",
                "2026-08-31T03:17:00Z",
                "2026-08-31T03:19:00Z",
                "2026-08-31T03:21:00Z",
                "2026-08-31T03:23:00Z",
            )
            for round_number, (head, submitted_at) in enumerate(
                zip(heads, review_times), 1
            ):
                finding_id = (
                    f"FINDING_MULTI_{round_number}"
                    if round_number <= 6
                    else None
                )
                comments = (
                    [
                        {
                            "id": finding_id,
                            "createdAt": iso(
                                datetime.fromisoformat(
                                    submitted_at.replace("Z", "+00:00")
                                )
                                - timedelta(seconds=30)
                            ),
                            "body": "member-specific finding",
                            "author": {
                                "id": "ACTOR_COPILOT_001",
                                "login": (
                                    "copilot-pull-request-reviewer[bot]"
                                ),
                            },
                        }
                    ]
                    if finding_id
                    else []
                )
                reviews.append(
                    {
                        "id": f"REMOTE_MULTI_{round_number}",
                        "databaseId": 5000 + round_number,
                        "state": "COMMENTED",
                        "submittedAt": submitted_at,
                        "body": (
                            "### 🟡 Changes recommended"
                            if finding_id
                            else "### 🟢 Approval recommended"
                        ),
                        "commit": {"oid": head},
                        "author": {
                            "id": "ACTOR_COPILOT_001",
                            "login": "copilot-pull-request-reviewer[bot]",
                        },
                        "comments": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": comments,
                        },
                    }
                )
                if finding_id:
                    threads.append(
                        {
                            "id": f"THREAD_MULTI_{round_number}",
                            "isResolved": True,
                            "comments": {
                                "pageInfo": {"hasNextPage": False},
                                "nodes": [
                                    {
                                        "id": finding_id,
                                        "createdAt": comments[0]["createdAt"],
                                        "author": comments[0]["author"],
                                        "pullRequestReview": {
                                            "id": f"REMOTE_MULTI_{round_number}"
                                        },
                                    }
                                ],
                            },
                        }
                    )
            pr["reviews"]["nodes"] = reviews
            pr["reviewThreads"]["nodes"] = threads
            pr["comments"]["nodes"] = [
                {
                    "id": "DISPOSITION_MULTI_3",
                    "createdAt": "2026-08-31T03:15:30Z",
                    "body": (
                        "workflow-review-family-disposition:v2 "
                        + json.dumps(
                            {
                                "held_round": 3,
                                "held_head_sha": heads[2],
                                "authorized_next_head_sha": heads[3],
                                "action": "redesign",
                            },
                            separators=(",", ":"),
                        )
                    ),
                    "author": {
                        "id": "ACTOR_COORDINATOR_001",
                        "login": "independent-coordinator",
                    },
                },
                {
                    "id": "DISPOSITION_MULTI_6",
                    "createdAt": "2026-08-31T03:21:30Z",
                    "body": (
                        "workflow-review-family-disposition:v2 "
                        + json.dumps(
                            {
                                "held_round": 6,
                                "held_head_sha": heads[5],
                                "authorized_next_head_sha": heads[6],
                                "action": "redesign",
                            },
                            separators=(",", ":"),
                        )
                    ),
                    "author": {
                        "id": "ACTOR_COORDINATOR_001",
                        "login": "independent-coordinator",
                    },
                },
            ]
            receipt = signed_receipt(
                reporter.normalized_json(report),
                base=base,
                candidate=heads[0],
                nonce="multi-head-receipt-0001",
            )
            envelope = json.loads(receipt)
            collected = json.loads(
                trusted_review_gate.collect_live_evidence_bytes(
                    contract,
                    repository,
                    heads[-1],
                    report,
                    envelope,
                    [],
                    adapter=StaticAdapter(payload),
                    clock=lambda: datetime(
                        2026, 8, 31, 3, 24, tzinfo=timezone.utc
                    ),
                )
            )
            self.assertEqual(
                collected["original_pre_review_head"], heads[0]
            )
            self.assertEqual(collected["candidate"]["sha"], heads[-1])
            self.assertEqual(
                collected["pre_reviews"][0]["candidate_sha"], heads[0]
            )

            ticks = count()

            def clock():
                return datetime(
                    2026, 8, 31, 3, 24, tzinfo=timezone.utc
                ) + timedelta(seconds=next(ticks))

            result = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repository,
                expected_candidate=heads[-1],
                expected_base=base,
                review_receipt_bytes=receipt,
                replay_store=self.replay,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=datetime(
                    2026, 8, 31, 4, 0, tzinfo=timezone.utc
                ),
                adapter=StaticAdapter(payload),
                clock=clock,
            )
            self.assertEqual(
                result["identity"]["original_pre_review_head"], heads[0]
            )
            self.assertEqual(result["identity"]["candidate_sha"], heads[-1])
            self.assertEqual(
                result["architecture_hold"]["consumed_disposition_ids"],
                ["DISPOSITION_MULTI_3", "DISPOSITION_MULTI_6"],
            )
            self.assertEqual(
                len(result["provenance"]["execution_receipt_seals"]), 7
            )
            self.assertTrue(result["gates"]["merge_allowed"])
            preserved_result = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repository,
                expected_candidate=heads[-1],
                expected_base=base,
                review_receipt_bytes=receipt,
                replay_store=self.replay,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=datetime(
                    2026, 9, 1, 4, 0, tzinfo=timezone.utc
                ),
                pre_review_state="preserved",
                adapter=StaticAdapter(payload),
                clock=clock,
            )
            self.assertTrue(preserved_result["gates"]["merge_allowed"])

            stale_payload = copy.deepcopy(payload)
            stale_payload["data"]["repository"]["pullRequest"]["reviews"][
                "nodes"
            ][-1]["commit"]["oid"] = heads[-2]
            stale_store = repository / "build" / "stale-replay"
            stale_store.mkdir(parents=True)
            stale_receipt = signed_receipt(
                reporter.normalized_json(report),
                base=base,
                candidate=heads[0],
                nonce="multi-head-stale-0002",
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "exact held round/head and next head|current exact|"
                "round 7 does not bind exact",
            ):
                trusted_review_gate._run_trusted_gate(
                    raw_contract=contract,
                    repository_root=repository,
                    expected_candidate=heads[-1],
                    expected_base=base,
                    review_receipt_bytes=stale_receipt,
                    replay_store=stale_store,
                    trusted_key_id=KEY_ID,
                    trusted_key_epoch=KEY_EPOCH,
                    trusted_key=KEY,
                    current_time=datetime(
                        2026, 8, 31, 4, 0, tzinfo=timezone.utc
                    ),
                    adapter=StaticAdapter(stale_payload),
                    clock=clock,
                )

            with self.assertRaisesRegex(
                reporter.PilotDataError, "already consumed"
            ):
                trusted_review_gate._run_trusted_gate(
                    raw_contract=contract,
                    repository_root=repository,
                    expected_candidate=heads[-1],
                    expected_base=base,
                    review_receipt_bytes=receipt,
                    replay_store=self.replay,
                    trusted_key_id=KEY_ID,
                    trusted_key_epoch=KEY_EPOCH,
                    trusted_key=KEY,
                    current_time=datetime(
                        2026, 8, 31, 4, 1, tzinfo=timezone.utc
                    ),
                    adapter=StaticAdapter(payload),
                    clock=clock,
                )
        finally:
            shutil.rmtree(repository)

    def test_fixture_query_shape_matches_actor_fragment_response(self):
        payload = self.adapter()
        pr = payload["data"]["repository"]["pullRequest"]
        self.assertIn("id", pr["author"])
        self.assertIn("id", payload["data"]["repository"]["owner"])
        self.assertIsNone(pr["commits"]["nodes"][0]["commit"]["pushedDate"])
        self.assertEqual(pr["baseRefOid"], BASE)


if __name__ == "__main__":
    unittest.main()
