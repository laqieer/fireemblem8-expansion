import copy
import unittest

from scripts.workflow_pilot import review_base_checker


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
    def assert_rejected(self, data, message):
        with self.assertRaisesRegex(review_base_checker.CheckError, message):
            review_base_checker.execute_registry(data)

    def test_closed_registry_derives_inputs_and_observes_real_rejection(self):
        result = review_base_checker.execute_registry(valid_input())
        self.assertEqual(result["registry_version"], 1)
        self.assertEqual(len(result["results"]), 3)
        adversarial = next(
            item
            for item in result["results"]
            if ":adversarial:" in item["assertion_id"]
        )
        self.assertTrue(adversarial["output"]["rejection_observed"])
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
        result = review_base_checker.execute_registry(data)
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
        self.assert_rejected(data, "disposition is not registered")

        data = valid_input()
        request = data["assertion_requests"][2]
        request["assertion_id"] = (
            "registry:sibling:wire:actions:affected-fixed:v2"
        )
        self.assert_rejected(data, "wrong family member")

        data = valid_input()
        request = data["assertion_requests"][2]
        request["assertion_id"] = (
            "registry:sibling:resource:disabled:not-applicable:wrong:v2"
        )
        self.assert_rejected(data, "reason is not explicitly registered")

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
        result = review_base_checker.execute_registry(data)
        output = result["results"][2]["output"]
        self.assertEqual(
            output["not_applicable_reason"], "feature-disabled-by-contract"
        )

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
        self.assert_rejected(data, "closed registry")

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
