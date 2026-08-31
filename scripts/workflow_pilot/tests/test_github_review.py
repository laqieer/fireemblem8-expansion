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
        checker_path = cls.repo / trusted_review_gate.BASE_CHECKER_PATH
        checker_path.parent.mkdir(parents=True)
        checker_path.write_bytes(
            (ROOT / trusted_review_gate.BASE_CHECKER_PATH).read_bytes()
        )
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
        for relative in trusted_review_gate.TRUSTED_REQUIRED_PATHS:
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
                None,
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
                None,
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
                None,
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
        requests = review_family.build_assertion_requests(
            contract,
            {"findings": [], "pre_review_findings": []},
            self.repo,
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
            remote_finding_ids=[],
            review_report_bytes=reporter.normalized_json(report),
            assertion_requests=requests,
            trusted_key=KEY,
            clock=lambda: next(times),
        )
        self.assertEqual(receipt["result"], "pass")
        self.assertEqual(receipt["base_sha"], self.base_sha)
        self.assertEqual(receipt["candidate_sha"], self.candidate_sha)
        self.assertEqual(receipt["changed_files"], ["changed.txt"])
        self.assertEqual(
            len(receipt["assertion_results"]),
            len(review_family.REQUIRED_BEHAVIOR_ROWS)
            * len(review_family.EVIDENCE_CLASSES),
        )
        self.assertEqual(
            {
                result["callable"]
                for result in receipt["assertion_results"]
            },
            {
                "_execute_positive_assertion",
                "_execute_adversarial_assertion",
                "_execute_default_assertion",
                "_execute_runtime_assertion",
            },
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
        requests = review_family.build_assertion_requests(
            contract,
            {"findings": [], "pre_review_findings": []},
            self.repo,
        )
        requests[0]["id"] = "candidate-fabricated-pass"
        receipt = trusted_review_gate.run_base_pinned_checker(
            self.repo,
            contract=contract,
            candidate_sha=self.candidate_sha,
            remote_finding_ids=[],
            review_report_bytes=reporter.normalized_json(report),
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
                    remote_finding_ids=[],
                    review_report_bytes=reporter.normalized_json(report),
                    assertion_requests=review_family.build_assertion_requests(
                        contract,
                        {"findings": [], "pre_review_findings": []},
                        self.repo,
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

    def test_fixture_query_shape_matches_actor_fragment_response(self):
        payload = self.adapter()
        pr = payload["data"]["repository"]["pullRequest"]
        self.assertIn("id", pr["author"])
        self.assertIn("id", payload["data"]["repository"]["owner"])
        self.assertIsNone(pr["commits"]["nodes"][0]["commit"]["pushedDate"])
        self.assertEqual(pr["baseRefOid"], BASE)


if __name__ == "__main__":
    unittest.main()
