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
from unittest import mock

from scripts.workflow_pilot import reporter, review_family, trusted_review_gate


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT_PATH = FIXTURES / "review_family_default.json"
ADAPTER_PATH = FIXTURES / "review_family_github_adapter.json"
SYNTHETIC_PULL_REQUEST = 901
BASE = "853cff1eb7bdb3ecce46f780473e81be73e24315"
CANDIDATE = "a8768e4f467c36f8bec60ee823d7d1735d3fcd45"
ISSUE_179_URL = "https://github.com/laqieer/fireemblem8-expansion/issues/179"
KEY = b"test-only-external-receipt-key-material-32-bytes"
KEY_ID = "test-review-root"
KEY_EPOCH = 7
COPILOT_ACTOR_ID = review_family.COPILOT_GRAPHQL_NODE_ID


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def graphql_actor(type_name, actor_id, login):
    return {
        "__typename": type_name,
        "id": actor_id,
        "login": login,
    }


def copilot_graphql_actor():
    return graphql_actor(
        review_family.COPILOT_GRAPHQL_TYPE,
        COPILOT_ACTOR_ID,
        review_family.COPILOT_GRAPHQL_LOGIN,
    )


def human_graphql_actor():
    return graphql_actor("User", "ACTOR_HUMAN_001", "human-reviewer")


def lookalike_graphql_actor():
    return graphql_actor("Bot", "BOT_LOOKALIKE_001", "copilot-pull-request-reviewer-bot")


def trusted_comment_actor():
    return graphql_actor("User", "ACTOR_COLLECTOR_001", "fresh-collector")


def repository_identity():
    return {
        "id": "REPO_SYNTHETIC_901",
        "name": "laqieer/fireemblem8-expansion",
    }


def decision_record_entry(
    *,
    pull_request=SYNTHETIC_PULL_REQUEST,
    risks=("none",),
    triggers=("none",),
):
    return {
        "pull_request": pull_request,
        "risk_boundaries": list(risks),
        "threshold": {
            "triggers": list(triggers),
            "override_history": [],
        },
        "gate_mode": "concurrent",
        "stack": {
            "depth": 0,
            "parent_pr": None,
            "exception_reason": None,
        },
        "pilot": {
            "included": False,
            "disposition": "baseline-only",
        },
    }


def authoritative_decision_comment(
    *,
    base_sha,
    head_sha,
    candidate_sha=None,
    decision=None,
    comment_id="COMMENT_DECISION_001",
    created_at="2026-08-31T03:11:30Z",
    updated_at="2026-08-31T03:11:30Z",
    author=None,
):
    if decision is None: decision = decision_record_entry(risks=("lifecycle", "protocol"), triggers=("changed-files", "risk-boundary"))
    payload = {
        "repository_id": repository_identity()["id"],
        "repository": repository_identity()["name"],
        "pull_request": SYNTHETIC_PULL_REQUEST,
        "base_sha": base_sha,
        "original_pre_review_head": head_sha,
        "candidate_sha": head_sha if candidate_sha is None else candidate_sha,
        "decision": copy.deepcopy(decision),
    }
    return {
        "id": comment_id,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "body": trusted_review_gate.EXTERNAL_DECISION_COMMENT_PREFIX
        + reporter.normalized_json(payload).decode("ascii").rstrip("\n"),
        "author": copy.deepcopy(author) if author is not None else trusted_comment_actor(),
    }


def authoritative_family_comment(
    *,
    base_sha,
    original_head,
    review_id,
    candidate_sha,
    mappings,
    comment_id="COMMENT_CLASSIFICATION_001",
    created_at="2026-08-31T03:37:30Z",
    updated_at="2026-08-31T03:37:30Z",
    author=None,
):
    payload = {
        "repository_id": repository_identity()["id"],
        "repository": repository_identity()["name"],
        "pull_request": SYNTHETIC_PULL_REQUEST,
        "base_sha": base_sha,
        "original_pre_review_head": original_head,
        "review_id": review_id,
        "candidate_sha": candidate_sha,
        "findings": copy.deepcopy(mappings),
    }
    return {
        "id": comment_id,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "body": trusted_review_gate.EXTERNAL_CLASSIFICATION_COMMENT_PREFIX
        + reporter.normalized_json(payload).decode("ascii").rstrip("\n"),
        "author": copy.deepcopy(author) if author is not None else trusted_comment_actor(),
    }


def authoritative_disposition_comment(
    *,
    held_round,
    held_head_sha,
    authorized_next_head_sha,
    action="redesign",
    comment_id="COMMENT_DISPOSITION_001",
    created_at="2026-08-31T03:36:00Z",
    updated_at=None,
    author=None,
):
    if updated_at is None:
        updated_at = created_at
    payload = {
        "action": action,
        "authorized_next_head_sha": authorized_next_head_sha,
        "held_head_sha": held_head_sha,
        "held_round": held_round,
    }
    return {
        "id": comment_id,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "body": "workflow-review-family-disposition:v2 "
        + reporter.normalized_json(payload).decode("ascii").rstrip("\n"),
        "author": copy.deepcopy(author) if author is not None else trusted_comment_actor(),
    }


def git(root, *arguments):
    return subprocess.run(
        reporter.git_command(root, *arguments),
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
    )


def optional_file_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def write_optional_tree_file(root: Path, relative: str, payload: bytes | None) -> None:
    target = root / relative
    if payload is None:
        if target.exists():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


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
        "pull_request": SYNTHETIC_PULL_REQUEST,
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
        "pull_request": SYNTHETIC_PULL_REQUEST,
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
            *trusted_review_gate.ASSERTION_INPUT_PATHS,
        ):
            write_optional_tree_file(cls.repo, relative, optional_file_bytes(ROOT / relative))
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
            *trusted_review_gate.ASSERTION_INPUT_PATHS,
        ):
            write_optional_tree_file(cls.trusted, relative, optional_file_bytes(ROOT / relative))
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

    def exact_graphql_payload(self, *, with_finding=False):
        payload = self.adapter()
        payload["data"]["repository"]["id"] = repository_identity()["id"]
        payload["data"]["repository"]["nameWithOwner"] = repository_identity()["name"]
        pr = payload["data"]["repository"]["pullRequest"]
        pr["baseRefOid"] = self.base_sha
        pr["headRefOid"] = self.candidate_sha
        pr["commits"]["nodes"][0]["commit"]["oid"] = self.candidate_sha
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = git(
            self.repo, "show", "-s", "--format=%cI", self.candidate_sha
        ).stdout.decode().strip().replace("+00:00", "Z")
        pr["reviews"]["nodes"][0]["commit"]["oid"] = self.candidate_sha
        pr["comments"]["nodes"] = []
        if with_finding:
            pr["reviews"]["nodes"][0]["body"] = "### 🟡 Changes recommended"
            pr["reviews"]["nodes"][0]["comments"]["nodes"] = [
                {
                    "id": "FINDING_ACTION_001",
                    "createdAt": "2026-08-31T03:36:30Z",
                    "updatedAt": "2026-08-31T03:36:30Z",
                    "body": "member-specific finding",
                    "author": copilot_graphql_actor(),
                }
            ]
            pr["reviewThreads"]["nodes"] = [
                {
                    "id": "THREAD_LIVE_001",
                    "isResolved": False,
                    "comments": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "FINDING_ACTION_001",
                                "createdAt": "2026-08-31T03:36:30Z",
                                "author": copilot_graphql_actor(),
                                "pullRequestReview": {"id": "REMOTE_REVIEW_LIVE_001"},
                            }
                        ],
                    },
                }
            ]
            pr["comments"]["nodes"].append(
                authoritative_family_comment(
                    base_sha=self.base_sha,
                    original_head=self.candidate_sha,
                    review_id="REMOTE_REVIEW_LIVE_001",
                    candidate_sha=self.candidate_sha,
                    mappings=[
                        {
                            "finding_id": "FINDING_ACTION_001",
                            "family": "action",
                        }
                    ],
                )
            )
        return payload

    def exact_review_receipt_envelope(self):
        return json.loads(
            signed_receipt(
                reporter.normalized_json(
                    review_report(
                        self.base_sha,
                        self.candidate_sha,
                        ["changed.txt"],
                        review_family.derive_change_records(
                            self.repo, self.base_sha, self.candidate_sha
                        ),
                    )
                ),
                base=self.base_sha,
                candidate=self.candidate_sha,
            )
        )

    def collect_exact_live_evidence(self, payload, *, kind="default", clock=None):
        return trusted_review_gate.collect_live_evidence_bytes(
            self.contract(
                base=self.base_sha,
                candidate=self.candidate_sha,
                kind=kind,
            ),
            self.repo,
            self.candidate_sha,
            self.candidate_sha,
            review_report(
                self.base_sha,
                self.candidate_sha,
                ["changed.txt"],
                review_family.derive_change_records(
                    self.repo, self.base_sha, self.candidate_sha
                ),
            ),
            self.exact_review_receipt_envelope(),
            [],
            adapter=StaticAdapter(payload),
            clock=(
                clock
                if clock is not None
                else lambda: datetime(2026, 8, 31, 3, 13, tzinfo=timezone.utc)
            ),
        )

    def decision_entry(
        self,
        *,
        pull_request=SYNTHETIC_PULL_REQUEST,
        risks=("none",),
        triggers=("none",),
    ):
        return {
            "pull_request": pull_request,
            "risk_boundaries": list(risks),
            "threshold": {
                "triggers": list(triggers),
                "override_history": [],
            },
            "gate_mode": "concurrent",
            "stack": {
                "depth": 0,
                "parent_pr": None,
                "exception_reason": None,
            },
            "pilot": {
                "included": False,
                "disposition": "baseline-only",
            },
        }

    def write_decision_record(self, root, *entries):
        path = Path(root) / trusted_review_gate.DECISION_RECORD_PATH
        decisions = json.loads(
            (ROOT / trusted_review_gate.DECISION_RECORD_PATH).read_text(encoding="utf-8")
        )
        decisions["pull_requests"] = [
            entry for entry in decisions["pull_requests"] if entry["pull_request"] != SYNTHETIC_PULL_REQUEST
        ]
        decisions["pull_requests"].extend(copy.deepcopy(list(entries)))
        path.write_bytes(reporter.normalized_json(decisions))

    def load_decision_record(self, root):
        return json.loads(
            (Path(root) / trusted_review_gate.DECISION_RECORD_PATH).read_text(
                encoding="utf-8"
            )
        )

    def write_raw_decision_record(self, root, decisions):
        (Path(root) / trusted_review_gate.DECISION_RECORD_PATH).write_bytes(
            reporter.normalized_json(decisions)
        )

    def commit_all(self, root, message):
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", message)

    def commit_all_at(self, root, message, when):
        environment = reporter.git_environment(offline=True)
        environment.update(
            {
                "GIT_AUTHOR_DATE": when,
                "GIT_COMMITTER_DATE": when,
            }
        )
        subprocess.run(
            reporter.git_command(root, "add", "-A"),
            env=environment,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(root, "commit", "-q", "-m", message),
            env=environment,
            check=True,
            capture_output=True,
        )

    def decision_record_entry(self, decisions, pull_request=SYNTHETIC_PULL_REQUEST):
        return next(
            entry
            for entry in decisions["pull_requests"]
            if entry["pull_request"] == pull_request
        )

    def decision_record_path(self, root):
        return Path(root) / trusted_review_gate.DECISION_RECORD_PATH

    def replay_receipt_path(
        self,
        root,
        *,
        repository="laqieer/fireemblem8-expansion",
        pull_request=SYNTHETIC_PULL_REQUEST,
        base_sha=BASE,
        candidate_sha=CANDIDATE,
        key_id=KEY_ID,
        key_epoch=KEY_EPOCH,
    ):
        scope_id = hashlib.sha256(
            reporter.normalized_json(
                trusted_review_gate.receipt_scope(
                    repository,
                    pull_request,
                    base_sha,
                    candidate_sha,
                    key_id,
                    key_epoch,
                )
            )
        ).hexdigest()
        return Path(root) / f"original-{scope_id}"

    def patched_os_open_once(self, predicate, mutate):
        real_open = trusted_review_gate.os.open
        fired = False

        def wrapper(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal fired
            if not fired and predicate(path, dir_fd):
                mutate()
                fired = True
            if dir_fd is None:
                return real_open(path, flags, mode)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        return wrapper

    def temporary_repo(self, name):
        artifact_root = ROOT / "build" / "test-artifacts"
        suffix = len(list(artifact_root.glob(f"{name}-{os.getpid()}-*")))
        root = artifact_root / f"{name}-{os.getpid()}-{suffix}"
        root.mkdir(parents=True)
        git(root, "init", "-q")
        git(root, "config", "user.email", "test@example.com")
        git(root, "config", "user.name", "Trusted Gate Temp Test")
        return root

    def build_decision_repo(self, *entries):
        repo = self.temporary_repo("decision-record")
        git(
            repo,
            "remote",
            "add",
            "origin",
            "https://github.com/laqieer/fireemblem8-expansion.git",
        )
        for relative in trusted_review_gate.ASSERTION_INPUT_PATHS:
            write_optional_tree_file(repo, relative, optional_file_bytes(ROOT / relative))
        (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        (repo / "changed.txt").write_text("base\n", encoding="utf-8")
        self.write_decision_record(repo, *entries)
        environment = reporter.git_environment(offline=True)
        environment.update(
            {
                "GIT_AUTHOR_DATE": "2026-08-31T03:00:00Z",
                "GIT_COMMITTER_DATE": "2026-08-31T03:00:00Z",
            }
        )
        subprocess.run(
            reporter.git_command(repo, "add", "."),
            env=environment,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(repo, "commit", "-q", "-m", "base"),
            env=environment,
            check=True,
            capture_output=True,
        )
        base = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
        (repo / "changed.txt").write_text("candidate\n", encoding="utf-8")
        environment.update(
            {
                "GIT_AUTHOR_DATE": "2026-08-31T03:05:00Z",
                "GIT_COMMITTER_DATE": "2026-08-31T03:05:00Z",
            }
        )
        subprocess.run(
            reporter.git_command(repo, "add", "changed.txt"),
            env=environment,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(repo, "commit", "-q", "-m", "candidate"),
            env=environment,
            check=True,
            capture_output=True,
        )
        candidate = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
        return repo, base, candidate

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
        self.assertNotIn("CHILD_RUNNER", assertion_source)
        self.assertNotIn("ACTION_PROBE_RUNNER", assertion_source)
        self.assertNotIn("candidate-fabricated-pass", assertion_source)
        self.assertIn("def evaluate_member_dispatch(", assertion_source)
        for forbidden_import in (
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
        self.assertIn("nameWithOwner", query)
        self.assertIn("\n    id\n", query)
        self.assertEqual(query.count("updatedAt"), 2)
        top_level_comments = query.rsplit("      comments(first: 100) {", 1)[1]
        self.assertIn("\n          updatedAt\n", top_level_comments)
        self.assertGreaterEqual(query.count("__typename"), 7)
        self.assertGreaterEqual(query.count("... on Node { id }"), 6)
        self.assertNotIn("author { id login }", query)
        self.assertNotIn("owner { id login }", query)
        self.assertIn("author { __typename", query)

    def test_top_level_authority_comment_helpers_match_unedited_graphql_shape(self):
        comments = [
            authoritative_decision_comment(
                base_sha=self.base_sha,
                head_sha=self.candidate_sha,
            ),
            authoritative_family_comment(
                base_sha=self.base_sha,
                original_head=self.candidate_sha,
                review_id="REMOTE_REVIEW_LIVE_001",
                candidate_sha=self.candidate_sha,
                mappings=[
                    {
                        "finding_id": "FINDING_ACTION_001",
                        "family": "action",
                    }
                ],
            ),
        ]
        for comment in comments:
            self.assertEqual(
                sorted(comment),
                ["author", "body", "createdAt", "id", "updatedAt"],
            )
            self.assertEqual(comment["updatedAt"], comment["createdAt"])
        finding = self.exact_graphql_payload(with_finding=True)["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["comments"]["nodes"][0]
        self.assertEqual(sorted(finding), ["author", "body", "createdAt", "id", "updatedAt"])
        self.assertEqual(finding["updatedAt"], finding["createdAt"])

    def test_exact_actor_parser_supports_explicit_graphql_and_rest_shapes(self):
        graphql_actor = trusted_review_gate._actor(
            copilot_graphql_actor(), "GraphQL author"
        )
        self.assertEqual(
            graphql_actor,
            {
                "id": review_family.COPILOT_GRAPHQL_NODE_ID,
                "login": review_family.COPILOT_GRAPHQL_LOGIN,
                "kind": "bot",
                "source": review_family.GITHUB_GRAPHQL_ACTOR_SOURCE,
                "type": review_family.COPILOT_GRAPHQL_TYPE,
            },
        )
        rest_actor = trusted_review_gate._actor(
            {
                "type": review_family.COPILOT_REST_TYPE,
                "node_id": review_family.COPILOT_REST_NODE_ID,
                "id": review_family.COPILOT_REST_DATABASE_ID,
                "login": review_family.COPILOT_REST_LOGIN,
            },
            "REST author",
        )
        self.assertTrue(review_family.is_authoritative_copilot_actor(rest_actor))

    def test_collect_live_evidence_rejects_nonexact_copilot_review_and_finding_authors(self):
        finding_cases = (
            ("login-bracket", "login", review_family.COPILOT_REST_LOGIN),
            ("login-suffix", "login", "copilot-pull-request-reviewer-bot"),
            ("login-prefix", "login", "evil-copilot-pull-request-reviewer"),
            ("login-case", "login", "Copilot-Pull-Request-Reviewer"),
            ("login-unicode", "login", "copilot-pull-request-revi\u0435wer"),
            ("type-user", "__typename", "User"),
            ("type-organization", "__typename", "Organization"),
            ("type-enterprise-owner", "__typename", "EnterpriseOwner"),
            ("type-mannequin", "__typename", "Mannequin"),
            ("id-drift", "id", "BOT_kgDOCnlnWB"),
        )
        for case_name, field, value in finding_cases:
            payload = self.exact_graphql_payload(with_finding=True)
            for author in (
                payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["comments"]["nodes"][0]["author"],
                payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"]["nodes"][0]["author"],
            ):
                author[field] = value
            with self.subTest(case=f"finding-{case_name}"), self.assertRaisesRegex(
                reporter.PilotDataError,
                "exact authoritative GitHub Copilot Bot",
            ):
                self.collect_exact_live_evidence(payload, kind="complete")

        payload = self.exact_graphql_payload(with_finding=True)
        payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"]["nodes"][0]["author"][
            "id"
        ] = "BOT_kgDOCnlnWB"
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "thread author does not match the exact authoritative review actor",
        ):
            self.collect_exact_live_evidence(payload, kind="complete")

    def test_collect_live_evidence_filters_threads_to_exact_copilot_findings(self):
        payload = self.exact_graphql_payload(with_finding=True)
        pr = payload["data"]["repository"]["pullRequest"]
        pr["reviews"]["nodes"].insert(
            0,
            {
                "id": "REMOTE_HUMAN_001",
                "databaseId": 2999,
                "state": "COMMENTED",
                "submittedAt": "2026-08-31T03:36:00Z",
                "body": "### 🟡 Changes recommended",
                "commit": {"oid": self.candidate_sha},
                "author": human_graphql_actor(),
                "comments": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "id": "HUMAN_FINDING_001",
                            "createdAt": "2026-08-31T03:36:10Z",
                            "body": "human finding",
                            "author": human_graphql_actor(),
                        }
                    ],
                },
            },
        )
        pr["reviews"]["nodes"].insert(
            1,
            {
                "id": "REMOTE_LOOKALIKE_001",
                "databaseId": 3000,
                "state": "COMMENTED",
                "submittedAt": "2026-08-31T03:36:20Z",
                "body": "### 🟡 Changes recommended",
                "commit": {"oid": self.candidate_sha},
                "author": lookalike_graphql_actor(),
                "comments": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "id": "LOOKALIKE_FINDING_001",
                            "createdAt": "2026-08-31T03:36:21Z",
                            "body": "lookalike finding",
                            "author": lookalike_graphql_actor(),
                        }
                    ],
                },
            },
        )
        pr["reviewThreads"]["nodes"][0]["comments"]["nodes"].append(
            {
                "id": "HUMAN_REPLY_001",
                "createdAt": "2026-08-31T03:36:45Z",
                "author": graphql_actor("User", "ACTOR_HUMAN_REPLY_001", "human-reviewer"),
                "pullRequestReview": {"id": "REMOTE_REVIEW_LIVE_001"},
            }
        )
        pr["reviewThreads"]["nodes"].append(
            {
                "id": "THREAD_HUMAN_001",
                "isResolved": True,
                "comments": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "id": "HUMAN_FINDING_001",
                            "createdAt": "2026-08-31T03:36:31Z",
                            "author": graphql_actor("User", "ACTOR_HUMAN_001", "human-reviewer"),
                            "pullRequestReview": {"id": "REMOTE_HUMAN_001"},
                        }
                    ],
                },
            }
        )
        pr["reviewThreads"]["nodes"].append(
            {
                "id": "THREAD_LOOKALIKE_001",
                "isResolved": False,
                "comments": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "id": "LOOKALIKE_FINDING_001",
                            "createdAt": "2026-08-31T03:36:32Z",
                            "author": graphql_actor("Bot", "BOT_LOOKALIKE_001", "copilot-pull-request-reviewer-bot"),
                            "pullRequestReview": {"id": "REMOTE_LOOKALIKE_001"},
                        }
                    ],
                },
            }
        )
        pr["reviewThreads"]["nodes"].append(
            {
                "id": "THREAD_HUMAN_INLINE_001",
                "isResolved": False,
                "comments": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "id": "HUMAN_FINDING_001",
                            "createdAt": "2026-08-31T03:36:10Z",
                            "author": human_graphql_actor(),
                            "pullRequestReview": {"id": "REMOTE_HUMAN_001"},
                        }
                    ],
                },
            }
        )
        pr["reviewThreads"]["nodes"].append(
            {
                "id": "THREAD_LOOKALIKE_INLINE_001",
                "isResolved": False,
                "comments": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "id": "LOOKALIKE_FINDING_001",
                            "createdAt": "2026-08-31T03:36:21Z",
                            "author": lookalike_graphql_actor(),
                            "pullRequestReview": {"id": "REMOTE_LOOKALIKE_001"},
                        }
                    ],
                },
            }
        )
        evidence = json.loads(self.collect_exact_live_evidence(payload, kind="complete"))
        self.assertEqual(
            [review["node_id"] for review in evidence["remote_reviews"]],
            ["REMOTE_REVIEW_LIVE_001"],
        )
        self.assertEqual(
            [finding["node_id"] for finding in evidence["findings"]],
            ["FINDING_ACTION_001"],
        )
        self.assertEqual(evidence["findings"][0]["family"], "action")
        self.assertEqual(
            evidence["findings"][0]["authority_comment_id"],
            "COMMENT_CLASSIFICATION_001",
        )
        self.assertEqual(
            evidence["threads"],
            [
                {
                    "node_id": "THREAD_LIVE_001",
                    "finding_id": "FINDING_ACTION_001",
                    "is_resolved": False,
                }
            ],
        )

        payload = self.exact_graphql_payload()
        payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"] = [
            {
                "id": "THREAD_HUMAN_ONLY_001",
                "isResolved": False,
                "comments": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "id": "HUMAN_ONLY_FINDING_001",
                            "createdAt": "2026-08-31T03:36:31Z",
                            "author": graphql_actor("User", "ACTOR_HUMAN_002", "human-reviewer"),
                            "pullRequestReview": {"id": "REMOTE_HUMAN_002"},
                        }
                    ],
                },
            }
        ]
        evidence = json.loads(self.collect_exact_live_evidence(payload))
        self.assertEqual(evidence["threads"], [])
        self.assertEqual(evidence["findings"], [])

    def test_issue_comments_ignore_unrelated_edited_deleted_authors_but_reject_prefixed_null_authors(self):
        payload = self.exact_graphql_payload()
        payload["data"]["repository"]["pullRequest"]["comments"]["nodes"] = [
            {
                "id": "COMMENT_UNRELATED_NULL_001",
                "createdAt": "2026-08-31T03:36:00Z",
                "updatedAt": "2026-08-31T03:36:30Z",
                "body": "ordinary unrelated comment",
                "author": None,
            }
        ]
        evidence = json.loads(self.collect_exact_live_evidence(payload))
        self.assertEqual(evidence["authoritative_trigger"], None)

        payload = self.exact_graphql_payload()
        comment = authoritative_disposition_comment(
            held_round=3,
            held_head_sha=self.candidate_sha,
            authorized_next_head_sha="b" * 40,
            comment_id="COMMENT_DISPOSITION_NULL_001",
        )
        comment["author"] = None
        payload["data"]["repository"]["pullRequest"]["comments"]["nodes"] = [comment]
        with self.assertRaisesRegex(reporter.PilotDataError, "must be an object"):
            self.collect_exact_live_evidence(payload)

    def test_disposition_comments_require_unedited_canonical_exact_trusted_shape(self):
        payload = self.exact_graphql_payload()
        comment = authoritative_disposition_comment(
            held_round=3,
            held_head_sha=self.candidate_sha,
            authorized_next_head_sha="b" * 40,
            created_at="2026-08-31T03:36:00Z",
        )
        payload["data"]["repository"]["pullRequest"]["comments"]["nodes"] = [comment]
        evidence = json.loads(self.collect_exact_live_evidence(payload))
        self.assertEqual(
            evidence["architecture_dispositions"],
            [
                {
                    "node_id": "COMMENT_DISPOSITION_001",
                    "held_round": 3,
                    "held_head_sha": self.candidate_sha,
                    "authorized_next_head_sha": "b" * 40,
                    "actor_id": trusted_comment_actor()["id"],
                    "action": "redesign",
                    "occurred_at": "2026-08-31T03:36:00Z",
                }
            ],
        )

        prefix = "workflow-review-family-disposition:v2 "

        def duplicate_body():
            return (
                prefix
                + '{"action":"redesign","authorized_next_head_sha":"'
                + ("b" * 40)
                + '","held_head_sha":"'
                + self.candidate_sha
                + '","held_round":3,"action":"redesign"}'
            )

        def edited_body(current):
            current["body"] = (
                prefix
                + reporter.normalized_json(
                    {
                        "action": "decompose",
                        "authorized_next_head_sha": "b" * 40,
                        "held_head_sha": self.candidate_sha,
                        "held_round": 3,
                    }
                )
                .decode("ascii")
                .rstrip("\n")
            )
            current["updatedAt"] = "2026-08-31T03:36:30Z"

        cases = (
            ("missing-updatedAt", lambda current: current.pop("updatedAt"), "updatedAt"),
            (
                "edited-timestamp",
                lambda current: current.__setitem__("updatedAt", "2026-08-31T03:36:30Z"),
                "must not be edited",
            ),
            ("edited-body", edited_body, "must not be edited"),
            (
                "wrong-actor",
                lambda current: current.__setitem__("author", human_graphql_actor()),
                "exact trusted coordinator actor",
            ),
            (
                "noncanonical",
                lambda current: current.__setitem__(
                    "body",
                    prefix
                    + json.dumps(
                        json.loads(current["body"][len(prefix) :]),
                        indent=2,
                        sort_keys=True,
                    ),
                ),
                "canonical closed JSON",
            ),
            (
                "duplicate-key",
                lambda current: current.__setitem__("body", duplicate_body()),
                "duplicate JSON key",
            ),
            (
                "trailing-content",
                lambda current: current.__setitem__(
                    "body", current["body"] + "\ntrailing"
                ),
                "invalid JSON",
            ),
        )
        for case_name, mutate, pattern in cases:
            payload = self.exact_graphql_payload()
            current = authoritative_disposition_comment(
                held_round=3,
                held_head_sha=self.candidate_sha,
                authorized_next_head_sha="b" * 40,
                created_at="2026-08-31T03:36:00Z",
            )
            mutate(current)
            payload["data"]["repository"]["pullRequest"]["comments"]["nodes"] = [
                current
            ]
            with self.subTest(case=case_name), self.assertRaisesRegex(
                reporter.PilotDataError,
                pattern,
            ):
                self.collect_exact_live_evidence(payload)

    def test_authoritative_family_comment_requires_unedited_updated_at(self):
        for case_name, mutate, pattern in (
            ("missing", lambda comment: comment.pop("updatedAt"), "updatedAt"),
            ("edited", lambda comment: comment.__setitem__("updatedAt", "2026-08-31T03:37:31Z"), "must not be edited"),
            ("malformed", lambda comment: comment.__setitem__("updatedAt", "2026-08-31 03:37:30Z"), "RFC 3339 UTC timestamp"),
        ):
            payload = self.exact_graphql_payload(with_finding=True)
            comment = payload["data"]["repository"]["pullRequest"]["comments"]["nodes"][0]
            mutate(comment)
            with self.subTest(case=case_name), self.assertRaisesRegex(
                reporter.PilotDataError,
                pattern,
            ):
                self.collect_exact_live_evidence(payload, kind="complete")
        for case_name, mutate, pattern in (
            ("missing-finding", lambda comment: comment.pop("updatedAt"), "updatedAt"),
            ("edited-finding", lambda comment: comment.__setitem__("updatedAt", "2026-08-31T03:36:31Z"), "must not be edited"),
            ("malformed-finding", lambda comment: comment.__setitem__("updatedAt", "2026-08-31 03:36:30Z"), "RFC 3339 UTC timestamp"),
        ):
            payload = self.exact_graphql_payload(with_finding=True); mutate(payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["comments"]["nodes"][0])
            with self.subTest(case=case_name), self.assertRaisesRegex(reporter.PilotDataError, pattern): self.collect_exact_live_evidence(payload, kind="complete")

    def test_non_authoritative_reviews_do_not_satisfy_copilot_authority(self):
        for actor in (human_graphql_actor(), lookalike_graphql_actor()):
            payload = self.exact_graphql_payload(with_finding=True)
            review = payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]
            review["author"] = copy.deepcopy(actor)
            review["comments"]["nodes"][0]["author"] = copy.deepcopy(actor)
            payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0][
                "comments"
            ]["nodes"][0]["author"] = copy.deepcopy(actor)
            evidence = json.loads(self.collect_exact_live_evidence(payload))
            self.assertEqual(evidence["remote_reviews"], [])
            self.assertEqual(evidence["findings"], [])
            self.assertEqual(evidence["threads"], [])
            report = review_family.build_report(
                self.contract(base=self.base_sha, candidate=self.candidate_sha),
                evidence,
                self.repo,
                self.candidate_sha,
            )
            self.assertFalse(report["gates"]["current_candidate_reviewed"])
            self.assertTrue(report["gates"]["remote_copilot_review_required"])

    def test_collect_live_evidence_rejects_missing_or_mismatched_copilot_threads(self):
        payload = self.exact_graphql_payload(with_finding=True)
        payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"] = []
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "remote finding 'FINDING_ACTION_001' has no review thread",
        ):
            self.collect_exact_live_evidence(payload, kind="complete")

        payload = self.exact_graphql_payload(with_finding=True)
        payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"]["nodes"][0]["pullRequestReview"]["id"] = (
            "REMOTE_REVIEW_OTHER_001"
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "review thread does not match its exact authoritative review",
        ):
            self.collect_exact_live_evidence(payload, kind="complete")

        payload = self.exact_graphql_payload(with_finding=True)
        payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"]["nodes"][0]["createdAt"] = (
            "2026-08-31T03:36:31Z"
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "review thread root does not preserve its exact authoritative chronology",
        ):
            self.collect_exact_live_evidence(payload, kind="complete")

        payload = self.exact_graphql_payload(with_finding=True)
        payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"].append(
            copy.deepcopy(
                payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]
            )
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "duplicate review threads",
        ):
            self.collect_exact_live_evidence(payload, kind="complete")

    def test_collect_live_evidence_rejects_family_authority_drift(self):
        payload = self.exact_graphql_payload(with_finding=True)
        payload["data"]["repository"]["pullRequest"]["comments"]["nodes"][0] = (
            authoritative_family_comment(
                base_sha=self.base_sha,
                original_head=self.candidate_sha,
                review_id="REMOTE_REVIEW_LIVE_001",
                candidate_sha=self.candidate_sha,
                mappings=[
                    {
                        "finding_id": "FINDING_ACTION_001",
                        "family": "generated",
                    }
                ],
            )
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "family-authority-drift",
        ):
            self.collect_exact_live_evidence(payload, kind="complete")

    def test_authoritative_family_classification_rejects_candidate_family_swaps(self):
        contract = self.contract(base=self.base_sha, candidate=self.candidate_sha, kind="default")
        contract["family_sweeps"] = [
            {
                "finding_id": "FINDING_ACTION_001",
                "siblings": [
                    {
                        "member": "owners",
                        "result": "affected-fixed",
                        "assertion_id": "registry:sibling:generated:owners:affected-fixed:v2",
                    },
                    {
                        "member": "outputs",
                        "result": "verified-unaffected",
                        "assertion_id": "registry:sibling:generated:outputs:verified-unaffected:v2",
                    },
                    {
                        "member": "consumers",
                        "result": "verified-unaffected",
                        "assertion_id": "registry:sibling:generated:consumers:verified-unaffected:v2",
                    },
                    {
                        "member": "drift-checks",
                        "result": "verified-unaffected",
                        "assertion_id": "registry:sibling:generated:drift-checks:verified-unaffected:v2",
                    },
                ],
            }
        ]
        payload = self.exact_graphql_payload(with_finding=True)
        committed = datetime.fromisoformat(
            git(self.repo, "show", "-s", "--format=%cI", self.candidate_sha)
            .stdout.decode()
            .strip()
        ).astimezone(timezone.utc)
        review_at = committed + timedelta(minutes=1)
        finding_at = review_at - timedelta(seconds=30)
        classification_at = review_at + timedelta(seconds=30)
        pr = payload["data"]["repository"]["pullRequest"]
        pr["createdAt"] = iso(review_at - timedelta(seconds=30))
        pr["reviews"]["nodes"][0]["submittedAt"] = iso(review_at)
        pr["reviews"]["nodes"][0]["comments"]["nodes"][0]["createdAt"] = iso(finding_at)
        pr["reviews"]["nodes"][0]["comments"]["nodes"][0]["updatedAt"] = iso(finding_at)
        pr["reviewThreads"]["nodes"][0]["comments"]["nodes"][0]["createdAt"] = iso(finding_at)
        pr["comments"]["nodes"][0]["createdAt"] = iso(classification_at)
        pr["comments"]["nodes"][0]["updatedAt"] = iso(classification_at)
        evidence = json.loads(
            self.collect_exact_live_evidence(
                payload,
                kind="complete",
                clock=lambda: classification_at + timedelta(seconds=30),
            )
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "family does not match its sweep",
        ):
            review_family.build_report(
                contract,
                evidence,
                self.repo,
                self.candidate_sha,
            )

    def test_candidate_decision_record_no_follow_and_tree_mode_guards(self):
        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        try:
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=candidate)
            )
            self.assertEqual(
                trusted_review_gate._load_authoritative_trigger(
                    contract, repo, candidate
                )["pull_request"],
                SYNTHETIC_PULL_REQUEST,
            )

            decision_path = self.decision_record_path(repo)
            shadow = decision_path.with_suffix(".shadow.json")
            shadow.write_text(decision_path.read_text(encoding="utf-8"), encoding="utf-8")
            decision_path.unlink()
            decision_path.symlink_to(shadow.name)
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "candidate decision record is unavailable for drift validation",
            ):
                trusted_review_gate._load_authoritative_trigger(
                    contract, repo, candidate
                )
        finally:
            shutil.rmtree(repo)

        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        try:
            github_dir = Path(repo) / ".github"
            shadow_dir = Path(repo) / ".github-real"
            github_dir.rename(shadow_dir)
            github_dir.symlink_to(shadow_dir.name)
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=candidate)
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "candidate decision record is unavailable for drift validation",
            ):
                trusted_review_gate._load_authoritative_trigger(
                    contract, repo, candidate
                )
        finally:
            shutil.rmtree(repo)

        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        outside = Path(repo).parent / "outside-github"
        try:
            outside.mkdir()
            github_dir = Path(repo) / ".github"
            github_dir.rename(outside / ".github")
            github_dir.symlink_to(outside / ".github")
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=candidate)
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "candidate decision record is unavailable for drift validation",
            ):
                trusted_review_gate._load_authoritative_trigger(
                    contract, repo, candidate
                )
        finally:
            shutil.rmtree(repo)
            if outside.exists():
                shutil.rmtree(outside)

        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        try:
            decision_path = self.decision_record_path(repo)
            shadow = decision_path.with_suffix(".same-content.json")
            shadow.write_text(decision_path.read_text(encoding="utf-8"), encoding="utf-8")
            decision_path.unlink()
            decision_path.symlink_to(shadow.name)
            self.commit_all(repo, "commit decision symlink")
            symlink_candidate = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=symlink_candidate)
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "candidate decision record has an unsafe type or mode",
            ):
                trusted_review_gate._load_authoritative_trigger(
                    contract, repo, symlink_candidate
                )
        finally:
            shutil.rmtree(repo)

        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        try:
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=candidate)
            )
            decision_path = self.decision_record_path(repo)
            replacement = decision_path.with_suffix(".replacement.json")
            replacement.write_text(
                decision_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            with mock.patch.object(
                trusted_review_gate.os,
                "open",
                side_effect=self.patched_os_open_once(
                    lambda path, dir_fd: (
                        dir_fd is not None
                        and os.fspath(path) == decision_path.name
                    ),
                    lambda: (
                        decision_path.unlink(),
                        replacement.rename(decision_path),
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "candidate decision record changed during no-follow access",
                ):
                    trusted_review_gate._load_authoritative_trigger(
                        contract, repo, candidate
                    )
        finally:
            shutil.rmtree(repo)

    def test_candidate_decision_record_openat_races_fail_closed(self):
        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        try:
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=candidate)
            )
            github_dir = Path(repo) / ".github"
            shadow_dir = Path(repo) / ".github-root-race"
            with mock.patch.object(
                trusted_review_gate.os,
                "open",
                side_effect=self.patched_os_open_once(
                    lambda path, dir_fd: (
                        dir_fd is None
                        and Path(path) == Path(repo).resolve()
                    ),
                    lambda: (
                        github_dir.rename(shadow_dir),
                        github_dir.symlink_to(shadow_dir.name),
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "candidate decision record is unavailable for drift validation",
                ):
                    trusted_review_gate._load_authoritative_trigger(
                        contract, repo, candidate
                    )
        finally:
            shutil.rmtree(repo)

        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        outside = Path(repo).parent / "outside-parent-race"
        try:
            outside.mkdir()
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=candidate)
            )
            github_dir = Path(repo) / ".github"
            with mock.patch.object(
                trusted_review_gate.os,
                "open",
                side_effect=self.patched_os_open_once(
                    lambda path, dir_fd: (
                        dir_fd is not None and os.fspath(path) == ".github"
                    ),
                    lambda: (
                        github_dir.rename(outside / ".github"),
                        github_dir.symlink_to(outside / ".github"),
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "candidate decision record is unavailable for drift validation",
                ):
                    trusted_review_gate._load_authoritative_trigger(
                        contract, repo, candidate
                    )
        finally:
            shutil.rmtree(repo)
            if outside.exists():
                shutil.rmtree(outside)

        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        try:
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=candidate)
            )
            decision_path = self.decision_record_path(repo)
            shadow = decision_path.with_suffix(".leaf-open-race.json")
            shadow.write_text(
                decision_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            with mock.patch.object(
                trusted_review_gate.os,
                "open",
                side_effect=self.patched_os_open_once(
                    lambda path, dir_fd: (
                        dir_fd is not None
                        and os.fspath(path) == decision_path.name
                    ),
                    lambda: (
                        decision_path.unlink(),
                        decision_path.symlink_to(shadow.name),
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "candidate decision record is unavailable for drift validation",
                ):
                    trusted_review_gate._load_authoritative_trigger(
                        contract, repo, candidate
                    )
        finally:
            shutil.rmtree(repo)

        for case_name, use_outside in (("in-repo", False), ("outside", True)):
            repo, base, candidate = self.build_decision_repo(self.decision_entry())
            outside = Path(repo).parent / f"outside-post-parent-{case_name}"
            try:
                if use_outside:
                    outside.mkdir()
                contract = review_family.validate_contract(
                    self.contract(base=base, candidate=candidate)
                )
                github_dir = Path(repo) / ".github"
                shadow_dir = (
                    outside / ".github"
                    if use_outside
                    else Path(repo) / ".github-post-parent"
                )
                with mock.patch.object(
                    trusted_review_gate.os,
                    "open",
                    side_effect=self.patched_os_open_once(
                        lambda path, dir_fd: (
                            dir_fd is not None
                            and os.fspath(path) == self.decision_record_path(repo).name
                        ),
                        lambda: (
                            github_dir.rename(shadow_dir),
                            github_dir.symlink_to(
                                shadow_dir
                                if use_outside
                                else shadow_dir.name
                            ),
                        ),
                    ),
                ):
                    with self.subTest(case=case_name), self.assertRaisesRegex(
                        reporter.PilotDataError,
                        "candidate decision record changed during no-follow access",
                    ):
                        trusted_review_gate._load_authoritative_trigger(
                            contract, repo, candidate
                        )
            finally:
                shutil.rmtree(repo)
                if outside.exists():
                    shutil.rmtree(outside)

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
        contract = self.contract(base=self.base_sha, candidate=self.candidate_sha)
        payload = self.adapter()
        pr = payload["data"]["repository"]["pullRequest"]
        pr["baseRefOid"] = self.base_sha
        pr["headRefOid"] = self.candidate_sha
        pr["commits"]["nodes"][0]["commit"]["oid"] = self.candidate_sha
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = git(
            self.repo, "show", "-s", "--format=%cI", self.candidate_sha
        ).stdout.decode().strip().replace("+00:00", "Z")
        pr["reviews"]["nodes"][0]["commit"]["oid"] = self.candidate_sha
        envelope = json.loads(
            signed_receipt(
                reporter.normalized_json(
                    review_report(
                        self.base_sha,
                        self.candidate_sha,
                        ["changed.txt"],
                        review_family.derive_change_records(
                            self.repo, self.base_sha, self.candidate_sha
                        ),
                    )
                ),
                base=self.base_sha,
                candidate=self.candidate_sha,
            )
        )
        evidence = json.loads(
            trusted_review_gate.collect_live_evidence_bytes(
                contract,
                self.repo,
                self.candidate_sha,
                self.candidate_sha,
                review_report(
                    self.base_sha,
                    self.candidate_sha,
                    ["changed.txt"],
                    review_family.derive_change_records(
                        self.repo, self.base_sha, self.candidate_sha
                    ),
                ),
                envelope,
                [],
                adapter=StaticAdapter(payload),
                clock=lambda: datetime(
                    2026, 8, 31, 3, 13, tzinfo=timezone.utc
                ),
            )
        )
        self.assertEqual(evidence["candidate_advances"], [])
        self.assertEqual(evidence["force_push_events"], [])
        self.assertEqual(evidence["pull_request"]["base_sha"], self.base_sha)
        self.assertEqual(evidence["pull_request"]["head_sha"], self.candidate_sha)

    def test_authoritative_graphql_base_must_match_exact_contract_base(self):
        payload = self.adapter()
        pr = payload["data"]["repository"]["pullRequest"]
        pr["baseRefOid"] = self.base_sha
        pr["headRefOid"] = self.candidate_sha
        pr["commits"]["nodes"][0]["commit"]["oid"] = self.candidate_sha
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = git(
            self.repo, "show", "-s", "--format=%cI", self.candidate_sha
        ).stdout.decode().strip().replace("+00:00", "Z")
        pr["reviews"]["nodes"][0]["commit"]["oid"] = self.candidate_sha
        payload["data"]["repository"]["pullRequest"]["baseRefOid"] = "f" * 40
        contract = self.contract(base=self.base_sha, candidate=self.candidate_sha)
        envelope = json.loads(
            signed_receipt(
                reporter.normalized_json(
                    review_report(
                        self.base_sha,
                        self.candidate_sha,
                        ["changed.txt"],
                        review_family.derive_change_records(
                            self.repo, self.base_sha, self.candidate_sha
                        ),
                    )
                ),
                base=self.base_sha,
                candidate=self.candidate_sha,
            )
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError, "authoritative PR base OID"
        ):
            trusted_review_gate.collect_live_evidence_bytes(
                contract,
                self.repo,
                self.candidate_sha,
                self.candidate_sha,
                review_report(
                    self.base_sha,
                    self.candidate_sha,
                    ["changed.txt"],
                    review_family.derive_change_records(
                        self.repo, self.base_sha, self.candidate_sha
                    ),
                ),
                envelope,
                [],
                adapter=StaticAdapter(payload),
            )

    def test_incomplete_collection_and_changed_second_snapshot_fail(self):
        payload = self.adapter()
        pr = payload["data"]["repository"]["pullRequest"]
        pr["baseRefOid"] = self.base_sha
        pr["headRefOid"] = self.candidate_sha
        pr["commits"]["nodes"][0]["commit"]["oid"] = self.candidate_sha
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = git(
            self.repo, "show", "-s", "--format=%cI", self.candidate_sha
        ).stdout.decode().strip().replace("+00:00", "Z")
        pr["reviews"]["nodes"][0]["commit"]["oid"] = self.candidate_sha
        payload["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"][
            "hasNextPage"
        ] = True
        contract = self.contract(base=self.base_sha, candidate=self.candidate_sha)
        envelope = json.loads(
            signed_receipt(
                reporter.normalized_json(
                    review_report(
                        self.base_sha,
                        self.candidate_sha,
                        ["changed.txt"],
                        review_family.derive_change_records(
                            self.repo, self.base_sha, self.candidate_sha
                        ),
                    )
                ),
                base=self.base_sha,
                candidate=self.candidate_sha,
            )
        )
        with self.assertRaisesRegex(reporter.PilotDataError, "bounded complete"):
            trusted_review_gate.collect_live_evidence_bytes(
                contract,
                self.repo,
                self.candidate_sha,
                self.candidate_sha,
                review_report(
                    self.base_sha,
                    self.candidate_sha,
                    ["changed.txt"],
                    review_family.derive_change_records(
                        self.repo, self.base_sha, self.candidate_sha
                    ),
                ),
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
        payload = reporter.normalized_json(review_report()); receipt = signed_receipt(payload)
        now = datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc); replay_path = self.replay_receipt_path(self.replay)
        shared = {"repository": "laqieer/fireemblem8-expansion", "pull_request": SYNTHETIC_PULL_REQUEST, "base_sha": BASE, "candidate_sha": CANDIDATE, "trusted_key_id": KEY_ID, "trusted_key_epoch": KEY_EPOCH, "trusted_key": KEY, "current_time": now}

        def verify(blob=receipt, **overrides):
            arguments = {**shared, "replay_store": self.replay, "consume_nonce": False, **overrides}
            return trusted_review_gate._verify_signed_receipt_bytes(blob, **arguments)

        def sized(target):
            baseline = len(signed_receipt(b"", nonce="n" * 16)) - 16
            for nonce_len in range(16, 129):
                encoded = target - baseline - nonce_len
                if encoded >= 0 and encoded % 4 == 0:
                    candidate = signed_receipt(b"x" * (encoded // 4 * 3), nonce="n" * nonce_len)
                    if len(candidate) == target: return candidate
            self.fail(f"no valid receipt for {target}")

        def fresh(name):
            root = self.temporary_repo(name); self.addCleanup(shutil.rmtree, root); return root, self.replay_receipt_path(root)

        verified, envelope = verify(consume_nonce=True); self.assertEqual(verified, payload); self.assertEqual(envelope["base_sha"], BASE); self.assertEqual(replay_path.read_bytes(), receipt)
        verified_again, envelope_again = verify(consume_nonce=True); self.assertEqual((verified_again, envelope_again), (payload, envelope))
        preserved_payload, _ = verify(current_time=now + timedelta(days=1), require_current_time=False, require_preserved=True); self.assertEqual(preserved_payload, payload)
        with self.assertRaisesRegex(reporter.PilotDataError, "re-signed"): verify(signed_receipt(payload, nonce="different-nonce-0002"), consume_nonce=True)

        for label, overrides in (("base", {"base_sha": "f" * 40}), ("head", {"candidate_sha": "f" * 40}), ("epoch", {"trusted_key_epoch": 8})):
            with self.subTest(label=label), self.assertRaisesRegex(reporter.PilotDataError, "outside trusted scope"): verify(replay_store=None, **overrides)

        limit = trusted_review_gate.MAX_AUTHENTICATED_RECEIPT_BYTES; at_limit = sized(limit); self.assertEqual(len(at_limit), limit)
        cap_store, cap_path = fresh("receipt-cap"); verify(at_limit, replay_store=cap_store, consume_nonce=True); self.assertEqual(cap_path.read_bytes(), at_limit)
        oversize = {limit + 1: sized(limit + 1), 1_198_104: sized(1_198_104)}
        for size, blob in oversize.items():
            store, path = fresh(f"receipt-oversize-{size}")
            with self.subTest(size=size), self.assertRaisesRegex(reporter.PilotDataError, "maximum size"): verify(blob, replay_store=store, consume_nonce=True)
            self.assertFalse(path.exists()); verify(at_limit, replay_store=store, consume_nonce=True); self.assertEqual(path.read_bytes(), at_limit)

        store, path = fresh("receipt-preserved-oversize"); path.write_bytes(oversize[1_198_104]); os.chmod(path, 0o600)
        with self.assertRaisesRegex(reporter.PilotDataError, "preserved original pre-review is unavailable"):
            trusted_review_gate.preserved_receipt_bytes(store, repository="laqieer/fireemblem8-expansion", pull_request=SYNTHETIC_PULL_REQUEST, base_sha=BASE, original_pre_review_head=CANDIDATE, key_id=KEY_ID, key_epoch=KEY_EPOCH)
        directory_fd = trusted_review_gate._open_replay_store_fd(store)
        try:
            with self.assertRaisesRegex(reporter.PilotDataError, "expected-bypass"):
                trusted_review_gate._read_replay_receipt_bytes(directory_fd, name=path.name, scope_id=path.name.removeprefix("original-"), expected_bytes=oversize[1_198_104], allow_missing=False, allow_temp_link=False, not_found_message="missing", invalid_message="expected-bypass")
        finally:
            os.close(directory_fd)

    def test_persist_original_receipt_is_atomic_and_idempotent(self):
        payload = b"atomic-replay-receipt\n"
        real_open, real_write, real_fsync, real_unlink = os.open, os.write, os.fsync, os.unlink
        publish_kwargs = {"repository": "laqieer/fireemblem8-expansion", "pull_request": SYNTHETIC_PULL_REQUEST, "base_sha": BASE, "original_pre_review_head": CANDIDATE, "key_id": KEY_ID, "key_epoch": KEY_EPOCH}
        temp_entries = lambda root: sorted(name for name in os.listdir(root) if name.startswith(".original-"))

        def fresh(name):
            root = self.temporary_repo(name)
            self.addCleanup(shutil.rmtree, root)
            return root, self.replay_receipt_path(root)

        def write_private(path, content):
            path.write_bytes(content); os.chmod(path, 0o600)

        def link_temp_alias(root, final_path, token="a" * 16):
            alias = Path(root) / trusted_review_gate._receipt_temp_name(final_path.name.removeprefix("original-"), token); os.link(final_path, alias); return alias

        def persist(root):
            trusted_review_gate.persist_original_receipt(payload, root, **publish_kwargs)

        for split in range(1, len(payload)):
            root, final_path = fresh("replay-short-write")
            state = {"calls": 0}

            def short_write(fd, data):
                blob = bytes(data)
                if state["calls"] == 0: state["calls"] += 1; return real_write(fd, blob[:split])
                return real_write(fd, blob)

            with self.subTest(case=f"short-write-{split}"), mock.patch.object(trusted_review_gate.os, "write", side_effect=short_write):
                persist(root); self.assertEqual(final_path.read_bytes(), payload)

        for case_name, first_outcome, final_expected in (("eintr", InterruptedError(), payload), ("zero", 0, None), ("write-error", OSError("write failed"), None)):
            root, final_path = fresh("replay-write-fault")
            state = {"calls": 0}

            def write_fault(fd, data):
                if state["calls"] == 0:
                    state["calls"] += 1
                    if isinstance(first_outcome, BaseException): raise first_outcome
                    return first_outcome
                return real_write(fd, bytes(data))

            with self.subTest(case=case_name), mock.patch.object(trusted_review_gate.os, "write", side_effect=write_fault):
                if final_expected is None:
                    with self.assertRaisesRegex(reporter.PilotDataError, "could not be published"): persist(root)
                    self.assertFalse(final_path.exists()); self.assertEqual(temp_entries(root), []); persist(root); self.assertEqual(final_path.read_bytes(), payload)
                else:
                    persist(root); self.assertEqual(final_path.read_bytes(), final_expected)

        for case_name, fsync_fault_call, published in (("file-fsync-error", 1, False), ("directory-fsync-error", 2, True)):
            root, final_path = fresh("replay-fsync-fault")
            state = {"calls": 0}

            def fsync_fault(fd):
                state["calls"] += 1
                if state["calls"] == fsync_fault_call: raise OSError("fsync failed")
                return real_fsync(fd)

            with self.subTest(case=case_name), mock.patch.object(trusted_review_gate.os, "fsync", side_effect=fsync_fault):
                with self.assertRaisesRegex(reporter.PilotDataError, "could not be published"): persist(root)
            self.assertEqual(final_path.exists(), published)
            if published: self.assertEqual(final_path.read_bytes(), payload)
            else: self.assertEqual(temp_entries(root), [])
            persist(root); self.assertEqual(final_path.read_bytes(), payload)

        root, final_path = fresh("replay-link-fault")
        with mock.patch.object(trusted_review_gate.os, "link", side_effect=OSError("link failed")):
            with self.assertRaisesRegex(reporter.PilotDataError, "could not be published"): persist(root)
        self.assertFalse(final_path.exists()); self.assertEqual(temp_entries(root), []); persist(root); self.assertEqual(final_path.read_bytes(), payload)

        root, final_path = fresh("replay-existing-temp")
        write_private(final_path, payload); alias = link_temp_alias(root, final_path); fsync_calls = {"count": 0}
        with mock.patch.object(trusted_review_gate.os, "fsync", side_effect=lambda fd: (fsync_calls.__setitem__("count", fsync_calls["count"] + 1), real_fsync(fd))[1]):
            persist(root)
        self.assertEqual(final_path.read_bytes(), payload); self.assertFalse(alias.exists()); self.assertEqual(temp_entries(root), []); self.assertEqual(fsync_calls["count"], 1)

        for case_name, unlink_side_effect in (("unlink-error", PermissionError("blocked")), ("unlink-enoent", FileNotFoundError("gone"))):
            root, final_path = fresh("replay-existing-temp-fault")
            write_private(final_path, payload); alias = link_temp_alias(root, final_path)

            def unlink_fault(name, *, dir_fd=None):
                if name == alias.name:
                    if isinstance(unlink_side_effect, FileNotFoundError): real_unlink(name, dir_fd=dir_fd)
                    raise unlink_side_effect
                return real_unlink(name, dir_fd=dir_fd)

            with self.subTest(case=case_name), mock.patch.object(trusted_review_gate.os, "unlink", side_effect=unlink_fault):
                if isinstance(unlink_side_effect, FileNotFoundError):
                    persist(root); self.assertEqual(final_path.read_bytes(), payload); self.assertFalse(alias.exists()); self.assertEqual(temp_entries(root), [])
                else:
                    with self.assertRaisesRegex(reporter.PilotDataError, "could not be published"): persist(root)
                    self.assertTrue(alias.exists())

        for case_name, winner_bytes, unlink_race, should_succeed in (("concurrent-same", payload, True, True), ("concurrent-different", b"other-receipt\n", False, False)):
            root, final_path = fresh("replay-concurrent")

            def losing_link(_src, dst, *, dst_dir_fd=None):
                descriptor = real_open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dst_dir_fd)
                try: real_write(descriptor, winner_bytes); real_fsync(descriptor)
                finally: os.close(descriptor)
                raise FileExistsError

            def unlink_racing(name, *, dir_fd=None):
                if unlink_race and name.startswith(".original-"): real_unlink(name, dir_fd=dir_fd); raise FileNotFoundError("gone")
                return real_unlink(name, dir_fd=dir_fd)

            with self.subTest(case=case_name), mock.patch.object(trusted_review_gate.os, "link", side_effect=losing_link), mock.patch.object(trusted_review_gate.os, "unlink", side_effect=unlink_racing):
                if should_succeed:
                    persist(root); self.assertEqual(final_path.read_bytes(), payload); self.assertEqual(temp_entries(root), [])
                else:
                    with self.assertRaisesRegex(reporter.PilotDataError, "consumed or re-signed"): persist(root)

        for case_name, existing_bytes, alias_tokens, extra_name, should_succeed in (("existing-exact", payload, (), None, True), ("existing-different", b"different\n", (), None, False), ("existing-partial", payload[:7], (), None, False), ("existing-hardlink", payload, (), "unexpected-link", False), ("existing-multi-temp", payload, ("b" * 16, "c" * 16), None, False)):
            root, final_path = fresh("replay-existing")
            write_private(final_path, existing_bytes)
            for token in alias_tokens: link_temp_alias(root, final_path, token)
            if extra_name is not None: os.link(final_path, Path(root) / extra_name)
            with self.subTest(case=case_name):
                if should_succeed:
                    persist(root); self.assertEqual(final_path.read_bytes(), payload); self.assertEqual(temp_entries(root), [])
                else:
                    with self.assertRaisesRegex(reporter.PilotDataError, "consumed or re-signed"): persist(root)

    def test_authoritative_trigger_decision_record_is_exact_and_fail_closed(self):
        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        try:
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=candidate)
            )
            trigger = trusted_review_gate._load_authoritative_trigger(
                contract, repo, candidate
            )
            self.assertEqual(
                trigger["path"], trusted_review_gate.DECISION_RECORD_PATH
            )
            self.assertEqual(
                trigger["risk_boundaries"], ["none"]
            )
            self.assertEqual(
                trigger["threshold_triggers"], ["none"]
            )
            self.assertFalse(trigger["pre_review_required"])

            mismatched = review_family.validate_contract(
                {
                    **self.contract(base=base, candidate=candidate),
                    "trigger": {
                        "risk_boundaries": ["lifecycle", "protocol"],
                        "threshold_triggers": [
                            "changed-files",
                            "changed-lines",
                            "major-boundaries",
                            "risk-boundary",
                        ],
                    },
                }
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "candidate trigger does not match the authoritative decision record",
            ):
                trusted_review_gate._load_authoritative_trigger(
                    mismatched, repo, candidate
                )
        finally:
            shutil.rmtree(repo)

        high_repo, high_base, high_candidate = self.build_decision_repo(
            self.decision_entry(
                risks=("lifecycle", "protocol"),
                triggers=("changed-files", "risk-boundary"),
            )
        )
        try:
            contract = review_family.validate_contract(
                {
                    **self.contract(base=high_base, candidate=high_candidate),
                    "trigger": {
                        "risk_boundaries": ["lifecycle", "protocol"],
                        "threshold_triggers": ["changed-files", "risk-boundary"],
                    },
                }
            )
            trigger = trusted_review_gate._load_authoritative_trigger(
                contract, high_repo, high_candidate
            )
            self.assertEqual(trigger["risk_boundaries"], ["lifecycle", "protocol"])
            self.assertEqual(
                trigger["threshold_triggers"], ["changed-files", "risk-boundary"]
            )
            self.assertTrue(trigger["pre_review_required"])
            self.assertEqual(
                trigger["blob_oid"],
                git(
                    high_repo,
                    "rev-parse",
                    f"{high_base}:{trusted_review_gate.DECISION_RECORD_PATH}",
                ).stdout.decode().strip(),
            )

            self.write_decision_record(high_repo, self.decision_entry())
            git(high_repo, "add", trusted_review_gate.DECISION_RECORD_PATH)
            git(high_repo, "commit", "-q", "-m", "downgrade candidate decision")
            downgraded = git(high_repo, "rev-parse", "HEAD").stdout.decode().strip()
            drifted_contract = review_family.validate_contract(
                {
                    **self.contract(base=high_base, candidate=downgraded),
                    "trigger": {
                        "risk_boundaries": ["lifecycle", "protocol"],
                        "threshold_triggers": ["changed-files", "risk-boundary"],
                    },
                }
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "candidate decision record drifts from the authoritative base decision",
            ):
                trusted_review_gate._load_authoritative_trigger(
                    drifted_contract, high_repo, downgraded
                )
        finally:
            shutil.rmtree(high_repo)

        missing_repo, missing_base, missing_candidate = self.build_decision_repo()
        try:
            contract = review_family.validate_contract(
                self.contract(base=missing_base, candidate=missing_candidate)
            )
            self.assertIsNone(
                trusted_review_gate._load_authoritative_trigger(
                    contract, missing_repo, missing_candidate
                )
            )
        finally:
            shutil.rmtree(missing_repo)

        duplicate_repo, duplicate_base, duplicate_candidate = self.build_decision_repo(
            self.decision_entry(),
            self.decision_entry(),
        )
        try:
            contract = review_family.validate_contract(
                self.contract(base=duplicate_base, candidate=duplicate_candidate)
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "repeats PR|exact base commit",
            ):
                trusted_review_gate._load_authoritative_trigger(
                    contract, duplicate_repo, duplicate_candidate
                )
        finally:
            shutil.rmtree(duplicate_repo)

    def test_external_preregistration_authorizes_future_pr_when_base_lacks_record(self):
        repo, base, candidate = self.build_decision_repo()
        try:
            decision = decision_record_entry(
                risks=("lifecycle", "protocol"),
                triggers=("changed-files", "risk-boundary"),
            )
            self.write_decision_record(repo, self.decision_entry(
                risks=("lifecycle", "protocol"),
                triggers=("changed-files", "risk-boundary"),
            ))
            self.commit_all_at(repo, "candidate gains decision entry", "2026-08-31T03:06:00Z")
            candidate = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
            payload = self.exact_graphql_payload()
            payload["data"]["repository"]["pullRequest"]["baseRefOid"] = base
            payload["data"]["repository"]["pullRequest"]["headRefOid"] = candidate
            payload["data"]["repository"]["pullRequest"]["createdAt"] = "2026-08-31T03:08:00Z"
            payload["data"]["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"]["oid"] = candidate
            payload["data"]["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"]["committedDate"] = git(
                repo, "show", "-s", "--format=%cI", candidate
            ).stdout.decode().strip().replace("+00:00", "Z")
            payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["commit"]["oid"] = candidate
            payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["submittedAt"] = "2026-08-31T03:15:00Z"
            payload["data"]["repository"]["pullRequest"]["comments"]["nodes"] = [
                authoritative_decision_comment(
                    base_sha=base,
                    head_sha=candidate,
                    decision=decision,
                )
            ]
            contract = self.contract(base=base, candidate=candidate)
            contract["trust_mode"] = "base-pinned"
            contract["trigger"] = {
                "risk_boundaries": ["lifecycle", "protocol"],
                "threshold_triggers": ["changed-files", "risk-boundary"],
            }
            receipt = signed_receipt(
                reporter.normalized_json(
                    review_report(
                        base,
                        candidate,
                        ["changed.txt", trusted_review_gate.DECISION_RECORD_PATH],
                        review_family.derive_change_records(repo, base, candidate),
                    )
                ),
                base=base,
                candidate=candidate,
                nonce="external-preregistration-0001",
            )
            result = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repo,
                expected_candidate=candidate,
                expected_remote_head=candidate,
                expected_base=base,
                review_receipt_bytes=receipt,
                replay_store=self.replay,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                adapter=StaticAdapter(payload),
                clock=lambda: datetime(2026, 8, 31, 4, 1, tzinfo=timezone.utc),
            )
            self.assertTrue(result["trigger"]["authoritative"])
            self.assertTrue(result["gates"]["current_candidate_reviewed"])
            self.assertFalse(result["bootstrap"]["mode"] == "introduction")
        finally:
            shutil.rmtree(repo)

    def test_external_preregistration_parser_keeps_original_and_current_heads_separate(self):
        original_head, current_head = "a" * 40, "b" * 40
        contract = self.contract(base=self.base_sha, candidate=current_head)
        contract["original_pre_review_head"] = original_head
        result = trusted_review_gate._parse_external_trigger_comment(
            [authoritative_decision_comment(base_sha=self.base_sha, head_sha=original_head, candidate_sha=current_head)],
            viewer=trusted_review_gate._graphql_actor(trusted_comment_actor(), "GitHub viewer"),
            repository_id=repository_identity()["id"],
            repository_name=repository_identity()["name"],
            contract=contract,
            current_candidate_sha=current_head,
            first_remote_review_at=datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc),
            actors=[],
        )
        self.assertEqual((result["original_pre_review_head"], result["candidate_sha"]), (original_head, current_head))

    def test_external_preregistration_must_be_exact_and_unique(self):
        cases = (
            ("missing", "missing", "requires authoritative trigger decision or trusted preregistration evidence"),
            ("missing-updatedAt", "drop-updatedAt", "updatedAt"),
            ("late", {"createdAt": "2026-08-31T03:15:00Z", "updatedAt": "2026-08-31T03:15:00Z"}, "not before first remote review"),
            ("edited", {"updatedAt": "2026-08-31T03:11:31Z"}, "must not be edited"),
            ("wrong-actor", {"author": human_graphql_actor()}, "exact trusted coordinator actor"),
            ("wrong-repo", {"repository": "other/repository"}, "exact repository"),
            ("wrong-base", {"base_sha": "f" * 40}, "exact base"),
            ("wrong-original-head", {"original_pre_review_head": "f" * 40}, "initial reviewed head"),
            ("wrong-head", {"candidate_sha": "f" * 40}, "current candidate head"),
            ("duplicate", "duplicate", "not unique"),
        )
        for case_name, override, pattern in cases:
            repo, base, candidate = self.build_decision_repo()
            try:
                self.write_decision_record(repo, self.decision_entry(
                    risks=("lifecycle", "protocol"),
                    triggers=("changed-files", "risk-boundary"),
                ))
                self.commit_all_at(repo, "candidate gains decision entry", "2026-08-31T03:06:00Z")
                candidate = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
                payload = self.exact_graphql_payload()
                pr = payload["data"]["repository"]["pullRequest"]
                pr["baseRefOid"] = base
                pr["headRefOid"] = candidate
                pr["createdAt"] = "2026-08-31T03:08:00Z"
                pr["commits"]["nodes"][0]["commit"]["oid"] = candidate
                pr["commits"]["nodes"][0]["commit"]["committedDate"] = git(
                    repo, "show", "-s", "--format=%cI", candidate
                ).stdout.decode().strip().replace("+00:00", "Z")
                pr["reviews"]["nodes"][0]["commit"]["oid"] = candidate
                pr["reviews"]["nodes"][0]["submittedAt"] = "2026-08-31T03:15:00Z"
                decision = decision_record_entry(
                    risks=("lifecycle", "protocol"),
                    triggers=("changed-files", "risk-boundary"),
                )
                comment = authoritative_decision_comment(
                    base_sha=base,
                    head_sha=candidate,
                    decision=decision,
                )
                if override == "missing":
                    pr["comments"]["nodes"] = []
                elif override == "drop-updatedAt":
                    comment.pop("updatedAt")
                    pr["comments"]["nodes"] = [comment]
                elif override == "duplicate":
                    pr["comments"]["nodes"] = [comment, copy.deepcopy(comment)]
                    pr["comments"]["nodes"][1]["id"] = "COMMENT_DECISION_002"
                else:
                    if override is not None:
                        for field in ("repository", "base_sha", "candidate_sha", "original_pre_review_head"):
                            if field in override:
                                body = json.loads(comment["body"][len(trusted_review_gate.EXTERNAL_DECISION_COMMENT_PREFIX):])
                                body[field] = override[field]
                                comment["body"] = trusted_review_gate.EXTERNAL_DECISION_COMMENT_PREFIX + reporter.normalized_json(body).decode("ascii").rstrip("\n")
                                override = {key: value for key, value in override.items() if key != field}
                        comment.update(override)
                    pr["comments"]["nodes"] = [comment]
                contract = self.contract(base=base, candidate=candidate)
                contract["trust_mode"] = "base-pinned"
                contract["trigger"] = {
                    "risk_boundaries": ["lifecycle", "protocol"],
                    "threshold_triggers": ["changed-files", "risk-boundary"],
                }
                receipt = signed_receipt(
                    reporter.normalized_json(
                        review_report(
                            base,
                            candidate,
                            ["changed.txt", trusted_review_gate.DECISION_RECORD_PATH],
                            review_family.derive_change_records(repo, base, candidate),
                        )
                    ),
                    base=base,
                    candidate=candidate,
                    nonce=f"external-preregistration-{case_name}",
                )
                with self.subTest(case=case_name), self.assertRaisesRegex(
                    reporter.PilotDataError,
                    pattern,
                ):
                    trusted_review_gate._run_trusted_gate(
                        raw_contract=contract,
                        repository_root=repo,
                        expected_candidate=candidate,
                        expected_remote_head=candidate,
                        expected_base=base,
                        review_receipt_bytes=receipt,
                        replay_store=self.replay,
                        trusted_key_id=KEY_ID,
                        trusted_key_epoch=KEY_EPOCH,
                        trusted_key=KEY,
                        current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                        adapter=StaticAdapter(payload),
                        clock=lambda: datetime(2026, 8, 31, 4, 1, tzinfo=timezone.utc),
                    )
            finally:
                shutil.rmtree(repo)

    def test_candidate_trigger_decision_record_must_preserve_exact_current_pr_entry(self):
        repo, base, candidate = self.build_decision_repo(self.decision_entry())
        try:
            decisions = self.load_decision_record(repo)
            decision_path = Path(repo) / trusted_review_gate.DECISION_RECORD_PATH
            decision_path.write_text(
                json.dumps(decisions, indent=2) + "\n",
                encoding="utf-8",
            )
            self.commit_all(repo, "reformat candidate decision record")
            reformatted = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
            contract = review_family.validate_contract(
                self.contract(base=base, candidate=reformatted)
            )
            trigger = trusted_review_gate._load_authoritative_trigger(
                contract, repo, reformatted
            )
            self.assertEqual(trigger["pull_request"], SYNTHETIC_PULL_REQUEST)
        finally:
            shutil.rmtree(repo)

        for case_name, pattern, mutate in (
            (
                "delete-file",
                "candidate decision record is unavailable for drift validation",
                lambda root: (Path(root) / trusted_review_gate.DECISION_RECORD_PATH).unlink(),
            ),
            (
                "rename-file",
                "candidate decision record is unavailable for drift validation",
                lambda root: (Path(root) / trusted_review_gate.DECISION_RECORD_PATH).rename(
                    Path(root)
                    / ".github"
                    / "workflow-pilot-decisions.next.json"
                ),
            ),
            (
                "empty-pull-requests",
                "candidate decision record does not contain the exact contract PR",
                lambda root: (
                    lambda decisions: (
                        decisions.__setitem__("pull_requests", []),
                        self.write_raw_decision_record(root, decisions),
                    )
                )(self.load_decision_record(root)),
            ),
            (
                "wrong-pr",
                "candidate decision record does not contain the exact contract PR",
                lambda root: (
                    lambda decisions: (
                        self.decision_record_entry(decisions).__setitem__(
                            "pull_request", SYNTHETIC_PULL_REQUEST + 1
                        ),
                        self.write_raw_decision_record(root, decisions),
                    )
                )(self.load_decision_record(root)),
            ),
            (
                "duplicate-pr",
                f"candidate trigger decisions repeats PR {SYNTHETIC_PULL_REQUEST}",
                lambda root: (
                    lambda decisions: (
                        decisions["pull_requests"].append(
                            copy.deepcopy(self.decision_record_entry(decisions))
                        ),
                        self.write_raw_decision_record(root, decisions),
                    )
                )(self.load_decision_record(root)),
            ),
        ):
            repo, base, candidate = self.build_decision_repo(self.decision_entry())
            try:
                mutate(repo)
                self.commit_all(repo, f"mutate candidate decision record: {case_name}")
                drifted = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
                contract = review_family.validate_contract(
                    self.contract(base=base, candidate=drifted)
                )
                with self.subTest(case=case_name), self.assertRaisesRegex(
                    reporter.PilotDataError,
                    pattern,
                ):
                    trusted_review_gate._load_authoritative_trigger(
                        contract, repo, drifted
                    )
            finally:
                shutil.rmtree(repo)

    def test_base_owned_trigger_decision_controls_end_to_end_and_drift_fails(self):
        repo, base, candidate = self.build_decision_repo(
            self.decision_entry(
                risks=("lifecycle", "protocol"),
                triggers=("changed-files", "risk-boundary"),
            )
        )
        try:
            payload = self.adapter()
            pr = payload["data"]["repository"]["pullRequest"]
            pr["createdAt"] = "2026-08-31T03:08:00Z"
            pr["baseRefOid"] = base
            pr["headRefOid"] = candidate
            pr["commits"]["nodes"][0]["commit"]["oid"] = candidate
            pr["commits"]["nodes"][0]["commit"]["committedDate"] = git(
                repo, "show", "-s", "--format=%cI", candidate
            ).stdout.decode().strip().replace("+00:00", "Z")
            pr["reviews"]["nodes"][0]["commit"]["oid"] = candidate
            pr["reviews"]["nodes"][0]["submittedAt"] = "2026-08-31T03:15:00Z"
            contract = self.contract(base=base, candidate=candidate)
            contract["trust_mode"] = "base-pinned"
            contract["trigger"] = {
                "risk_boundaries": ["lifecycle", "protocol"],
                "threshold_triggers": ["changed-files", "risk-boundary"],
            }
            receipt = signed_receipt(
                reporter.normalized_json(
                    review_report(
                        base,
                        candidate,
                        ["changed.txt"],
                        review_family.derive_change_records(repo, base, candidate),
                    )
                ),
                base=base,
                candidate=candidate,
                nonce="base-owned-trigger-0001",
            )
            result = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repo,
                expected_candidate=candidate,
                expected_remote_head=candidate,
                expected_base=base,
                review_receipt_bytes=receipt,
                replay_store=self.replay,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                adapter=StaticAdapter(payload),
                clock=lambda: datetime(2026, 8, 31, 4, 1, tzinfo=timezone.utc),
            )
            self.assertTrue(result["trigger"]["authoritative"])
            self.assertTrue(result["trigger"]["adversarial_pre_review_required"])

            self.write_decision_record(repo, self.decision_entry())
            git(repo, "add", trusted_review_gate.DECISION_RECORD_PATH)
            git(repo, "commit", "-q", "-m", "drift trigger decision")
            drift_candidate = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
            drift_payload = self.adapter()
            drift_pr = drift_payload["data"]["repository"]["pullRequest"]
            drift_pr["baseRefOid"] = base
            drift_pr["headRefOid"] = drift_candidate
            drift_pr["commits"]["nodes"][0]["commit"]["oid"] = drift_candidate
            drift_pr["commits"]["nodes"][0]["commit"]["committedDate"] = git(
                repo, "show", "-s", "--format=%cI", drift_candidate
            ).stdout.decode().strip().replace("+00:00", "Z")
            drift_pr["reviews"]["nodes"][0]["commit"]["oid"] = drift_candidate
            drift_contract = copy.deepcopy(contract)
            drift_contract["candidate_sha"] = drift_candidate
            drift_contract["original_pre_review_head"] = drift_candidate
            drift_receipt = signed_receipt(
                reporter.normalized_json(
                    review_report(
                        base,
                        drift_candidate,
                        ["changed.txt", trusted_review_gate.DECISION_RECORD_PATH],
                        review_family.derive_change_records(repo, base, drift_candidate),
                    )
                ),
                base=base,
                candidate=drift_candidate,
                nonce="base-owned-trigger-0002",
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "candidate decision record drifts from the authoritative base decision",
            ):
                trusted_review_gate._run_trusted_gate(
                    raw_contract=drift_contract,
                    repository_root=repo,
                    expected_candidate=drift_candidate,
                    expected_remote_head=drift_candidate,
                    expected_base=base,
                    review_receipt_bytes=drift_receipt,
                    replay_store=self.replay,
                    trusted_key_id=KEY_ID,
                    trusted_key_epoch=KEY_EPOCH,
                    trusted_key=KEY,
                    current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                    adapter=StaticAdapter(drift_payload),
                    clock=lambda: datetime(2026, 8, 31, 4, 1, tzinfo=timezone.utc),
                )
        finally:
            shutil.rmtree(repo)

    def test_missing_base_trigger_entry_uses_introduction_hold(self):
        repo, base, candidate = self.build_decision_repo()
        try:
            self.write_decision_record(
                repo,
                self.decision_entry(
                    risks=("lifecycle", "protocol"),
                    triggers=("changed-files", "risk-boundary"),
                ),
            )
            git(repo, "add", trusted_review_gate.DECISION_RECORD_PATH)
            git(repo, "commit", "-q", "-m", "candidate-only trigger")
            candidate_only = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
            payload = self.adapter()
            pr = payload["data"]["repository"]["pullRequest"]
            pr["baseRefOid"] = base
            pr["headRefOid"] = candidate_only
            pr["commits"]["nodes"][0]["commit"]["oid"] = candidate_only
            pr["commits"]["nodes"][0]["commit"]["committedDate"] = git(
                repo, "show", "-s", "--format=%cI", candidate_only
            ).stdout.decode().strip().replace("+00:00", "Z")
            pr["reviews"]["nodes"][0]["commit"]["oid"] = candidate_only
            contract = self.contract(base=base, candidate=candidate_only)
            contract["trigger"] = {
                "risk_boundaries": ["lifecycle", "protocol"],
                "threshold_triggers": ["changed-files", "risk-boundary"],
            }
            receipt = signed_receipt(
                reporter.normalized_json(
                    review_report(
                        base,
                        candidate_only,
                        ["changed.txt", trusted_review_gate.DECISION_RECORD_PATH],
                        review_family.derive_change_records(repo, base, candidate_only),
                    )
                ),
                base=base,
                candidate=candidate_only,
                nonce="candidate-only-trigger-0001",
            )
            result = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repo,
                expected_candidate=candidate_only,
                expected_remote_head=candidate_only,
                expected_base=base,
                review_receipt_bytes=receipt,
                replay_store=self.replay,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                adapter=StaticAdapter(payload),
                clock=lambda: datetime(2026, 8, 31, 4, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(result["bootstrap"]["mode"], "introduction")
            self.assertFalse(result["gates"]["trusted_push_allowed"])
            self.assertFalse(result["gates"]["merge_allowed"])
        finally:
            shutil.rmtree(repo)

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
            "reviewer_actor_id": COPILOT_ACTOR_ID,
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
        receipt_bytes = signed_receipt(
            reporter.normalized_json(report),
            base=self.base_sha,
            candidate=self.candidate_sha,
            nonce="checker-receipt-0001",
        )
        receipt_envelope = json.loads(receipt_bytes)
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
            captured_github_payload=self.adapter(),
            original_review_report_bytes=reporter.normalized_json(report),
            original_review_receipt=receipt_envelope,
            original_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
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
            "reviewer_actor_id": COPILOT_ACTOR_ID,
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
        receipt_bytes = signed_receipt(
            reporter.normalized_json(report),
            base=self.base_sha,
            candidate=self.candidate_sha,
            nonce="checker-receipt-0002",
        )
        receipt_envelope = json.loads(receipt_bytes)
        receipt = trusted_review_gate.run_base_pinned_checker(
            self.repo,
            contract=contract,
            candidate_sha=self.candidate_sha,
            review_round=1,
            review_context=remote_review,
            all_remote_reviews=[remote_review],
            remote_findings=[],
            remote_finding_ids=[],
            captured_github_payload=self.adapter(),
            original_review_report_bytes=reporter.normalized_json(report),
            original_review_receipt=receipt_envelope,
            original_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
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
                    captured_github_payload=self.adapter(),
                    original_review_report_bytes=reporter.normalized_json(
                        report
                    ),
                    original_review_receipt=receipt_envelope,
                    original_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
                    assertion_requests=review_family.build_assertion_requests(
                        contract, evidence, self.candidate_sha, 1
                    ),
                    trusted_key=KEY,
                )
        finally:
            dirty.unlink()

    def test_synthetic_introduction_mode_is_non_self_attesting(self):
        repository = self.temporary_repo("synthetic-introduction")
        try:
            (repository / "README.md").write_text("synthetic\n", encoding="utf-8")
            git(repository, "add", "README.md")
            git(repository, "commit", "-q", "-m", "synthetic base")
            base = git(repository, "rev-parse", "HEAD").stdout.decode().strip()
            (repository / "README.md").write_text("synthetic head\n", encoding="utf-8")
            git(repository, "add", "README.md")
            git(repository, "commit", "-q", "-m", "synthetic head")
            candidate = git(repository, "rev-parse", "HEAD").stdout.decode().strip()
            self.assertFalse(
                trusted_review_gate._base_contains_gate(repository, base)
            )
            contract = self.contract(base=base, candidate=candidate)
            result = trusted_review_gate._bootstrap_result(
                contract, base, candidate
            )
        finally:
            shutil.rmtree(repository)
        self.assertEqual(result["bootstrap"]["mode"], "introduction")
        self.assertTrue(
            result["bootstrap"]["external_coordinator_review_required"]
        )
        self.assertFalse(result["gates"]["trusted_push_allowed"])
        self.assertFalse(result["gates"]["merge_allowed"])

    def test_pre_push_round_three_hold_binds_remote_head_to_local_descendant(self):
        repository = self.temporary_repo("pre-push-round-three")
        try:
            git(
                repository,
                "remote",
                "add",
                "origin",
                "https://github.com/laqieer/fireemblem8-expansion.git",
            )
            for relative in (
                *trusted_review_gate.TRUSTED_REQUIRED_PATHS,
                *trusted_review_gate.ASSERTION_INPUT_PATHS,
            ):
                write_optional_tree_file(
                    repository, relative, optional_file_bytes(ROOT / relative)
                )
            (repository / ".gitignore").write_text("build/\n", encoding="utf-8")
            (repository / "feature.txt").write_text("base\n", encoding="utf-8")
            self.write_decision_record(
                repository,
                self.decision_entry(
                    risks=("lifecycle", "protocol"),
                    triggers=(
                        "changed-files",
                        "changed-lines",
                        "major-boundaries",
                        "risk-boundary",
                    ),
                ),
            )

            def commit(message, content, timestamp):
                (repository / "feature.txt").write_text(content, encoding="utf-8")
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
                    reporter.git_command(repository, "commit", "-q", "-m", message),
                    env=environment,
                    check=True,
                    capture_output=True,
                )
                return git(repository, "rev-parse", "HEAD").stdout.decode().strip()

            base = commit("base", "base\n", "2026-08-31T03:00:00Z")
            head_a = commit("head-a", "head-a\n", "2026-08-31T03:01:00Z")
            head_b = commit("head-b", "head-b\n", "2026-08-31T03:02:00Z")
            head_c = commit("head-c", "head-c\n", "2026-08-31T03:03:00Z")
            proposed_head = commit(
                "proposed-head", "head-d\n", "2026-08-31T03:04:00Z"
            )
            contract = reporter.load_json(CONTRACT_PATH)
            contract["base_sha"] = base
            contract["original_pre_review_head"] = head_c
            contract["candidate_sha"] = proposed_head
            contract["trust_mode"] = "base-pinned"
            contract["trigger"] = {
                "risk_boundaries": ["lifecycle", "protocol"],
                "threshold_triggers": [
                    "changed-files",
                    "changed-lines",
                    "major-boundaries",
                    "risk-boundary",
                ],
            }
            report = review_report(
                base,
                head_c,
                ["feature.txt"],
                review_family.derive_change_records(repository, base, head_c),
            )
            def make_receipt(nonce):
                return signed_receipt(
                    reporter.normalized_json(report),
                    base=base,
                    candidate=head_c,
                    nonce=nonce,
                )

            def fresh_replay(name):
                replay = repository / "build" / name
                replay.mkdir(parents=True, exist_ok=True)
                return replay

            receipt = make_receipt("pre-push-round-three-0001")

            def payload(remote_head):
                return {
                    "data": {
                        "viewer": {
                            "__typename": "User",
                            "id": "ACTOR_COLLECTOR_001",
                            "login": "fresh-collector",
                        },
                        "repository": {
                            "viewerPermission": "READ",
                            "owner": {
                                "__typename": "User",
                                "id": "ACTOR_OWNER_001",
                                "login": "repository-owner",
                            },
                            "pullRequest": {
                                "id": "PR_SYNTHETIC_PREPUSH",
                                "number": SYNTHETIC_PULL_REQUEST,
                                "createdAt": "2026-08-31T03:00:00Z",
                                "baseRefOid": base,
                                "headRefOid": remote_head,
                                "author": {
                                    "__typename": "User",
                                    "id": "ACTOR_IMPLEMENTER_001",
                                    "login": "implementation-agent",
                                },
                                "commits": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [
                                        {
                                            "commit": {
                                                "id": "COMMIT_HEAD_A",
                                                "oid": head_a,
                                                "pushedDate": None,
                                                "committedDate": "2026-08-31T03:01:00Z",
                                            }
                                        },
                                        {
                                            "commit": {
                                                "id": "COMMIT_HEAD_B",
                                                "oid": head_b,
                                                "pushedDate": None,
                                                "committedDate": "2026-08-31T03:02:00Z",
                                            }
                                        },
                                        {
                                            "commit": {
                                                "id": "COMMIT_HEAD_C",
                                                "oid": head_c,
                                                "pushedDate": None,
                                                "committedDate": "2026-08-31T03:03:00Z",
                                            }
                                        },
                                    ],
                                },
                                "timelineItems": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [],
                                },
                                "reviews": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [
                                        {
                                            "id": f"REMOTE_PREPUSH_{round_number}",
                                            "databaseId": 8000 + round_number,
                                            "state": "COMMENTED",
                                            "submittedAt": f"2026-08-31T03:1{round_number}:00Z",
                                            "body": "### 🟡 Changes recommended",
                                            "commit": {"oid": head_c},
                                            "author": copilot_graphql_actor(),
                                            "comments": {
                                                "pageInfo": {"hasNextPage": False},
                                                "nodes": [],
                                            },
                                        }
                                        for round_number in (1, 2, 3)
                                    ],
                                },
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [],
                                },
                                "comments": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [
                                        authoritative_disposition_comment(
                                            held_round=3,
                                            held_head_sha=head_c,
                                            authorized_next_head_sha=proposed_head,
                                            comment_id="DISPOSITION_PREPUSH_3",
                                            created_at="2026-08-31T03:13:30Z",
                                        )
                                    ],
                                },
                            },
                        },
                    }
                }

            ticks = count()

            def clock():
                return datetime(
                    2026, 8, 31, 4, 0, tzinfo=timezone.utc
                ) + timedelta(seconds=next(ticks))

            result = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repository,
                expected_candidate=proposed_head,
                expected_remote_head=head_c,
                expected_base=base,
                review_receipt_bytes=receipt,
                replay_store=self.replay,
                trusted_key_id=KEY_ID,
                trusted_key_epoch=KEY_EPOCH,
                trusted_key=KEY,
                current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                adapter=StaticAdapter(payload(head_c)),
                clock=clock,
            )
            self.assertTrue(result["gates"]["trusted_push_allowed"])
            self.assertFalse(result["gates"]["current_candidate_reviewed"])
            self.assertFalse(result["gates"]["merge_allowed"])
            self.assertEqual(result["identity"]["remote_head_sha"], head_c)
            self.assertEqual(
                result["architecture_hold"]["consumed_disposition_ids"],
                ["DISPOSITION_PREPUSH_3"],
            )

            with self.assertRaisesRegex(
                reporter.PilotDataError, "expected remote head"
            ):
                trusted_review_gate._run_trusted_gate(
                    raw_contract=contract,
                    repository_root=repository,
                    expected_candidate=proposed_head,
                    expected_remote_head=head_c,
                    expected_base=base,
                    review_receipt_bytes=make_receipt("pre-push-remote-0002"),
                    replay_store=fresh_replay("pre-push-remote-replay"),
                    trusted_key_id=KEY_ID,
                    trusted_key_epoch=KEY_EPOCH,
                    trusted_key=KEY,
                    current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                    adapter=StaticAdapter(payload(proposed_head)),
                    clock=clock,
                )

            unrelated = self.temporary_repo("pre-push-unrelated")
            try:
                git(
                    unrelated,
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/laqieer/fireemblem8-expansion.git",
                )
                for relative in (
                    *trusted_review_gate.TRUSTED_REQUIRED_PATHS,
                    *trusted_review_gate.ASSERTION_INPUT_PATHS,
                ):
                    write_optional_tree_file(
                        unrelated,
                        relative,
                        optional_file_bytes(ROOT / relative),
                    )
                (unrelated / ".gitignore").write_text("build/\n", encoding="utf-8")
                self.write_decision_record(
                    unrelated,
                    self.decision_entry(
                        risks=("lifecycle", "protocol"),
                        triggers=(
                            "changed-files",
                            "changed-lines",
                            "major-boundaries",
                            "risk-boundary",
                        ),
                    ),
                )
                (unrelated / "feature.txt").write_text("base\n", encoding="utf-8")
                git(unrelated, "add", "-A")
                subprocess.run(
                    reporter.git_command(unrelated, "commit", "-q", "-m", "base"),
                    env={
                        **reporter.git_environment(offline=True),
                        "GIT_AUTHOR_DATE": "2026-08-31T03:00:00Z",
                        "GIT_COMMITTER_DATE": "2026-08-31T03:00:00Z",
                    },
                    check=True,
                    capture_output=True,
                )
                unrelated_base = git(unrelated, "rev-parse", "HEAD").stdout.decode().strip()
                (unrelated / "feature.txt").write_text("head-a\n", encoding="utf-8")
                git(unrelated, "add", "feature.txt")
                subprocess.run(
                    reporter.git_command(unrelated, "commit", "-q", "-m", "head-a"),
                    env={
                        **reporter.git_environment(offline=True),
                        "GIT_AUTHOR_DATE": "2026-08-31T03:01:00Z",
                        "GIT_COMMITTER_DATE": "2026-08-31T03:01:00Z",
                    },
                    check=True,
                    capture_output=True,
                )
                unrelated_a = git(unrelated, "rev-parse", "HEAD").stdout.decode().strip()
                git(unrelated, "checkout", "-q", unrelated_base)
                (unrelated / "feature.txt").write_text("proposed\n", encoding="utf-8")
                git(unrelated, "add", "feature.txt")
                subprocess.run(
                    reporter.git_command(unrelated, "commit", "-q", "-m", "proposed"),
                    env={
                        **reporter.git_environment(offline=True),
                        "GIT_AUTHOR_DATE": "2026-08-31T03:02:00Z",
                        "GIT_COMMITTER_DATE": "2026-08-31T03:02:00Z",
                    },
                    check=True,
                    capture_output=True,
                )
                unrelated_b = git(unrelated, "rev-parse", "HEAD").stdout.decode().strip()
                unrelated_contract = copy.deepcopy(contract)
                unrelated_contract["base_sha"] = unrelated_base
                unrelated_contract["original_pre_review_head"] = unrelated_a
                unrelated_contract["candidate_sha"] = unrelated_b
                unrelated_payload = payload(unrelated_a)
                unrelated_payload["data"]["repository"]["pullRequest"]["baseRefOid"] = unrelated_base
                unrelated_payload["data"]["repository"]["pullRequest"]["headRefOid"] = unrelated_a
                for node in unrelated_payload["data"]["repository"]["pullRequest"]["commits"]["nodes"]:
                    node["commit"]["oid"] = unrelated_a
                for review in unrelated_payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"]:
                    review["commit"]["oid"] = unrelated_a
                unrelated_report = review_report(
                    unrelated_base,
                    unrelated_a,
                    ["feature.txt"],
                    review_family.derive_change_records(unrelated, unrelated_base, unrelated_a),
                )
                unrelated_receipt = signed_receipt(
                    reporter.normalized_json(unrelated_report),
                    base=unrelated_base,
                    candidate=unrelated_a,
                    nonce="pre-push-unrelated-0003",
                )
                unrelated_replay = unrelated / "build" / "replay"
                unrelated_replay.mkdir(parents=True, exist_ok=True)
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "not a descendant of the current remote head",
                ):
                    trusted_review_gate._run_trusted_gate(
                        raw_contract=unrelated_contract,
                        repository_root=unrelated,
                        expected_candidate=unrelated_b,
                        expected_remote_head=unrelated_a,
                        expected_base=unrelated_base,
                        review_receipt_bytes=unrelated_receipt,
                        replay_store=unrelated_replay,
                        trusted_key_id=KEY_ID,
                        trusted_key_epoch=KEY_EPOCH,
                        trusted_key=KEY,
                        current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                        adapter=StaticAdapter(unrelated_payload),
                        clock=clock,
                    )
            finally:
                shutil.rmtree(unrelated)

            bad_disposition = payload(head_c)
            bad_disposition["data"]["repository"]["pullRequest"]["comments"]["nodes"][0] = (
                authoritative_disposition_comment(
                    held_round=3,
                    held_head_sha=head_b,
                    authorized_next_head_sha=proposed_head,
                    comment_id="DISPOSITION_PREPUSH_3",
                    created_at="2026-08-31T03:13:30Z",
                )
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "exact held round/head and next head",
            ):
                trusted_review_gate._run_trusted_gate(
                    raw_contract=contract,
                    repository_root=repository,
                    expected_candidate=proposed_head,
                    expected_remote_head=head_c,
                    expected_base=base,
                    review_receipt_bytes=make_receipt("pre-push-disposition-0004"),
                    replay_store=fresh_replay("pre-push-disposition-replay"),
                    trusted_key_id=KEY_ID,
                    trusted_key_epoch=KEY_EPOCH,
                    trusted_key=KEY,
                    current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                    adapter=StaticAdapter(bad_disposition),
                    clock=clock,
                )

            dirty = repository / "dirty.txt"
            dirty.write_text("dirty\n", encoding="utf-8")
            try:
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "tracked, index, or untracked changes|must be clean|clean candidate worktree",
                ):
                    trusted_review_gate._run_trusted_gate(
                        raw_contract=contract,
                        repository_root=repository,
                        expected_candidate=proposed_head,
                        expected_remote_head=head_c,
                        expected_base=base,
                        review_receipt_bytes=make_receipt("pre-push-dirty-0005"),
                        replay_store=fresh_replay("pre-push-dirty-replay"),
                        trusted_key_id=KEY_ID,
                        trusted_key_epoch=KEY_EPOCH,
                        trusted_key=KEY,
                        current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                        adapter=StaticAdapter(payload(head_c)),
                        clock=clock,
                    )
            finally:
                dirty.unlink()

            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "state changed during gate evaluation|expected remote head",
            ):
                trusted_review_gate._run_trusted_gate(
                    raw_contract=contract,
                    repository_root=repository,
                    expected_candidate=proposed_head,
                    expected_remote_head=head_c,
                    expected_base=base,
                    review_receipt_bytes=make_receipt("pre-push-race-0006"),
                    replay_store=fresh_replay("pre-push-race-replay"),
                    trusted_key_id=KEY_ID,
                    trusted_key_epoch=KEY_EPOCH,
                    trusted_key=KEY,
                    current_time=datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
                    adapter=StaticAdapter(payload(head_c), payload("f" * 40)),
                    clock=clock,
                )
        finally:
            shutil.rmtree(repository)

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
                *trusted_review_gate.ASSERTION_INPUT_PATHS,
            ):
                write_optional_tree_file(
                    repository, relative, optional_file_bytes(ROOT / relative)
                )
            (repository / ".gitignore").write_text(
                "build/\n", encoding="utf-8"
            )
            (repository / "feature.txt").write_text("base\n", encoding="utf-8")
            self.write_decision_record(
                repository,
                self.decision_entry(
                    risks=("lifecycle", "protocol"),
                    triggers=("changed-files", "risk-boundary"),
                ),
            )
            baseline_inputs = {
                relative: optional_file_bytes(repository / relative)
                for relative in trusted_review_gate.ASSERTION_INPUT_PATHS
            }

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

            def restore_baseline():
                for relative, payload in baseline_inputs.items():
                    write_optional_tree_file(repository, relative, payload)

            def replace_once(relative, old, new):
                path = repository / relative
                text = path.read_text(encoding="utf-8")
                if old not in text:
                    raise AssertionError(f"missing pattern in {relative}")
                path.write_text(text.replace(old, new, 1), encoding="utf-8")

            def set_member_health(family, member, healthy):
                if healthy:
                    return
                if (family, member) == ("action", "actions"):
                    replace_once(
                        "scripts/workflow_pilot/review_base_checker.py",
                        'ACTION_SEQUENCE = ("read-candidate", "emit-local-report")',
                        'ACTION_SEQUENCE = ("emit-local-report", "read-candidate")',
                    )
                    return
                if (family, member) == ("generated", "owners"):
                    decision_path = repository / "docs/test-cases/registry.json"
                    registry = json.loads(decision_path.read_text(encoding="utf-8"))
                    feature = next(
                        item
                        for item in registry["features"]
                        if item["id"] == "workflow-governance"
                    )
                    feature["issue_urls"] = [
                        url
                        for url in feature["issue_urls"]
                        if url != ISSUE_179_URL
                    ]
                    decision_path.write_bytes(reporter.normalized_json(registry))
                    return
                if (family, member) == ("lifecycle", "entries"):
                    replace_once(
                        "scripts/workflow_pilot/review_family.py",
                        '"reason": "third-consecutive-change-request",',
                        '"reason": "held-head-broken",',
                    )
                    return
                if (family, member) == ("resource", "enabled"):
                    replace_once(
                        "scripts/workflow_pilot/trusted_review_gate.py",
                        "    if authoritative is None:\n",
                        "    if True:\n",
                    )
                    return
                if (family, member) == ("wire", "producers"):
                    replace_once(
                        "scripts/workflow_pilot/trusted_review_gate.py",
                        '"result_manifest": build_result_manifest(execution_receipts),',
                        '"candidate_manifest": build_result_manifest(execution_receipts),',
                    )
                    return
                if (family, member) == ("action", "items"):
                    replace_once(
                        "scripts/workflow_pilot/review_base_checker.py",
                        '"finding_member": parsed["member"],',
                        '"finding_member": parsed["family"],',
                    )
                    return
                raise AssertionError(f"unsupported member {family}/{member}")

            heads = []
            for index, timestamp in enumerate(commit_times, 1):
                restore_baseline()
                if index <= len(affected_sequence):
                    family, member = affected_sequence[index - 1]
                    set_member_health(family, member, False)
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
                            "updatedAt": iso(
                                datetime.fromisoformat(
                                    submitted_at.replace("Z", "+00:00")
                                )
                                - timedelta(seconds=30)
                            ),
                            "body": "member-specific finding",
                            "author": copilot_graphql_actor(),
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
                        "author": copilot_graphql_actor(),
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
                *[
                    authoritative_family_comment(
                        base_sha=base,
                        original_head=heads[0],
                        review_id=f"REMOTE_MULTI_{round_number}",
                        candidate_sha=heads[round_number - 1],
                        mappings=[
                            {
                                "finding_id": f"FINDING_MULTI_{round_number}",
                                "family": affected_sequence[round_number - 1][0],
                            }
                        ],
                        comment_id=f"CLASSIFICATION_MULTI_{round_number}",
                        created_at=iso(
                            datetime.fromisoformat(
                                review_times[round_number - 1].replace("Z", "+00:00")
                            )
                            + timedelta(seconds=30)
                        ),
                        updated_at=iso(
                            datetime.fromisoformat(
                                review_times[round_number - 1].replace("Z", "+00:00")
                            )
                            + timedelta(seconds=30)
                        ),
                    )
                    for round_number in range(1, 7)
                ],
                authoritative_disposition_comment(
                    held_round=3,
                    held_head_sha=heads[2],
                    authorized_next_head_sha=heads[3],
                    comment_id="DISPOSITION_MULTI_3",
                    created_at="2026-08-31T03:15:30Z",
                ),
                authoritative_disposition_comment(
                    held_round=6,
                    held_head_sha=heads[5],
                    authorized_next_head_sha=heads[6],
                    comment_id="DISPOSITION_MULTI_6",
                    created_at="2026-08-31T03:21:30Z",
                ),
            ]
            receipt = signed_receipt(
                reporter.normalized_json(report),
                base=base,
                candidate=heads[0],
                nonce="multi-head-receipt-0001",
            )
            envelope = json.loads(receipt)
            trigger = trusted_review_gate._load_authoritative_trigger(
                review_family.validate_contract(contract),
                repository,
                heads[-1],
            )
            collected = json.loads(
                trusted_review_gate.collect_live_evidence_bytes(
                    contract,
                    repository,
                    heads[-1],
                    heads[-1],
                    report,
                    envelope,
                    [],
                    authoritative_trigger=trigger,
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
            validated_contract = review_family.validate_contract(contract)
            receipt_sha256 = hashlib.sha256(receipt).hexdigest()
            round_clock = lambda: datetime(2026, 8, 31, 3, 24, tzinfo=timezone.utc)
            wire_receipt = trusted_review_gate.run_base_pinned_checker(repository, contract=validated_contract, candidate_sha=heads[5], review_round=6, review_context=collected["remote_reviews"][5], all_remote_reviews=collected["remote_reviews"], remote_findings=collected["findings"], remote_finding_ids=collected["remote_reviews"][5]["finding_ids"], captured_github_payload=payload, original_review_report_bytes=reporter.normalized_json(report), original_review_receipt=envelope, original_receipt_sha256=receipt_sha256, assertion_requests=review_family.build_assertion_requests(contract, collected, heads[5], 6), trusted_key=KEY, clock=round_clock)
            self.assertNotEqual(wire_receipt["result"], "fail")
            self.assertIn(next(item for item in wire_receipt["assertion_results"] if item["assertion_id"] == review_family.member_assertion_id("wire", "producers", "affected-fixed"))["status"], {"pass", "hold"})

            git(repository, "checkout", "-q", base)
            restore_baseline()
            (repository / "feature.txt").write_text("rewritten-head\n", encoding="utf-8")
            rewritten_head = commit("rewritten head", "2026-08-31T03:24:00Z")
            git(repository, "checkout", "-q", heads[-1])
            rewritten_reviews = copy.deepcopy(collected["remote_reviews"])
            rewritten_reviews[5]["candidate_sha"] = rewritten_head
            rewritten_receipt = trusted_review_gate.run_base_pinned_checker(repository, contract=validated_contract, candidate_sha=rewritten_head, review_round=6, review_context=rewritten_reviews[5], all_remote_reviews=rewritten_reviews, remote_findings=collected["findings"], remote_finding_ids=rewritten_reviews[5]["finding_ids"], captured_github_payload=payload, original_review_report_bytes=reporter.normalized_json(report), original_review_receipt=envelope, original_receipt_sha256=receipt_sha256, assertion_requests=review_family.build_assertion_requests(contract, {"remote_reviews": rewritten_reviews, "pre_review_findings": collected["pre_review_findings"]}, rewritten_head, 6), trusted_key=KEY, clock=round_clock)
            self.assertEqual(rewritten_receipt["result"], "fail")

            ticks = count()

            def clock():
                return datetime(
                    2026, 8, 31, 3, 24, tzinfo=timezone.utc
                ) + timedelta(seconds=next(ticks))

            result = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repository,
                expected_candidate=heads[-1],
                expected_remote_head=heads[-1],
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
            self.assertTrue(result["authority_hold"]["required"])
            self.assertEqual(
                result["authority_hold"]["reason"],
                "authority-dependency-changed",
            )
            self.assertFalse(result["gates"]["merge_allowed"])
            preserved_result = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repository,
                expected_candidate=heads[-1],
                expected_remote_head=heads[-1],
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
            self.assertTrue(preserved_result["authority_hold"]["required"])
            self.assertFalse(preserved_result["gates"]["merge_allowed"])

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
                    expected_remote_head=heads[-1],
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

            repeated = trusted_review_gate._run_trusted_gate(
                raw_contract=contract,
                repository_root=repository,
                expected_candidate=heads[-1],
                expected_remote_head=heads[-1],
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
            self.assertEqual(
                repeated["architecture_hold"]["consumed_disposition_ids"],
                ["DISPOSITION_MULTI_3", "DISPOSITION_MULTI_6"],
            )
        finally:
            shutil.rmtree(repository)

    def test_fixture_query_shape_matches_actor_fragment_response(self):
        payload = self.adapter()
        pr = payload["data"]["repository"]["pullRequest"]
        self.assertEqual(payload["data"]["repository"]["id"], "REPO_SYNTHETIC_901")
        self.assertEqual(
            payload["data"]["repository"]["nameWithOwner"],
            "laqieer/fireemblem8-expansion",
        )
        self.assertEqual(payload["data"]["viewer"]["__typename"], "User")
        self.assertEqual(payload["data"]["repository"]["owner"]["__typename"], "User")
        self.assertEqual(pr["author"]["__typename"], "User")
        self.assertEqual(
            pr["reviews"]["nodes"][0]["author"],
            copilot_graphql_actor(),
        )
        self.assertEqual(pr["comments"]["nodes"], [])
        self.assertIsNone(pr["commits"]["nodes"][0]["commit"]["pushedDate"])
        self.assertEqual(pr["baseRefOid"], BASE)


if __name__ == "__main__":
    unittest.main()
