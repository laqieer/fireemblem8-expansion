import copy
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import reporter


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASELINE = FIXTURES / "baseline.json"
BASELINE_EXPECTED = FIXTURES / "baseline_expected.json"
DECISIONS = ROOT / ".github" / "workflow-pilot-decisions.json"
REVIEWER_OVERRIDE_REPRO_SHA = "980dbee7337633b97fb4d8217ae7cc71f34a9035"
TEST_ARTIFACTS = ROOT / "build" / "test-artifacts"
TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)
BASELINE_AUTHORITY = Path(
    os.environ.get("WORKFLOW_PILOT_TEST_AUTHORITY_ROOT", ROOT)
)


def git_run(
    repository_root,
    *arguments,
    input=None,
    check=True,
    capture_output=True,
    text=False,
    offline=True,
    environment=None,
):
    clean_environment = reporter.git_environment(offline=offline)
    if environment is not None:
        clean_environment.update(environment)
    return subprocess.run(
        reporter.git_command(Path(repository_root), *arguments),
        input=input,
        check=check,
        capture_output=capture_output,
        text=text,
        env=clean_environment,
    )


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
                "is_resolved": True,
                "outdated": True,
                "path": "scripts/feature.py",
            }
        ],
        "review_thread_event_source": {
            "kind": reporter.REVIEW_THREAD_EVENT_SOURCE,
            "complete": True,
            "coverage_start": "2026-01-01T00:00:00Z",
            "coverage_end": "2026-01-01T10:05:00Z",
            "unavailable_reason": None,
        },
        "review_thread_events": [
            {
                "delivery_id": 1000,
                "delivery_guid": "00000000-0000-4000-8000-000000000001",
                "delivered_at": "2026-01-01T06:00:00Z",
                "event": "pull_request_review_thread",
                "action": "resolved",
                "repository": "example/workflow",
                "pr_number": 1,
                "review_id": 10,
                "finding_id": 100,
                "thread_id": "thread:100",
                "actor": "review-owner",
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
                "parents": [sha("0")],
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
                "parents": [sha("0"), sha("c")],
                "message": "Merge pull request #1",
            },
            {
                "sha": sha("0"),
                "committed_at": "2025-12-31T23:00:00Z",
                "parents": [],
                "message": "test base",
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
                "reason": "removal loses the decision boundary",
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


def _replace_commit_identities(value, replacements):
    if isinstance(value, dict):
        return {
            key: _replace_commit_identities(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_commit_identities(item, replacements)
            for item in value
        ]
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
    return value


@contextlib.contextmanager
def git_authority(fixture):
    fixture = copy.deepcopy(fixture)
    reporter.validate_fixture(fixture)
    with tempfile.TemporaryDirectory(
        prefix="workflow-pilot-authority-",
        dir=TEST_ARTIFACTS,
    ) as temporary:
        repository_root = Path(temporary)
        git_run(repository_root, "init", "-q", "-b", "master")
        git_run(
            repository_root,
            "remote",
            "add",
            "origin",
            f"https://github.com/{fixture['repository']}.git",
        )
        empty_tree = (
            git_run(
                repository_root,
                "mktree",
                input=b"",
            )
            .stdout.decode("ascii")
            .strip()
        )
        commits = {commit["sha"]: commit for commit in fixture["commits"]}
        replacements = {}
        pending = set(commits)
        while pending:
            ready = [
                sha_value
                for sha_value in sorted(pending)
                if all(
                    parent_sha in replacements
                    for parent_sha in commits[sha_value]["parents"]
                )
                and all(
                    referenced_sha in replacements
                    for referenced_sha in commits
                    if referenced_sha in commits[sha_value]["message"]
                )
            ]
            if not ready:
                raise AssertionError("test fixture commit graph is cyclic or incomplete")
            for old_sha in ready:
                commit = commits[old_sha]
                message = _replace_commit_identities(
                    commit["message"],
                    replacements,
                )
                command = [
                    "commit-tree",
                    empty_tree,
                ]
                for parent_sha in commit["parents"]:
                    command.extend(("-p", replacements[parent_sha]))
                environment = {
                    "GIT_AUTHOR_NAME": "Pilot Test",
                    "GIT_AUTHOR_EMAIL": "pilot@example.invalid",
                    "GIT_COMMITTER_NAME": "Pilot Test",
                    "GIT_COMMITTER_EMAIL": "pilot@example.invalid",
                    "GIT_AUTHOR_DATE": commit["committed_at"],
                    "GIT_COMMITTER_DATE": commit["committed_at"],
                }
                replacements[old_sha] = (
                    git_run(
                        repository_root,
                        *command,
                        input=(message + "\n").encode("utf-8"),
                        environment=environment,
                    )
                    .stdout.decode("ascii")
                    .strip()
                )
                pending.remove(old_sha)
        yield (
            _replace_commit_identities(fixture, replacements),
            repository_root,
        )


def authoritative_report(fixture, decisions, repository_root=None):
    if repository_root is not None:
        return reporter.build_report(fixture, decisions, repository_root)
    if fixture["repository"] == "laqieer/fireemblem8-expansion":
        return reporter.build_report(fixture, decisions, BASELINE_AUTHORITY)
    with git_authority(fixture) as (authoritative_fixture, authority_root):
        return reporter.build_report(
            authoritative_fixture,
            decisions,
            authority_root,
        )


def expected_from_report(report):
    paths = {}
    for path in reporter.EXPECTED_RESULT_PATHS:
        value = report
        for component in path.split("."):
            value = value[component]
        paths[path] = copy.deepcopy(value)
    return {"schema_version": reporter.SCHEMA_VERSION, "paths": paths}


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


def add_stack_pr(
    fixture,
    decisions,
    *,
    number,
    parent_pr,
    depth,
    sha_character,
    exception_reason=None,
):
    parent = (
        fixture["pull_requests"][0]
        if parent_pr == 1
        else next(
            item for item in fixture["pull_requests"] if item["number"] == parent_pr
        )
    )
    head_sha = sha(sha_character)
    fixture["pull_requests"].append(
        {
            "number": number,
            "state": "open",
            "created_at": f"2026-01-01T09:{number:02d}:00Z",
            "merged_at": None,
            "closed_at": None,
            "base_ref": parent["head_branch"],
            "head_branch": f"agent/{number}",
            "head_sha": head_sha,
            "merge_sha": None,
            "issue_numbers": [],
            "review_ids": [],
            "commit_shas": [head_sha],
            "additions": 10,
            "deletions": 0,
            "files": [f"scripts/stack_{number}.py"],
        }
    )
    fixture["commits"].append(
        {
            "sha": head_sha,
            "committed_at": f"2026-01-01T09:{number:02d}:00Z",
            "parents": [parent["head_sha"]],
            "message": f"feat: stack layer {number}",
        }
    )
    decision = copy.deepcopy(minimal_decisions()["pull_requests"][0])
    decision["pull_request"] = number
    decision["stack"] = {
        "depth": depth,
        "parent_pr": parent_pr,
        "exception_reason": exception_reason,
    }
    decisions["pull_requests"].append(decision)


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
        result = authoritative_report(self.fixture, self.decisions)
        reporter.check_expected(result, self.expected)
        self.assertEqual(
            {
                path: self.expected["paths"][path]
                for path in (
                    "snapshot.repository",
                    "snapshot.base_sha",
                    "snapshot.captured_at",
                    "snapshot.lifecycle_as_of",
                    "snapshot.window.start",
                    "snapshot.window.end",
                )
            },
            {
                "snapshot.repository": "laqieer/fireemblem8-expansion",
                "snapshot.base_sha": (
                    "b8e7f9125e11d322ca37b5288b141bbd52902b61"
                ),
                "snapshot.captured_at": "2026-08-30T11:17:08Z",
                "snapshot.lifecycle_as_of": "2026-08-30T12:23:00Z",
                "snapshot.window.start": "2026-08-20T00:00:00Z",
                "snapshot.window.end": "2026-08-30T11:17:08Z",
            },
        )
        self.assertEqual(len(result["identities"]["pull_requests"]), 64)
        self.assertEqual(len(result["identities"]["issues"]), 53)
        self.assertEqual(len(result["identities"]["reviews"]), 566)
        self.assertEqual(len(result["identities"]["findings"]), 643)
        self.assertEqual(len(result["identities"]["commits"]), 1017)
        self.assertGreaterEqual(len(result["identities"]["workflow_runs"]), 1000)
        self.assertEqual(
            result["identities"]["seal"],
            self.expected["paths"]["identities.seal"],
        )

    def test_normalized_result_is_byte_identical(self):
        first = reporter.normalized_json(
            authoritative_report(self.fixture, self.decisions)
        )
        second = reporter.normalized_json(
            authoritative_report(
                json.loads(json.dumps(self.fixture)),
                json.loads(json.dumps(self.decisions)),
            )
        )
        self.assertEqual(first, second)

    def test_expected_paths_are_a_complete_closed_contract(self):
        report = authoritative_report(self.fixture, self.decisions)
        self.assertEqual(
            set(self.expected["paths"]),
            reporter.EXPECTED_RESULT_PATHS,
        )
        for removed_path in sorted(reporter.EXPECTED_RESULT_PATHS):
            with self.subTest(removed_path=removed_path):
                expected = copy.deepcopy(self.expected)
                del expected["paths"][removed_path]
                changed_report = copy.deepcopy(report)
                changed_report["builds"]["runs"] = -1
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "must exactly match the frozen result contract",
                ):
                    reporter.check_expected(changed_report, expected)

        for path in ("builds.renamed", "builds..runs", ".builds.runs"):
            with self.subTest(path=path):
                expected = copy.deepcopy(self.expected)
                expected["paths"][path] = 326
                pattern = (
                    "is malformed"
                    if path in {"builds..runs", ".builds.runs"}
                    else "must exactly match the frozen result contract"
                )
                with self.assertRaisesRegex(reporter.PilotDataError, pattern):
                    reporter.check_expected(report, expected)

        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "duplicate JSON key 'builds.runs'",
        ):
            reporter.parse_json(
                '{"schema_version":1,"paths":'
                '{"builds.runs":326,"builds.runs":326}}',
                "duplicate expected fixture",
            )

    def test_expected_path_deletion_cannot_hide_reporter_regression(self):
        report = authoritative_report(self.fixture, self.decisions)
        report["delivery"]["pr_open_to_merge_median_hours"] = "0.0"
        expected = copy.deepcopy(self.expected)
        del expected["paths"]["delivery.pr_open_to_merge_median_hours"]
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "delivery.pr_open_to_merge_median_hours",
        ):
            reporter.check_expected(report, expected)

    def test_expected_rejects_coordinated_snapshot_identity_drift(self):
        repository = copy.deepcopy(self.fixture)
        repository["repository"] = "laqieer/coordinated-drift"
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "does not match the checked-out origin",
        ):
            reporter.build_report(repository, self.decisions, BASELINE_AUTHORITY)

        base_sha = copy.deepcopy(self.fixture)
        base_sha["base_sha"] = base_sha["commits"][0]["sha"]
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "outside the frozen base history",
        ):
            reporter.build_report(base_sha, self.decisions, BASELINE_AUTHORITY)

        mutations = []
        captured = copy.deepcopy(self.fixture)
        captured["captured_at"] = "2026-08-30T11:17:09Z"
        captured["window"]["end"] = captured["captured_at"]
        mutations.append(captured)

        window_start = copy.deepcopy(self.fixture)
        window_start["window"]["start"] = "2026-08-19T23:59:59Z"
        mutations.append(window_start)

        for fixture in mutations:
            with self.subTest(snapshot=fixture["repository"], window=fixture["window"]):
                report = authoritative_report(fixture, self.decisions)
                expected = copy.deepcopy(self.expected)
                expected["paths"]["identities.seal"] = report["identities"]["seal"]
                expected["paths"]["computed.seal"] = report["computed"]["seal"]
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "expected path 'snapshot\\.",
                ):
                    reporter.check_expected(report, expected)

    def test_cli_expected_rejects_coordinated_capture_window_drift(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["captured_at"] = "2026-08-30T11:17:09Z"
        fixture["window"]["end"] = fixture["captured_at"]
        report = authoritative_report(fixture, self.decisions)
        expected = copy.deepcopy(self.expected)
        expected["paths"]["identities.seal"] = report["identities"]["seal"]
        expected["paths"]["computed.seal"] = report["computed"]["seal"]
        arguments = [
            "--fixture",
            str(BASELINE),
            "--decisions",
            str(DECISIONS),
            "--expected",
            str(BASELINE_EXPECTED),
            "--repository-root",
            str(ROOT),
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(
                reporter,
                "load_json",
                side_effect=[fixture, self.decisions, expected],
            ),
            mock.patch.object(reporter, "validate_executable_deletion_proofs"),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(reporter.main(arguments), 2)
        self.assertIn("expected path 'snapshot.captured_at'", stderr.getvalue())

    def test_baseline_has_no_synthetic_resolution_timestamps(self):
        self.assertTrue(self.fixture["review_findings"])
        self.assertTrue(
            all(
                "resolved_at" not in finding
                for finding in self.fixture["review_findings"]
            )
        )
        self.assertEqual(
            self.fixture["review_thread_event_source"],
            {
                "kind": reporter.REVIEW_THREAD_EVENT_SOURCE,
                "complete": False,
                "coverage_start": None,
                "coverage_end": None,
                "unavailable_reason": (
                    "historical-review-thread-events-not-collected"
                ),
            },
        )
        result = authoritative_report(self.fixture, self.decisions)
        timing = result["delivery"]["first_push_to_clean_review"]
        self.assertEqual(timing["status"], "unavailable")
        self.assertIsNone(timing["median_hours"])
        self.assertFalse(timing["pilot_ready"])
        self.assertEqual(result["reviews"]["rounds"], 34)
        self.assertEqual(result["reviews"]["valid_findings"], 101)
        self.assertEqual(result["reviews"]["current_unresolved_findings"], 0)

    def test_frozen_review_cannot_be_rebound_to_base_history(self):
        fixture = copy.deepcopy(self.fixture)
        review = next(
            item for item in fixture["reviews"] if item["id"] == 5037233057
        )
        self.assertEqual(
            review["commit_sha"],
            "0301760c273ff0a5f8e6475f9bd373503f4a5aae",
        )
        review["commit_sha"] = "f701e692090c86cfd85fbcbe17fa9a2b96a46030"
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "review 5037233057 commit is outside PR 150 candidate history",
        ):
            authoritative_report(fixture, self.decisions)

    def test_frozen_pr150_events_cannot_predate_creation(self):
        for event_type in ("base_changed", "security_finding"):
            with self.subTest(event_type=event_type):
                fixture = copy.deepcopy(self.fixture)
                if event_type == "base_changed":
                    event = next(
                        item
                        for item in fixture["events"]
                        if item["type"] == event_type
                        and item["pr_number"] == 150
                    )
                else:
                    pr = next(
                        item
                        for item in fixture["pull_requests"]
                        if item["number"] == 150
                    )
                    event = {
                        "id": "security:pre-creation-reproducer",
                        "type": event_type,
                        "occurred_at": "2026-08-20T00:00:00Z",
                        "pr_number": 150,
                        "sha": pr["commit_shas"][0],
                    }
                    fixture["events"].append(event)
                event["occurred_at"] = "2026-08-20T00:00:00Z"
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "precedes PR 150 creation",
                ):
                    authoritative_report(fixture, self.decisions)

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
            "--repository-root",
            str(ROOT),
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
                authoritative_report(self.fixture, self.decisions)
            ),
        )

    def test_cli_requires_exact_repository_and_decision_paths(self):
        common = [
            sys.executable,
            "-m",
            "scripts.workflow_pilot.reporter",
            "--fixture",
            str(BASELINE),
            "--expected",
            str(BASELINE_EXPECTED),
        ]
        wrong_root = subprocess.run(
            [
                *common,
                "--decisions",
                str(DECISIONS),
                "--repository-root",
                str(ROOT / "scripts"),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(wrong_root.returncode, 2)
        self.assertIn(b"exact Git top level", wrong_root.stderr)

        wrong_decisions = subprocess.run(
            [
                *common,
                "--decisions",
                str(BASELINE_EXPECTED),
                "--repository-root",
                str(ROOT),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(wrong_decisions.returncode, 2)
        self.assertIn(b"--decisions must identify", wrong_decisions.stderr)

        with tempfile.TemporaryDirectory(
            prefix="workflow-pilot-inputs-",
            dir=TEST_ARTIFACTS,
        ) as temporary:
            alternate_fixture = Path(temporary) / "baseline.json"
            alternate_expected = Path(temporary) / "baseline_expected.json"
            shutil.copy2(BASELINE, alternate_fixture)
            shutil.copy2(BASELINE_EXPECTED, alternate_expected)
            for option, replacement in (
                ("--fixture", alternate_fixture),
                ("--expected", alternate_expected),
            ):
                with self.subTest(option=option):
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
                        "--repository-root",
                        str(ROOT),
                    ]
                    command[command.index(option) + 1] = str(replacement)
                    result = subprocess.run(
                        command,
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(
                        f"{option} must identify".encode("ascii"),
                        result.stderr,
                    )


class RepositoryAuthorityTests(unittest.TestCase):
    def test_override_blob_commits_derive_from_strict_fixture_and_decisions(self):
        from scripts.workflow_pilot import hydrate_authority

        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_override(fixture, decisions)
        with tempfile.TemporaryDirectory(
            prefix="workflow-pilot-blob-inputs-",
            dir=TEST_ARTIFACTS,
        ) as temporary:
            fixture_path = Path(temporary) / "fixture.json"
            decisions_path = Path(temporary) / "decisions.json"
            fixture_path.write_bytes(reporter.normalized_json(fixture))
            decisions_path.write_bytes(reporter.normalized_json(decisions))
            repository, commits, decision_commits = (
                hydrate_authority.required_override_decision_commits(
                    fixture_path,
                    decisions_path,
                )
            )
            self.assertEqual(repository, fixture["repository"])
            self.assertEqual(
                commits,
                sorted(commit["sha"] for commit in fixture["commits"]),
            )
            self.assertEqual(decision_commits, [sha("a"), sha("b")])

            decisions["pull_requests"][0]["threshold"]["override_history"][0][
                "enabled"
            ] = False
            decisions_path.write_bytes(reporter.normalized_json(decisions))
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "digest does not match",
            ):
                hydrate_authority.required_override_decision_commits(
                    fixture_path,
                    decisions_path,
                )

    def test_repository_authority_uses_minimal_offline_git_environment(self):
        hostile = {
            "GIT_DIR": "/redirected",
            "GIT_OBJECT_DIRECTORY": "/redirected/objects",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "remote.origin.url",
            "GIT_CONFIG_VALUE_0": "https://github.com/attacker/repository.git",
            "PRESERVED": "must-not-leak",
        }
        with mock.patch.dict(os.environ, hostile, clear=True):
            environment = reporter.git_environment(offline=True)
        self.assertEqual(
            environment,
            {
                "GIT_CONFIG_COUNT": "0",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
        )
        self.assertEqual(reporter.trusted_git_executable(), "/usr/bin/git")
        self.assertEqual(
            reporter.git_command(ROOT, "rev-parse", "HEAD"),
            (
                "/usr/bin/git",
                "--no-replace-objects",
                "-C",
                str(ROOT),
                "rev-parse",
                "HEAD",
            ),
        )

    def test_ambient_git_repository_object_and_config_redirects_are_ignored(self):
        fixture = minimal_fixture()
        with git_authority(fixture) as (authoritative_fixture, repository_root):
            with git_authority(minimal_fixture()) as (_, alternate_root):
                hostile = {
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(
                        ROOT / ".git" / "objects"
                    ),
                    "GIT_CEILING_DIRECTORIES": str(repository_root),
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_GLOBAL": str(alternate_root / ".git" / "config"),
                    "GIT_CONFIG_KEY_0": "remote.origin.url",
                    "GIT_CONFIG_PARAMETERS": (
                        "'remote.origin.url'='https://github.com/attacker/repository.git'"
                    ),
                    "GIT_CONFIG_SYSTEM": str(alternate_root / ".git" / "config"),
                    "GIT_CONFIG_VALUE_0": (
                        "https://github.com/attacker/repository.git"
                    ),
                    "GIT_DIR": str(alternate_root / ".git"),
                    "GIT_EXEC_PATH": str(alternate_root),
                    "GIT_NO_REPLACE_OBJECTS": "0",
                    "GIT_OBJECT_DIRECTORY": str(
                        alternate_root / ".git" / "objects"
                    ),
                    "GIT_REPLACE_REF_BASE": "refs/attacker/",
                    "GIT_WORK_TREE": str(alternate_root),
                }
                with mock.patch.dict(os.environ, hostile, clear=False):
                    report = reporter.build_report(
                        authoritative_fixture,
                        minimal_decisions(),
                        repository_root,
                    )
        self.assertEqual(report["delivery"]["merged_pull_requests"], 1)

    def test_repository_replace_refs_grafts_and_object_alternates_reject(self):
        fixture = minimal_fixture()
        with git_authority(fixture) as (authoritative_fixture, repository_root):
            commits = {
                commit["message"]: commit["sha"]
                for commit in authoritative_fixture["commits"]
            }
            git_run(
                repository_root,
                "replace",
                commits["feat: begin"],
                commits["fix: review"],
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "replacement refs are not permitted",
            ):
                reporter.build_report(
                    authoritative_fixture,
                    minimal_decisions(),
                    repository_root,
                )
            git_run(
                repository_root,
                "replace",
                "-d",
                commits["feat: begin"],
            )

            graft_path = (
                repository_root
                / git_run(
                    repository_root,
                    "rev-parse",
                    "--git-path",
                    "info/grafts",
                ).stdout.decode("utf-8").strip()
            )
            graft_path.parent.mkdir(parents=True, exist_ok=True)
            graft_path.write_text(
                f"{commits['fix: finish']} {commits['test base']}\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "graft file is not permitted",
            ):
                reporter.build_report(
                    authoritative_fixture,
                    minimal_decisions(),
                    repository_root,
                )
            graft_path.unlink()

            alternates_path = (
                repository_root
                / git_run(
                    repository_root,
                    "rev-parse",
                    "--git-path",
                    "objects/info/alternates",
                ).stdout.decode("utf-8").strip()
            )
            alternates_path.parent.mkdir(parents=True, exist_ok=True)
            alternates_path.write_text(
                f"{ROOT / '.git' / 'objects'}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "alternate object store is not permitted",
            ):
                reporter.build_report(
                    authoritative_fixture,
                    minimal_decisions(),
                    repository_root,
                )

    def test_report_construction_requires_explicit_repository_authority(self):
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "requires an explicit repository authority root",
        ):
            reporter.build_report(minimal_fixture(), minimal_decisions())

    def test_temporary_real_repository_accepts_exact_git_facts(self):
        fixture = minimal_fixture()
        with git_authority(fixture) as (authoritative_fixture, repository_root):
            report = reporter.build_report(
                authoritative_fixture,
                minimal_decisions(),
                repository_root,
            )
        self.assertEqual(report["delivery"]["merged_pull_requests"], 1)
        self.assertEqual(len(report["identities"]["commits"]), 5)

    def test_real_repository_rejects_fabricated_git_facts(self):
        fixture = minimal_fixture()
        with git_authority(fixture) as (authoritative_fixture, repository_root):
            mutations = []

            changed = copy.deepcopy(authoritative_fixture)
            changed["repository"] = "example/fabricated"
            for event in changed["review_thread_events"]:
                event["repository"] = changed["repository"]
            mutations.append((changed, "checked-out origin"))

            changed = copy.deepcopy(authoritative_fixture)
            changed["commits"][-1]["committed_at"] = "2025-12-31T23:00:01Z"
            mutations.append((changed, "timestamp does not match"))

            changed = copy.deepcopy(authoritative_fixture)
            changed["commits"][0]["parents"] = []
            mutations.append((changed, "parents do not match"))

            changed = copy.deepcopy(authoritative_fixture)
            changed["commits"][0]["message"] = "fabricated message"
            mutations.append((changed, "message does not match"))

            changed = copy.deepcopy(authoritative_fixture)
            old_merge = changed["pull_requests"][0]["merge_sha"]
            fabricated = "f" * 40
            changed = _replace_commit_identities(
                changed,
                {old_merge: fabricated},
            )
            mutations.append((changed, "does not exist"))

            changed = copy.deepcopy(authoritative_fixture)
            changed["pull_requests"][0]["commit_shas"] = changed[
                "pull_requests"
            ][0]["commit_shas"][1:]
            mutations.append((changed, "outside PR 1 candidate history"))

            for changed, pattern in mutations:
                with self.subTest(pattern=pattern):
                    with self.assertRaisesRegex(
                        reporter.PilotDataError,
                        pattern,
                    ):
                        reporter.build_report(
                            changed,
                            minimal_decisions(),
                            repository_root,
                        )

    def test_frozen_fixture_is_bound_to_actual_checkout(self):
        fixture = reporter.load_json(BASELINE)
        data = reporter.validate_fixture(fixture)
        authority = reporter.validate_repository_authority(
            BASELINE_AUTHORITY,
            data,
        )
        self.assertEqual(len(authority["commits"]), 1017)
        self.assertEqual(authority["reverts"], [])

    def test_review_commit_candidate_and_availability_boundaries(self):
        for commit_sha, pattern in (
            (sha("0"), "outside PR 1 candidate history"),
            (sha("9"), "outside PR 1 candidate history"),
            (sha("d"), "outside PR 1 candidate history"),
            (sha("c"), "precedes its reviewed commit"),
        ):
            with self.subTest(commit_sha=commit_sha):
                fixture = minimal_fixture()
                if commit_sha == sha("9"):
                    fixture["commits"].append(
                        {
                            "sha": sha("9"),
                            "committed_at": "2026-01-01T02:00:00Z",
                            "parents": [],
                            "message": "unrelated commit",
                        }
                    )
                fixture["reviews"][0]["commit_sha"] = commit_sha
                with self.assertRaisesRegex(reporter.PilotDataError, pattern):
                    reporter.validate_fixture(fixture)

        fixture = minimal_fixture()
        fixture["reviews"][0]["commit_sha"] = sha("b")
        fixture["reviews"][0]["submitted_at"] = "2026-01-01T03:00:00Z"
        authoritative_report(fixture, minimal_decisions())

    def test_run_commit_availability_exact_reproducer_and_boundary(self):
        fixture = minimal_fixture()
        fixture["workflow_runs"][0]["head_sha"] = sha("c")
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "workflow run 1 predates repository commit",
        ):
            authoritative_report(fixture, minimal_decisions())

        fixture = minimal_fixture()
        run = fixture["workflow_runs"][2]
        run["created_at"] = "2026-01-01T05:59:59Z"
        run["started_at"] = run["created_at"]
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "workflow run 3 predates repository commit",
        ):
            authoritative_report(fixture, minimal_decisions())

        fixture = minimal_fixture()
        fixture["workflow_runs"][2]["created_at"] = "2026-01-01T06:00:00Z"
        authoritative_report(fixture, minimal_decisions())

    def test_run_availability_covers_status_workflow_and_master_scopes(self):
        for workflow in ("Build CI", "Running Copilot Code Review"):
            for status in ("queued", "in_progress", "completed"):
                with self.subTest(workflow=workflow, status=status):
                    fixture = minimal_fixture()
                    run = fixture["workflow_runs"][4]
                    run.update(
                        {
                            "workflow": workflow,
                            "status": status,
                            "conclusion": (
                                "success" if status == "completed" else None
                            ),
                            "created_at": "2026-01-01T06:00:00Z",
                            "started_at": (
                                None if status == "queued" else "2026-01-01T06:00:00Z"
                            ),
                            "completed_at": (
                                "2026-01-01T06:00:00Z"
                                if status == "completed"
                                else None
                            ),
                            "head_sha": sha("c"),
                        }
                    )
                    authoritative_report(fixture, minimal_decisions())

        fixture = minimal_fixture()
        fixture["workflow_runs"][3].update(
            {
                "created_at": "2026-01-01T09:00:00Z",
                "started_at": "2026-01-01T09:00:00Z",
                "head_sha": sha("d"),
                "head_branch": "master",
            }
        )
        authoritative_report(fixture, minimal_decisions())


class StrictPrimitiveAndMessageTests(unittest.TestCase):
    def set_value(self, root, path, value):
        current = root
        for component in path[:-1]:
            current = current[component]
        current[path[-1]] = value

    def test_every_fixture_integer_family_rejects_booleans(self):
        paths = (
            ("schema_version",),
            ("workflow_sample_size",),
            ("spotlight_pr",),
            ("pull_requests", 0, "number"),
            ("pull_requests", 0, "issue_numbers", 0),
            ("pull_requests", 0, "review_ids", 0),
            ("pull_requests", 0, "additions"),
            ("pull_requests", 0, "deletions"),
            ("issues", 0, "number"),
            ("reviews", 0, "id"),
            ("reviews", 0, "pr_number"),
            ("review_findings", 0, "id"),
            ("review_findings", 0, "review_id"),
            ("review_thread_events", 0, "delivery_id"),
            ("review_thread_events", 0, "pr_number"),
            ("review_thread_events", 0, "review_id"),
            ("review_thread_events", 0, "finding_id"),
            ("workflow_runs", 0, "id"),
            ("workflow_runs", 0, "attempt"),
            ("events", 0, "pr_number"),
            ("events", 4, "minutes"),
        )
        for path in paths:
            for boolean in (False, True):
                with self.subTest(path=path, boolean=boolean):
                    fixture = minimal_fixture()
                    self.set_value(fixture, path, boolean)
                    with self.assertRaisesRegex(
                        reporter.PilotDataError,
                        "must be an integer",
                    ):
                        reporter.validate_fixture(fixture)

        for boolean in (False, True):
            with self.subTest(override_index=boolean):
                fixture = minimal_fixture()
                fixture["events"].append(
                    {
                        "id": "override:boolean-index",
                        "type": "threshold_override_introduced",
                        "occurred_at": "2026-01-01T01:00:00Z",
                        "pr_number": 1,
                        "sha": sha("a"),
                        "override_index": boolean,
                        "decision_digest": "0" * 64,
                    }
                )
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "override_index must be an integer",
                ):
                    reporter.validate_fixture(fixture)

    def test_every_decision_and_expected_integer_family_rejects_booleans(self):
        paths = (
            ("schema_version",),
            ("pull_requests", 0, "pull_request"),
            ("pull_requests", 0, "stack", "depth"),
            ("pull_requests", 0, "stack", "parent_pr"),
            ("artifacts", 0, "max_maintenance_minutes"),
            ("artifacts", 0, "estimated_maintenance_minutes"),
        )
        data = reporter.validate_fixture(minimal_fixture())
        for path in paths:
            for boolean in (False, True):
                with self.subTest(path=path, boolean=boolean):
                    decisions = minimal_decisions()
                    self.set_value(decisions, path, boolean)
                    with self.assertRaisesRegex(
                        reporter.PilotDataError,
                        "must be an integer",
                    ):
                        reporter.validate_decisions(decisions, data)

        report = authoritative_report(
            reporter.load_json(BASELINE),
            reporter.load_json(DECISIONS),
        )
        for boolean in (False, True):
            with self.subTest(expected_schema_version=boolean):
                expected = reporter.load_json(BASELINE_EXPECTED)
                expected["schema_version"] = boolean
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "expected.schema_version must be an integer",
                ):
                    reporter.check_expected(report, expected)

        expected = reporter.load_json(BASELINE_EXPECTED)
        integer_paths = [
            path
            for path, value in expected["paths"].items()
            if type(value) is int
        ]
        self.assertGreater(len(integer_paths), 0)
        for path in integer_paths:
            for boolean in (False, True):
                with self.subTest(expected_path=path, boolean=boolean):
                    changed = copy.deepcopy(expected)
                    changed["paths"][path] = boolean
                    with self.assertRaisesRegex(
                        reporter.PilotDataError,
                        "must be an integer",
                    ):
                        reporter.check_expected(report, changed)

        for boolean in (False, True):
            with self.subTest(historical_schema_version=boolean):
                historical = minimal_decisions()
                historical["schema_version"] = boolean
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "schema_version must be an integer",
                ):
                    reporter.historical_override_entry(
                        historical,
                        sha("a"),
                        1,
                        0,
                    )

    def test_boolean_fields_remain_boolean(self):
        fixture = minimal_fixture()
        decisions = minimal_decisions()
        self.assertIs(fixture["review_findings"][0]["is_resolved"], True)
        self.assertIs(fixture["review_findings"][0]["outdated"], True)
        self.assertIs(
            fixture["review_thread_event_source"]["complete"],
            True,
        )
        self.assertIs(decisions["pull_requests"][0]["pilot"]["included"], False)
        authoritative_report(fixture, decisions)

    def test_commit_message_canonicalization_preserves_authored_whitespace(self):
        cases = {
            b"subject\n": "subject",
            b"subject\n\n": "subject\n",
            b"subject \n": "subject ",
            b" subject\n": " subject",
            b"subject\n\nbody\n": "subject\n\nbody",
            b"subject\n\nbody\n\n": "subject\n\nbody\n",
        }
        canonical = {
            reporter.canonical_commit_message(raw, sha("a"))
            for raw in cases
        }
        self.assertEqual(canonical, set(cases.values()))
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    reporter.canonical_commit_message(raw, sha("a")),
                    expected,
                )

    def test_fixture_commit_message_whitespace_cannot_collide(self):
        fixture = reporter.load_json(BASELINE)
        decisions = reporter.load_json(DECISIONS)
        original = fixture["commits"][0]["message"]
        mutations = (
            original + "\n",
            original + " ",
            original + "\n\n",
            original + "\nchanged body",
            " " + original,
        )
        for message in mutations:
            with self.subTest(message=repr(message[-40:])):
                changed = copy.deepcopy(fixture)
                changed["commits"][0]["message"] = message
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "message does not match the Git object database",
                ):
                    authoritative_report(changed, decisions)

        multiline = next(
            commit
            for commit in fixture["commits"]
            if "\n\n" in commit["message"]
        )
        actual = reporter._load_git_commit_objects(
            BASELINE_AUTHORITY,
            [multiline["sha"]],
        )
        self.assertEqual(actual[multiline["sha"]]["message"], multiline["message"])

    def test_real_commit_preserves_authored_terminal_blank_line(self):
        fixture = minimal_fixture()
        fixture["commits"][1]["message"] = "subject\n\nbody\n"
        authoritative_report(fixture, minimal_decisions())


class CohortIdentitySealTests(unittest.TestCase):
    def identity_data(self):
        return reporter.validate_fixture(minimal_fixture())

    def test_every_frozen_identity_family_changes_the_expected_seal(self):
        baseline_data = self.identity_data()
        seal = reporter.cohort_identity_seal(baseline_data)
        baseline_report = authoritative_report(
            minimal_fixture(),
            minimal_decisions(),
        )
        expected = expected_from_report(baseline_report)
        expected["paths"]["identities.seal"] = seal
        families = {
            "pull_requests": (1, 2),
            "issues": (1, 2),
            "reviews": (10, 12),
            "runs": (1, 10),
            "findings": (100, 101),
            "commits": (sha("a"), sha("9")),
        }
        for family, (old, new) in families.items():
            with self.subTest(family=family):
                changed = copy.deepcopy(baseline_data)
                changed[family][new] = changed[family].pop(old)
                report = copy.deepcopy(baseline_report)
                report["identities"]["seal"] = reporter.cohort_identity_seal(
                    changed
                )
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "identities.seal",
                ):
                    reporter.check_expected(report, expected)

    def test_identity_seal_normalizes_family_ordering(self):
        forward = self.identity_data()
        reverse = copy.deepcopy(forward)
        for family in (
            "artifacts",
            "commits",
            "edges",
            "events",
            "findings",
            "issues",
            "pull_requests",
            "reviews",
            "review_thread_events",
            "runs",
        ):
            reverse[family] = dict(reversed(list(reverse[family].items())))
        reverse["pull_requests"][1]["commit_shas"].reverse()
        reverse["pull_requests"][1]["review_ids"].reverse()
        self.assertEqual(
            reporter.cohort_identity_seal(forward),
            reporter.cohort_identity_seal(reverse),
        )

    def test_metric_relationship_mutations_change_expected_seal(self):
        baseline_data = self.identity_data()
        baseline_data["repository_authority"] = {"reverts": []}
        seal = reporter.cohort_identity_seal(baseline_data)
        baseline_report = authoritative_report(
            minimal_fixture(),
            minimal_decisions(),
        )
        expected = expected_from_report(baseline_report)
        expected["paths"]["identities.seal"] = seal
        mutations = {}

        changed = copy.deepcopy(baseline_data)
        changed["reviews"][10]["commit_sha"] = sha("b")
        mutations["review-commit"] = (changed, reporter.report_reviews)

        changed = copy.deepcopy(baseline_data)
        event = changed["events"].pop("supersede:1")
        event["id"] = "supersede:renamed"
        changed["events"][event["id"]] = event
        mutations["event-id"] = (changed, reporter.report_events)

        changed = copy.deepcopy(baseline_data)
        changed["events"]["supersede:1"]["occurred_at"] = (
            "2026-01-01T06:00:01Z"
        )
        mutations["event-timestamp"] = (changed, reporter.report_events)

        changed = copy.deepcopy(baseline_data)
        changed["events"]["supersede:1"]["pr_number"] = 2
        mutations["event-pr"] = (changed, reporter.report_events)

        changed = copy.deepcopy(baseline_data)
        changed["events"]["supersede:1"]["new_sha"] = sha("b")
        mutations["event-sha"] = (changed, reporter.report_events)

        changed = copy.deepcopy(baseline_data)
        changed["findings"][100]["review_id"] = 11
        mutations["finding-review"] = (changed, reporter.report_reviews)

        changed = copy.deepcopy(baseline_data)
        changed["runs"][5]["head_sha"] = sha("b")
        mutations["run-head"] = (changed, reporter.report_builds)

        changed = copy.deepcopy(baseline_data)
        changed["runs"][5]["created_at"] = "2026-01-01T07:30:01Z"
        mutations["run-timestamp"] = (changed, reporter.report_builds)

        for relationship, (changed, report_function) in mutations.items():
            with self.subTest(relationship=relationship):
                self.assertEqual(
                    report_function(baseline_data),
                    report_function(changed),
                )
                report = copy.deepcopy(baseline_report)
                report["identities"]["seal"] = reporter.cohort_identity_seal(
                    changed
                )
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "identities.seal",
                ):
                    reporter.check_expected(report, expected)

    def test_expected_contract_cannot_omit_identity_seal(self):
        report = authoritative_report(minimal_fixture(), minimal_decisions())
        expected = expected_from_report(report)
        del expected["paths"]["identities.seal"]
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "must exactly match the frozen result contract",
        ):
            reporter.check_expected(report, expected)


class DecisionSemanticsSealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = reporter.load_json(BASELINE)
        cls.decisions = reporter.load_json(DECISIONS)
        cls.report = authoritative_report(cls.fixture, cls.decisions)
        cls.expected = reporter.load_json(BASELINE_EXPECTED)

    def assert_decision_change(
        self,
        name,
        mutate,
        computed_unchanged=True,
    ):
        fixture = copy.deepcopy(self.fixture)
        decisions = copy.deepcopy(self.decisions)
        mutate(fixture, decisions)
        with self.subTest(name=name):
            try:
                report = authoritative_report(fixture, decisions)
            except reporter.PilotDataError:
                return
            if computed_unchanged:
                self.assertEqual(
                    report["computed"]["seal"],
                    self.report["computed"]["seal"],
                )
            self.assertNotEqual(
                report["decisions"]["seal"],
                self.report["decisions"]["seal"],
            )
            expected = copy.deepcopy(self.expected)
            if not computed_unchanged:
                expected["paths"]["computed.seal"] = report["computed"]["seal"]
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "decisions.seal",
            ):
                reporter.check_expected(report, expected)

    def test_pr_governance_fields_are_sealed_or_fail_validation(self):
        mutations = (
            ("pull-request", lambda _, d: d["pull_requests"][0].update(
                {"pull_request": 151}
            )),
            ("risk-boundaries", lambda _, d: d["pull_requests"][0].update(
                {"risk_boundaries": ["security"]}
            )),
            ("threshold-triggers", lambda _, d: d["pull_requests"][0][
                "threshold"
            ].update({"triggers": ["none"]})),
            ("override-history", lambda _, d: d["pull_requests"][0][
                "threshold"
            ]["override_history"].append(
                {"enabled": True, "reason": "Schema-valid new override."}
            )),
            ("gate-mode", lambda _, d: d["pull_requests"][0].update(
                {"gate_mode": "review-first"}
            )),
            ("stack-depth", lambda _, d: d["pull_requests"][0]["stack"].update(
                {"depth": 1}
            )),
            ("stack-parent", lambda _, d: d["pull_requests"][0]["stack"].update(
                {"parent_pr": 150}
            )),
            ("stack-exception", lambda _, d: d["pull_requests"][0]["stack"].update(
                {"exception_reason": "Schema-valid exception reason."}
            )),
            ("pilot-inclusion", lambda _, d: d["pull_requests"][0]["pilot"].update(
                {"included": True}
            )),
            ("pilot-disposition", lambda _, d: d["pull_requests"][0]["pilot"].update(
                {"disposition": "paused"}
            )),
        )
        for name, mutate in mutations:
            self.assert_decision_change(
                name,
                mutate,
                computed_unchanged=not name.startswith("disposition"),
            )

    def test_artifact_governance_fields_are_sealed_or_fail_validation(self):
        mutations = (
            ("artifact-id", lambda _, d: d["artifacts"][0].update(
                {"artifact_id": "alternate-artifact"}
            )),
            ("owner", lambda _, d: d["artifacts"][0].update(
                {"owner": "alternate-governance"}
            )),
            ("consumer", lambda f, d: (
                d["artifacts"][0].update(
                    {"executable_consumer": "alternate-consumer"}
                ),
                f["dependency_edges"][0].update(
                    {"source": "alternate-consumer"}
                ),
            )),
            ("unique-decision", lambda _, d: d["artifacts"][0].update(
                {"unique_decision": "alternate-unique-decision"}
            )),
            ("consistency-check", lambda f, d: (
                d["artifacts"][0].update(
                    {"consistency_check": "alternate-check"}
                ),
                f["dependency_edges"][1].update({"source": "alternate-check"}),
            )),
            ("maximum-cost", lambda _, d: d["artifacts"][0].update(
                {"max_maintenance_minutes": 6}
            )),
            ("estimated-cost", lambda _, d: d["artifacts"][0].update(
                {"estimated_maintenance_minutes": 3}
            )),
            ("delete-when", lambda _, d: d["artifacts"][0].update(
                {"deletion_criterion": "Delete after all dependents retire."}
            )),
            ("expiry", lambda _, d: d["artifacts"][0].update(
                {"expires_at": "2026-09-01T00:00:00Z"}
            )),
            ("disposition-time", lambda _, d: d["artifacts"][0]["history"][0].update(
                {"recorded_at": "2026-08-30T12:22:31Z"}
            )),
            ("disposition", lambda _, d: d["artifacts"][0]["history"][0].update(
                {"disposition": "Consolidate"}
            )),
            ("disposition-reason", lambda _, d: d["artifacts"][0]["history"][0].update(
                {"reason": "Alternate schema-valid disposition reason."}
            )),
        )
        for name, mutate in mutations:
            self.assert_decision_change(
                name,
                mutate,
                computed_unchanged=not name.startswith("disposition"),
            )

    def test_authority_proof_review_and_dependency_relationships_are_sealed(self):
        data = reporter.validate_fixture(minimal_fixture())
        decisions = reporter.validate_decisions(
            minimal_decisions(),
            data,
        )
        baseline = reporter.decision_semantics_seal(data, decisions)
        mutations = []

        changed_data = copy.deepcopy(data)
        changed_data["artifacts"]["contract"]["path"] = ".github/alternate.json"
        mutations.append(("authoritative-source", changed_data, copy.deepcopy(decisions)))

        changed_data = copy.deepcopy(data)
        changed_data["events"]["artifact:proof"]["reason"] = (
            "Alternate schema-valid deletion result."
        )
        mutations.append(("verify-deletion", changed_data, copy.deepcopy(decisions)))

        changed_data = copy.deepcopy(data)
        changed_data["reviews"][10]["submitted_at"] = "2026-01-01T04:00:01Z"
        mutations.append(("review-boundary", changed_data, copy.deepcopy(decisions)))

        changed_data = copy.deepcopy(data)
        changed_decisions = copy.deepcopy(decisions)
        changed_data["edges"]["consume:contract"]["source"] = "alternate-consumer"
        changed_decisions["artifacts"]["contract"]["executable_consumer"] = (
            "alternate-consumer"
        )
        mutations.append(
            ("dependency-association", changed_data, changed_decisions)
        )

        changed_data = copy.deepcopy(data)
        changed_decisions = copy.deepcopy(decisions)
        changed_decisions["pull_requests"][1]["threshold"][
            "override_history"
        ].append({"enabled": True, "reason": "Sealed override reason."})
        changed_data["events"]["override:sealed"] = {
            "id": "override:sealed",
            "type": "threshold_override_introduced",
            "occurred_at": "2026-01-01T01:00:00Z",
            "pr_number": 1,
            "sha": sha("a"),
            "override_index": 0,
            "decision_digest": reporter.threshold_override_digest(
                1,
                0,
                changed_decisions["pull_requests"][1]["threshold"][
                    "override_history"
                ][0],
            ),
        }
        mutations.append(
            ("override-provenance", changed_data, changed_decisions)
        )

        for name, changed_data, changed_decisions in mutations:
            with self.subTest(name=name):
                self.assertNotEqual(
                    reporter.decision_semantics_seal(
                        changed_data,
                        changed_decisions,
                    ),
                    baseline,
                )

        override_data = copy.deepcopy(data)
        override_decisions = copy.deepcopy(decisions)
        override = {"enabled": True, "reason": "Sealed override reason."}
        override_decisions["pull_requests"][1]["threshold"][
            "override_history"
        ].append(override)
        override_data["events"]["override:sealed"] = {
            "id": "override:sealed",
            "type": "threshold_override_introduced",
            "occurred_at": "2026-01-01T01:00:00Z",
            "pr_number": 1,
            "sha": sha("a"),
            "override_index": 0,
            "decision_digest": reporter.threshold_override_digest(
                1,
                0,
                override,
            ),
        }
        override_seal = reporter.decision_semantics_seal(
            override_data,
            override_decisions,
        )
        for field, value in (
            ("enabled", False),
            ("reason", "Changed schema-valid override reason."),
        ):
            with self.subTest(override_field=field):
                changed_decisions = copy.deepcopy(override_decisions)
                changed_decisions["pull_requests"][1]["threshold"][
                    "override_history"
                ][0][field] = value
                self.assertNotEqual(
                    reporter.decision_semantics_seal(
                        override_data,
                        changed_decisions,
                    ),
                    override_seal,
                )

        changed_data = copy.deepcopy(override_data)
        changed_data["events"]["override:sealed"]["occurred_at"] = (
            "2026-01-01T01:00:01Z"
        )
        self.assertNotEqual(
            reporter.decision_semantics_seal(
                changed_data,
                override_decisions,
            ),
            override_seal,
        )


class FormulaAndClassificationTests(unittest.TestCase):
    def assert_rejected(self, fixture, pattern):
        with self.assertRaisesRegex(reporter.PilotDataError, pattern):
            authoritative_report(fixture, minimal_decisions())

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
                fixture["pull_requests"][0]["review_ids"] = []
                fixture["reviews"] = []
                fixture["review_findings"] = []
                fixture["review_thread_events"] = []
                fixture["events"] = [
                    event
                    for event in fixture["events"]
                    if "pr_number" not in event
                ]
                if boundary == "2026-01-01T00:00:00Z":
                    earlier_times = {
                        sha("0"): "2025-12-31T22:00:00Z",
                        sha("a"): "2025-12-31T23:00:00Z",
                        sha("b"): "2025-12-31T23:20:00Z",
                        sha("c"): "2025-12-31T23:40:00Z",
                        sha("d"): "2025-12-31T23:59:59Z",
                    }
                    for commit in fixture["commits"]:
                        commit["committed_at"] = earlier_times[commit["sha"]]
                report = authoritative_report(fixture, minimal_decisions())
                self.assertEqual(report["delivery"]["merged_pull_requests"], 1)

    def test_build_cancellation_duplicate_supersession_and_overhead_formulas(self):
        result = authoritative_report(minimal_fixture(), minimal_decisions())
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
        data["repository_authority"] = {"reverts": []}
        self.assertEqual(
            reporter.report_classifications(data)[0],
            {
                "pr": 1,
                "work_state": "merged",
                "flags": ["still-running", "superseded"],
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
        data["repository_authority"] = {"reverts": []}
        classification = reporter.report_classifications(data)[0]
        self.assertEqual(classification["work_state"], "cancelled")
        self.assertEqual(
            classification["flags"],
            [
                "bulk-deletion",
                "generated-only",
                "stacked",
                "still-running",
                "superseded",
            ],
        )

        fixture["pull_requests"][0].update(
            {
                "state": "open",
                "closed_at": None,
            }
        )
        data = reporter.validate_fixture(fixture)
        data["repository_authority"] = {"reverts": []}
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
                "message": f"Revert delivery\n\nThis reverts commit {sha('d')}.",
            }
        )
        fixture["base_sha"] = sha("e")
        result = authoritative_report(fixture, minimal_decisions())
        self.assertIn("reverted", result["classifications"][0]["flags"])
        self.assertEqual(len(result["events"]["reverts"]), 1)
        self.assertNotEqual(
            result["events"]["reverts"][0]["commit"],
            result["events"]["reverts"][0]["reverts"],
        )

    def test_active_run_flag_coexists_with_every_pr_work_state(self):
        states = (
            ("merged", "merged"),
            ("closed", "cancelled"),
            ("open", "still-running"),
        )
        for run_status in ("queued", "in_progress"):
            for pr_state, work_state in states:
                for run_branch in ("agent/one", "refs/pull/1/head"):
                    with self.subTest(
                        run_status=run_status,
                        pr_state=pr_state,
                        run_branch=run_branch,
                    ):
                        fixture = minimal_fixture()
                        pr = fixture["pull_requests"][0]
                        if pr_state == "closed":
                            pr.update(
                                {
                                    "state": "closed",
                                    "merged_at": None,
                                    "merge_sha": None,
                                }
                            )
                        elif pr_state == "open":
                            pr.update(
                                {
                                    "state": "open",
                                    "merged_at": None,
                                    "closed_at": None,
                                    "merge_sha": None,
                                }
                            )
                        run = fixture["workflow_runs"][3]
                        run.update(
                            {
                                "status": run_status,
                                "conclusion": None,
                                "completed_at": None,
                                "head_branch": run_branch,
                            }
                        )
                        data = reporter.validate_fixture(fixture)
                        data["repository_authority"] = {"reverts": []}
                        classification = reporter.report_classifications(data)[0]
                        self.assertEqual(classification["work_state"], work_state)
                        self.assertIn("still-running", classification["flags"])

    def test_terminal_runs_do_not_add_still_running_flag(self):
        fixture = minimal_fixture()
        fixture["workflow_runs"][3].update(
            {
                "status": "completed",
                "conclusion": "success",
                "completed_at": "2026-01-01T09:30:00Z",
            }
        )
        data = reporter.validate_fixture(fixture)
        data["repository_authority"] = {"reverts": []}
        classification = reporter.report_classifications(data)[0]
        self.assertEqual(classification["work_state"], "merged")
        self.assertNotIn("still-running", classification["flags"])

    def test_minimal_fixture_reports_coexisting_classification_summary(self):
        result = authoritative_report(minimal_fixture(), minimal_decisions())
        self.assertEqual(
            result["classification_summary"],
            {
                "flags": {
                    "bulk_deletion": 0,
                    "generated_only": 0,
                    "reverted": 0,
                    "stacked": 0,
                    "still_running": 1,
                    "superseded": 1,
                },
                "work_states": {
                    "cancelled": 0,
                    "merged": 1,
                    "still_running": 0,
                },
            },
        )

    def test_revert_requires_later_authoritative_timestamp(self):
        for committed_at in (
            "2026-01-01T08:59:59Z",
            "2026-01-01T09:00:00Z",
        ):
            with self.subTest(committed_at=committed_at):
                fixture = minimal_fixture()
                fixture["commits"].append(
                    {
                        "sha": sha("e"),
                        "committed_at": committed_at,
                        "parents": [sha("d")],
                        "message": (
                            "Revert delivery\n\n"
                            f"This reverts commit {sha('d')}."
                        ),
                    }
                )
                fixture["base_sha"] = sha("e")
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "is not later than target",
                ):
                    authoritative_report(fixture, minimal_decisions())

    def test_revert_requires_target_ancestry_and_frozen_history(self):
        fixture = minimal_fixture()
        fixture["commits"].extend(
            [
                {
                    "sha": sha("e"),
                    "committed_at": "2026-01-01T09:30:00Z",
                    "parents": [sha("0")],
                    "message": (
                        "Unrelated revert\n\n"
                        f"This reverts commit {sha('d')}."
                    ),
                },
                {
                    "sha": sha("f"),
                    "committed_at": "2026-01-01T09:40:00Z",
                    "parents": [sha("d"), sha("e")],
                    "message": "Snapshot both histories",
                },
            ]
        )
        fixture["base_sha"] = sha("f")
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "is not descended from target",
        ):
            authoritative_report(fixture, minimal_decisions())

        fixture = minimal_fixture()
        fixture["commits"].append(
            {
                "sha": sha("e"),
                "committed_at": "2026-01-01T09:30:00Z",
                "parents": [sha("d")],
                "message": (
                    "Unknown target\n\n"
                    f"This reverts commit {sha('f')}."
                ),
            }
        )
        fixture["base_sha"] = sha("e")
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "targets unavailable commit",
        ):
            authoritative_report(fixture, minimal_decisions())

    def test_real_git_revert_message_has_one_exact_final_trailer(self):
        with tempfile.TemporaryDirectory(
            prefix="workflow-pilot-real-revert-",
            dir=TEST_ARTIFACTS,
        ) as temporary:
            repository_root = Path(temporary)
            git_run(repository_root, "init", "-q", "-b", "master")
            git_run(repository_root, "config", "user.name", "Pilot Test")
            git_run(
                repository_root,
                "config",
                "user.email",
                "pilot@example.invalid",
            )
            tracked = repository_root / "tracked.txt"
            tracked.write_text("before\n", encoding="ascii")
            git_run(repository_root, "add", "tracked.txt")
            git_run(repository_root, "commit", "-q", "-m", "initial")
            tracked.write_text("after\n", encoding="ascii")
            git_run(repository_root, "commit", "-q", "-am", "change")
            target = git_run(
                repository_root,
                "rev-parse",
                "HEAD",
                text=True,
            ).stdout.strip()
            git_run(repository_root, "revert", "--no-edit", target)
            revert = git_run(
                repository_root,
                "rev-parse",
                "HEAD",
                text=True,
            ).stdout.strip()
            message = reporter._load_git_commit_objects(
                repository_root,
                [revert],
            )[revert]["message"]
            self.assertEqual(
                reporter.canonical_revert_target(message, revert),
                target,
            )
            self.assertEqual(
                message,
                f'Revert "change"\n\nThis reverts commit {target}.',
            )

    def test_noncanonical_revert_trailers_fail_closed(self):
        target = sha("d")
        trailer = f"This reverts commit {target}."
        variants = (
            f"Revert delivery\n\nthis reverts commit {target}.",
            f"Revert delivery\n\nThis reverts commit {target[:12]}.",
            f"Revert delivery\n\nThis reverts commit {target.upper()}.",
            f"Revert delivery\n\n{trailer}\ntrailing text",
            f"Revert delivery\n\n{trailer}\n",
            f"Revert delivery\n\n {trailer}",
            f"Revert delivery\n\nprefix {trailer}",
            f"Revert delivery\n\n{trailer}\n\n{trailer}",
        )
        for message in variants:
            with self.subTest(message=repr(message)):
                fixture = minimal_fixture()
                fixture["commits"].append(
                    {
                        "sha": sha("e"),
                        "committed_at": "2026-01-01T09:30:00Z",
                        "parents": [target],
                        "message": message,
                    }
                )
                fixture["base_sha"] = sha("e")
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "invalid canonical revert trailer",
                ):
                    authoritative_report(fixture, minimal_decisions())

    def test_review_round_and_density_formulas(self):
        result = authoritative_report(minimal_fixture(), minimal_decisions())
        self.assertEqual(result["reviews"]["rounds"], 2)
        self.assertEqual(result["reviews"]["superseded_rounds"], 0)
        self.assertEqual(result["reviews"]["valid_findings"], 1)
        self.assertEqual(result["reviews"]["current_resolved_findings"], 1)
        self.assertEqual(result["reviews"]["current_unresolved_findings"], 0)
        self.assertEqual(result["reviews"]["valid_findings_per_kloc"], "1.000")
        self.assertEqual(result["reviews"]["valid_findings_per_review"], "0.500")
        self.assertEqual(
            result["delivery"]["first_push_to_clean_review"]["median_hours"],
            "7.0",
        )
        self.assertTrue(
            result["delivery"]["first_push_to_clean_review"]["pilot_ready"]
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
            post_merge = event_type in {
                "broken_master",
                "escaped_defect",
                "security_finding",
            }
            event = {
                "id": event_id,
                "type": event_type,
                "occurred_at": (
                    "2026-01-01T09:30:00Z"
                    if post_merge
                    else "2026-01-01T08:30:00Z"
                ),
                "pr_number": 1,
                "sha": sha("d") if post_merge else sha("c"),
            }
            fixture["events"].append(event)
        events = authoritative_report(fixture, minimal_decisions())["events"]
        self.assertEqual(events["conflicts"], 1)
        self.assertEqual(events["escaped_defects"], 1)
        self.assertEqual(events["broken_master"], 1)
        self.assertEqual(events["security_findings"], 1)
        self.assertEqual(events["manual_rejects"], 1)

    def test_pr_event_creation_closure_and_commit_boundaries(self):
        fixture = minimal_fixture()
        next(
            event for event in fixture["events"] if event["id"] == "base:1"
        )["occurred_at"] = fixture["pull_requests"][0]["created_at"]
        next(
            event for event in fixture["events"] if event["id"] == "saved:review"
        )["occurred_at"] = fixture["pull_requests"][0]["closed_at"]
        next(
            event
            for event in fixture["events"]
            if event["id"] == "supersede:1"
        )["occurred_at"] = "2026-01-01T06:00:00Z"
        authoritative_report(fixture, minimal_decisions())

        fixture = minimal_fixture()
        next(
            event
            for event in fixture["events"]
            if event["id"] == "supersede:1"
        )["occurred_at"] = "2026-01-01T05:59:59Z"
        self.assert_rejected(fixture, "predates commit availability")

    def test_post_close_event_phase_and_history_rules(self):
        for event_type in ("broken_master", "escaped_defect", "security_finding"):
            with self.subTest(allowed=event_type):
                fixture = minimal_fixture()
                fixture["events"].append(
                    {
                        "id": f"post-close:{event_type}",
                        "type": event_type,
                        "occurred_at": "2026-01-01T09:30:00Z",
                        "pr_number": 1,
                        "sha": sha("d"),
                    }
                )
                authoritative_report(fixture, minimal_decisions())

        post_close_events = (
            {
                "id": "post-close:base",
                "type": "base_changed",
                "occurred_at": "2026-01-01T09:30:00Z",
                "pr_number": 1,
                "old_base": "parent",
                "new_base": "master",
            },
            {
                "id": "post-close:review",
                "type": "review_saved",
                "occurred_at": "2026-01-01T09:30:00Z",
                "pr_number": 1,
                "minutes": 1,
            },
            {
                "id": "post-close:conflict",
                "type": "conflict_detected",
                "occurred_at": "2026-01-01T09:30:00Z",
                "pr_number": 1,
                "sha": sha("c"),
            },
            {
                "id": "post-close:manual",
                "type": "manual_reject",
                "occurred_at": "2026-01-01T09:30:00Z",
                "pr_number": 1,
                "sha": sha("c"),
            },
            {
                "id": "post-close:supersession",
                "type": "candidate_superseded",
                "occurred_at": "2026-01-01T09:30:00Z",
                "pr_number": 1,
                "old_sha": sha("b"),
                "new_sha": sha("c"),
            },
        )
        for event in post_close_events:
            with self.subTest(rejected=event["type"]):
                fixture = minimal_fixture()
                fixture["events"].append(event)
                self.assert_rejected(fixture, "follows PR 1 closure")

        fixture = minimal_fixture()
        fixture["events"].append(
            {
                "id": "post-close:security-candidate",
                "type": "security_finding",
                "occurred_at": "2026-01-01T09:30:00Z",
                "pr_number": 1,
                "sha": sha("c"),
            }
        )
        self.assert_rejected(fixture, "outside PR 1 causal history")

    def test_close_reopen_and_override_events_require_open_phase(self):
        fixture = minimal_fixture()
        fixture["events"].append(
            {
                "id": "close:duplicate",
                "type": "closed",
                "occurred_at": "2026-01-01T05:05:30Z",
                "pr_number": 1,
            }
        )
        self.assert_rejected(fixture, "closes an already closed PR")

        fixture = minimal_fixture()
        fixture["events"].append(
            {
                "id": "reopen:duplicate",
                "type": "reopened",
                "occurred_at": "2026-01-01T04:30:00Z",
                "pr_number": 1,
            }
        )
        self.assert_rejected(fixture, "reopens an already open PR")

        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_override(
            fixture,
            decisions,
            commit_sha=sha("b"),
            occurred_at="2026-01-01T09:30:00Z",
        )
        self.assert_rejected(fixture, "follows PR 1 closure")

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
        events = authoritative_report(fixture, minimal_decisions())["events"]
        self.assertEqual(events["spotlight_pr"], 1)
        self.assertEqual(events["security_findings"], 1)

    def test_safety_event_sha_requires_commit_and_matching_pr_history(self):
        for event_sha, pattern in (
            (sha("f"), "has no authoritative commit"),
            (sha("d"), "outside PR 2 causal history"),
        ):
            with self.subTest(event_sha=event_sha):
                fixture = minimal_fixture()
                add_second_pr(fixture)
                fixture["events"].append(
                    {
                        "id": "escape:cross-pr",
                        "type": "manual_reject",
                        "occurred_at": "2026-01-01T09:30:00Z",
                        "pr_number": 2,
                        "sha": event_sha,
                    }
                )
                with self.assertRaisesRegex(reporter.PilotDataError, pattern):
                    authoritative_report(fixture, minimal_decisions())

    def test_review_capture_boundary_and_temporal_binding(self):
        fixture = minimal_fixture()
        fixture["pull_requests"][0]["merged_at"] = fixture["captured_at"]
        fixture["pull_requests"][0]["closed_at"] = fixture["captured_at"]
        fixture["reviews"][1]["submitted_at"] = fixture["captured_at"]
        authoritative_report(fixture, minimal_decisions())

        mutations = (
            ("submitted_at", "2026-01-01T10:00:01Z", "analysis window"),
            ("submitted_at", "2026-01-01T00:59:59Z", "precedes PR"),
        )
        for field, value, pattern in mutations:
            with self.subTest(field=field, value=value):
                changed = minimal_fixture()
                changed["reviews"][1][field] = value
                self.assert_rejected(fixture=changed, pattern=pattern)

        fixture = minimal_fixture()
        fixture["reviews"][0]["submitted_at"] = "2026-01-01T01:30:00Z"
        fixture["commits"][0]["committed_at"] = "2026-01-01T02:00:00Z"
        self.assert_rejected(fixture=fixture, pattern="reviewed commit")

        fixture = minimal_fixture()
        fixture["reviews"][0]["state"] = "APPROVED"
        self.assert_rejected(fixture=fixture, pattern="COMMENTED state")

        fixture = minimal_fixture()
        fixture["reviews"][0]["submitted_at"] = "2026-01-01T09:00:02Z"
        self.assert_rejected(fixture=fixture, pattern="follows PR 1 closure")

    def test_finding_capture_boundary_and_temporal_binding(self):
        fixture = minimal_fixture()
        fixture["pull_requests"][0]["merged_at"] = fixture["captured_at"]
        fixture["pull_requests"][0]["closed_at"] = fixture["captured_at"]
        fixture["review_findings"][0]["created_at"] = fixture["captured_at"]
        fixture["review_findings"][0]["is_resolved"] = False
        fixture["review_thread_event_source"] = {
            "kind": reporter.REVIEW_THREAD_EVENT_SOURCE,
            "complete": False,
            "coverage_start": None,
            "coverage_end": None,
            "unavailable_reason": "historical-review-thread-events-not-collected",
        }
        fixture["review_thread_events"] = []
        authoritative_report(fixture, minimal_decisions())

        for value, pattern in (
            ("2026-01-01T10:00:01Z", "analysis window"),
            ("2026-01-01T00:59:59Z", "PR 1 creation"),
        ):
            with self.subTest(value=value):
                changed = minimal_fixture()
                changed["review_findings"][0]["created_at"] = value
                self.assert_rejected(fixture=changed, pattern=pattern)

        fixture = minimal_fixture()
        fixture["review_findings"][0]["created_at"] = "2026-01-01T09:00:02Z"
        fixture["review_findings"][0]["is_resolved"] = False
        fixture["review_thread_event_source"] = {
            "kind": reporter.REVIEW_THREAD_EVENT_SOURCE,
            "complete": False,
            "coverage_start": None,
            "coverage_end": None,
            "unavailable_reason": "historical-review-thread-events-not-collected",
        }
        fixture["review_thread_events"] = []
        self.assert_rejected(fixture=fixture, pattern="follows PR 1 closure")

    def test_review_thread_events_cannot_change_state_after_capture(self):
        fixture = minimal_fixture()
        fixture["pull_requests"][0]["merged_at"] = fixture["captured_at"]
        fixture["pull_requests"][0]["closed_at"] = fixture["captured_at"]
        fixture["review_thread_events"][0]["delivered_at"] = fixture["captured_at"]
        authoritative_report(fixture, minimal_decisions())

        fixture = minimal_fixture()
        fixture["review_thread_events"][0]["delivered_at"] = (
            "2026-01-01T10:00:01Z"
        )
        self.assert_rejected(fixture=fixture, pattern="analysis window")

        fixture = minimal_fixture()
        fixture["reviews"][0]["submitted_at"] = "2026-01-01T05:00:00Z"
        fixture["review_thread_events"][0]["delivered_at"] = (
            "2026-01-01T04:30:00Z"
        )
        self.assert_rejected(fixture=fixture, pattern="precedes its review")

        fixture = minimal_fixture()
        fixture["review_thread_events"][0]["delivered_at"] = (
            "2026-01-01T09:00:02Z"
        )
        self.assert_rejected(fixture=fixture, pattern="follows PR 1 closure")

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
        builds = authoritative_report(fixture, minimal_decisions())["builds"]
        self.assertEqual(builds["sample_size"], 5)
        self.assertEqual(builds["spotlight"]["runs"], 4)
        self.assertEqual(builds["spotlight"]["success"], 1)

    def test_queued_run_accrues_zero_duration_even_with_started_at(self):
        fixture = minimal_fixture()
        fixture["workflow_runs"][3]["status"] = "queued"
        builds = authoritative_report(fixture, minimal_decisions())["builds"]
        self.assertEqual(builds["minutes"], 150)
        self.assertEqual(builds["spotlight"]["minutes"], 150)

    def test_every_workflow_run_has_coherent_status_and_timestamps(self):
        mutations = (
            (
                {"completed_at": "2026-01-01T07:29:59Z"},
                "ends before it starts",
            ),
            (
                {"started_at": None},
                "completed_at requires started_at",
            ),
            (
                {"completed_at": None},
                "completed status requires completed_at",
            ),
            (
                {"started_at": "2026-01-01T07:29:59Z"},
                "started_at precedes creation",
            ),
            (
                {"completed_at": "2026-01-01T10:00:01Z"},
                "completed_at follows the snapshot",
            ),
            (
                {"conclusion": None},
                "conclusion must be one of",
            ),
            (
                {"conclusion": "timed_out"},
                "conclusion must be one of",
            ),
            (
                {
                    "status": "in_progress",
                    "conclusion": None,
                    "completed_at": None,
                    "started_at": None,
                },
                "in_progress status requires started_at",
            ),
            (
                {"status": "in_progress", "conclusion": None},
                "in_progress status requires null",
            ),
            (
                {"status": "queued", "conclusion": "success", "completed_at": None},
                "queued status requires null",
            ),
            (
                {"status": "queued", "conclusion": None,
                 "started_at": "2026-01-01T10:00:01Z", "completed_at": None},
                "started_at follows the snapshot",
            ),
        )
        for changes, pattern in mutations:
            with self.subTest(changes=changes):
                fixture = minimal_fixture()
                fixture["workflow_runs"][4].update(changes)
                with self.assertRaisesRegex(reporter.PilotDataError, pattern):
                    authoritative_report(fixture, minimal_decisions())

    def test_non_build_run_status_boundaries_and_conclusions_are_accepted(self):
        for conclusion in sorted(reporter.RUN_CONCLUSIONS):
            with self.subTest(conclusion=conclusion):
                fixture = minimal_fixture()
                fixture["workflow_runs"][4].update(
                    {
                        "conclusion": conclusion,
                        "started_at": "2026-01-01T10:00:00Z",
                        "completed_at": "2026-01-01T10:00:00Z",
                    }
                )
                authoritative_report(fixture, minimal_decisions())

        fixture = minimal_fixture()
        fixture["workflow_runs"][4].update(
            {
                "status": "queued",
                "conclusion": None,
                "started_at": None,
                "completed_at": None,
            }
        )
        authoritative_report(fixture, minimal_decisions())

        fixture["workflow_runs"][4]["started_at"] = "2026-01-01T10:00:00Z"
        authoritative_report(fixture, minimal_decisions())

        fixture["workflow_runs"][4]["status"] = "in_progress"
        authoritative_report(fixture, minimal_decisions())

    def test_terminal_build_conclusion_partition_is_exhaustive(self):
        fixture = minimal_fixture()
        for run, conclusion in zip(
            fixture["workflow_runs"][:3],
            ("neutral", "skipped", "action_required"),
        ):
            run["conclusion"] = conclusion
        builds = authoritative_report(fixture, minimal_decisions())["builds"]
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
    def assert_rejected(
        self,
        fixture=None,
        decisions=None,
        pattern=None,
        repository_root=None,
    ):
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            pattern or ".+",
        ):
            authoritative_report(
                fixture if fixture is not None else minimal_fixture(),
                decisions if decisions is not None else minimal_decisions(),
                repository_root,
            )

    def make_override_case(
        self,
        *,
        first_tree="exact",
        introduction="a",
        override_count=1,
    ):
        directory = self.enterContext(
            tempfile.TemporaryDirectory(
                prefix="workflow-pilot-git-",
                dir=TEST_ARTIFACTS,
            )
        )
        repository_root = Path(directory)
        git_run(repository_root, "init", "-q", "-b", "master")
        git_run(
            repository_root,
            "config",
            "user.name",
            "Pilot Test",
        )
        git_run(
            repository_root,
            "config",
            "user.email",
            "pilot@example.invalid",
        )

        git_run(
            repository_root,
            "remote",
            "add",
            "origin",
            "https://github.com/example/workflow.git",
        )
        decisions = minimal_decisions()
        overrides = [
            {
                "enabled": index % 2 == 0,
                "reason": f"Immutable pre-review override {index}.",
            }
            for index in range(override_count)
        ]
        decisions["pull_requests"][0]["threshold"]["override_history"] = copy.deepcopy(
            overrides
        )
        exact_tree = copy.deepcopy(decisions)
        if first_tree == "exact":
            first_decisions = exact_tree
        elif first_tree == "missing-file":
            first_decisions = None
        elif first_tree == "missing-entry":
            first_decisions = minimal_decisions()
        elif first_tree == "changed-entry":
            first_decisions = copy.deepcopy(exact_tree)
            first_decisions["pull_requests"][0]["threshold"]["override_history"][0][
                "enabled"
            ] = not overrides[0]["enabled"]
        elif first_tree == "invalid-schema":
            first_decisions = copy.deepcopy(exact_tree)
            first_decisions["pull_requests"][0]["gate_mode"] = "unknown"
        else:
            self.fail(f"unknown first-tree mode {first_tree}")

        decision_path = repository_root / reporter.DECISION_RECORD_PATH
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        dates = {
            "0": "2025-12-31T23:00:00+00:00",
            "a": "2026-01-01T01:00:00+00:00",
            "b": "2026-01-01T03:00:00+00:00",
            "c": "2026-01-01T06:00:00+00:00",
            "d": "2026-01-01T09:00:00+00:00",
        }
        messages = {
            "0": "test base",
            "a": "feat: begin",
            "b": "fix: review",
            "c": "fix: finish",
            "d": "Merge pull request #1",
        }
        shas = {}
        for letter in ("0", "a", "b", "c"):
            tree_decisions = first_decisions if letter == "a" else exact_tree
            if tree_decisions is None:
                decision_path.unlink(missing_ok=True)
            else:
                decision_path.write_bytes(reporter.normalized_json(tree_decisions))
            (repository_root / "marker.txt").write_text(
                f"{letter}\n", encoding="ascii"
            )
            git_run(repository_root, "add", "-A")
            environment = {
                "GIT_AUTHOR_DATE": dates[letter],
                "GIT_COMMITTER_DATE": dates[letter],
            }
            git_run(
                repository_root,
                "commit",
                "-q",
                "-m",
                messages[letter],
                environment=environment,
            )
            shas[letter] = (
                git_run(
                    repository_root,
                    "rev-parse",
                    "HEAD",
                    text=True,
                )
                .stdout.strip()
            )
        git_run(
            repository_root,
            "checkout",
            "-q",
            "-b",
            "integration",
            shas["0"],
        )
        environment = {
            "GIT_AUTHOR_DATE": dates["d"],
            "GIT_COMMITTER_DATE": dates["d"],
        }
        git_run(
            repository_root,
            "merge",
            "--no-ff",
            "-q",
            "-m",
            messages["d"],
            "master",
            environment=environment,
        )
        shas["d"] = (
            git_run(
                repository_root,
                "rev-parse",
                "HEAD",
                text=True,
            )
            .stdout.strip()
        )

        fixture_text = json.dumps(minimal_fixture())
        for letter, commit_sha in shas.items():
            fixture_text = fixture_text.replace(sha(letter), commit_sha)
        fixture = json.loads(fixture_text)
        introduction_sha = shas[introduction]
        introduced_at = next(
            commit["committed_at"]
            for commit in fixture["commits"]
            if commit["sha"] == introduction_sha
        )
        for index, override in enumerate(overrides):
            fixture["events"].append(
                {
                    "id": f"override:introduced:{index}",
                    "type": "threshold_override_introduced",
                    "occurred_at": introduced_at,
                    "pr_number": 1,
                    "sha": introduction_sha,
                    "override_index": index,
                    "decision_digest": reporter.threshold_override_digest(
                        1, index, override
                    ),
                }
            )
        return repository_root, fixture, decisions, overrides, shas

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

    def test_pre_review_override_exists_in_immutable_reviewed_tree(self):
        repository_root, fixture, decisions, _, _ = self.make_override_case()
        authoritative_report(fixture, decisions, repository_root)

    def test_reviewer_old_sha_fixture_event_reproducer_is_rejected(self):
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "lacks .github/workflow-pilot-decisions.json",
        ):
            reporter.load_decisions_from_commit(ROOT, REVIEWER_OVERRIDE_REPRO_SHA)

        fixture = reporter.load_json(BASELINE)
        decisions = reporter.load_json(DECISIONS)
        override = {
            "enabled": True,
            "reason": "A newly authored event cannot create historical provenance.",
        }
        decisions["pull_requests"][0]["threshold"]["override_history"].append(
            override
        )
        pr = next(
            item for item in fixture["pull_requests"] if item["number"] == 150
        )
        cited_sha = pr["commit_shas"][0]
        self.assertEqual(cited_sha, REVIEWER_OVERRIDE_REPRO_SHA)
        commit = next(
            item
            for item in fixture["commits"]
            if item["sha"] == cited_sha
        )
        fixture["events"].append(
            {
                "id": "override:reviewer-reproducer",
                "type": "threshold_override_introduced",
                "occurred_at": commit["committed_at"],
                "pr_number": 150,
                "sha": cited_sha,
                "override_index": 0,
                "decision_digest": reporter.threshold_override_digest(
                    150,
                    0,
                    override,
                ),
            }
        )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            repository_root=ROOT,
            pattern="lacks .github/workflow-pilot-decisions.json",
        )

    def test_missing_or_changed_historical_override_is_rejected(self):
        for first_tree, pattern in (
            ("missing-file", "lacks .github/workflow-pilot-decisions.json"),
            ("missing-entry", "lacks PR 1 threshold override 0"),
            ("changed-entry", "differs from its immutable introduction tree"),
            ("invalid-schema", "gate_mode must be one of"),
        ):
            with self.subTest(first_tree=first_tree):
                repository_root, fixture, decisions, _, _ = self.make_override_case(
                    first_tree=first_tree
                )
                self.assert_rejected(
                    fixture=fixture,
                    decisions=decisions,
                    repository_root=repository_root,
                    pattern=pattern,
                )

    def test_override_digest_mismatch_is_rejected(self):
        repository_root, fixture, decisions, _, _ = self.make_override_case()
        fixture["events"][-1]["decision_digest"] = "0" * 64
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            repository_root=repository_root,
            pattern="digest does not match",
        )

    def test_non_candidate_override_commit_is_rejected(self):
        repository_root, fixture, decisions, _, shas = self.make_override_case()
        event = fixture["events"][-1]
        event["sha"] = shas["d"]
        event["occurred_at"] = "2026-01-01T09:00:00Z"
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            repository_root=repository_root,
            pattern="outside PR 1 causal history",
        )

    def test_post_review_override_commit_is_rejected_even_when_backdated(self):
        for introduction, pattern in (
            ("b", "not present at the first reviewed commit"),
            ("c", "does not predate the first review"),
        ):
            with self.subTest(introduction=introduction):
                repository_root, fixture, decisions, _, _ = (
                    self.make_override_case(
                        first_tree="missing-entry",
                        introduction=introduction,
                    )
                )
                self.assert_rejected(
                    fixture=fixture,
                    decisions=decisions,
                    repository_root=repository_root,
                    pattern=pattern,
                )

    def test_backdated_override_commit_fixture_and_event_are_rejected(self):
        repository_root, fixture, decisions, _, _ = self.make_override_case()
        fixture["events"][-1]["occurred_at"] = "2026-01-01T00:30:00Z"
        introduction_sha = fixture["events"][-1]["sha"]
        next(
            commit
            for commit in fixture["commits"]
            if commit["sha"] == introduction_sha
        )["committed_at"] = "2026-01-01T00:30:00Z"
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            repository_root=repository_root,
            pattern="precedes PR 1 creation",
        )

    def test_reordered_override_with_recomputed_digests_is_rejected(self):
        repository_root, fixture, decisions, _, _ = self.make_override_case(
            override_count=2
        )
        history = decisions["pull_requests"][0]["threshold"]["override_history"]
        history.reverse()
        for event in fixture["events"][-2:]:
            index = event["override_index"]
            event["decision_digest"] = reporter.threshold_override_digest(
                1, index, history[index]
            )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            repository_root=repository_root,
            pattern="differs from its immutable introduction tree",
        )

    def test_current_override_mutation_with_recomputed_digest_is_rejected(self):
        repository_root, fixture, decisions, _, _ = self.make_override_case()
        override = decisions["pull_requests"][0]["threshold"]["override_history"][0]
        override["enabled"] = not override["enabled"]
        fixture["events"][-1]["decision_digest"] = reporter.threshold_override_digest(
            1, 0, override
        )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            repository_root=repository_root,
            pattern="differs from its immutable introduction tree",
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
        fixture["review_findings"][0]["is_resolved"] = False
        fixture["review_thread_events"] = []
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
                "is_resolved": True,
                "outdated": True,
                "path": "scripts/feature.py",
            }
        )
        fixture["review_thread_events"].append(
            {
                "delivery_id": 1001,
                "delivery_guid": "00000000-0000-4000-8000-000000000002",
                "delivered_at": "2026-01-01T07:00:00Z",
                "event": "pull_request_review_thread",
                "action": "resolved",
                "repository": "example/workflow",
                "pr_number": 1,
                "review_id": 12,
                "finding_id": 101,
                "thread_id": "thread:101",
                "actor": "review-owner",
            }
        )
        result = authoritative_report(fixture, minimal_decisions())
        timing = result["delivery"]["first_push_to_clean_review"]
        self.assertEqual(timing["status"], "unavailable")
        self.assertEqual(timing["reason"], "no-authoritative-clean-review-boundary")
        self.assertIsNone(timing["median_hours"])
        self.assertFalse(timing["pilot_ready"])

    def test_resolution_after_review_does_not_retroactively_make_it_clean(self):
        fixture = minimal_fixture()
        fixture["review_thread_events"][0]["delivered_at"] = (
            "2026-01-01T08:00:01Z"
        )
        result = authoritative_report(fixture, minimal_decisions())
        timing = result["delivery"]["first_push_to_clean_review"]
        self.assertEqual(timing["status"], "unavailable")
        self.assertEqual(timing["reason"], "no-authoritative-clean-review-boundary")
        self.assertIsNone(timing["median_hours"])
        self.assertFalse(timing["pilot_ready"])

    def test_authoritative_resolution_before_review_is_numeric(self):
        result = authoritative_report(minimal_fixture(), minimal_decisions())
        timing = result["delivery"]["first_push_to_clean_review"]
        self.assertEqual(timing["status"], "available")
        self.assertEqual(timing["median_hours"], "7.0")
        self.assertTrue(timing["pilot_ready"])

    def test_authoritative_unresolve_transition_prevents_clean_review(self):
        fixture = minimal_fixture()
        fixture["review_thread_events"].append(
            {
                "delivery_id": 1001,
                "delivery_guid": "00000000-0000-4000-8000-000000000002",
                "delivered_at": "2026-01-01T07:00:00Z",
                "event": "pull_request_review_thread",
                "action": "unresolved",
                "repository": "example/workflow",
                "pr_number": 1,
                "review_id": 10,
                "finding_id": 100,
                "thread_id": "thread:100",
                "actor": "review-owner",
            }
        )
        fixture["review_findings"][0]["is_resolved"] = False
        timing = authoritative_report(fixture, minimal_decisions())["delivery"][
            "first_push_to_clean_review"
        ]
        self.assertEqual(timing["status"], "unavailable")
        self.assertIsNone(timing["median_hours"])
        self.assertFalse(timing["pilot_ready"])

    def test_absent_resolution_history_is_explicitly_unavailable(self):
        fixture = minimal_fixture()
        fixture["review_thread_event_source"] = {
            "kind": reporter.REVIEW_THREAD_EVENT_SOURCE,
            "complete": False,
            "coverage_start": None,
            "coverage_end": None,
            "unavailable_reason": "historical-review-thread-events-not-collected",
        }
        fixture["review_thread_events"] = []
        result = authoritative_report(fixture, minimal_decisions())
        timing = result["delivery"]["first_push_to_clean_review"]
        self.assertEqual(timing["status"], "unavailable")
        self.assertEqual(
            timing["reason"],
            "historical-review-thread-events-not-collected",
        )
        self.assertIsNone(timing["median_hours"])
        self.assertFalse(timing["pilot_ready"])
        self.assertEqual(result["reviews"]["valid_findings"], 1)
        self.assertEqual(result["reviews"]["current_unresolved_findings"], 0)

    def test_synthetic_finding_timestamp_and_untrusted_source_are_rejected(self):
        fixture = minimal_fixture()
        fixture["review_findings"][0]["resolved_at"] = "2026-01-01T06:00:00Z"
        self.assert_rejected(fixture=fixture, pattern="unknown fields: resolved_at")

        fixture = minimal_fixture()
        fixture["review_thread_event_source"]["kind"] = "fixture-authored"
        self.assert_rejected(
            fixture=fixture,
            pattern="review_thread_event_source.kind",
        )

    def test_review_thread_delivery_identity_coverage_and_state_fail_closed(self):
        fixture = minimal_fixture()
        fixture["review_thread_events"][0]["thread_id"] = "thread:other"
        self.assert_rejected(fixture=fixture, pattern="contradicts its finding")

        fixture = minimal_fixture()
        fixture["review_thread_event_source"]["coverage_start"] = (
            "2026-01-01T05:00:00Z"
        )
        self.assert_rejected(
            fixture=fixture,
            pattern="coverage starts after finding history",
        )

        fixture = minimal_fixture()
        fixture["review_findings"][0]["is_resolved"] = False
        self.assert_rejected(
            fixture=fixture,
            pattern="contradicts authoritative deliveries",
        )

        fixture = minimal_fixture()
        fixture["review_thread_event_source"] = {
            "kind": reporter.REVIEW_THREAD_EVENT_SOURCE,
            "complete": False,
            "coverage_start": None,
            "coverage_end": None,
            "unavailable_reason": "historical-review-thread-events-not-collected",
        }
        self.assert_rejected(
            fixture=fixture,
            pattern="unavailable review-thread event source cannot contain",
        )

    def test_genuine_depth_three_stack_requires_exception(self):
        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_stack_pr(
            fixture,
            decisions,
            number=2,
            parent_pr=1,
            depth=1,
            sha_character="e",
        )
        add_stack_pr(
            fixture,
            decisions,
            number=3,
            parent_pr=2,
            depth=2,
            sha_character="f",
        )
        add_stack_pr(
            fixture,
            decisions,
            number=4,
            parent_pr=3,
            depth=3,
            sha_character="1",
        )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="exception_reason",
        )
        decisions["pull_requests"][-1]["stack"]["exception_reason"] = (
            "The protocol layer requires one temporary third dependent layer."
        )
        authoritative_report(fixture, decisions)

    def test_stack_depth_parent_presence_and_authoritative_base_are_enforced(self):
        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_stack_pr(
            fixture,
            decisions,
            number=2,
            parent_pr=1,
            depth=2,
            sha_character="e",
        )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern=r"depth must equal parent depth \+ 1 \(1\)",
        )

        fixture = minimal_fixture()
        decisions = minimal_decisions()
        fixture["pull_requests"][0]["base_ref"] = "agent/missing"
        decisions["pull_requests"][0]["stack"].update(
            {"depth": 1, "parent_pr": 999}
        )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="has no authoritative PR",
        )

        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_stack_pr(
            fixture,
            decisions,
            number=2,
            parent_pr=1,
            depth=1,
            sha_character="e",
        )
        add_stack_pr(
            fixture,
            decisions,
            number=3,
            parent_pr=2,
            depth=2,
            sha_character="f",
        )
        decisions["pull_requests"] = [
            decision
            for decision in decisions["pull_requests"]
            if decision["pull_request"] != 2
        ]
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="has no parent decision",
        )

        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_stack_pr(
            fixture,
            decisions,
            number=2,
            parent_pr=1,
            depth=1,
            sha_character="e",
        )
        add_stack_pr(
            fixture,
            decisions,
            number=3,
            parent_pr=1,
            depth=1,
            sha_character="f",
        )
        decisions["pull_requests"][1]["stack"]["parent_pr"] = 3
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="parent contradicts the authoritative PR base",
        )

    def test_stack_root_cycle_and_maximum_depth_fail_closed(self):
        decisions = minimal_decisions()
        decisions["pull_requests"][0]["stack"]["depth"] = 1
        self.assert_rejected(
            fixture=minimal_fixture(),
            decisions=decisions,
            pattern="root must have depth 0",
        )

        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_stack_pr(
            fixture,
            decisions,
            number=2,
            parent_pr=1,
            depth=4,
            sha_character="e",
        )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="exceeds the supported maximum",
        )

        fixture = minimal_fixture()
        decisions = minimal_decisions()
        add_stack_pr(
            fixture,
            decisions,
            number=2,
            parent_pr=1,
            depth=1,
            sha_character="e",
        )
        fixture["pull_requests"][0]["base_ref"] = "agent/2"
        decisions["pull_requests"][0]["stack"].update(
            {"depth": 1, "parent_pr": 2}
        )
        self.assert_rejected(
            fixture=fixture,
            decisions=decisions,
            pattern="parent cycle",
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
            pattern="in_progress status requires null",
        )


class ArtifactLifecycleTests(unittest.TestCase):
    def assert_rejected(self, fixture, decisions, pattern):
        with self.assertRaisesRegex(reporter.PilotDataError, pattern):
            authoritative_report(fixture, decisions)

    def fixture_with_all_proof_kinds(self):
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
                    "id": "artifact:dependency-change",
                    "type": "dependency_changed",
                    "occurred_at": "2026-01-01T09:02:10Z",
                    "artifact_id": "contract",
                    "dependency_id": "dependency:api",
                },
                {
                    "id": "artifact:dependency-proof",
                    "type": "deletion_proof",
                    "occurred_at": "2026-01-01T09:02:20Z",
                    "artifact_id": "contract",
                    "trigger_event_id": "artifact:dependency-change",
                    "semantic_result": "fail",
                    "reason": "removal loses the decision boundary",
                    "restored_result": "pass",
                },
            ]
        )
        proof_times = (
            "2026-01-01T09:04:10Z",
            "2026-01-01T09:04:20Z",
            "2026-01-01T09:04:30Z",
        )
        proofs = [
            event
            for event in fixture["events"]
            if event["type"] == "deletion_proof"
        ]
        for proof, occurred_at in zip(proofs, proof_times):
            proof["occurred_at"] = occurred_at
        return fixture

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
            "contradicts current disposition",
        )

    def test_every_lifecycle_proof_must_restore_in_any_event_order(self):
        fixture = self.fixture_with_all_proof_kinds()
        proof_ids = [
            event["id"]
            for event in fixture["events"]
            if event["type"] == "deletion_proof"
        ]
        for proof_id in proof_ids:
            with self.subTest(proof_id=proof_id):
                mutated = copy.deepcopy(fixture)
                proof = next(
                    event for event in mutated["events"] if event["id"] == proof_id
                )
                proof["restored_result"] = "fail"
                self.assert_rejected(
                    mutated,
                    minimal_decisions(),
                    rf"deletion proof '{re.escape(proof_id)}' did not restore",
                )

        fixture["events"].reverse()
        report = authoritative_report(fixture, minimal_decisions())
        self.assertEqual(
            report["artifacts"]["current"][0]["current_disposition"],
            "Graduate",
        )

    def test_every_mixed_lifecycle_proof_field_and_kind_fails_independently(self):
        fixture = self.fixture_with_all_proof_kinds()
        proofs = [
            event
            for event in fixture["events"]
            if event["type"] == "deletion_proof"
        ]
        triggers = {
            event["id"]: event
            for event in fixture["events"]
            if event["type"] in reporter.DELETION_TRIGGER_TYPES
        }
        self.assertEqual(
            {
                triggers[proof["trigger_event_id"]]["type"]
                for proof in proofs
            },
            reporter.DELETION_TRIGGER_TYPES,
        )

        for proof_index, proof in enumerate(proofs):
            other = proofs[(proof_index + 1) % len(proofs)]
            trigger = triggers[proof["trigger_event_id"]]
            mutations = (
                (
                    "id",
                    lambda item, other=other: item.update({"id": other["id"]}),
                    "duplicate event",
                ),
                (
                    "kind",
                    lambda item: item.update({"type": "artifact_checkpoint"}),
                    "unknown fields",
                ),
                (
                    "occurred_at",
                    lambda item, trigger=trigger: item.update(
                        {"occurred_at": trigger["occurred_at"]}
                    ),
                    "must strictly follow its trigger",
                ),
                (
                    "artifact_id",
                    lambda item: item.update({"artifact_id": "missing"}),
                    "unknown artifact",
                ),
                (
                    "trigger_event_id",
                    lambda item, other=other: item.update(
                        {"trigger_event_id": other["trigger_event_id"]}
                    ),
                    "must have exactly one deletion proof",
                ),
                (
                    "semantic_result",
                    lambda item: item.update({"semantic_result": "pass"}),
                    "contradicts current disposition",
                ),
                (
                    "reason",
                    lambda item: item.update({"reason": "mixed result"}),
                    "mixed semantic reason",
                ),
                (
                    "restored_result",
                    lambda item: item.update({"restored_result": "fail"}),
                    "did not restore",
                ),
            )
            for field, mutate, pattern in mutations:
                with self.subTest(
                    trigger_kind=trigger["type"],
                    proof=proof["id"],
                    field=field,
                ):
                    changed = copy.deepcopy(fixture)
                    changed_proof = next(
                        event
                        for event in changed["events"]
                        if event["id"] == proof["id"]
                    )
                    mutate(changed_proof)
                    self.assert_rejected(
                        changed,
                        minimal_decisions(),
                        pattern,
                    )

            with self.subTest(
                trigger_kind=trigger["type"],
                proof=proof["id"],
                field="current-disposition-time",
            ):
                changed = copy.deepcopy(fixture)
                changed_proof = next(
                    event
                    for event in changed["events"]
                    if event["id"] == proof["id"]
                )
                changed_proof["occurred_at"] = (
                    minimal_decisions()["artifacts"][0]["history"][-1][
                        "recorded_at"
                    ]
                )
                self.assert_rejected(
                    changed,
                    minimal_decisions(),
                    "current disposition must strictly follow every deletion proof",
                )

    def test_committed_artifact_proofs_execute_removal_and_restoration(self):
        fixture = reporter.load_json(BASELINE)
        decisions = reporter.load_json(DECISIONS)
        paths = [
            ROOT / profile["path"]
            for profile in reporter.EXECUTABLE_DELETION_PROOFS.values()
        ]
        before = {path: path.read_bytes() for path in paths}
        results = reporter.validate_executable_deletion_proofs(
            ROOT,
            BASELINE,
            DECISIONS,
            BASELINE_EXPECTED,
            fixture,
            decisions,
        )
        self.assertEqual(
            results,
            {
                artifact_id: {
                    "removal": "fail",
                    "reason": reporter.DELETION_PROOF_REASON,
                    "restoration": "pass",
                }
                for artifact_id in reporter.EXECUTABLE_DELETION_PROOFS
            },
        )
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_lifecycle_launcher_isolated_from_sitecustomize_and_closed(self):
        from scripts.workflow_pilot import isolated_launcher

        self.assertEqual(
            isolated_launcher.LIFECYCLE_CHECKS,
            {"workflow-pilot-reporter", "workflow-pilot-tests"},
        )
        with tempfile.TemporaryDirectory(
            prefix="workflow-pilot-lifecycle-launcher-",
            dir=TEST_ARTIFACTS,
        ) as temporary:
            sandbox = Path(temporary) / "sandbox"
            for relative in {
                *reporter.DELETION_PROOF_SUPPORT_PATHS,
                *(
                    profile["path"]
                    for profile in reporter.EXECUTABLE_DELETION_PROOFS.values()
                ),
            }:
                target = sandbox / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            exit_hook = "import os\nos._exit(0)\n"
            (sandbox / "sitecustomize.py").write_text(
                exit_hook,
                encoding="ascii",
            )
            user_site = (
                Path(temporary)
                / "user"
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            )
            user_site.mkdir(parents=True)
            (user_site / "sitecustomize.py").write_text(
                exit_hook,
                encoding="ascii",
            )
            for source, environment in (
                ("repository", {"PYTHONPATH": str(sandbox)}),
                (
                    "user",
                    {
                        "HOME": str(Path(temporary)),
                        "PYTHONUSERBASE": str(Path(temporary) / "user"),
                        "PYTHONPATH": str(user_site),
                    },
                ),
            ):
                with self.subTest(source=source):
                    completed = subprocess.run(
                        ["/usr/bin/python3", "-c", "raise SystemExit(9)"],
                        env={"PATH": "/usr/bin:/bin", **environment},
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0)

            with mock.patch.dict(
                os.environ,
                {
                    "PYTHONPATH": str(sandbox),
                    "PYTHONUSERBASE": str(Path(temporary) / "user"),
                },
                clear=False,
            ):
                completed = reporter._run_deletion_proof_check(
                    sandbox,
                    ROOT,
                    "workflow-pilot-reporter",
                )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())

            launcher = sandbox / reporter.ISOLATED_LAUNCHER_PATH
            common = [
                "/usr/bin/python3",
                "-I",
                str(launcher),
                "lifecycle-check",
                "--artifact-root",
                str(sandbox),
                "--authority-root",
                str(ROOT),
                "--check",
                "workflow-pilot-reporter",
            ]
            self.assertEqual(list(completed.args), common)
            self.assertEqual(common[:2], ["/usr/bin/python3", "-I"])
            self.assertNotIn("-c", common)
            self.assertNotIn("-m", common)
            self.assertNotIn("-E", common)
            for changed in (
                [*common[:3], "arbitrary", *common[4:]],
                [
                    *common[:5],
                    str(Path(temporary)),
                    *common[6:],
                ],
                [*common[:-1], "arbitrary"],
                [*common, "extra"],
            ):
                self.assertEqual(
                    subprocess.run(
                        changed,
                        check=False,
                        capture_output=True,
                    ).returncode,
                    2,
                )

    def test_empty_git_authority_cannot_validate_executable_proofs(self):
        fixture = reporter.load_json(BASELINE)
        decisions = reporter.load_json(DECISIONS)
        with tempfile.TemporaryDirectory(
            prefix="workflow-pilot-empty-authority-",
            dir=TEST_ARTIFACTS,
        ) as temporary:
            repository_root = Path(temporary)
            git_run(repository_root, "init", "-q", "-b", "master")
            git_run(
                repository_root,
                "remote",
                "add",
                "origin",
                "https://github.com/laqieer/fireemblem8-expansion.git",
            )
            hostile = {
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(
                    ROOT / ".git" / "objects"
                ),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "remote.origin.url",
                "GIT_CONFIG_VALUE_0": (
                    "https://github.com/laqieer/fireemblem8-expansion.git"
                ),
                "GIT_OBJECT_DIRECTORY": str(ROOT / ".git" / "objects"),
            }
            with mock.patch.dict(os.environ, hostile, clear=False):
                with self.assertRaisesRegex(
                    reporter.PilotDataError,
                    "does not exist in the repository",
                ):
                    reporter.validate_executable_deletion_proofs(
                        repository_root,
                        BASELINE,
                        DECISIONS,
                        BASELINE_EXPECTED,
                        fixture,
                        decisions,
                    )

    def test_fabricated_proof_and_fixture_commands_are_not_executable(self):
        fixture = reporter.load_json(BASELINE)
        decisions = reporter.load_json(DECISIONS)
        next(
            event
            for event in fixture["events"]
            if event["type"] == "deletion_proof"
        )["reason"] = "self-authored claim"
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "mixed semantic reason",
        ):
            authoritative_report(
                fixture,
                decisions,
            )

        fixture = reporter.load_json(BASELINE)
        decisions = reporter.load_json(DECISIONS)
        fixture["dependency_edges"][0]["source"] = "python3 -c arbitrary"
        decisions["artifacts"][0]["executable_consumer"] = "python3 -c arbitrary"
        authoritative_report(fixture, decisions)
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "consumer differs from its executable allowlist",
        ):
            reporter.validate_executable_deletion_proofs(
                ROOT,
                BASELINE,
                DECISIONS,
                BASELINE_EXPECTED,
                fixture,
                decisions,
            )

    def test_stale_executable_deletion_proof_is_rejected(self):
        with tempfile.TemporaryDirectory(
            prefix="workflow-pilot-stale-proof-",
            dir=TEST_ARTIFACTS,
        ) as temporary:
            repository_root = Path(temporary) / "authority"
            repository_root.mkdir()
            git_run(
                repository_root,
                "clone",
                "-q",
                "--no-hardlinks",
                str(ROOT),
                ".",
            )
            git_run(
                repository_root,
                "remote",
                "set-url",
                "origin",
                "https://github.com/laqieer/fireemblem8-expansion.git",
            )
            copy_paths = {
                *reporter.DELETION_PROOF_SUPPORT_PATHS,
                *(
                    profile["path"]
                    for profile in reporter.EXECUTABLE_DELETION_PROOFS.values()
                ),
            }
            for relative in copy_paths:
                source = ROOT / relative
                target = repository_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            expected_path = repository_root / reporter.BASELINE_EXPECTED_PATH
            expected = reporter.load_json(expected_path)
            expected["paths"]["builds.runs"] = -1
            expected_path.write_bytes(reporter.normalized_json(expected))

            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "stale executable deletion-proof baseline does not pass",
            ):
                reporter.validate_executable_deletion_proofs(
                    repository_root,
                    repository_root / reporter.BASELINE_FIXTURE_PATH,
                    repository_root / reporter.DECISION_RECORD_PATH,
                    expected_path,
                    reporter.load_json(
                        repository_root / reporter.BASELINE_FIXTURE_PATH
                    ),
                    reporter.load_json(
                        repository_root / reporter.DECISION_RECORD_PATH
                    ),
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
        report = authoritative_report(fixture, decisions)
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
        report = authoritative_report(minimal_fixture(), decisions)
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
                    "reason": "removal loses the decision boundary",
                    "restored_result": "pass",
                },
            ]
        )
        result = authoritative_report(fixture, minimal_decisions())
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
