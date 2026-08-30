import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.workflow_pilot import reporter


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASELINE = FIXTURES / "baseline.json"
BASELINE_EXPECTED = FIXTURES / "baseline_expected.json"
DECISIONS = ROOT / ".github" / "workflow-pilot-decisions.json"


def sha(character):
    return character * 40


def minimal_fixture():
    return {
        "schema_version": 1,
        "repository": "example/workflow",
        "base_sha": sha("d"),
        "captured_at": "2026-01-01T10:00:00Z",
        "lifecycle_as_of": "2026-01-01T10:05:00Z",
        "window": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T10:00:00Z",
        },
        "default_branch": "master",
        "workflow_sample_size": 5,
        "build_workflow": "Build CI",
        "spotlight_pr": 1,
        "pull_requests": [
            {
                "number": 1,
                "state": "merged",
                "created_at": "2026-01-01T01:00:00Z",
                "merged_at": "2026-01-01T09:00:00Z",
                "closed_at": "2026-01-01T09:00:01Z",
                "base_ref": "master",
                "head_branch": "agent/one",
                "head_sha": sha("c"),
                "merge_sha": sha("d"),
                "issue_numbers": [1],
                "review_ids": [10, 11],
                "commit_shas": [sha("a"), sha("b"), sha("c")],
                "additions": 900,
                "deletions": 100,
                "files": ["scripts/feature.py", "scripts/test_feature.py"],
            }
        ],
        "issues": [
            {
                "number": 1,
                "state": "closed",
                "created_at": "2026-01-01T00:30:00Z",
                "closed_at": "2026-01-01T09:00:01Z",
            }
        ],
        "reviews": [
            {
                "id": 10,
                "pr_number": 1,
                "author": reporter.REVIEW_BOT,
                "submitted_at": "2026-01-01T04:00:00Z",
                "commit_sha": sha("a"),
                "state": "COMMENTED",
                "thread_ids": ["thread:100"],
            },
            {
                "id": 11,
                "pr_number": 1,
                "author": reporter.REVIEW_BOT,
                "submitted_at": "2026-01-01T08:00:00Z",
                "commit_sha": sha("c"),
                "state": "COMMENTED",
                "thread_ids": [],
            },
        ],
        "review_findings": [
            {
                "id": 100,
                "review_id": 10,
                "thread_id": "thread:100",
                "created_at": "2026-01-01T04:01:00Z",
                "resolved_at": "2026-01-01T06:00:00Z",
                "outdated": True,
                "path": "scripts/feature.py",
            }
        ],
        "workflow_runs": [
            {
                "id": 1,
                "workflow": "Build CI",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-01-01T02:00:00Z",
                "started_at": "2026-01-01T02:00:00Z",
                "completed_at": "2026-01-01T03:00:00Z",
                "head_sha": sha("a"),
                "head_branch": "agent/one",
                "attempt": 1,
            },
            {
                "id": 2,
                "workflow": "Build CI",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "cancelled",
                "created_at": "2026-01-01T03:00:00Z",
                "started_at": "2026-01-01T03:00:00Z",
                "completed_at": "2026-01-01T03:30:00Z",
                "head_sha": sha("b"),
                "head_branch": "agent/one",
                "attempt": 1,
            },
            {
                "id": 3,
                "workflow": "Build CI",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-01-01T06:00:00Z",
                "started_at": "2026-01-01T06:00:00Z",
                "completed_at": "2026-01-01T07:00:00Z",
                "head_sha": sha("c"),
                "head_branch": "agent/one",
                "attempt": 1,
            },
            {
                "id": 4,
                "workflow": "Build CI",
                "event": "push",
                "status": "in_progress",
                "conclusion": None,
                "created_at": "2026-01-01T09:00:00Z",
                "started_at": "2026-01-01T09:00:00Z",
                "completed_at": None,
                "head_sha": sha("c"),
                "head_branch": "agent/one",
                "attempt": 1,
            },
            {
                "id": 5,
                "workflow": "Running Copilot Code Review",
                "event": "dynamic",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-01-01T07:30:00Z",
                "started_at": "2026-01-01T07:30:00Z",
                "completed_at": "2026-01-01T07:35:00Z",
                "head_sha": sha("c"),
                "head_branch": "agent/one",
                "attempt": 1,
            },
        ],
        "commits": [
            {
                "sha": sha("a"),
                "committed_at": "2026-01-01T01:00:00Z",
                "parents": [],
                "message": "feat: begin",
            },
            {
                "sha": sha("b"),
                "committed_at": "2026-01-01T03:00:00Z",
                "parents": [sha("a")],
                "message": "fix: review",
            },
            {
                "sha": sha("c"),
                "committed_at": "2026-01-01T06:00:00Z",
                "parents": [sha("b")],
                "message": "fix: finish",
            },
            {
                "sha": sha("d"),
                "committed_at": "2026-01-01T09:00:00Z",
                "parents": [sha("c")],
                "message": "Merge pull request #1",
            },
        ],
        "events": [
            {
                "id": "base:1",
                "type": "base_changed",
                "occurred_at": "2026-01-01T05:00:00Z",
                "pr_number": 1,
                "old_base": "parent",
                "new_base": "master",
            },
            {
                "id": "close:1",
                "type": "closed",
                "occurred_at": "2026-01-01T05:05:00Z",
                "pr_number": 1,
            },
            {
                "id": "reopen:1",
                "type": "reopened",
                "occurred_at": "2026-01-01T05:06:00Z",
                "pr_number": 1,
            },
            {
                "id": "supersede:1",
                "type": "candidate_superseded",
                "occurred_at": "2026-01-01T06:00:00Z",
                "pr_number": 1,
                "old_sha": sha("b"),
                "new_sha": sha("c"),
            },
            {
                "id": "saved:build",
                "type": "build_saved",
                "occurred_at": "2026-01-01T07:00:00Z",
                "pr_number": 1,
                "minutes": 50,
            },
            {
                "id": "saved:review",
                "type": "review_saved",
                "occurred_at": "2026-01-01T07:00:00Z",
                "pr_number": 1,
                "minutes": 20,
            },
            {
                "id": "overhead:coordination",
                "type": "pilot_coordination",
                "occurred_at": "2026-01-01T07:00:00Z",
                "pr_number": 1,
                "minutes": 8,
            },
            {
                "id": "overhead:metadata",
                "type": "metadata_maintenance",
                "occurred_at": "2026-01-01T07:00:00Z",
                "pr_number": 1,
                "minutes": 2,
            },
            {
                "id": "artifact:checkpoint",
                "type": "artifact_checkpoint",
                "occurred_at": "2026-01-01T09:01:00Z",
                "artifact_id": "contract",
            },
            {
                "id": "artifact:proof",
                "type": "deletion_proof",
                "occurred_at": "2026-01-01T09:02:00Z",
                "artifact_id": "contract",
                "trigger_event_id": "artifact:checkpoint",
                "semantic_result": "fail",
                "reason": "removal loses the decision boundary",
                "restored_result": "pass",
            },
            {
                "id": "artifact:pre-graduation",
                "type": "pre_graduation",
                "occurred_at": "2026-01-01T09:03:00Z",
                "artifact_id": "contract",
            },
            {
                "id": "artifact:pre-graduation-proof",
                "type": "deletion_proof",
                "occurred_at": "2026-01-01T09:04:00Z",
                "artifact_id": "contract",
                "trigger_event_id": "artifact:pre-graduation",
                "semantic_result": "fail",
                "reason": "graduation still requires the decision boundary",
                "restored_result": "pass",
            },
        ],
        "artifacts": [
            {
                "id": "contract",
                "path": ".github/contract.json",
                "dependency_ids": ["consume:contract", "check:contract"],
            }
        ],
        "dependency_edges": [
            {
                "id": "consume:contract",
                "type": "consumes",
                "source": "reporter",
                "target": "contract",
            },
            {
                "id": "check:contract",
                "type": "checks",
                "source": "tests",
                "target": "contract",
            },
        ],
    }


def minimal_decisions():
    return {
        "schema_version": 1,
        "pull_requests": [
            {
                "pull_request": 1,
                "risk_boundaries": ["none"],
                "threshold": {
                    "triggers": ["none"],
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
        ],
        "artifacts": [
            {
                "artifact_id": "contract",
                "owner": "workflow-governance",
                "executable_consumer": "reporter",
                "unique_decision": "pilot-contract",
                "consistency_check": "tests",
                "max_maintenance_minutes": 5,
                "estimated_maintenance_minutes": 1,
                "deletion_criterion": "Delete after the pilot.",
                "expires_at": None,
                "history": [
                    {
                        "recorded_at": "2026-01-01T09:05:00Z",
                        "disposition": "Graduate",
                        "reason": "The pilot still consumes the contract.",
                    }
                ],
            }
        ],
    }


def add_second_pr(fixture):
    fixture["pull_requests"].append(
        {
            "number": 2,
            "state": "open",
            "created_at": "2026-01-01T09:10:00Z",
            "merged_at": None,
            "closed_at": None,
            "base_ref": "master",
            "head_branch": "agent/two",
            "head_sha": sha("e"),
            "merge_sha": None,
            "issue_numbers": [],
            "review_ids": [],
            "commit_shas": [sha("e")],
            "additions": 10,
            "deletions": 0,
            "files": ["scripts/second.py"],
        }
    )
    fixture["commits"].append(
        {
            "sha": sha("e"),
            "committed_at": "2026-01-01T09:10:00Z",
            "parents": [sha("d")],
            "message": "feat: second candidate",
        }
    )


def add_override(fixture, decisions, commit_sha=sha("b"), occurred_at=None):
    override = {
        "enabled": True,
        "reason": "The declared risk boundary needs review-first handling.",
    }
    decisions["pull_requests"][0]["threshold"]["override_history"].append(override)
    commit = next(
        item for item in fixture["commits"] if item["sha"] == commit_sha
    )
    fixture["events"].append(
        {
            "id": "override:introduced:0",
            "type": "threshold_override_introduced",
            "occurred_at": occurred_at or commit["committed_at"],
            "pr_number": 1,
            "sha": commit_sha,
            "override_index": 0,
            "decision_digest": reporter.threshold_override_digest(1, 0, override),
        }
    )
    return override


class BaselineFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = reporter.load_json(BASELINE)
        cls.decisions = reporter.load_json(DECISIONS)
        cls.expected = reporter.load_json(BASELINE_EXPECTED)

    def test_frozen_baseline_and_expected_values(self):
        result = reporter.build_report(self.fixture, self.decisions)
        reporter.check_expected(result, self.expected)
        self.assertEqual(len(result["identities"]["pull_requests"]), 64)
        self.assertEqual(len(result["identities"]["issues"]), 53)
        self.assertEqual(len(result["identities"]["reviews"]), 566)
        self.assertEqual(len(result["identities"]["commits"]), 1017)
        self.assertGreaterEqual(len(result["identities"]["workflow_runs"]), 1000)

    def test_normalized_result_is_byte_identical(self):
        first = reporter.normalized_json(
            reporter.build_report(self.fixture, self.decisions)
        )
        second = reporter.normalized_json(
            reporter.build_report(
                json.loads(json.dumps(self.fixture)),
                json.loads(json.dumps(self.decisions)),
            )
        )
        self.assertEqual(first, second)

    def test_cli_checks_expected_without_live_github(self):
        command = [
            sys.executable,
            "-m",
            "scripts.workflow_pilot.reporter",
            "--fixture",
            str(BASELINE),
            "--decisions",
            str(DECISIONS),
            "--expected",
            str(BASELINE_EXPECTED),
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(
            result.stdout,
            reporter.normalized_json(
                reporter.build_report(self.fixture, self.decisions)
            ),
        )


class FormulaAndClassificationTests(unittest.TestCase):
    def test_boundary_timestamps_are_inclusive(self):
        for boundary in (
            "2026-01-01T00:00:00Z",
            "2026-01-01T10:00:00Z",
        ):
            with self.subTest(boundary=boundary):
                fixture = minimal_fixture()
                fixture["pull_requests"][0]["created_at"] = (
                    "2025-12-31T23:00:00Z"
                )
                fixture["issues"][0]["created_at"] = "2025-12-31T22:00:00Z"
                fixture["pull_requests"][0]["merged_at"] = boundary
                fixture["pull_requests"][0]["closed_at"] = boundary
                report = reporter.build_report(fixture, minimal_decisions())
                self.assertEqual(report["delivery"]["merged_pull_requests"], 1)

    def test_build_cancellation_duplicate_supersession_and_overhead_formulas(self):
        result = reporter.build_report(minimal_fixture(), minimal_decisions())
        self.assertEqual(result["builds"]["runs"], 4)
        self.assertEqual(result["builds"]["failure"], 1)
        self.assertEqual(result["builds"]["cancelled"], 1)
        self.assertEqual(result["builds"]["success"], 1)
        self.assertEqual(result["builds"]["active"], 1)
        self.assertEqual(result["builds"]["action_required"], 0)
        self.assertEqual(result["builds"]["neutral"], 0)
        self.assertEqual(result["builds"]["skipped"], 0)
        self.assertEqual(result["builds"]["duplicate_unchanged_sha"], 1)
        self.assertEqual(result["events"]["superseded_candidates"], 2)
        self.assertEqual(
            result["efficiency"],
            {
                "saved_build_minutes": 50,
                "saved_review_minutes": 20,
                "pilot_coordination_minutes": 8,
                "metadata_maintenance_minutes": 2,
                "net_saved_minutes": 60,
            },
        )

    def test_cancelled_still_running_stack_generated_bulk_and_revert_classes(self):
        fixture = minimal_fixture()
        data = reporter.validate_fixture(fixture)
        self.assertEqual(
            reporter.report_classifications(data)[0],
            {
                "pr": 1,
                "work_state": "merged",
                "flags": ["superseded"],
                "current_head_sha": sha("c"),
            },
        )

        fixture["pull_requests"][0].update(
            {
                "state": "closed",
                "merged_at": None,
                "merge_sha": None,
                "files": ["reports/old.generated.json"],
                "additions": 0,
                "deletions": 2000,
                "base_ref": "parent",
            }
        )
        data = reporter.validate_fixture(fixture)
        classification = reporter.report_classifications(data)[0]
        self.assertEqual(classification["work_state"], "cancelled")
        self.assertEqual(
            classification["flags"],
            ["bulk-deletion", "generated-only", "stacked", "superseded"],
        )

        fixture["pull_requests"][0].update(
            {
                "state": "open",
                "closed_at": None,
            }
        )
        data = reporter.validate_fixture(fixture)
        self.assertEqual(
            reporter.report_classifications(data)[0]["work_state"],
            "still-running",
        )

        fixture = minimal_fixture()
        fixture["commits"].append(
            {
                "sha": sha("e"),
                "committed_at": "2026-01-01T09:30:00Z",
                "parents": [sha("d")],
                "message": f"Revert delivery\n\nThis reverts commit {sha('d')}.\n",
            }
        )
        data = reporter.validate_fixture(fixture)
        self.assertIn("reverted", reporter.report_classifications(data)[0]["flags"])
        self.assertEqual(
            reporter.report_events(data)["reverts"],
            [{"commit": sha("e"), "reverts": sha("d")}],
        )

    def test_review_round_and_density_formulas(self):
        result = reporter.build_report(minimal_fixture(), minimal_decisions())
        self.assertEqual(result["reviews"]["rounds"], 2)
        self.assertEqual(result["reviews"]["superseded_rounds"], 0)
        self.assertEqual(result["reviews"]["valid_findings"], 1)
        self.assertEqual(result["reviews"]["valid_findings_per_kloc"], "1.000")
        self.assertEqual(result["reviews"]["valid_findings_per_review"], "0.500")
        self.assertEqual(
            result["delivery"]["first_push_to_clean_review"]["median_hours"],
            "7.0",
        )

    def test_conflict_and_safety_outcome_events(self):
        fixture = minimal_fixture()
        for event_type, event_id in (
            ("conflict_detected", "conflict:1"),
            ("escaped_defect", "escape:1"),
            ("broken_master", "broken:1"),
            ("security_finding", "security:1"),
            ("manual_reject", "manual:1"),
        ):
            event = {
                "id": event_id,
                "type": event_type,
                "occurred_at": "2026-01-01T09:30:00Z",
                "pr_number": 1,
                "sha": sha("d"),
            }
            fixture["events"].append(event)
        events = reporter.build_report(fixture, minimal_decisions())["events"]
        self.assertEqual(events["conflicts"], 1)
        self.assertEqual(events["escaped_defects"], 1)
        self.assertEqual(events["broken_master"], 1)
        self.assertEqual(events["security_findings"], 1)
        self.assertEqual(events["manual_rejects"], 1)

    def test_safety_events_aggregate_across_pull_requests(self):
        fixture = minimal_fixture()
        add_second_pr(fixture)
        fixture["events"].append(
            {
                "id": "security:cross-pr",
                "type": "security_finding",
                "occurred_at": "2026-01-01T09:30:00Z",
                "pr_number": 2,
                "sha": sha("e"),
            }
        )
        events = reporter.build_report(fixture, minimal_decisions())["events"]
        self.assertEqual(events["spotlight_pr"], 1)
        self.assertEqual(events["security_findings"], 1)

    def test_safety_event_sha_requires_commit_and_matching_pr_history(self):
        for event_sha, pattern in (
            (sha("f"), "has no authoritative commit"),
            (sha("d"), "outside PR 2 candidate/merge history"),
        ):
            with self.subTest(event_sha=event_sha):
                fixture = minimal_fixture()
                add_second_pr(fixture)
                fixture["events"].append(
                    {
                        "id": "escape:cross-pr",
                        "type": "escaped_defect",
                        "occurred_at": "2026-01-01T09:30:00Z",
                        "pr_number": 2,
                        "sha": event_sha,
                    }
                )
                with self.assertRaisesRegex(reporter.PilotDataError, pattern):
                    reporter.build_report(fixture, minimal_decisions())

    def test_spotlight_builds_use_only_latest_declared_sample(self):
        fixture = minimal_fixture()
        fixture["workflow_runs"].append(
            {
                "id": 6,
                "workflow": "Build CI",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-01-01T01:30:00Z",
                "started_at": "2026-01-01T01:30:00Z",
                "completed_at": "2026-01-01T01:45:00Z",
                "head_sha": sha("a"),
                "head_branch": "agent/one",
                "attempt": 1,
            }
        )
        builds = reporter.build_report(fixture, minimal_decisions())["builds"]
        self.assertEqual(builds["sample_size"], 5)
        self.assertEqual(builds["spotlight"]["runs"], 4)
        self.assertEqual(builds["spotlight"]["success"], 1)

    def test_queued_run_accrues_zero_duration_even_with_started_at(self):
        fixture = minimal_fixture()
        fixture["workflow_runs"][3]["status"] = "queued"
        builds = reporter.build_report(fixture, minimal_decisions())["builds"]
        self.assertEqual(builds["minutes"], 150)
        self.assertEqual(builds["spotlight"]["minutes"], 150)

    def test_terminal_build_conclusion_partition_is_exhaustive(self):
        fixture = minimal_fixture()
        for run, conclusion in zip(
            fixture["workflow_runs"][:3],
            ("neutral", "skipped", "action_required"),
        ):
            run["conclusion"] = conclusion
        builds = reporter.build_report(fixture, minimal_decisions())["builds"]
        self.assertEqual(builds["neutral"], 1)
        self.assertEqual(builds["skipped"], 1)
        self.assertEqual(builds["action_required"], 1)
        self.assertEqual(
            sum(
                builds[key]
                for key in (
                    "success",
                    "failure",
                    "cancelled",
                    "neutral",
                    "skipped",
                    "action_required",
                    "active",
                )
            ),
            builds["runs"],
        )


class FailClosedDataTests(unittest.TestCase):
    def assert_rejected(self, fixture=None, decisions=None, pattern=None):
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            pattern or ".+",
        ):
            reporter.build_report(
                fixture if fixture is not None else minimal_fixture(),
                decisions if decisions is not None else minimal_decisions(),
            )

    def test_missing_authoritative_issue_commit_and_page_data(self):
        fixture = minimal_fixture()
        fixture["issues"] = []
        self.assert_rejected(fixture=fixture, pattern="missing issue")

        fixture = minimal_fixture()
        fixture["commits"] = [
            commit for commit in fixture["commits"] if commit["sha"] != sha("b")
        ]
        self.assert_rejected(fixture=fixture, pattern="references missing commit")

        fixture = minimal_fixture()
        fixture["workflow_runs"] = fixture["workflow_runs"][:-1]
        self.assert_rejected(fixture=fixture, pattern="needs 5")

    def test_authoritative_commit_ancestry_is_derived(self):
        fixture = minimal_fixture()
        merge = next(
            commit for commit in fixture["commits"] if commit["sha"] == sha("d")
        )
        merge["parents"] = []
        self.assert_rejected(
            fixture=fixture,
            pattern="head is not an ancestor",
        )

    def test_missing_decision_and_derived_fact_override(self):
        decisions = minimal_decisions()
        decisions["pull_requests"] = []
        self.assert_rejected(decisions=decisions, pattern="missing required decision")

        decisions = minimal_decisions()
        decisions["pull_requests"][0]["head_sha"] = sha("f")
        self.assert_rejected(decisions=decisions, pattern="unknown fields: head_sha")

    def test_pre_review_override_binds_to_authoritative_introduction(self):
        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_override(fixture, decisions)
        reporter.build_report(fixture, decisions)

    def test_honest_post_review_override_is_rejected(self):
        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_override(fixture, decisions, commit_sha=sha("c"))
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="introduced after first review",
        )

    def test_backdated_override_provenance_is_rejected(self):
        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_override(
            fixture,
            decisions,
            commit_sha=sha("c"),
            occurred_at="2026-01-01T03:00:00Z",
        )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="occurrence does not match",
        )

    def test_changed_pre_review_override_is_rejected_after_review(self):
        fixture = minimal_fixture()
        decisions = minimal_decisions()
        override = add_override(fixture, decisions)
        reporter.build_report(fixture, decisions)
        override["enabled"] = False
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="changed after its authoritative introduction",
        )

    def test_inserted_override_without_provenance_is_rejected(self):
        decisions = minimal_decisions()
        decisions["pull_requests"][0]["threshold"]["override_history"].append(
            {"enabled": True, "reason": "No authoritative introduction exists."}
        )
        self.assert_rejected(
            decisions=decisions,
            pattern="lack exact authoritative introduction coverage",
        )

    def test_override_provenance_without_decision_record_is_rejected(self):
        fixture = minimal_fixture()
        add_second_pr(fixture)
        override = {
            "enabled": True,
            "reason": "No decision record owns this authoritative event.",
        }
        fixture["events"].append(
            {
                "id": "override:orphan",
                "type": "threshold_override_introduced",
                "occurred_at": "2026-01-01T09:10:00Z",
                "pr_number": 2,
                "sha": sha("e"),
                "override_index": 0,
                "decision_digest": reporter.threshold_override_digest(
                    2, 0, override
                ),
            }
        )
        self.assert_rejected(
            fixture=fixture,
            pattern="has no decision record for PRs 2",
        )

    def test_editable_override_timestamp_is_not_part_of_decision_schema(self):
        fixture = minimal_fixture()
        decisions = minimal_decisions()
        override = add_override(fixture, decisions)
        override["recorded_at"] = "2026-01-01T02:00:00Z"
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="unknown fields: recorded_at",
        )

    def test_clean_review_requires_all_prior_findings_resolved(self):
        fixture = minimal_fixture()
        fixture["review_findings"][0]["resolved_at"] = None
        fixture["reviews"].insert(
            1,
            {
                "id": 12,
                "pr_number": 1,
                "author": reporter.REVIEW_BOT,
                "submitted_at": "2026-01-01T05:00:00Z",
                "commit_sha": sha("b"),
                "state": "COMMENTED",
                "thread_ids": ["thread:101"],
            },
        )
        fixture["pull_requests"][0]["review_ids"].insert(1, 12)
        fixture["review_findings"].append(
            {
                "id": 101,
                "review_id": 12,
                "thread_id": "thread:101",
                "created_at": "2026-01-01T05:01:00Z",
                "resolved_at": "2026-01-01T07:00:00Z",
                "outdated": True,
                "path": "scripts/feature.py",
            }
        )
        self.assert_rejected(
            fixture=fixture,
            pattern="no authoritative clean-review boundary",
        )

    def test_resolution_after_review_does_not_retroactively_make_it_clean(self):
        fixture = minimal_fixture()
        fixture["review_findings"][0]["resolved_at"] = "2026-01-01T08:00:01Z"
        self.assert_rejected(
            fixture=fixture,
            pattern="no authoritative clean-review boundary",
        )

    def test_stack_parent_depth_and_exception_are_consistent(self):
        fixture = minimal_fixture()
        fixture["pull_requests"].append(
            {
                "number": 2,
                "state": "open",
                "created_at": "2026-01-01T00:30:00Z",
                "merged_at": None,
                "closed_at": None,
                "base_ref": "master",
                "head_branch": "agent/two",
                "head_sha": sha("e"),
                "merge_sha": None,
                "issue_numbers": [],
                "review_ids": [],
                "commit_shas": [sha("e")],
                "additions": 10,
                "deletions": 0,
                "files": ["scripts/parent.py"],
            }
        )
        fixture["commits"].append(
            {
                "sha": sha("e"),
                "committed_at": "2026-01-01T00:30:00Z",
                "parents": [],
                "message": "feat: parent",
            }
        )
        fixture["pull_requests"][0]["base_ref"] = "agent/two"
        decisions = minimal_decisions()
        stack = decisions["pull_requests"][0]["stack"]
        stack.update({"depth": 2, "parent_pr": 2})
        reporter.build_report(fixture, decisions)

        stack.update({"depth": 3, "exception_reason": None})
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="exception_reason",
        )
        stack["exception_reason"] = "foundation -> protocol -> feature"
        reporter.build_report(fixture, decisions)

        stack["parent_pr"] = 999
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="no authoritative PR",
        )

    def test_unknown_risk_event_edge_disposition_and_top_level_field(self):
        mutations = []

        decisions = minimal_decisions()
        decisions["pull_requests"][0]["risk_boundaries"] = ["mystery"]
        mutations.append((minimal_fixture(), decisions, "risk_boundaries"))

        fixture = minimal_fixture()
        fixture["events"][0]["type"] = "mystery"
        mutations.append((fixture, minimal_decisions(), "events\\[0\\].type"))

        fixture = minimal_fixture()
        fixture["dependency_edges"][0]["type"] = "mystery"
        mutations.append((fixture, minimal_decisions(), "dependency_edges\\[0\\].type"))

        decisions = minimal_decisions()
        decisions["artifacts"][0]["history"][0]["disposition"] = "Keep"
        mutations.append((minimal_fixture(), decisions, "disposition"))

        fixture = minimal_fixture()
        fixture["derived_head_sha"] = sha("f")
        mutations.append((fixture, minimal_decisions(), "unknown fields"))

        for fixture, decisions, pattern in mutations:
            with self.subTest(pattern=pattern):
                self.assert_rejected(
                    fixture=fixture,
                    decisions=decisions,
                    pattern=pattern,
                )

    def test_active_run_cannot_claim_a_conclusion(self):
        fixture = minimal_fixture()
        fixture["workflow_runs"][3]["conclusion"] = "success"
        self.assert_rejected(
            fixture=fixture,
            pattern="active status requires null",
        )


class ArtifactLifecycleTests(unittest.TestCase):
    def assert_rejected(self, fixture, decisions, pattern):
        with self.assertRaisesRegex(reporter.PilotDataError, pattern):
            reporter.build_report(fixture, decisions)

    def test_orphan_duplicate_expired_and_deletion_ready_artifacts_reject(self):
        fixture = minimal_fixture()
        fixture["dependency_edges"][0]["type"] = "derives"
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "claims non-consumer edge",
        )

        fixture = minimal_fixture()
        fixture["artifacts"].append(copy.deepcopy(fixture["artifacts"][0]))
        self.assert_rejected(fixture, minimal_decisions(), "duplicate artifact")

        decisions = minimal_decisions()
        decisions["artifacts"][0]["expires_at"] = "2026-01-01T09:59:00Z"
        self.assert_rejected(
            minimal_fixture(),
            decisions,
            "expired but not deleted",
        )

        fixture = minimal_fixture()
        for event in fixture["events"]:
            if event["type"] == "deletion_proof":
                event["semantic_result"] = "pass"
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "deletion-ready but not Delete",
        )

    def test_necessary_artifact_removal_fails_then_restore_passes(self):
        fixture = minimal_fixture()
        fixture["events"][-1]["restored_result"] = "fail"
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "was not restored",
        )

        fixture["events"][-1]["restored_result"] = "pass"
        report = reporter.build_report(fixture, minimal_decisions())
        self.assertEqual(
            report["artifacts"]["current"][0]["current_disposition"],
            "Graduate",
        )

    def test_deletion_ready_artifact_passes_only_with_delete_disposition(self):
        fixture = minimal_fixture()
        for event in fixture["events"]:
            if event["type"] == "deletion_proof":
                event["semantic_result"] = "pass"
        decisions = minimal_decisions()
        decisions["artifacts"][0]["history"].append(
            {
                "recorded_at": "2026-01-01T09:30:00Z",
                "disposition": "Delete",
                "reason": "The non-destructive proof preserved every invariant.",
            }
        )
        report = reporter.build_report(fixture, decisions)
        self.assertEqual(
            report["artifacts"]["current"][0]["current_disposition"],
            "Delete",
        )
        self.assertEqual(len(report["artifacts"]["current"][0]["history"]), 2)

    def test_all_four_dispositions_are_auditable_history(self):
        decisions = minimal_decisions()
        decisions["artifacts"][0]["history"] = [
            {
                "recorded_at": "2026-01-01T08:00:00Z",
                "disposition": "Derive",
                "reason": "Derive Git-owned fields.",
            },
            {
                "recorded_at": "2026-01-01T08:30:00Z",
                "disposition": "Consolidate",
                "reason": "Consolidate the remaining decision.",
            },
            {
                "recorded_at": "2026-01-01T09:00:00Z",
                "disposition": "Graduate",
                "reason": "The contract remains necessary.",
            },
        ]
        decisions["artifacts"][0]["history"][-1]["recorded_at"] = (
            "2026-01-01T09:05:00Z"
        )
        report = reporter.build_report(minimal_fixture(), decisions)
        artifact = report["artifacts"]["current"][0]
        self.assertEqual(
            [entry["disposition"] for entry in artifact["history"]],
            ["Derive", "Consolidate", "Graduate"],
        )
        self.assertEqual(artifact["current_disposition"], "Graduate")

    def test_duplicate_unique_artifact_decision_rejects(self):
        fixture = reporter.load_json(BASELINE)
        decisions = reporter.load_json(DECISIONS)
        decisions["artifacts"][1]["unique_decision"] = decisions["artifacts"][0][
            "unique_decision"
        ]
        self.assert_rejected(
            fixture,
            decisions,
            "artifact unique decisions contains duplicates",
        )

    def test_dependency_change_invalidates_review_from_edge(self):
        fixture = minimal_fixture()
        fixture["dependency_edges"].append(
            {
                "id": "review:dependency",
                "type": "review_depends_on",
                "source": "review:10",
                "target": "dependency:api",
            }
        )
        fixture["events"].extend(
            [
                {
                    "id": "dependency:changed",
                    "type": "dependency_changed",
                    "occurred_at": "2026-01-01T05:00:00Z",
                    "artifact_id": "contract",
                    "dependency_id": "dependency:api",
                },
                {
                    "id": "dependency:proof",
                    "type": "deletion_proof",
                    "occurred_at": "2026-01-01T05:01:00Z",
                    "artifact_id": "contract",
                    "trigger_event_id": "dependency:changed",
                    "semantic_result": "fail",
                    "reason": "the dependency remains required",
                    "restored_result": "pass",
                },
            ]
        )
        result = reporter.build_report(fixture, minimal_decisions())
        self.assertEqual(result["artifacts"]["invalidated_review_ids"], [10])

    def test_consumes_and_checks_edges_require_exact_reverse_ownership(self):
        for edge_type in ("consumes", "checks"):
            with self.subTest(edge_type=edge_type, direction="edge-to-artifact"):
                fixture = minimal_fixture()
                fixture["dependency_edges"].append(
                    {
                        "id": f"{edge_type}:unclaimed",
                        "type": edge_type,
                        "source": f"other-{edge_type}",
                        "target": "contract",
                    }
                )
                self.assert_rejected(
                    fixture,
                    minimal_decisions(),
                    "is not claimed exactly by target artifact",
                )

            with self.subTest(edge_type=edge_type, direction="artifact-to-edge"):
                fixture = minimal_fixture()
                edge_id = next(
                    edge["id"]
                    for edge in fixture["dependency_edges"]
                    if edge["type"] == edge_type
                )
                fixture["artifacts"][0]["dependency_ids"].remove(edge_id)
                self.assert_rejected(
                    fixture,
                    minimal_decisions(),
                    "is not claimed exactly by target artifact",
                )

        fixture = reporter.load_json(BASELINE)
        decisions = reporter.load_json(DECISIONS)
        fixture["artifacts"][1]["dependency_ids"].append(
            fixture["artifacts"][0]["dependency_ids"][0]
        )
        self.assert_rejected(
            fixture,
            decisions,
            "ambiguous artifact ownership",
        )

    def test_edge_siblings_reject_semantic_duplicates_and_orphans(self):
        fixture = minimal_fixture()
        duplicate = copy.deepcopy(fixture["dependency_edges"][0])
        duplicate["id"] = "consume:duplicate"
        fixture["dependency_edges"].append(duplicate)
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "duplicates an existing dependency edge",
        )

        fixture = minimal_fixture()
        fixture["dependency_edges"].append(
            {
                "id": "derive:orphan",
                "type": "derives",
                "source": "contract",
                "target": "missing-artifact",
            }
        )
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "must connect authoritative artifacts",
        )

        fixture = minimal_fixture()
        fixture["dependency_edges"].append(
            {
                "id": "review:orphan",
                "type": "review_depends_on",
                "source": "review:999",
                "target": "dependency:api",
            }
        )
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "references missing review",
        )

        fixture = minimal_fixture()
        fixture["events"].extend(
            [
                {
                    "id": "dependency:orphan",
                    "type": "dependency_changed",
                    "occurred_at": "2026-01-01T05:00:00Z",
                    "artifact_id": "contract",
                    "dependency_id": "dependency:orphan",
                },
                {
                    "id": "dependency:orphan-proof",
                    "type": "deletion_proof",
                    "occurred_at": "2026-01-01T05:01:00Z",
                    "artifact_id": "contract",
                    "trigger_event_id": "dependency:orphan",
                    "semantic_result": "fail",
                    "reason": "the dependency remains required",
                    "restored_result": "pass",
                },
            ]
        )
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "has no review dependency edge",
        )

    def test_lifecycle_proofs_and_dispositions_are_strictly_causal(self):
        fixture = minimal_fixture()
        fixture["events"][-1]["occurred_at"] = fixture["events"][-2]["occurred_at"]
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "must strictly follow its trigger",
        )

        fixture = minimal_fixture()
        fixture["events"][-1]["occurred_at"] = "2026-01-01T09:02:59Z"
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "must strictly follow its trigger",
        )

        decisions = minimal_decisions()
        decisions["artifacts"][0]["history"][-1]["recorded_at"] = (
            "2026-01-01T09:03:30Z"
        )
        self.assert_rejected(
            minimal_fixture(),
            decisions,
            "current disposition must strictly follow every deletion proof",
        )

    def test_lifecycle_as_of_covers_events_dispositions_and_expiry(self):
        fixture = minimal_fixture()
        fixture["events"][-1]["occurred_at"] = "2026-01-01T10:06:00Z"
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "follows lifecycle_as_of",
        )

        decisions = minimal_decisions()
        decisions["artifacts"][0]["history"][-1]["recorded_at"] = (
            "2026-01-01T10:06:00Z"
        )
        self.assert_rejected(
            minimal_fixture(),
            decisions,
            "history follows lifecycle_as_of",
        )

        decisions = minimal_decisions()
        decisions["artifacts"][0]["history"][-1]["recorded_at"] = (
            "2026-01-01T10:04:00Z"
        )
        decisions["artifacts"][0]["expires_at"] = "2026-01-01T10:02:00Z"
        self.assert_rejected(
            minimal_fixture(),
            decisions,
            "expired but not deleted",
        )

    def test_orphan_and_missing_deletion_proofs_reject(self):
        fixture = minimal_fixture()
        fixture["events"] = [
            event for event in fixture["events"] if event["id"] != "artifact:proof"
        ]
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "must have exactly one deletion proof",
        )

        fixture = minimal_fixture()
        fixture["events"][-1]["trigger_event_id"] = "missing"
        self.assert_rejected(
            fixture,
            minimal_decisions(),
            "has no valid trigger event",
        )


if __name__ == "__main__":
    unittest.main()
