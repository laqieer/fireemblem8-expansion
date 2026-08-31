import copy
import unittest

from scripts.workflow_pilot import review_base_checker


def valid_input():
    return {
        "schema_version": 2,
        "repository": "example/project",
        "pull_request": 7,
        "base_sha": "a" * 40,
        "base_tree": "b" * 40,
        "candidate_sha": "c" * 40,
        "candidate_tree": "d" * 40,
        "head_sha": "c" * 40,
        "changed_files": ["docs/a.md", "scripts/a.py"],
        "remote_finding_ids": ["REMOTE_NODE_1"],
        "limits": {
            "max_duration_minutes": 30,
            "max_findings_per_review": 10,
            "max_reviewed_files": 40,
            "max_siblings_per_finding": 5,
            "max_siblings_per_handoff": 50,
        },
        "review_report": {
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
            "findings": [
                {
                    "id": "LOCAL-ACTION-1",
                    "family": "action",
                    "created_at": "2026-08-31T04:00:30Z",
                }
            ],
        },
        "assertion_requests": [
            {
                "id": "result-actor-permission-bounds-positive",
                "assertion_id": "actor-permission-bounds:positive",
                "context": None,
            },
            {
                "id": "result-sibling-LOCAL-ACTION-1-actions",
                "assertion_id": "sibling:action:actions",
                "context": {
                    "finding_id": "LOCAL-ACTION-1",
                    "family": "action",
                    "member": "actions",
                },
            },
        ],
    }


class ReviewBaseCheckerTests(unittest.TestCase):
    def assert_rejected(self, data, message):
        with self.assertRaisesRegex(review_base_checker.CheckError, message):
            review_base_checker.execute_registry(data)

    def test_closed_registry_executes_bound_assertions(self):
        result = review_base_checker.execute_registry(valid_input())
        self.assertEqual(result["registry_version"], 1)
        self.assertEqual(
            [item["status"] for item in result["results"]],
            ["pass", "pass"],
        )
        self.assertEqual(
            result["results"][0]["input_sha256"], result["input_sha256"]
        )
        self.assertEqual(
            result["results"][0]["command_id"], result["command_id"]
        )

    def test_local_findings_have_independent_namespace_and_chronology(self):
        for finding_id, created_at, message in (
            ("REMOTE_NODE_1", "2026-08-31T04:00:30Z", "LOCAL- namespace"),
            ("LOCAL-ACTION-1", "2026-08-31T03:59:59Z", "outside"),
            ("LOCAL-ACTION-1", "2026-08-31T04:01:01Z", "outside"),
        ):
            data = valid_input()
            data["review_report"]["findings"][0]["id"] = finding_id
            data["review_report"]["findings"][0]["created_at"] = created_at
            with self.subTest(finding_id=finding_id, created_at=created_at):
                self.assert_rejected(data, message)

    def test_remote_ids_never_become_pre_review_findings(self):
        data = valid_input()
        data["remote_finding_ids"] = ["LOCAL-ACTION-1"]
        self.assert_rejected(data, "overlap the independent namespace")

    def test_rejects_missing_extra_and_duplicate_changed_file_coverage(self):
        for mutation in ("missing", "extra", "duplicate"):
            data = valid_input()
            if mutation == "missing":
                data["review_report"]["reviewed_files"].pop()
            elif mutation == "extra":
                data["review_report"]["reviewed_files"].append("docs/extra.md")
            else:
                data["review_report"]["reviewed_files"].append("docs/a.md")
            with self.subTest(mutation=mutation):
                self.assert_rejected(data, "cover every|contains duplicates")

    def test_rejects_actor_aliases_mutation_and_action_reordering(self):
        for login in ("IMPLEMENTER", "implementer[bot]", "implementer_bot"):
            data = valid_input()
            data["review_report"]["reviewer_login"] = login
            with self.subTest(login=login):
                self.assert_rejected(data, "identities overlap")

        data = valid_input()
        data["review_report"]["permissions"] = ["contents:write"]
        self.assert_rejected(data, "not exactly read-only")

        data = valid_input()
        data["review_report"]["actions"].reverse()
        self.assert_rejected(data, "not exact read then report")

    def test_fabricated_assertion_ids_and_context_fail(self):
        data = valid_input()
        data["assertion_requests"][0]["assertion_id"] = "candidate:claimed-pass"
        self.assert_rejected(data, "closed registry")

        data = valid_input()
        data["assertion_requests"][1]["context"]["member"] = "targets"
        self.assert_rejected(data, "outside the closed|does not match")

    def test_boolean_bounds_and_head_mismatch_fail(self):
        data = valid_input()
        data["limits"]["max_reviewed_files"] = True
        self.assert_rejected(data, "must be an integer")

        data = valid_input()
        data["head_sha"] = "e" * 40
        self.assert_rejected(data, "head does not equal candidate")

    def test_semantic_mutation_with_same_shape_fails(self):
        mutation = copy.deepcopy(valid_input())
        mutation["review_report"]["reviewed_files"] = [
            "docs/a.md",
            "scripts/other.py",
        ]
        self.assert_rejected(mutation, "cover every exact changed file")


if __name__ == "__main__":
    unittest.main()
