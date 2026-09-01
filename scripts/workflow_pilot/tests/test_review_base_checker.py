import copy
import json
import os
import shutil
import subprocess
import unittest
from itertools import count
from pathlib import Path

from scripts.workflow_pilot import (
    reporter,
    review_assertions,
    review_base_checker,
    review_family,
)


ROOT = Path(__file__).resolve().parents[3]
ASSERTION_PROGRAM = "scripts/workflow_pilot/review_assertions.py"
ASSERTION_INPUTS = review_base_checker.ASSERTION_INPUT_PATHS
ISSUE_179_URL = "https://github.com/laqieer/fireemblem8-expansion/issues/179"


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
        cls.input_snapshots = {
            relative: (ROOT / relative).read_bytes() for relative in ASSERTION_INPUTS
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
        (cls.repo / ".gitignore").write_text("build/\n", encoding="utf-8")

        cls._restore_baseline()
        cls._set_generated_owners_health(False)
        cls.base = cls._commit("base")
        cls.base_tree = git_text(cls.repo, "rev-parse", f"{cls.base}^{{tree}}")

        cls._restore_baseline()
        cls._set_action_items_health(False)
        cls.head1 = cls._commit("head-1")
        cls.head1_tree = git_text(cls.repo, "rev-parse", f"{cls.head1}^{{tree}}")

        cls._restore_baseline()
        cls.head2 = cls._commit("head-2")
        cls.head2_tree = git_text(cls.repo, "rev-parse", f"{cls.head2}^{{tree}}")

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
    def _write_relative(cls, relative, data):
        target = cls.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_text(data, encoding="utf-8")

    @classmethod
    def _restore_baseline(cls):
        for relative, payload in cls.input_snapshots.items():
            cls._write_relative(relative, payload)
        obsolete = cls.repo / "scripts/workflow_pilot/assertion_subjects"
        if obsolete.exists():
            shutil.rmtree(obsolete)

    @classmethod
    def _replace_once(cls, relative, old, new):
        path = cls.repo / relative
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise AssertionError(f"missing pattern in {relative}: {old}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    @classmethod
    def _commit(cls, message):
        subprocess.run(
            reporter.git_command(cls.repo, "add", "-A"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(cls.repo, "commit", "-q", "-m", message),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        return git_text(cls.repo, "rev-parse", "HEAD")

    @classmethod
    def _set_action_actions_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/review_base_checker.py",
            'ACTION_SEQUENCE = ("read-candidate", "emit-local-report")',
            'ACTION_SEQUENCE = ("emit-local-report", "read-candidate")',
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/review_base_checker.py",
                'ACTION_SEQUENCE = ("emit-local-report", "read-candidate")',
                'ACTION_SEQUENCE = ("read-candidate", "emit-local-report")',
            )

    @classmethod
    def _set_action_items_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/review_base_checker.py",
            '"finding_member": parsed["member"],',
            '"finding_member": parsed["family"],',
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/review_base_checker.py",
                '"finding_member": parsed["family"],',
                '"finding_member": parsed["member"],',
            )

    @classmethod
    def _make_action_enforcement_bypass_head(cls):
        cls._restore_baseline()
        cls._replace_once(
            "scripts/workflow_pilot/review_base_checker.py",
            '    if report["actions"] != list(ACTION_SEQUENCE):',
            '    if False and report["actions"] != list(ACTION_SEQUENCE):',
        )
        head = cls._commit("action-enforcement-bypass")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")

    @classmethod
    def _make_action_binding_special_case_head(cls):
        cls._restore_baseline()
        cls._replace_once(
            "scripts/workflow_pilot/review_base_checker.py",
            '"finding_member": parsed["member"],',
            '"finding_member": parsed["member"] if finding_id == "FINDING" else parsed["family"],',
        )
        head = cls._commit("action-binding-special-case")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")

    @classmethod
    def _set_generated_owners_health(cls, healthy):
        registry_path = cls.repo / "docs/test-cases/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        feature = next(
            item
            for item in registry["features"]
            if item["id"] == "workflow-governance"
        )
        urls = [url for url in feature["issue_urls"] if url != ISSUE_179_URL]
        feature["issue_urls"] = (
            sorted(set(feature["issue_urls"])) if healthy else urls
        )
        registry_path.write_bytes(reporter.normalized_json(registry))

    @classmethod
    def _set_lifecycle_entries_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/review_family.py",
            '"reason": "third-consecutive-change-request",',
            '"reason": "third-consecutive-change-request-broken",',
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/review_family.py",
                '"reason": "third-consecutive-change-request-broken",',
                '"reason": "third-consecutive-change-request",',
            )

    @classmethod
    def _set_resource_enabled_health(cls, healthy):
        decision_path = cls.repo / ".github/workflow-pilot-decisions.json"
        decisions = json.loads(decision_path.read_text(encoding="utf-8"))
        decisions["pull_requests"] = [
            record
            for record in decisions["pull_requests"]
            if healthy or record["pull_request"] != 189
        ]
        if healthy and all(
            record["pull_request"] != 189 for record in decisions["pull_requests"]
        ):
            decisions["pull_requests"].append(
                {
                    "pull_request": 189,
                    "risk_boundaries": ["lifecycle", "protocol"],
                    "threshold": {
                        "triggers": [
                            "changed-files",
                            "changed-lines",
                            "major-boundaries",
                            "risk-boundary",
                        ],
                        "override_history": [],
                    },
                    "gate_mode": "concurrent",
                    "stack": {
                        "depth": 0,
                        "parent_pr": None,
                        "exception_reason": None,
                    },
                    "pilot": {"included": False, "disposition": "baseline-only"},
                }
            )
        decision_path.write_bytes(reporter.normalized_json(decisions))

    @classmethod
    def _set_wire_producers_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/trusted_review_gate.py",
            '"result_manifest": [',
            '"candidate_manifest": [',
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/trusted_review_gate.py",
                '"candidate_manifest": [',
                '"result_manifest": [',
            )

    @classmethod
    def _make_wire_producer_spoof_head(cls, kind):
        cls._restore_baseline()
        cls._set_wire_producers_health(False)
        path = cls.repo / "scripts/workflow_pilot/trusted_review_gate.py"
        text = path.read_text(encoding="utf-8")
        marker = "    return reporter.normalized_json(raw_evidence)\n"
        spoof = (
            "        raw_evidence = {\n"
            '            "authoritative_trigger": authoritative_trigger,\n'
            '            "execution_receipts": execution_receipts,\n'
            '            "result_manifest": [\n'
            "                result\n"
            '                for receipt in execution_receipts\n'
            '                for result in receipt["assertion_results"]\n'
            "            ],\n"
            "        }\n"
        )
        dead_assignment = (
            "    raw_evidence = {\n"
            '        "authoritative_trigger": authoritative_trigger,\n'
            '        "execution_receipts": execution_receipts,\n'
            '        "result_manifest": [\n'
            "            result\n"
            '            for receipt in execution_receipts\n'
            '            for result in receipt["assertion_results"]\n'
            "        ],\n"
            "    }\n"
        )
        if kind == "false":
            injection = "    if False:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "not-true":
            injection = "    if not True:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "eq-compare":
            injection = "    if 0 == 1:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "gt-compare":
            injection = "    if 1 > 2:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "nested-function":
            injection = "    def _dead_result_manifest():\n" + spoof + "\n"
            text = text.replace(marker, injection + marker, 1)
        elif kind == "after-return":
            text = text.replace(marker, marker + dead_assignment, 1)
        else:
            raise AssertionError(kind)
        path.write_text(text, encoding="utf-8")
        head = cls._commit(f"wire-producer-spoof-{kind}")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")

    @classmethod
    def _make_witness_only_head(cls):
        cls._restore_baseline()
        cls._set_action_items_health(False)
        cls._write_relative(
            "scripts/workflow_pilot/assertion_subjects/action_items.json",
            reporter.normalized_json(
                {
                    "schema_version": 1,
                    "family": "action",
                    "member": "items",
                    "payload": {"items": ["looks", "valid"]},
                }
            ),
        )
        witness_head = cls._commit("witness-only-head")
        return witness_head, git_text(cls.repo, "rev-parse", f"{witness_head}^{{tree}}")

    @classmethod
    def _make_item_spoof_head(cls, kind):
        cls._restore_baseline()
        cls._set_action_items_health(False)
        path = cls.repo / "scripts/workflow_pilot/review_base_checker.py"
        text = path.read_text(encoding="utf-8")
        if kind == "comment":
            text += '\n# "finding_member": parsed["member"],\n'
        elif kind == "docstring":
            text += '\n""" "finding_member": parsed["member"], """\n'
        elif kind == "dead-if":
            text += (
                '\nif False:\n'
                '    SPOOF = {"finding_member": parsed["member"]}\n'
            )
        elif kind == "constant":
            text += '\nSPOOF_FINDING_MEMBER = \'"finding_member": parsed["member"],\'\n'
        else:
            raise AssertionError(kind)
        path.write_text(text, encoding="utf-8")
        head = cls._commit(f"item-spoof-{kind}")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")

    @classmethod
    def _make_item_refactor_head(cls):
        cls._restore_baseline()
        path = cls.repo / "scripts/workflow_pilot/review_base_checker.py"
        text = path.read_text(encoding="utf-8")
        old = (
            '        "finding_member": parsed["member"],\n'
            '        "finding_review_id": finding["review_id"],\n'
        )
        new = (
            '        "finding_review_id": finding["review_id"],\n'
            '        "finding_member"   :   parsed["member"],\n'
        )
        if old not in text:
            raise AssertionError("missing refactor block")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        head = cls._commit("item-refactor")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")

    def case_dir(self):
        case_root = self.root / f"case-{next(self.case_ids)}"
        case_root.mkdir()
        return case_root

    def materialize_input_root(self, commit_sha, destination):
        destination.mkdir()
        for relative in ASSERTION_INPUTS:
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
                    "id": "LOCAL-GENERATED-1",
                    "family": "generated",
                    "created_at": "2026-09-01T00:00:30Z",
                }
            ],
        }

    def remote_reviews(self, second_head=None):
        second_head = self.head2 if second_head is None else second_head
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
            "candidate_sha": second_head,
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
            for relative in ASSERTION_INPUTS
        ]

    def build_input(self, *, review_round, candidate_sha=None, candidate_tree=None, assertion_requests=None):
        case_root = self.case_dir()
        origin_sha = self.base if review_round == 1 else self.head1
        head_sha = (self.head1 if review_round == 1 else self.head2) if candidate_sha is None else candidate_sha
        head_tree = git_text(self.repo, "rev-parse", f"{head_sha}^{{tree}}") if candidate_tree is None else candidate_tree
        origin_root = case_root / "origin"
        head_root = case_root / "head"
        self.materialize_input_root(origin_sha, origin_root)
        self.materialize_input_root(head_sha, head_root)
        program_path = case_root / "review_assertions.py"
        self.materialize_program(program_path)
        round1, round2 = self.remote_reviews(second_head=head_sha)
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
            "candidate_tree": head_tree,
            "head_sha": head_sha,
            "review_round": review_round,
            "review_context": copy.deepcopy(review_context),
            "all_remote_reviews": copy.deepcopy(all_remote_reviews),
            "remote_findings": copy.deepcopy(self.remote_findings()),
            "trust_mode": "base-pinned",
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
                                "registry:sibling:generated:owners:affected-fixed:v2"
                            ),
                            "finding_id": "LOCAL-GENERATED-1",
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

    def evaluate_member_contract(self, family, member, commit_sha, binding=None):
        case_root = self.case_dir()
        root = case_root / "member-root"
        self.materialize_input_root(commit_sha, root)
        return review_assertions.evaluate_member_contract(family, member, root, binding)

    def assert_member_rejected(self, family, member, commit_sha, message, binding=None):
        with self.assertRaisesRegex(review_assertions.AssertionFailure, message):
            self.evaluate_member_contract(family, member, commit_sha, binding)

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
            if item["authority_binding"]["finding_id"] == "LOCAL-GENERATED-1"
        )
        self.assertEqual(member["authority_binding"]["finding_family"], "generated")
        self.assertEqual(member["authority_binding"]["finding_member"], "owners")
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
                    "assertion_id": "registry:sibling:generated:outputs:verified-unaffected:v2",
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
                    "finding_id": "LOCAL-GENERATED-1",
                }
            ],
        )
        self.assert_rejected(data, "family does not match")

    def test_reused_checkout_and_fake_origin_or_program_identity_fail(self):
        data = self.build_input(review_round=1)
        data["origin_root"] = str(self.repo)
        data["head_root"] = str(self.repo)
        self.assert_rejected(data, "production inputs|reuse one checkout")

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
            self.assert_rejected(data, "production inputs")
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

    def test_obsolete_witness_json_cannot_manufacture_pass(self):
        witness_head, witness_tree = self._make_witness_only_head()
        data = self.build_input(
            review_round=2,
            candidate_sha=witness_head,
            candidate_tree=witness_tree,
        )
        self.assert_rejected(data, "member-item authority binding is incomplete")

    def test_comment_docstring_dead_branch_and_constant_spoofs_fail(self):
        for kind in ("comment", "docstring", "dead-if", "constant"):
            with self.subTest(kind=kind):
                spoof_head, spoof_tree = self._make_item_spoof_head(kind)
                data = self.build_input(
                    review_round=2,
                    candidate_sha=spoof_head,
                    candidate_tree=spoof_tree,
                )
                self.assert_rejected(
                    data, "member-item authority binding is incomplete"
                )

    def test_action_sequence_enforcement_bypass_fails(self):
        bypass_head, bypass_tree = self._make_action_enforcement_bypass_head()
        data = self.build_input(
            review_round=2,
            candidate_sha=bypass_head,
            candidate_tree=bypass_tree,
            assertion_requests=[
                {
                    "assertion_id": (
                        "registry:sibling:action:actions:verified-unaffected:v2"
                    ),
                    "finding_id": "FINDING-ACTION-1",
                }
            ],
        )
        self.assert_rejected(data, "read-only action sequence is not enforced")

    def test_action_sequence_verified_unaffected_runs_live_validator(self):
        data = self.build_input(
            review_round=2,
            assertion_requests=[
                {
                    "assertion_id": (
                        "registry:sibling:action:actions:verified-unaffected:v2"
                    ),
                    "finding_id": "FINDING-ACTION-1",
                }
            ],
        )
        result = self.execute(data)
        self.assertEqual(
            result["results"][0]["output"]["program_case"],
            "member/action/actions/verified-unaffected",
        )

    def test_member_binding_special_case_does_not_spoof_real_finding_id(self):
        spoof_head, spoof_tree = self._make_action_binding_special_case_head()
        data = self.build_input(
            review_round=2,
            candidate_sha=spoof_head,
            candidate_tree=spoof_tree,
        )
        self.assert_rejected(data, "member-item authority binding is incomplete")

    def test_whitespace_and_order_refactor_preserves_member_fix(self):
        refactor_head, refactor_tree = self._make_item_refactor_head()
        data = self.build_input(
            review_round=2,
            candidate_sha=refactor_head,
            candidate_tree=refactor_tree,
        )
        result = self.execute(data)
        member = next(
            item
            for item in result["results"]
            if item["authority_binding"]["finding_id"] == "FINDING-ACTION-1"
        )
        self.assertEqual(member["output"]["origin_status"], "fail")
        self.assertEqual(member["output"]["head_status"], "pass")

    def test_wire_producer_dead_ast_spoofs_fail(self):
        for kind in (
            "false",
            "not-true",
            "eq-compare",
            "gt-compare",
            "nested-function",
            "after-return",
        ):
            with self.subTest(kind=kind):
                spoof_head, _ = self._make_wire_producer_spoof_head(kind)
                self.assert_member_rejected(
                    "wire",
                    "producers",
                    spoof_head,
                    "wire producers are incomplete",
                )

    def test_wire_producer_real_contract_still_passes(self):
        self.assertEqual(
            self.evaluate_member_contract("wire", "producers", self.head2),
            {"producers": True},
        )

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
