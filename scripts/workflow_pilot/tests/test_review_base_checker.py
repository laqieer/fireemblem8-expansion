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


def valid_input():
    changes = [
        modified_change("docs/a.md", "1", "2"),
        modified_change("scripts/a.py", "3", "4"),
    ]
    return {
        "schema_version": 2,
        "repository": "example/project",
        "pull_request": 7,
        "base_sha": "a" * 40,
        "base_tree": "b" * 40,
        "candidate_sha": "c" * 40,
        "candidate_tree": "d" * 40,
        "head_sha": "c" * 40,
        "trust_mode": "base-pinned",
        "pre_review_required": True,
        "changed_files": ["docs/a.md", "scripts/a.py"],
        "changes": changes,
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
            "reviewed_changes": copy.deepcopy(changes),
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
                "check_id": "behavior:actor-permission-bounds:positive:v1",
                "claimed_disposition": None,
                "inputs": {
                    "row_id": "actor-permission-bounds",
                    "repository": "example/project",
                    "pull_request": 7,
                    "base_sha": "a" * 40,
                    "head_sha": "c" * 40,
                },
            },
            {
                "id": "result-sibling-LOCAL-ACTION-1-actions",
                "assertion_id": "sibling:action:actions",
                "check_id": "sibling:action:actions:affected-fixed:v1",
                "claimed_disposition": "affected-fixed",
                "inputs": {
                    "finding_id": "LOCAL-ACTION-1",
                    "family": "action",
                    "member": "actions",
                    "changed_paths": ["docs/a.md"],
                    "change_evidence": [copy.deepcopy(changes[0])],
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
        for item in result["results"]:
            self.assertRegex(item["inputs_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(item["output_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(item["check_id"].endswith(":v1"))

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

    def test_fabricated_assertion_ids_inputs_and_outcomes_fail(self):
        data = valid_input()
        data["assertion_requests"][0]["assertion_id"] = "candidate:claimed-pass"
        self.assert_rejected(data, "closed assertion identity|closed registry")

        data = valid_input()
        data["assertion_requests"][1]["inputs"]["member"] = "targets"
        self.assert_rejected(data, "does not match")

        data = valid_input()
        request = data["assertion_requests"][1]
        request["claimed_disposition"] = "verified-unaffected"
        self.assert_rejected(data, "check identity|verified-unaffected")

        data = valid_input()
        request = data["assertion_requests"][1]
        request["claimed_disposition"] = "not-applicable"
        request["check_id"] = "sibling:action:actions:not-applicable:v1"
        self.assert_rejected(data, "no supported base-owned assertion")

        data = valid_input()
        request = data["assertion_requests"][0]
        request["id"] = "result-actor-permission-bounds-runtime"
        request["assertion_id"] = "actor-permission-bounds:runtime"
        request["check_id"] = "behavior:actor-permission-bounds:runtime:v1"
        self.assert_rejected(data, "runtime assertion inputs")

    def test_verified_unaffected_requires_equal_concrete_blobs(self):
        data = valid_input()
        request = data["assertion_requests"][1]
        request["claimed_disposition"] = "verified-unaffected"
        request["check_id"] = (
            "sibling:action:actions:verified-unaffected:v1"
        )
        request["inputs"] = {
            "finding_id": "LOCAL-ACTION-1",
            "family": "action",
            "member": "actions",
            "unchanged_evidence": [
                {
                    "path": "Makefile",
                    "base_mode": "100644",
                    "base_blob_oid": "5" * 40,
                    "head_mode": "100644",
                    "head_blob_oid": "5" * 40,
                }
            ],
        }
        result = review_base_checker.execute_registry(data)
        self.assertEqual(
            result["results"][1]["claimed_disposition"],
            "verified-unaffected",
        )
        data["assertion_requests"][1]["inputs"]["unchanged_evidence"][0][
            "head_blob_oid"
        ] = "6" * 40
        self.assert_rejected(data, "does not bind equal blobs")

    def test_status_evidence_is_exact_and_unknown_status_rejects(self):
        data = valid_input()
        data["review_report"]["reviewed_changes"].pop()
        self.assert_rejected(data, "status/blob evidence")

        data = valid_input()
        data["changes"][0]["status"] = "X"
        self.assert_rejected(data, "status is not supported")

        data = valid_input()
        data["changes"][0]["status"] = "D"
        self.assert_rejected(data, "contradicts status")

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
