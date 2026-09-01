import copy
import json
import os
import shutil
import subprocess
import unittest
from itertools import count
from pathlib import Path

from scripts.workflow_pilot import reporter, review_base_checker, review_family


ROOT = Path(__file__).resolve().parents[3]
ASSERTION_PROGRAM = "scripts/workflow_pilot/review_assertions.py"
ASSERTION_SUBJECTS = review_base_checker.ASSERTION_SUBJECT_PATHS


def git_text(root, *arguments):
    return subprocess.run(
        reporter.git_command(root, *arguments),
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def git_bytes(root, *arguments):
    return subprocess.run(
        reporter.git_command(root, *arguments),
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
    ).stdout


def changed_files(changes):
    return sorted(
        {
            path
            for change in changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )


class ReviewBaseCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifacts = ROOT / "build" / "test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        cls.root = artifacts / f"review-base-checker-{os.getpid()}"
        cls.repo = cls.root / "repo"
        cls.root.mkdir(parents=True)
        cls.repo.mkdir()
        cls.case_ids = count()
        cls.subject_snapshots = {
            relative: (ROOT / relative).read_bytes()
            for relative in ASSERTION_SUBJECTS
        }
        subprocess.run(
            reporter.git_command(cls.repo, "init", "-q"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(
                cls.repo, "config", "user.email", "test@example.com"
            ),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(cls.repo, "config", "user.name", "Checker Test"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        for relative in (
            review_base_checker.CHECKER_RELPATH,
            ASSERTION_PROGRAM,
            *ASSERTION_SUBJECTS,
        ):
            target = cls.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        (cls.repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        cls._restore_subject("action", "actions")
        cls._restore_subject("action", "items")
        cls._break_subject("action", "actions")
        subprocess.run(
            reporter.git_command(cls.repo, "add", "-A"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(cls.repo, "commit", "-q", "-m", "base"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        cls.base = git_text(cls.repo, "rev-parse", "HEAD")
        cls.base_tree = git_text(cls.repo, "rev-parse", "HEAD^{tree}")

        cls._restore_subject("action", "actions")
        cls._break_subject("action", "items")
        subprocess.run(
            reporter.git_command(cls.repo, "add", "-A"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(cls.repo, "commit", "-q", "-m", "head-1"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        cls.head1 = git_text(cls.repo, "rev-parse", "HEAD")
        cls.head1_tree = git_text(cls.repo, "rev-parse", "HEAD^{tree}")

        cls._restore_subject("action", "items")
        subprocess.run(
            reporter.git_command(cls.repo, "add", "-A"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(cls.repo, "commit", "-q", "-m", "head-2"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        cls.head2 = git_text(cls.repo, "rev-parse", "HEAD")
        cls.head2_tree = git_text(cls.repo, "rev-parse", "HEAD^{tree}")
        cls.program_blob_oid = git_text(
            cls.repo, "rev-parse", f"{cls.base}:{ASSERTION_PROGRAM}"
        )
        cls.original_changes = review_family.derive_change_records(
            cls.repo, cls.base, cls.head1
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    @classmethod
    def _subject_path(cls, family, member):
        return (
            cls.repo
            / "scripts/workflow_pilot/assertion_subjects"
            / f"{family}_{member.replace('-', '_')}.json"
        )

    @classmethod
    def _restore_subject(cls, family, member):
        relative = (
            "scripts/workflow_pilot/assertion_subjects/"
            f"{family}_{member.replace('-', '_')}.json"
        )
        cls._subject_path(family, member).write_bytes(cls.subject_snapshots[relative])

    @classmethod
    def _break_subject(cls, family, member):
        path = cls._subject_path(family, member)
        subject = json.loads(path.read_text(encoding="utf-8"))
        if (family, member) == ("action", "actions"):
            subject["payload"]["actions"] = [
                "emit-local-report",
                "read-candidate",
            ]
        elif (family, member) == ("action", "items"):
            subject["payload"]["items"] = [
                "semantic-output",
                "semantic-output",
            ]
        else:
            raise AssertionError(f"unsupported broken subject {family}/{member}")
        path.write_bytes(reporter.normalized_json(subject))

    def case_dir(self):
        case_root = self.root / f"case-{next(self.case_ids)}"
        case_root.mkdir()
        return case_root

    def materialize_subject_root(self, commit_sha, destination):
        destination.mkdir()
        for relative in ASSERTION_SUBJECTS:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git_bytes(self.repo, "show", f"{commit_sha}:{relative}"))

    def materialize_program(self, destination):
        destination.write_bytes(
            git_bytes(self.repo, "show", f"{self.base}:{ASSERTION_PROGRAM}")
        )

    def review_report(self):
        return {
            "schema_version": 2,
            "report_id": "PRE_REVIEW_001",
            "repository": "example/project",
            "pull_request": 7,
            "base_sha": self.base,
            "candidate_sha": self.head1,
            "reviewer_actor_id": "REVIEWER_1",
            "reviewer_login": "fresh-reviewer",
            "implementer_actor_id": "IMPLEMENTER_1",
            "implementer_login": "implementer",
            "started_at": "2026-09-01T00:00:00Z",
            "completed_at": "2026-09-01T00:01:00Z",
            "permissions": ["contents:read"],
            "actions": ["read-candidate", "emit-local-report"],
            "reviewed_files": changed_files(self.original_changes),
            "reviewed_changes": copy.deepcopy(self.original_changes),
            "findings": [
                {
                    "id": "LOCAL-ACTION-1",
                    "family": "action",
                    "created_at": "2026-09-01T00:00:30Z",
                }
            ],
        }

    def remote_reviews(self):
        round1 = {
            "id": 1001,
            "node_id": "REMOTE_REVIEW_1",
            "round": 1,
            "reviewer_actor_id": "COPILOT",
            "candidate_sha": self.head1,
            "submitted_at": "2026-09-01T00:02:00Z",
            "state": "COMMENTED",
            "body": "### 🟡 Changes recommended",
            "body_classification": "changes-recommended",
            "body_has_findings": True,
            "outcome": "changes-requested",
            "finding_ids": ["FINDING-ACTION-1"],
        }
        round2 = {
            "id": 1002,
            "node_id": "REMOTE_REVIEW_2",
            "round": 2,
            "reviewer_actor_id": "COPILOT",
            "candidate_sha": self.head2,
            "submitted_at": "2026-09-01T00:04:00Z",
            "state": "COMMENTED",
            "body": "### 🟢 Approval recommended",
            "body_classification": "clean-approval",
            "body_has_findings": False,
            "outcome": "clean",
            "finding_ids": [],
        }
        return round1, round2

    def remote_findings(self):
        return [
            {
                "node_id": "FINDING-ACTION-1",
                "review_id": "REMOTE_REVIEW_1",
                "candidate_sha": self.head1,
                "created_at": "2026-09-01T00:01:30Z",
                "author_actor_id": "COPILOT",
                "family": "action",
            }
        ]

    def assertion_artifacts(self, origin_sha, head_sha):
        return [
            {
                "path": relative,
                "origin_blob_oid": git_text(
                    self.repo, "rev-parse", f"{origin_sha}:{relative}"
                ),
                "head_blob_oid": git_text(
                    self.repo, "rev-parse", f"{head_sha}:{relative}"
                ),
            }
            for relative in ASSERTION_SUBJECTS
        ]

    def build_input(self, *, review_round, assertion_requests=None):
        case_root = self.case_dir()
        origin_sha = self.base if review_round == 1 else self.head1
        head_sha = self.head1 if review_round == 1 else self.head2
        origin_root = case_root / "origin"
        head_root = case_root / "head"
        self.materialize_subject_root(origin_sha, origin_root)
        self.materialize_subject_root(head_sha, head_root)
        program_path = case_root / "review_assertions.py"
        self.materialize_program(program_path)
        round1, round2 = self.remote_reviews()
        all_remote_reviews = [round1] if review_round == 1 else [round1, round2]
        review_context = all_remote_reviews[review_round - 1]
        changes = review_family.derive_change_records(self.repo, self.base, head_sha)
        data = {
            "schema_version": 2,
            "repository": "example/project",
            "repository_root": str(self.repo),
            "pull_request": 7,
            "base_sha": self.base,
            "base_tree": self.base_tree,
            "original_pre_review_head": self.head1,
            "original_changes": copy.deepcopy(self.original_changes),
            "original_receipt_sha256": "9" * 64,
            "assertion_program_path": str(program_path),
            "assertion_program_blob_oid": self.program_blob_oid,
            "assertion_program_argv": list(review_base_checker.ASSERTION_PROGRAM_ARGV),
            "finding_origin_sha": origin_sha,
            "finding_origin_tree": git_text(self.repo, "rev-parse", f"{origin_sha}^{{tree}}"),
            "origin_root": str(origin_root),
            "head_root": str(head_root),
            "assertion_input_artifacts": self.assertion_artifacts(origin_sha, head_sha),
            "candidate_sha": head_sha,
            "candidate_tree": git_text(self.repo, "rev-parse", f"{head_sha}^{{tree}}"),
            "head_sha": head_sha,
            "review_round": review_round,
            "review_context": copy.deepcopy(review_context),
            "all_remote_reviews": copy.deepcopy(all_remote_reviews),
            "remote_findings": copy.deepcopy(self.remote_findings()),
            "trust_mode": "base-pinned",
            "pre_review_required": True,
            "changed_files": changed_files(changes),
            "changes": changes,
            "remote_finding_ids": copy.deepcopy(review_context["finding_ids"]),
            "limits": {
                "max_duration_minutes": 30,
                "max_findings_per_review": 10,
                "max_reviewed_files": 40,
                "max_siblings_per_finding": 5,
                "max_siblings_per_handoff": 50,
            },
            "original_pre_review": self.review_report(),
            "assertion_requests": copy.deepcopy(
                assertion_requests
                or (
                    [
                        {
                            "assertion_id": (
                                "registry:behavior:actor-permission-bounds:positive:v2"
                            ),
                            "finding_id": None,
                        },
                        {
                            "assertion_id": (
                                "registry:sibling:action:actions:affected-fixed:v2"
                            ),
                            "finding_id": "LOCAL-ACTION-1",
                        },
                    ]
                    if review_round == 1
                    else [
                        {
                            "assertion_id": (
                                "registry:behavior:round-lifecycle:runtime:v2"
                            ),
                            "finding_id": None,
                        },
                        {
                            "assertion_id": (
                                "registry:sibling:action:items:affected-fixed:v2"
                            ),
                            "finding_id": "FINDING-ACTION-1",
                        },
                    ]
                )
            ),
        }
        return data

    def execute(self, data):
        return review_base_checker.execute_registry(copy.deepcopy(data))

    def assert_rejected(self, data, message):
        with self.assertRaisesRegex(review_base_checker.CheckError, message):
            self.execute(data)

    def test_round_one_executes_local_finding_with_authoritative_binding(self):
        result = self.execute(self.build_input(review_round=1))
        self.assertEqual(result["registry_version"], 1)
        self.assertEqual(len(result["results"]), 2)
        behavior = next(
            item
            for item in result["results"]
            if item["authority_binding"]["finding_id"] is None
        )
        self.assertEqual(behavior["authority_binding"]["head_sha"], self.head1)
        self.assertEqual(behavior["authority_binding"]["head_tree"], self.head1_tree)
        member = next(
            item
            for item in result["results"]
            if item["authority_binding"]["finding_id"] == "LOCAL-ACTION-1"
        )
        self.assertEqual(member["authority_binding"]["finding_family"], "action")
        self.assertEqual(member["authority_binding"]["finding_member"], "actions")
        self.assertEqual(member["authority_binding"]["finding_review_round"], 0)
        self.assertEqual(member["authority_binding"]["finding_head_sha"], self.head1)
        self.assertEqual(member["authority_binding"]["finding_origin_sha"], self.base)
        self.assertEqual(member["authority_binding"]["head_sha"], self.head1)
        self.assertEqual(member["output"]["origin_status"], "fail")
        self.assertEqual(member["output"]["head_status"], "pass")

    def test_registered_not_applicable_requires_exact_reason_context(self):
        data = self.build_input(
            review_round=1,
            assertion_requests=[
                {
                    "assertion_id": (
                        "registry:sibling:resource:disabled:not-applicable:"
                        "feature-disabled-by-contract:v2"
                    ),
                    "finding_id": "LOCAL-RESOURCE-1",
                }
            ],
        )
        data["original_pre_review"]["findings"] = [
            {
                "id": "LOCAL-RESOURCE-1",
                "family": "resource",
                "created_at": "2026-09-01T00:00:30Z",
            }
        ]
        result = self.execute(data)
        self.assertEqual(
            result["results"][0]["output"]["reason"],
            "feature-disabled-by-contract",
        )

    def test_fake_finding_id_and_wrong_family_real_id_fail(self):
        data = self.build_input(
            review_round=1,
            assertion_requests=[
                {
                    "assertion_id": "registry:sibling:action:items:verified-unaffected:v2",
                    "finding_id": "LOCAL-FAKE-99",
                }
            ],
        )
        self.assert_rejected(data, "authoritative source round")

        data = self.build_input(
            review_round=1,
            assertion_requests=[
                {
                    "assertion_id": "registry:sibling:wire:producers:verified-unaffected:v2",
                    "finding_id": "LOCAL-ACTION-1",
                }
            ],
        )
        self.assert_rejected(data, "family does not match")

    def test_reused_checkout_and_fake_origin_or_program_identity_fail(self):
        data = self.build_input(review_round=1)
        data["origin_root"] = str(self.repo)
        data["head_root"] = str(self.repo)
        self.assert_rejected(data, "assertion subjects|reuse one checkout")

        data = self.build_input(review_round=1)
        data["finding_origin_sha"] = "f" * 40
        self.assert_rejected(data, "finding_origin_sha")

        data = self.build_input(review_round=1)
        data["finding_origin_tree"] = "e" * 40
        self.assert_rejected(data, "authoritative round binding")

        data = self.build_input(review_round=1)
        data["assertion_program_blob_oid"] = "d" * 40
        self.assert_rejected(data, "assertion program")

    def test_dirty_and_swapped_materialized_roots_fail(self):
        data = self.build_input(review_round=2)
        dirty = Path(data["head_root"]) / "dirty.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        try:
            self.assert_rejected(data, "assertion subjects")
        finally:
            dirty.unlink()

        data = self.build_input(review_round=2)
        origin_root = data["origin_root"]
        data["origin_root"] = data["head_root"]
        data["head_root"] = origin_root
        self.assert_rejected(data, "exact Git blob authority")

    def test_stale_round_head_and_current_finding_collection_fail(self):
        data = self.build_input(review_round=2)
        data["review_context"]["candidate_sha"] = self.head1
        self.assert_rejected(data, "current assertion round/head")

        data = self.build_input(review_round=2)
        data["review_context"]["round"] = 1
        self.assert_rejected(data, "current assertion round/head")

        data = self.build_input(review_round=2)
        data["remote_finding_ids"] = ["FINDING-ACTION-1"]
        self.assert_rejected(data, "current review findings")

    def test_status_coverage_and_original_report_are_exact(self):
        data = self.build_input(review_round=1)
        data["original_pre_review"]["reviewed_changes"].pop()
        self.assert_rejected(data, "status/blob evidence")

        data = self.build_input(review_round=1)
        data["changes"][0]["status"] = "X"
        self.assert_rejected(data, "status is not supported")

        data = self.build_input(review_round=1)
        data["changed_files"].append("fabricated.txt")
        self.assert_rejected(data, "changed files do not match status record")

    def test_local_namespace_actor_and_limits_remain_strict(self):
        data = self.build_input(review_round=1)
        data["original_pre_review"]["findings"][0]["id"] = "REMOTE_NODE"
        self.assert_rejected(data, "LOCAL- namespace")

        data = self.build_input(review_round=1)
        data["original_pre_review"]["reviewer_login"] = "IMPLEMENTER_bot"
        self.assert_rejected(data, "identities overlap")

        data = self.build_input(review_round=1)
        data["limits"]["max_reviewed_files"] = True
        self.assert_rejected(data, "must be an integer")

    def test_round_two_remote_finding_executes_real_remediation_round(self):
        data = self.build_input(review_round=2)
        self.assertEqual(data["original_pre_review"]["candidate_sha"], self.head1)
        result = self.execute(data)
        self.assertEqual(result["registry_version"], 1)
        self.assertEqual(len(result["results"]), 2)
        member = next(
            item
            for item in result["results"]
            if item["authority_binding"]["finding_id"] == "FINDING-ACTION-1"
        )
        self.assertEqual(member["authority_binding"]["finding_family"], "action")
        self.assertEqual(member["authority_binding"]["finding_member"], "items")
        self.assertEqual(
            member["authority_binding"]["finding_review_id"], "REMOTE_REVIEW_1"
        )
        self.assertEqual(member["authority_binding"]["finding_review_round"], 1)
        self.assertEqual(member["authority_binding"]["finding_head_sha"], self.head1)
        self.assertEqual(member["authority_binding"]["finding_head_tree"], self.head1_tree)
        self.assertEqual(
            member["authority_binding"]["finding_origin_sha"], self.head1
        )
        self.assertEqual(
            member["authority_binding"]["finding_origin_tree"], self.head1_tree
        )
        self.assertEqual(member["authority_binding"]["head_sha"], self.head2)
        self.assertEqual(member["authority_binding"]["head_tree"], self.head2_tree)
        self.assertEqual(member["output"]["origin_status"], "fail")
        self.assertEqual(member["output"]["head_status"], "pass")


if __name__ == "__main__":
    unittest.main()
