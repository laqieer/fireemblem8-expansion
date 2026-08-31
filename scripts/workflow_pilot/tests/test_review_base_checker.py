import copy
import json
import os
import shutil
import unittest
from pathlib import Path

from scripts.workflow_pilot import reporter, review_base_checker


ROOT = Path(__file__).resolve().parents[3]


def modified_change(path, old, new):
    return {
        "status": "M",
        "similarity": None,
        "old_path": path,
        "new_path": path,
        "base_mode": "100644",
        "base_blob_oid": old * 40,
        "head_mode": "100644",
        "head_blob_oid": new * 40,
    }


def original_report(changes):
    return {
        "schema_version": 2,
        "report_id": "REPORT_1",
        "repository": "example/project",
        "pull_request": 7,
        "base_sha": "a" * 40,
        "candidate_sha": "c" * 40,
        "reviewer_actor_id": "REVIEWER_1",
        "reviewer_login": "fresh-reviewer",
        "implementer_actor_id": "IMPLEMENTER_1",
        "implementer_login": "implementer",
        "started_at": "2026-08-31T04:00:00Z",
        "completed_at": "2026-08-31T04:01:00Z",
        "permissions": ["contents:read"],
        "actions": ["read-candidate", "emit-local-report"],
        "reviewed_files": ["scripts/a.py", "docs/a.md"],
        "reviewed_changes": copy.deepcopy(changes),
        "findings": [
            {
                "id": "LOCAL-ACTION-1",
                "family": "action",
                "created_at": "2026-08-31T04:00:30Z",
            }
        ],
    }


def valid_input():
    changes = [
        modified_change("docs/a.md", "1", "2"),
        modified_change("scripts/a.py", "3", "4"),
    ]
    review = {
        "id": 1001,
        "node_id": "REMOTE_REVIEW_1",
        "round": 1,
        "reviewer_actor_id": "COPILOT",
        "candidate_sha": "c" * 40,
        "submitted_at": "2026-08-31T04:02:00Z",
        "state": "COMMENTED",
        "body": "### 🟢 Approval recommended",
        "body_classification": "clean-approval",
        "body_has_findings": False,
        "outcome": "clean",
        "finding_ids": [],
    }
    return {
        "schema_version": 2,
        "repository": "example/project",
        "pull_request": 7,
        "base_sha": "a" * 40,
        "base_tree": "b" * 40,
        "original_pre_review_head": "c" * 40,
        "original_changes": copy.deepcopy(changes),
        "original_receipt_sha256": "9" * 64,
        "candidate_sha": "c" * 40,
        "candidate_tree": "d" * 40,
        "head_sha": "c" * 40,
        "review_round": 1,
        "review_context": copy.deepcopy(review),
        "all_remote_reviews": [review],
        "remote_findings": [],
        "trust_mode": "base-pinned",
        "pre_review_required": True,
        "changed_files": ["docs/a.md", "scripts/a.py"],
        "changes": changes,
        "remote_finding_ids": [],
        "limits": {
            "max_duration_minutes": 30,
            "max_findings_per_review": 10,
            "max_reviewed_files": 40,
            "max_siblings_per_finding": 5,
            "max_siblings_per_handoff": 50,
        },
        "original_pre_review": original_report(changes),
        "assertion_requests": [
            {
                "assertion_id": (
                    "registry:behavior:actor-permission-bounds:positive:v2"
                ),
                "finding_id": None,
            },
            {
                "assertion_id": (
                    "registry:behavior:actor-permission-bounds:adversarial:v2"
                ),
                "finding_id": None,
            },
            {
                "assertion_id": (
                    "registry:sibling:action:actions:affected-fixed:v2"
                ),
                "finding_id": "LOCAL-ACTION-1",
            },
        ],
    }


class ReviewBaseCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = (
            ROOT
            / "build"
            / "test-artifacts"
            / f"base-assertion-program-{os.getpid()}"
        )
        cls.origin = cls.root / "origin"
        cls.head = cls.root / "head"
        cls.origin.mkdir(parents=True)
        cls.head.mkdir(parents=True)
        program = ROOT / "scripts/workflow_pilot/review_assertions.py"
        cls.program = cls.root / "review_assertions.py"
        cls.program.write_bytes(program.read_bytes())
        subjects = ROOT / "scripts/workflow_pilot/assertion_subjects"
        shutil.copytree(
            subjects,
            cls.origin / "scripts/workflow_pilot/assertion_subjects",
        )
        shutil.copytree(
            subjects,
            cls.head / "scripts/workflow_pilot/assertion_subjects",
        )
        origin_subject = (
            cls.origin
            / "scripts/workflow_pilot/assertion_subjects/action_actions.json"
        )
        data = json.loads(origin_subject.read_text(encoding="utf-8"))
        data["payload"]["actions"] = [
            "emit-local-report",
            "read-candidate",
        ]
        origin_subject.write_bytes(reporter.normalized_json(data))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    def prepare(self, data):
        data = copy.deepcopy(data)
        data.update(
            {
                "assertion_program_path": str(self.program),
                "assertion_program_blob_oid": "8" * 40,
                "assertion_program_argv": [
                    "/usr/bin/python3",
                    "-I",
                    "review_assertions.py",
                    "--stdin",
                ],
                "finding_origin_sha": "a" * 40,
                "finding_origin_tree": "7" * 40,
                "origin_root": str(self.origin),
                "head_root": str(self.head),
                "assertion_input_artifacts": [],
            }
        )
        return data

    def execute(self, data):
        return review_base_checker.execute_registry(self.prepare(data))

    def assert_rejected(self, data, message):
        with self.assertRaisesRegex(review_base_checker.CheckError, message):
            self.execute(data)

    def test_closed_registry_derives_inputs_and_observes_real_rejection(self):
        result = self.execute(valid_input())
        self.assertEqual(result["registry_version"], 1)
        self.assertEqual(len(result["results"]), 3)
        adversarial = next(
            item
            for item in result["results"]
            if ":adversarial:" in item["assertion_id"]
        )
        self.assertTrue(adversarial["output"]["rejection_observed"])
        affected = next(
            item
            for item in result["results"]
            if item["claimed_disposition"] == "affected-fixed"
        )
        self.assertEqual(affected["output"]["origin_status"], "fail")
        self.assertEqual(affected["output"]["head_status"], "pass")
        for item in result["results"]:
            self.assertEqual(item["status"], "pass")
            self.assertRegex(item["inputs_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(item["output_sha256"], r"^[0-9a-f]{64}$")

    def test_original_pre_review_head_is_independent_of_current_head(self):
        data = valid_input()
        data["candidate_sha"] = "e" * 40
        data["candidate_tree"] = "f" * 40
        data["head_sha"] = "e" * 40
        data["review_round"] = 2
        data["review_context"] = {
            **data["review_context"],
            "id": 1002,
            "node_id": "REMOTE_REVIEW_2",
            "round": 2,
            "candidate_sha": "e" * 40,
            "finding_ids": [],
        }
        data["all_remote_reviews"].append(data["review_context"])
        data["assertion_requests"] = [
            {
                "assertion_id": (
                    "registry:behavior:authority-causality:runtime:v2"
                ),
                "finding_id": None,
            }
        ]
        result = self.execute(data)
        self.assertEqual(result["results"][0]["candidate_sha"], "e" * 40)
        self.assertEqual(
            data["original_pre_review"]["candidate_sha"], "c" * 40
        )

    def test_member_assertions_are_specific_and_swaps_fail(self):
        data = valid_input()
        request = data["assertion_requests"][2]
        request["assertion_id"] = (
            "registry:sibling:action:items:affected-fixed:v2"
        )
        self.assert_rejected(data, "origin assertion unexpectedly passed")

        data = valid_input()
        request = data["assertion_requests"][2]
        request["assertion_id"] = (
            "registry:sibling:wire:actions:affected-fixed:v2"
        )
        self.assert_rejected(data, "member is absent from registry")

        data = valid_input()
        request = data["assertion_requests"][2]
        request["assertion_id"] = (
            "registry:sibling:resource:disabled:not-applicable:wrong:v2"
        )
        self.assert_rejected(data, "reason is not registered")

    def test_registered_not_applicable_requires_exact_reason_context(self):
        data = valid_input()
        data["trust_mode"] = "introduction"
        data["assertion_requests"][2] = {
            "assertion_id": (
                "registry:sibling:resource:disabled:not-applicable:"
                "feature-disabled-by-contract:v2"
            ),
            "finding_id": "LOCAL-RESOURCE-1",
        }
        data["original_pre_review"]["findings"][0] = {
            "id": "LOCAL-RESOURCE-1",
            "family": "resource",
            "created_at": "2026-08-31T04:00:30Z",
        }
        result = self.execute(data)
        output = result["results"][2]["output"]
        self.assertEqual(
            output["reason"], "feature-disabled-by-contract"
        )

    def test_verified_unaffected_executes_both_member_specific_trees(self):
        data = valid_input()
        data["assertion_requests"][2] = {
            "assertion_id": (
                "registry:sibling:action:items:verified-unaffected:v2"
            ),
            "finding_id": "LOCAL-ACTION-1",
        }
        result = self.execute(data)
        output = result["results"][2]["output"]
        self.assertEqual(output["origin_status"], "pass")
        self.assertEqual(output["head_status"], "pass")

        subject_path = (
            self.head
            / "scripts/workflow_pilot/assertion_subjects/action_items.json"
        )
        original = subject_path.read_bytes()
        subject = json.loads(original)
        subject["payload"]["items"] = ["different", "semantic-output"]
        subject_path.write_bytes(reporter.normalized_json(subject))
        try:
            self.assert_rejected(data, "semantic outputs are not equivalent")
        finally:
            subject_path.write_bytes(original)

    def test_stale_round_head_and_fabricated_result_fields_fail(self):
        data = valid_input()
        data["review_context"]["candidate_sha"] = "f" * 40
        self.assert_rejected(data, "current assertion round/head")

        data = valid_input()
        data["review_context"]["round"] = 2
        self.assert_rejected(data, "current assertion round/head")

        data = valid_input()
        data["assertion_requests"][0]["assertion_id"] = (
            "registry:behavior:actor-permission-bounds:positive:v1"
        )
        self.assert_rejected(data, "absent from exact-base registry")

    def test_status_coverage_and_original_report_are_exact(self):
        data = valid_input()
        data["original_pre_review"]["reviewed_changes"].pop()
        self.assert_rejected(data, "status/blob evidence")

        data = valid_input()
        data["changes"][0]["status"] = "X"
        self.assert_rejected(data, "status is not supported")

        data = valid_input()
        data["changed_files"].append("fabricated.txt")
        self.assert_rejected(data, "do not match status record")

    def test_local_namespace_actor_and_bounds_remain_strict(self):
        data = valid_input()
        data["original_pre_review"]["findings"][0]["id"] = "REMOTE_NODE"
        self.assert_rejected(data, "LOCAL- namespace")

        data = valid_input()
        data["original_pre_review"]["reviewer_login"] = "IMPLEMENTER_bot"
        self.assert_rejected(data, "identities overlap")

        data = valid_input()
        data["limits"]["max_reviewed_files"] = True
        self.assert_rejected(data, "must be an integer")


if __name__ == "__main__":
    unittest.main()
