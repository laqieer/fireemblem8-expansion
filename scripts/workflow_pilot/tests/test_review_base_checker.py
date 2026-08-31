import copy
import unittest

from scripts.workflow_pilot import review_base_checker


def valid_input():
    return {
        "schema_version": 1,
        "repository": "example/project",
        "pull_request": 7,
        "base_sha": "a" * 40,
        "base_tree": "b" * 40,
        "candidate_sha": "c" * 40,
        "candidate_tree": "d" * 40,
        "changed_files": ["docs/a.md", "scripts/a.py"],
        "github_finding_ids": ["FINDING_1"],
        "review_report": {
            "schema_version": 1,
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
            "finding_ids": ["FINDING_1"],
        },
    }


class ReviewBaseCheckerTests(unittest.TestCase):
    def assert_rejected(self, data, message):
        with self.assertRaisesRegex(review_base_checker.CheckError, message):
            review_base_checker.validate_input(data)

    def test_accepts_complete_independent_report(self):
        result = review_base_checker.validate_input(valid_input())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["reviewed_files"], ["docs/a.md", "scripts/a.py"])
        self.assertEqual(result["finding_ids"], ["FINDING_1"])

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

    def test_rejects_missing_extra_and_duplicate_finding_ids(self):
        for mutation in ("missing", "extra", "duplicate"):
            data = valid_input()
            if mutation == "missing":
                data["review_report"]["finding_ids"] = []
            elif mutation == "extra":
                data["review_report"]["finding_ids"].append("FINDING_2")
            else:
                data["review_report"]["finding_ids"].append("FINDING_1")
            with self.subTest(mutation=mutation):
                self.assert_rejected(data, "do not match|contains duplicates")

    def test_rejects_actor_aliases_and_mutating_permissions(self):
        for login in ("IMPLEMENTER", "implementer[bot]", "implementer_bot"):
            data = valid_input()
            data["review_report"]["reviewer_login"] = login
            with self.subTest(login=login):
                self.assert_rejected(data, "identities overlap")

        data = valid_input()
        data["review_report"]["permissions"] = ["contents:write"]
        self.assert_rejected(data, "not exactly read-only")

    def test_rejects_action_reversal_duplication_and_interleaving(self):
        mutations = (
            ["emit-local-report", "read-candidate"],
            ["read-candidate", "read-candidate", "emit-local-report"],
            ["read-candidate", "comment", "emit-local-report"],
        )
        for actions in mutations:
            data = valid_input()
            data["review_report"]["actions"] = actions
            with self.subTest(actions=actions):
                self.assert_rejected(data, "not exact read then report")

    def test_rejects_scope_sha_and_time_mutations(self):
        for field, value in (
            ("repository", "other/project"),
            ("pull_request", 8),
            ("base_sha", "e" * 40),
            ("candidate_sha", "f" * 40),
        ):
            data = valid_input()
            data["review_report"][field] = value
            with self.subTest(field=field):
                self.assert_rejected(data, "does not match")

        data = valid_input()
        data["review_report"]["completed_at"] = data["review_report"]["started_at"]
        self.assert_rejected(data, "interval is not positive")

    def test_rejects_unknown_fields_and_unsafe_paths(self):
        data = valid_input()
        data["review_report"]["unknown"] = True
        self.assert_rejected(data, "unknown fields")

        data = valid_input()
        data["review_report"]["reviewed_files"][0] = "../outside"
        self.assert_rejected(data, "repository-relative")

    def test_semantic_mutation_with_same_shape_fails(self):
        data = valid_input()
        mutation = copy.deepcopy(data)
        mutation["review_report"]["reviewed_files"] = [
            "docs/a.md",
            "scripts/other.py",
        ]
        self.assert_rejected(mutation, "cover every exact changed file")


if __name__ == "__main__":
    unittest.main()
