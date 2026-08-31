import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import agent_handoff, reporter
from scripts.workflow_pilot.tests import test_reporter


ROOT = Path(__file__).resolve().parents[3]
TEST_ARTIFACTS = ROOT / "build" / "test-artifacts"
TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)
AUTHORITY_OWNERS = {}


def git(repository_root, *arguments):
    return subprocess.run(
        reporter.git_command(repository_root, *arguments),
        cwd=repository_root,
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def git_with_input(repository_root, arguments, value, environment=None):
    git_environment = reporter.git_environment(offline=True)
    if environment is not None:
        git_environment.update(environment)
    return subprocess.run(
        reporter.git_command(repository_root, *arguments),
        cwd=repository_root,
        env=git_environment,
        input=value,
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()


def owner_write_blob_ref(owner_root, reference, payload):
    object_id = git_with_input(
        owner_root,
        ("hash-object", "-w", "--stdin"),
        agent_handoff.normalized_json(payload),
    )
    git(owner_root, "push", "-q", "origin", f"{object_id}:{reference}")
    return object_id


def owner_create_authority_commit(owner_root, record, parent=None):
    blob = git_with_input(
        owner_root,
        ("hash-object", "-w", "--stdin"),
        agent_handoff.normalized_json(record),
    )
    tree = git_with_input(
        owner_root,
        ("mktree",),
        f"100644 blob {blob}\tauthority.json\n".encode("ascii"),
    )
    arguments = ["commit-tree", tree]
    if parent is not None:
        arguments.extend(("-p", parent))
    return git_with_input(
        owner_root,
        tuple(arguments),
        b"workflow-pilot handoff authority\n",
        {
            "GIT_AUTHOR_NAME": "Authority Owner",
            "GIT_AUTHOR_EMAIL": "owner@example.invalid",
            "GIT_COMMITTER_NAME": "Authority Owner",
            "GIT_COMMITTER_EMAIL": "owner@example.invalid",
            "GIT_AUTHOR_DATE": "2026-08-31T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-08-31T00:00:00Z",
        },
    )


def set_history_authority(
    repository_root,
    sequence,
    head_seal,
    *,
    issue=178,
    pull_request=200,
):
    owner_root = AUTHORITY_OWNERS[str(repository_root)]
    reference = agent_handoff.history_authority_ref(issue, pull_request)
    if sequence == 0:
        plan = agent_handoff.plan_history_authority(
            repository_root,
            "example/workflow",
            issue,
            pull_request,
            operation="bootstrap",
        )
        if plan != agent_handoff.plan_history_authority(
            repository_root,
            "example/workflow",
            issue,
            pull_request,
            operation="bootstrap",
        ):
            raise AssertionError("bootstrap plan is not deterministic")
        parent = None
    else:
        current = agent_handoff.read_history_authority(
            repository_root,
            "example/workflow",
            issue,
            pull_request,
        )
        plan = agent_handoff.plan_history_authority(
            repository_root,
            "example/workflow",
            issue,
            pull_request,
            operation="advance",
            expected_object_id=current["object_id"],
            expected_sequence=current["sequence"],
            new_head_seal=head_seal,
        )
        if plan != agent_handoff.plan_history_authority(
            repository_root,
            "example/workflow",
            issue,
            pull_request,
            operation="advance",
            expected_object_id=current["object_id"],
            expected_sequence=current["sequence"],
            new_head_seal=head_seal,
        ):
            raise AssertionError("advance plan is not deterministic")
        parent = current["object_id"]
    planned_sequence = plan["record"]["sequence"]
    if planned_sequence != sequence:
        raise AssertionError("authority test sequence mismatch")
    object_id = owner_create_authority_commit(
        owner_root,
        plan["record"],
        parent,
    )
    lease = plan["expected_remote_object_id"] or ("0" * 40)
    git(
        owner_root,
        "push",
        "-q",
        f"--force-with-lease={reference}:{lease}",
        "origin",
        f"{object_id}:{reference}",
    )
    return agent_handoff.read_history_authority(
        repository_root,
        "example/workflow",
        issue,
        pull_request,
    )


@contextmanager
def handoff_repository():
    with tempfile.TemporaryDirectory(
        prefix="agent-handoff-",
        dir=TEST_ARTIFACTS,
    ) as temporary:
        test_root = Path(temporary)
        remote_root = test_root / "authority.git"
        owner_root = test_root / "owner"
        repository_root = test_root / "implementation"
        remote_root.mkdir()
        owner_root.mkdir()
        repository_root.mkdir()
        git(remote_root, "init", "-q", "--bare")
        git(owner_root, "init", "-q", "-b", "master")
        git(owner_root, "config", "user.name", "Authority Owner")
        git(owner_root, "config", "user.email", "owner@example.invalid")
        git(owner_root, "remote", "add", "origin", str(remote_root))
        owner_write_blob_ref(
            owner_root,
            agent_handoff.REPOSITORY_IDENTITY_REF,
            {
                "schema_version": 1,
                "repository": "example/workflow",
            },
        )
        git(repository_root, "init", "-q", "-b", "master")
        git(repository_root, "config", "user.name", "Handoff Test")
        git(repository_root, "config", "user.email", "handoff@example.invalid")
        git(
            repository_root,
            "remote",
            "add",
            "origin",
            str(remote_root),
        )
        AUTHORITY_OWNERS[str(repository_root)] = owner_root
        seed = repository_root / "README.md"
        seed.write_text("base\n", encoding="utf-8")
        git(repository_root, "add", "README.md")
        git(repository_root, "commit", "-q", "-m", "test: base")
        base_sha = git(repository_root, "rev-parse", "HEAD")

        seed.write_text("base\nparent\n", encoding="utf-8")
        git(repository_root, "add", "README.md")
        git(repository_root, "commit", "-q", "-m", "test: assigned parent")
        parent_sha = git(repository_root, "rev-parse", "HEAD")
        git(repository_root, "switch", "-q", "-c", "agent/issue-178")

        implementation = repository_root / "scripts" / "workflow_pilot"
        implementation.mkdir(parents=True)
        (implementation / "change.py").write_text(
            "HANDOFF = True\nEVIDENCE = 'focused'\n",
            encoding="utf-8",
        )
        git(repository_root, "add", "scripts/workflow_pilot/change.py")
        git(
            repository_root,
            "commit",
            "-q",
            "-m",
            "feat(workflow): test bounded handoff\n\n"
            + agent_handoff.COPILOT_TRAILER,
        )
        result_sha = git(repository_root, "rev-parse", "HEAD")
        set_history_authority(repository_root, 0, None)
        try:
            yield repository_root, base_sha, parent_sha, result_sha
        finally:
            del AUTHORITY_OWNERS[str(repository_root)]


def timestamped_states(receipt=None):
    if receipt is not None:
        started = datetime.fromisoformat(
            receipt["started_at"].replace("Z", "+00:00")
        )
        completed = datetime.fromisoformat(
            receipt["completed_at"].replace("Z", "+00:00")
        )
        return [
            {
                "state": "assignment_sent",
                "at": (started - timedelta(seconds=2))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            {
                "state": "assignment_received",
                "at": (started - timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            {"state": "progressing", "at": receipt["started_at"]},
            {
                "state": "committed",
                "at": (completed + timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            {
                "state": "handed_off",
                "at": (completed + timedelta(seconds=2))
                .isoformat()
                .replace("+00:00", "Z"),
            },
        ]
    return [
        {"state": "assignment_sent", "at": "2026-01-01T01:00:00Z"},
        {"state": "assignment_received", "at": "2026-01-01T01:01:00Z"},
        {"state": "progressing", "at": "2026-01-01T01:02:00Z"},
        {"state": "committed", "at": "2026-01-01T01:04:00Z"},
        {"state": "handed_off", "at": "2026-01-01T01:05:00Z"},
    ]


def evidence(status="passed", completed_at="2026-01-01T01:03:00Z"):
    exit_code = 0 if status == "passed" else None
    return [
        {
            "id": "acceptance",
            "kind": "acceptance",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "Acceptance criteria were exercised.",
        },
        {
            "id": "focused-check",
            "kind": "check",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "The focused module passed.",
        },
        {
            "id": "budget-lines",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "Git provided the changed-line count.",
        },
        {
            "id": "budget-rom",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "No ROM-producing path changed.",
        },
        {
            "id": "budget-ram",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "No RAM-owning path changed.",
        },
        {
            "id": "budget-protocol",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "The one admitted protocol change is versioned.",
        },
    ]


def delivery_graph(
    *,
    child_issue=178,
    child_status="pending",
    child_handoff_id="issue-178-round-1",
    child_candidate_sha="b" * 40,
    parent_master_sha="a" * 40,
    merge_status="done",
    build_status="in_progress",
    remote_status="pending",
    watcher_process="running",
    watcher_status="in_progress",
    watcher_conclusion=None,
    recovery_status="not_required",
):
    def task(
        task_id,
        issue,
        pull_request,
        phase,
        status,
        *,
        handoff_id=None,
        candidate_sha=None,
    ):
        return {
            "id": task_id,
            "issue": issue,
            "pull_request": pull_request,
            "phase": phase,
            "status": status,
            "status_reason": (
                (
                    "workflow_failed"
                    if phase == "post_merge_build"
                    else (
                        "owner_interrupted"
                        if phase == "implementation"
                        else "dependency"
                    )
                )
                if status == "blocked"
                else None
            ),
            "handoff_id": handoff_id,
            "candidate_sha": candidate_sha,
        }

    return {
        "relationships": [
            {
                "child_issue": child_issue,
                "parent_issue": 176,
                "handoff_id": child_handoff_id,
                "type": "code_contract",
            }
        ],
        "tasks": [
            task("parent-merge", 176, 183, "merge", merge_status),
            task(
                "parent-post-merge-build",
                176,
                183,
                "post_merge_build",
                build_status,
                candidate_sha=parent_master_sha,
            ),
            task("parent-completion", 176, 183, "completion", "pending"),
            task("parent-closure", 176, 183, "closure", "pending"),
            task(
                "parent-remote",
                176,
                183,
                "remote_completion",
                remote_status,
            ),
            task(
                "parent-recovery",
                176,
                183,
                "fix_forward_revert",
                recovery_status,
            ),
            task(
                "child-implement",
                child_issue,
                200,
                "implementation",
                child_status,
                handoff_id=child_handoff_id,
                candidate_sha=child_candidate_sha,
            ),
        ],
        "dependencies": [
            {
                "task": "child-implement",
                "depends_on": "parent-merge",
                "type": "code_contract",
            },
            {
                "task": "parent-completion",
                "depends_on": "parent-post-merge-build",
                "type": "delivery_gate",
            },
            {
                "task": "parent-closure",
                "depends_on": "parent-post-merge-build",
                "type": "delivery_gate",
            },
            {
                "task": "parent-remote",
                "depends_on": "parent-post-merge-build",
                "type": "delivery_gate",
            },
        ],
        "workflow_runs": (
            []
            if build_status == "pending" and watcher_process is None
            else [
                {
                    "id": 9002,
                    "run_task": "parent-post-merge-build",
                    "head_sha": parent_master_sha,
                    "status": watcher_status,
                    "conclusion": watcher_conclusion,
                    "source": "github-actions-api",
                }
            ]
        ),
        "watchers": (
            []
            if watcher_process is None
            else [
                {
                    "id": "parent-master-watcher",
                    "run_id": 9002,
                    "process_state": watcher_process,
                }
            ]
        ),
    }


def handoff_document(repository_root, parent_sha, result_sha):
    receipt = agent_handoff.execute_allowed_check(
        receipt_id="receipt-focused-module",
        check_id="focused-module",
        contract="git-diff-check",
        repository_root=repository_root,
        parent_sha=parent_sha,
        candidate_sha=result_sha,
    )
    return {
        "schema_version": 1,
        "repository": "example/workflow",
        "prior_handoffs": [],
        "history_authority": agent_handoff.read_history_authority(
            repository_root,
            "example/workflow",
            178,
            200,
        ),
        "delivery_graph": delivery_graph(
            child_status="done",
            child_candidate_sha=result_sha,
            parent_master_sha=parent_sha,
        ),
        "coordinators": [
            {
                "id": "coordinator-1",
                "availability": {
                    "mode": "always_on",
                    "autostop_enabled": False,
                    "stop_on_disconnect": False,
                    "evaluation_source": "coordinator-runtime",
                    "evaluated_at": "2026-08-31T03:00:00Z",
                    "unattended_until": "2026-09-01T03:00:00Z",
                    "plan": None,
                },
            }
        ],
        "handoffs": [
            {
                "id": "issue-178-round-1",
                "issue": 178,
                "pull_request": 200,
                "owner_id": "owner-1",
                "owner_database_id": None,
                "replaces_handoff_id": None,
                "assigned_parent_sha": parent_sha,
                "expected_branch": "agent/issue-178",
                "allowed_worktree": str(repository_root),
                "allowed_scope": ["scripts/workflow_pilot/"],
                "finding_ids": ["F-178-1"],
                "acceptance_criteria": [
                    {
                        "id": "AC-178-1",
                        "text": "Only an exact clean descendant enters trusted push.",
                        "evidence_ids": [
                            "acceptance",
                            "budget-lines",
                            "budget-rom",
                            "budget-ram",
                            "budget-protocol",
                        ],
                    }
                ],
                "required_checks": [
                    {
                        "id": "focused-module",
                        "contract": "git-diff-check",
                        "receipt_id": "receipt-focused-module",
                        "evidence_id": "focused-check",
                    }
                ],
                "budgets": {
                    "changed_lines": 20,
                    "rom_bytes": 0,
                    "ram_bytes": 0,
                    "protocol_changes": 1,
                },
                "prohibited_remote_actions": sorted(
                    agent_handoff.PROHIBITED_REMOTE_ACTIONS
                ),
                "max_lifetime_seconds": 3600,
                "max_peak_rss_bytes": 536870912,
                "coordination_turns": 2,
                "peak_rss_bytes": 134217728,
                "states": timestamped_states(receipt),
                "evidence": evidence(completed_at=receipt["completed_at"]),
                "check_receipts": [receipt],
                "result": {
                    "sha": result_sha,
                    "budget_usage": {
                        "rom_bytes": 0,
                        "ram_bytes": 0,
                        "protocol_changes": 1,
                    },
                },
                "interruption": None,
            }
        ],
        "workflow_runs": [],
        "watchers": [],
        "remote_actions": [],
    }


def add_run(document, result_sha, conclusion="success", process_result="success"):
    document["workflow_runs"] = [
        {
            "id": 9001,
            "handoff_id": document["handoffs"][0]["id"],
            "head_sha": result_sha,
            "status": "completed",
            "conclusion": conclusion,
            "observed_at": "2026-01-01T01:20:00Z",
            "source": "github-actions-api",
        }
    ]
    document["watchers"] = [
        {
            "id": "watcher-9001",
            "coordinator_id": "coordinator-1",
            "run_id": 9001,
            "head_sha": result_sha,
            "kind": "direct_shell",
            "started_at": "2026-01-01T01:10:00Z",
            "ended_at": "2026-01-01T01:15:00Z",
            "process_result": process_result,
        }
    ]


class DeliveryDependencyGraphTests(unittest.TestCase):
    def test_parent_merge_unblocks_child_before_parent_remote_completion(self):
        report = agent_handoff.evaluate_delivery_graph(delivery_graph())

        self.assertEqual(report["rejection_codes"], [])
        self.assertIn("child-implement", report["ready_tasks"])
        self.assertEqual(
            report["relationships"][0],
            {
                "child_issue": 178,
                "parent_issue": 176,
                "handoff_id": "issue-178-round-1",
                "type": "code_contract",
                "implementation_task": {
                    "id": "child-implement",
                    "issue": 178,
                    "pull_request": 200,
                    "status": "pending",
                    "status_reason": None,
                    "handoff_id": "issue-178-round-1",
                    "candidate_sha": "b" * 40,
                },
                "required_edge": {
                    "task": "child-implement",
                    "depends_on": "parent-merge",
                    "type": "code_contract",
                },
                "parent_merge_status": "done",
                "implementation_ready": True,
            },
        )
        blocked = {
            item["id"]: item["blocked_by"] for item in report["blocked_tasks"]
        }
        self.assertEqual(
            blocked["parent-remote"],
            ["parent-post-merge-build"],
        )

    def test_pending_parent_merge_blocks_child_implementation(self):
        graph = delivery_graph(
            merge_status="pending",
            build_status="pending",
            watcher_process=None,
        )
        report = agent_handoff.evaluate_delivery_graph(graph)

        self.assertNotIn("child-implement", report["ready_tasks"])
        self.assertIn(
            {
                "id": "child-implement",
                "blocked_by": ["parent-merge"],
            },
            report["blocked_tasks"],
        )
        self.assertFalse(report["relationships"][0]["implementation_ready"])

    def test_healthy_pending_master_watcher_is_not_a_todo_dependency(self):
        report = agent_handoff.evaluate_delivery_graph(delivery_graph())

        self.assertIn("child-implement", report["ready_tasks"])
        self.assertEqual(
            report["watchers"],
            [
                {
                    "id": "parent-master-watcher",
                    "run_id": 9002,
                    "run_task": "parent-post-merge-build",
                    "process_state": "running",
                    "head_sha": "a" * 40,
                    "authoritative_status": "in_progress",
                    "conclusion": None,
                    "orthogonal_to_todos": True,
                }
            ],
        )

        invalid = delivery_graph()
        invalid["dependencies"].append(
            {
                "task": "child-implement",
                "depends_on": "parent-master-watcher",
                "type": "delivery_gate",
            }
        )
        invalid_report = agent_handoff.evaluate_delivery_graph(invalid)
        self.assertIn(
            "watcher-todo-dependency",
            invalid_report["rejection_codes"],
        )

    def test_terminal_failed_master_requires_recovery_without_rewriting_history(self):
        pending_report = agent_handoff.evaluate_delivery_graph(delivery_graph())
        failed = delivery_graph(
            build_status="blocked",
            watcher_process="error",
            watcher_status="completed",
            watcher_conclusion="failure",
            recovery_status="in_progress",
        )
        failed_report = agent_handoff.evaluate_delivery_graph(failed)

        self.assertIn("child-implement", pending_report["ready_tasks"])
        self.assertIn("child-implement", failed_report["ready_tasks"])
        self.assertEqual(
            failed_report["master_recovery"],
            [
                {
                    "parent_issue": 176,
                    "required": True,
                    "task": "parent-recovery",
                    "status": "in_progress",
                }
            ],
        )
        self.assertNotIn(
            "missing-master-recovery",
            failed_report["rejection_codes"],
        )

    def test_code_contract_edge_to_parent_remote_rejects_and_names_merge_edge(self):
        graph = delivery_graph()
        graph["dependencies"][0] = {
            "task": "child-implement",
            "depends_on": "parent-remote",
            "type": "code_contract",
        }
        report = agent_handoff.evaluate_delivery_graph(graph)

        self.assertIn(
            "missing-required-code-contract-edge",
            report["rejection_codes"],
        )
        self.assertIn(
            "wrong-code-contract-edge",
            report["rejection_codes"],
        )
        self.assertIn(
            {
                "task": "child-implement",
                "depends_on": "parent-merge",
                "type": "code_contract",
            },
            report["required_edges"],
        )
        self.assertNotIn("child-implement", report["ready_tasks"])

    def test_parent_completion_closure_and_remote_keep_post_merge_gate(self):
        report = agent_handoff.evaluate_delivery_graph(delivery_graph())
        required = {
            (item["task"], item["depends_on"], item["type"])
            for item in report["required_edges"]
        }
        for task_id in (
            "parent-completion",
            "parent-closure",
            "parent-remote",
        ):
            with self.subTest(task=task_id):
                self.assertIn(
                    (
                        task_id,
                        "parent-post-merge-build",
                        "delivery_gate",
                    ),
                    required,
                )

        invalid = delivery_graph()
        invalid["dependencies"] = [
            item
            for item in invalid["dependencies"]
            if item["task"] != "parent-closure"
        ]
        invalid_report = agent_handoff.evaluate_delivery_graph(invalid)
        self.assertIn(
            "missing-parent-post-merge-gate",
            invalid_report["rejection_codes"],
        )


class ExactHandoffTests(unittest.TestCase):
    def test_exact_clean_strict_descendant_is_accepted(self):
        with handoff_repository() as (root, _base, parent, result):
            report = agent_handoff.validate_document(
                handoff_document(root, parent, result),
                root,
            )

        self.assertTrue(report["summary"]["trusted_push_eligible"])
        self.assertFalse(report["summary"]["delivery_eligible"])
        self.assertEqual(report["summary"]["rejection_codes"], [])
        self.assertEqual(report["handoffs"][0]["outcome"], "accepted")
        self.assertEqual(report["handoffs"][0]["changed_lines"], 2)
        self.assertRegex(report["input_seal"], r"^[0-9a-f]{64}$")
        self.assertRegex(report["result_seal"], r"^[0-9a-f]{64}$")

    def test_unmerged_parent_contract_blocks_full_handoff(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document["delivery_graph"] = delivery_graph(
                merge_status="pending",
                build_status="pending",
                watcher_process=None,
                child_status="done",
                child_candidate_sha=result,
                parent_master_sha=parent,
            )
            report = agent_handoff.validate_document(document, root)

        self.assertFalse(report["summary"]["trusted_push_eligible"])
        self.assertIn(
            "code-contract-not-merged",
            report["handoffs"][0]["rejection_codes"],
        )
        self.assertIn(
            "task-status-dependency-mismatch",
            report["handoffs"][0]["rejection_codes"],
        )

    def test_handoff_issue_relationship_and_task_status_are_bound(self):
        with handoff_repository() as (root, _base, parent, result):
            wrong_issue = handoff_document(root, parent, result)
            wrong_issue["handoffs"][0]["issue"] = 999
            set_history_authority(
                root,
                0,
                None,
                issue=999,
                pull_request=200,
            )
            wrong_issue[
                "history_authority"
            ] = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                999,
                200,
            )
            report = agent_handoff.validate_document(wrong_issue, root)
            self.assertIn(
                "missing-handoff-code-contract",
                report["handoffs"][0]["rejection_codes"],
            )

            blocked = handoff_document(root, parent, result)
            child_task = next(
                task
                for task in blocked["delivery_graph"]["tasks"]
                if task["id"] == "child-implement"
            )
            child_task["status"] = "blocked"
            child_task["status_reason"] = "owner_interrupted"
            report = agent_handoff.validate_document(blocked, root)
            self.assertIn(
                "handoff-task-status-mismatch",
                report["handoffs"][0]["rejection_codes"],
            )

            duplicate_relation = handoff_document(root, parent, result)
            duplicate_relation["delivery_graph"]["relationships"].append(
                copy.deepcopy(
                    duplicate_relation["delivery_graph"]["relationships"][0]
                )
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "contains duplicates",
            ):
                agent_handoff.validate_document(duplicate_relation, root)

            duplicate_task = handoff_document(root, parent, result)
            task_copy = copy.deepcopy(
                next(
                    task
                    for task in duplicate_task["delivery_graph"]["tasks"]
                    if task["id"] == "child-implement"
                )
            )
            task_copy["id"] = "child-implement-duplicate"
            duplicate_task["delivery_graph"]["tasks"].append(task_copy)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "duplicate tasks",
            ):
                agent_handoff.validate_document(duplicate_task, root)

            missing_relation = handoff_document(root, parent, result)
            missing_relation["delivery_graph"]["relationships"] = []
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must name a code/contract dependency",
            ):
                agent_handoff.validate_document(missing_relation, root)

            missing_task = handoff_document(root, parent, result)
            missing_task["delivery_graph"]["tasks"] = [
                task
                for task in missing_task["delivery_graph"]["tasks"]
                if task["phase"] != "implementation"
            ]
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "unknown delivery task",
            ):
                agent_handoff.validate_document(missing_task, root)

    def test_parent_post_merge_run_is_sha_status_and_conclusion_bound(self):
        with handoff_repository() as (root, _base, parent, result):
            wrong_sha = handoff_document(root, parent, result)
            wrong_sha["delivery_graph"]["workflow_runs"][0][
                "head_sha"
            ] = result
            report = agent_handoff.validate_document(wrong_sha, root)
            self.assertIn(
                "watcher-run-mismatch",
                report["handoffs"][0]["rejection_codes"],
            )

            failed = handoff_document(root, parent, result)
            post_build = next(
                task
                for task in failed["delivery_graph"]["tasks"]
                if task["id"] == "parent-post-merge-build"
            )
            recovery = next(
                task
                for task in failed["delivery_graph"]["tasks"]
                if task["id"] == "parent-recovery"
            )
            post_build["status"] = "blocked"
            post_build["status_reason"] = "workflow_failed"
            recovery["status"] = "in_progress"
            run = failed["delivery_graph"]["workflow_runs"][0]
            run["status"] = "completed"
            run["conclusion"] = "failure"
            failed["delivery_graph"]["watchers"][0]["process_state"] = "error"
            report = agent_handoff.validate_document(failed, root)
            self.assertTrue(report["summary"]["trusted_push_eligible"])
            self.assertFalse(report["summary"]["delivery_eligible"])
            self.assertTrue(
                report["delivery_graph"]["relationships"][0][
                    "implementation_ready"
                ]
            )
            self.assertFalse(
                report["delivery_graph"]["parent_delivery"][0][
                    "delivery_eligible"
                ]
            )

            premature = handoff_document(root, parent, result)
            next(
                task
                for task in premature["delivery_graph"]["tasks"]
                if task["id"] == "parent-closure"
            )["status"] = "done"
            report = agent_handoff.validate_document(premature, root)
            self.assertIn(
                "task-status-dependency-mismatch",
                report["handoffs"][0]["rejection_codes"],
            )
            self.assertFalse(report["summary"]["delivery_eligible"])

    def test_cli_emits_canonical_result_and_fails_closed(self):
        with (
            handoff_repository() as (root, _base, parent, result),
            tempfile.TemporaryDirectory(
                prefix="agent-handoff-cli-",
                dir=TEST_ARTIFACTS,
            ) as fixture_directory,
        ):
            fixture_path = Path(fixture_directory) / "handoff.json"
            document = handoff_document(root, parent, result)
            fixture_path.write_text(json.dumps(document), encoding="utf-8")
            command = [
                sys.executable,
                "-m",
                "scripts.workflow_pilot.agent_handoff",
                "--fixture",
                str(fixture_path),
                "--worktree",
                str(root),
            ]
            accepted = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())
            accepted_result = json.loads(accepted.stdout)
            self.assertTrue(accepted_result["summary"]["trusted_push_eligible"])
            self.assertEqual(
                accepted.stdout,
                agent_handoff.normalized_json(accepted_result),
            )

            document["handoffs"][0]["result"]["sha"] = parent
            fixture_path.write_text(json.dumps(document), encoding="utf-8")
            rejected = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn(
                "stale-result",
                json.loads(rejected.stdout)["summary"]["rejection_codes"],
            )

    def test_stale_wrong_parent_and_unrelated_branch_reject(self):
        with handoff_repository() as (root, base, parent, result):
            cases = {}
            stale = handoff_document(root, parent, result)
            stale["handoffs"][0]["result"]["sha"] = parent
            cases["stale"] = (stale, "stale-result")

            wrong_parent = handoff_document(root, parent, result)
            wrong_parent["handoffs"][0]["assigned_parent_sha"] = base
            cases["wrong-parent"] = (wrong_parent, "wrong-parent")

            unrelated = handoff_document(root, parent, result)
            unrelated["handoffs"][0]["expected_branch"] = "agent/unrelated"
            cases["unrelated"] = (unrelated, "unrelated-branch")

            for name, (document, code) in cases.items():
                with self.subTest(name=name):
                    report = agent_handoff.validate_document(document, root)
                    self.assertFalse(report["summary"]["trusted_push_eligible"])
                    self.assertIn(code, report["summary"]["rejection_codes"])

    def test_dirty_conflicting_missing_and_incomplete_results_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            dirty = root / "scripts" / "workflow_pilot" / "dirty.py"
            dirty.write_text("DIRTY = True\n", encoding="utf-8")
            report = agent_handoff.validate_document(
                handoff_document(root, parent, result),
                root,
            )
            self.assertIn("dirty-worktree", report["summary"]["rejection_codes"])
            dirty.unlink()

            incomplete = handoff_document(root, parent, result)
            incomplete["handoffs"][0]["states"] = [
                incomplete["handoffs"][0]["states"][0],
                incomplete["handoffs"][0]["states"][2],
            ]
            incomplete["handoffs"][0]["result"] = None
            report = agent_handoff.validate_document(incomplete, root)
            self.assertIn(
                "incomplete-lifecycle",
                report["summary"]["rejection_codes"],
            )

            missing_evidence = handoff_document(root, parent, result)
            missing_evidence["handoffs"][0]["evidence"] = []
            report = agent_handoff.validate_document(missing_evidence, root)
            self.assertIn("missing-evidence", report["summary"]["rejection_codes"])
            self.assertIn("incomplete-check", report["summary"]["rejection_codes"])

            missing_commit = handoff_document(root, parent, result)
            missing_commit["handoffs"][0]["result"]["sha"] = "f" * 40
            report = agent_handoff.validate_document(missing_commit, root)
            self.assertIn("missing-commit", report["summary"]["rejection_codes"])

    def test_assignment_states_are_distinct_and_not_inferred(self):
        with handoff_repository() as (root, _base, parent, result):
            for state_name in (
                "assignment_received",
                "progressing",
                "committed",
                "handed_off",
            ):
                with self.subTest(state=state_name):
                    document = handoff_document(root, parent, result)
                    document["handoffs"][0]["states"] = [
                        state
                        for state in document["handoffs"][0]["states"]
                        if state["state"] != state_name
                    ]
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(
                        "incomplete-lifecycle",
                        report["summary"]["rejection_codes"],
                    )

            document = handoff_document(root, parent, result)
            document["handoffs"][0]["states"] = document["handoffs"][0][
                "states"
            ][1:]
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must start with assignment_sent",
            ):
                agent_handoff.validate_document(document, root)

    def test_in_progress_assignment_prefixes_are_valid_but_never_eligible(self):
        with handoff_repository() as (root, _base, parent, result):
            for prefix_length in (1, 2, 3):
                with self.subTest(prefix_length=prefix_length):
                    document = handoff_document(root, parent, result)
                    handoff = document["handoffs"][0]
                    handoff["states"] = handoff["states"][:prefix_length]
                    handoff["result"] = None
                    handoff["evidence"] = []
                    handoff["required_checks"][0]["receipt_id"] = None
                    handoff["check_receipts"] = []
                    child_task = next(
                        task
                        for task in document["delivery_graph"]["tasks"]
                        if task["phase"] == "implementation"
                    )
                    child_task["candidate_sha"] = parent
                    child_task["status"] = (
                        "in_progress" if prefix_length == 3 else "pending"
                    )
                    report = agent_handoff.validate_document(document, root)
                    self.assertEqual(
                        report["handoffs"][0]["outcome"],
                        "in_progress",
                    )
                    self.assertEqual(
                        report["handoffs"][0]["rejection_codes"],
                        [],
                    )
                    self.assertFalse(
                        report["summary"]["trusted_push_eligible"]
                    )
                    self.assertFalse(report["summary"]["delivery_eligible"])

    def test_conflicting_worktree_rejects(self):
        with handoff_repository() as (root, _base, _parent, result):
            change = root / "scripts" / "workflow_pilot" / "change.py"
            git(root, "switch", "-q", "-c", "conflict-side")
            change.write_text("SIDE = True\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: side\n\n" + agent_handoff.COPILOT_TRAILER,
            )

            git(root, "switch", "-q", "agent/issue-178")
            change.write_text("MAIN = True\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: main\n\n" + agent_handoff.COPILOT_TRAILER,
            )
            main_result = git(root, "rev-parse", "HEAD")
            merge = subprocess.run(
                reporter.git_command(root, "merge", "--no-edit", "conflict-side"),
                cwd=root,
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(merge.returncode, 0)

            report = agent_handoff.validate_document(
                handoff_document(root, result, main_result),
                root,
            )

        self.assertIn(
            "conflicting-worktree",
            report["summary"]["rejection_codes"],
        )

    def test_missing_terminal_copilot_trailer_rejects(self):
        with handoff_repository() as (root, _base, _parent, result):
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("HANDOFF = False\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(root, "commit", "-q", "-m", "fix: no trailer")
            no_trailer = git(root, "rev-parse", "HEAD")
            document = handoff_document(root, result, no_trailer)
            report = agent_handoff.validate_document(document, root)

        self.assertIn(
            "missing-copilot-trailer",
            report["summary"]["rejection_codes"],
        )

    def test_required_checks_use_closed_receipts_not_passed_labels(self):
        with handoff_repository() as (root, _base, parent, result):
            accepted = handoff_document(root, parent, result)
            report = agent_handoff.validate_document(accepted, root)
            self.assertNotIn(
                "invalid-check-receipt",
                report["summary"]["rejection_codes"],
            )

            literal_false = handoff_document(root, parent, result)
            literal_false["handoffs"][0]["required_checks"][0][
                "contract"
            ] = "false"
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must be one of git-diff-check",
            ):
                agent_handoff.validate_document(literal_false, root)

            shell_false = handoff_document(root, parent, result)
            shell_false["handoffs"][0]["required_checks"][0][
                "command"
            ] = "false"
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "unknown fields: command",
            ):
                agent_handoff.validate_document(shell_false, root)

            missing = handoff_document(root, parent, result)
            missing["handoffs"][0]["check_receipts"] = []
            report = agent_handoff.validate_document(missing, root)
            self.assertIn(
                "invalid-check-receipt",
                report["summary"]["rejection_codes"],
            )

            mutations = {
                "check_id": "wrong-check",
                "argv": ["/usr/bin/false"],
                "candidate_sha": parent,
                "worktree_identity": "0" * 64,
            }
            for field, value in mutations.items():
                with self.subTest(receipt_field=field):
                    document = handoff_document(root, parent, result)
                    receipt = document["handoffs"][0]["check_receipts"][0]
                    receipt[field] = value
                    receipt["seal"] = agent_handoff.seal_check_receipt(receipt)
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(
                        "invalid-check-receipt",
                        report["summary"]["rejection_codes"],
                    )

            wrong_time = handoff_document(root, parent, result)
            receipt = wrong_time["handoffs"][0]["check_receipts"][0]
            receipt["completed_at"] = "2026-09-03T00:00:00Z"
            receipt["seal"] = agent_handoff.seal_check_receipt(receipt)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "follows its owner boundary",
            ):
                agent_handoff.validate_document(wrong_time, root)

            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("TRAILING = True  \n", encoding="utf-8")
            (root / ".gitattributes").write_text(
                "*.py diff=hostile\n",
                encoding="utf-8",
            )
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(root, "add", ".gitattributes")
            git(
                root,
                "config",
                "core.whitespace",
                "-trailing-space",
            )
            git(root, "config", "diff.external", "/usr/bin/true")
            git(root, "config", "diff.hostile.textconv", "/usr/bin/true")
            git(root, "config", "alias.diff", "!/usr/bin/true")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: failing safe check\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            failing_result = git(root, "rev-parse", "HEAD")
            hostile_config = root / ".git" / "hostile-global"
            hostile_config.write_text(
                "[core]\n\twhitespace = -trailing-space\n"
                "[diff]\n\texternal = /usr/bin/true\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_CONFIG_GLOBAL": str(hostile_config),
                    "GIT_CONFIG_SYSTEM": str(hostile_config),
                    "GIT_EXTERNAL_DIFF": "/usr/bin/true",
                },
            ):
                failing = handoff_document(root, result, failing_result)
            self.assertNotEqual(
                failing["handoffs"][0]["check_receipts"][0]["exit_code"],
                0,
            )
            report = agent_handoff.validate_document(failing, root)
            self.assertIn(
                "required-check-failed",
                report["summary"]["rejection_codes"],
            )

            local_attributes = root / ".git" / "info" / "attributes"
            local_attributes.write_text(
                "*.py -text\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "local attributes file is not permitted",
            ):
                agent_handoff.execute_allowed_check(
                    receipt_id="attributes",
                    check_id="focused-module",
                    contract="git-diff-check",
                    repository_root=root,
                    parent_sha=result,
                    candidate_sha=failing_result,
                )

    def test_tracked_whitespace_attributes_cannot_disable_raw_check(self):
        cases = (
            (
                ".gitattributes",
                "*.py whitespace=-trailing-space\n",
            ),
            (
                "scripts/workflow_pilot/.gitattributes",
                "*.py whitespace=-trailing-space\n",
            ),
            (
                ".gitattributes",
                "[attr]relaxed whitespace=-trailing-space\n*.py relaxed\n",
            ),
            (
                ".gitattributes",
                "*.py -whitespace\n",
            ),
        )
        for attribute_path, attribute_text in cases:
            with self.subTest(
                attribute_path=attribute_path,
                attribute_text=attribute_text,
            ):
                with handoff_repository() as (root, _base, _parent, result):
                    path = root / attribute_path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(attribute_text, encoding="utf-8")
                    change = root / "scripts" / "workflow_pilot" / "change.py"
                    change.write_text("TRACKED = True  \n", encoding="utf-8")
                    git(root, "add", attribute_path)
                    git(root, "add", "scripts/workflow_pilot/change.py")
                    git(
                        root,
                        "commit",
                        "-q",
                        "-m",
                        "test: hostile whitespace attrs\n\n"
                        + agent_handoff.COPILOT_TRAILER,
                    )
                    candidate = git(root, "rev-parse", "HEAD")
                    receipt = agent_handoff.execute_allowed_check(
                        receipt_id="tracked-attrs",
                        check_id="focused-module",
                        contract="git-diff-check",
                        repository_root=root,
                        parent_sha=result,
                        candidate_sha=candidate,
                    )
                    self.assertNotEqual(receipt["exit_code"], 0)

        with handoff_repository() as (root, _base, _parent, result):
            (root / ".gitattributes").write_text(
                "*.py whitespace=-trailing-space\n",
                encoding="utf-8",
            )
            git(root, "add", ".gitattributes")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: parent whitespace attrs\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            attribute_parent = git(root, "rev-parse", "HEAD")
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("PARENT_ATTR = True  \n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: parent attrs cannot hide whitespace\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            receipt = agent_handoff.execute_allowed_check(
                receipt_id="parent-attrs",
                check_id="focused-module",
                contract="git-diff-check",
                repository_root=root,
                parent_sha=attribute_parent,
                candidate_sha=candidate,
            )
            self.assertNotEqual(receipt["exit_code"], 0)

        with handoff_repository() as (root, _base, _parent, result):
            (root / ".gitattributes").write_text(
                "*.md text\n",
                encoding="utf-8",
            )
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("BENIGN = True\n", encoding="utf-8")
            git(root, "add", ".gitattributes")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: benign attrs remain allowed\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            receipt = agent_handoff.execute_allowed_check(
                receipt_id="benign-attrs",
                check_id="focused-module",
                contract="git-diff-check",
                repository_root=root,
                parent_sha=result,
                candidate_sha=candidate,
            )
            self.assertEqual(receipt["exit_code"], 0)

    def test_scope_line_resource_protocol_lifetime_and_rss_budgets_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            cases = {}
            lines = handoff_document(root, parent, result)
            lines["handoffs"][0]["budgets"]["changed_lines"] = 1
            cases["lines"] = (lines, "changed-lines-budget-exceeded")

            scope = handoff_document(root, parent, result)
            scope["handoffs"][0]["allowed_scope"] = ["docs/"]
            cases["scope"] = (scope, "scope-violation")

            for field in ("rom_bytes", "ram_bytes", "protocol_changes"):
                document = handoff_document(root, parent, result)
                document["handoffs"][0]["budgets"][field] = 0
                document["handoffs"][0]["result"]["budget_usage"][field] = 1
                code = field.replace("_", "-") + "-budget-exceeded"
                cases[field] = (document, code)

            lifetime = handoff_document(root, parent, result)
            lifetime["handoffs"][0]["max_lifetime_seconds"] = 1
            cases["lifetime"] = (lifetime, "owner-lifetime-exceeded")

            rss = handoff_document(root, parent, result)
            rss["handoffs"][0]["max_peak_rss_bytes"] = 1
            cases["rss"] = (rss, "owner-rss-exceeded")

            for name, (document, code) in cases.items():
                with self.subTest(name=name):
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(code, report["summary"]["rejection_codes"])

    def test_duplicate_owner_and_watcher_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            duplicate_owner = handoff_document(root, parent, result)
            second = copy.deepcopy(duplicate_owner["handoffs"][0])
            second["id"] = "issue-178-round-2"
            duplicate_owner["handoffs"].append(second)
            owner_report = agent_handoff.validate_document(duplicate_owner, root)
            self.assertIn(
                "duplicate-owner",
                owner_report["summary"]["rejection_codes"],
            )

            duplicate_coordinator = handoff_document(root, parent, result)
            second_coordinator = copy.deepcopy(
                duplicate_coordinator["coordinators"][0]
            )
            second_coordinator["id"] = "coordinator-2"
            duplicate_coordinator["coordinators"].append(second_coordinator)
            coordinator_report = agent_handoff.validate_document(
                duplicate_coordinator,
                root,
            )
            self.assertIn(
                "duplicate-coordinator",
                coordinator_report["summary"]["rejection_codes"],
            )

            duplicate_watcher = handoff_document(root, parent, result)
            add_run(duplicate_watcher, result)
            second_watcher = copy.deepcopy(duplicate_watcher["watchers"][0])
            second_watcher["id"] = "watcher-9001-duplicate"
            duplicate_watcher["watchers"].append(second_watcher)
            watcher_report = agent_handoff.validate_document(
                duplicate_watcher,
                root,
            )
            self.assertIn(
                "duplicate-watcher",
                watcher_report["summary"]["rejection_codes"],
            )

    def test_implementation_owner_remote_actions_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            identities = (
                ("owner-1", None, "Owner-1", None),
                ("Build-Bot[bot]", None, "build-bot[BOT]", None),
                ("canonical-owner", 42, "renamed-owner", 42),
            )
            for owner_login, owner_id, actor_login, actor_id in identities:
                with self.subTest(owner=owner_login, actor=actor_login):
                    document = handoff_document(root, parent, result)
                    document["handoffs"][0]["owner_id"] = owner_login
                    document["handoffs"][0]["owner_database_id"] = owner_id
                    document["remote_actions"] = [
                        {
                            "id": "remote:push",
                            "handoff_id": "issue-178-round-1",
                            "actor_id": actor_login,
                            "actor_database_id": actor_id,
                            "action": "push",
                            "occurred_at": "2026-01-01T01:06:00Z",
                        }
                    ]
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(
                        "implementation-owner-remote-action",
                        report["summary"]["rejection_codes"],
                    )

    def test_closed_owner_history_chain_rotates_across_documents(self):
        def relabel(document, handoff_id):
            document["handoffs"][0]["id"] = handoff_id
            relationship = document["delivery_graph"]["relationships"][0]
            relationship["handoff_id"] = handoff_id
            child_task = next(
                task
                for task in document["delivery_graph"]["tasks"]
                if task["phase"] == "implementation"
            )
            child_task["handoff_id"] = handoff_id

        def shift_times(document, seconds):
            delta = timedelta(seconds=seconds)

            def shifted(value):
                return (
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                    + delta
                ).isoformat().replace("+00:00", "Z")

            handoff = document["handoffs"][0]
            for state in handoff["states"]:
                state["at"] = shifted(state["at"])
            for item in handoff["evidence"]:
                item["completed_at"] = shifted(item["completed_at"])
            for receipt in handoff["check_receipts"]:
                receipt["started_at"] = shifted(receipt["started_at"])
                receipt["completed_at"] = shifted(receipt["completed_at"])
                receipt["seal"] = agent_handoff.seal_check_receipt(receipt)

        with handoff_repository() as (root, _base, parent, first_result):
            first = handoff_document(root, parent, first_result)
            first["handoffs"][0]["owner_id"] = "Owner-1"
            first["handoffs"][0]["owner_database_id"] = None
            first_report = agent_handoff.validate_document(first, root)
            first_receipt = agent_handoff.make_history_receipt(
                first,
                first_report,
                "issue-178-round-1",
            )
            self.assertEqual(first_receipt["candidate_sha"], first_result)
            genesis = first["history_authority"]
            expected_plan = agent_handoff.plan_history_authority(
                root,
                "example/workflow",
                178,
                200,
                operation="advance",
                expected_object_id=genesis["object_id"],
                expected_sequence=0,
                new_head_seal=first_receipt["seal"],
            )
            plan_cli = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.workflow_pilot.agent_handoff",
                    "--authority-operation",
                    "advance",
                    "--worktree",
                    str(root),
                    "--repository",
                    "example/workflow",
                    "--issue",
                    "178",
                    "--pull-request",
                    "200",
                    "--expected-object-id",
                    genesis["object_id"],
                    "--expected-sequence",
                    "0",
                    "--new-head-seal",
                    first_receipt["seal"],
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(plan_cli.returncode, 0, plan_cli.stderr.decode())
            self.assertEqual(json.loads(plan_cli.stdout), expected_plan)
            set_history_authority(root, 1, first_receipt["seal"])
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "compare-and-swap expectation is stale",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    200,
                    operation="advance",
                    expected_object_id=first["history_authority"][
                        "object_id"
                    ],
                    expected_sequence=0,
                    new_head_seal=first_receipt["seal"],
                )

            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("HANDOFF = 'second'\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: second handoff\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            second_result = git(root, "rev-parse", "HEAD")

            omitted = handoff_document(root, first_result, second_result)
            relabel(omitted, "issue-178-round-2")
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "reset, truncated, or not at canonical head",
            ):
                agent_handoff.validate_document(omitted, root)

            stale_authority = handoff_document(
                root,
                first_result,
                second_result,
            )
            relabel(stale_authority, "issue-178-round-2")
            stale_authority["prior_handoffs"] = [first_receipt]
            stale_authority["history_authority"] = first[
                "history_authority"
            ]
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "does not match the canonical Git ref",
            ):
                agent_handoff.validate_document(stale_authority, root)

            reused = handoff_document(root, first_result, second_result)
            relabel(reused, "issue-178-round-2")
            shift_times(reused, 10)
            reused["prior_handoffs"] = [first_receipt]
            reused["handoffs"][0]["owner_id"] = "owner-1"
            reused["handoffs"][0]["owner_database_id"] = None
            reused_report = agent_handoff.validate_document(reused, root)
            self.assertIn(
                "closed-owner-reused",
                reused_report["summary"]["rejection_codes"],
            )

            fresh = handoff_document(root, first_result, second_result)
            relabel(fresh, "issue-178-round-2")
            shift_times(fresh, 10)
            fresh["prior_handoffs"] = [first_receipt]
            fresh["handoffs"][0]["owner_id"] = "owner-2"
            fresh["handoffs"][0]["owner_database_id"] = 43
            fresh_report = agent_handoff.validate_document(fresh, root)
            self.assertTrue(fresh_report["summary"]["trusted_push_eligible"])
            second_receipt = agent_handoff.make_history_receipt(
                fresh,
                fresh_report,
                "issue-178-round-2",
            )
            agent_handoff.validate_prior_handoffs(
                [first_receipt, second_receipt]
            )

            tampered = copy.deepcopy(first_receipt)
            tampered["candidate_sha"] = parent
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "seal does not verify",
            ):
                agent_handoff.validate_prior_handoffs([tampered])

            gap = copy.deepcopy(second_receipt)
            gap["sequence"] = 3
            gap["seal"] = agent_handoff.seal_history_receipt(gap)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "contiguous",
            ):
                agent_handoff.validate_prior_handoffs(
                    [first_receipt, gap]
                )

            fork = copy.deepcopy(second_receipt)
            fork["previous_seal"] = agent_handoff.ZERO_SEAL
            fork["seal"] = agent_handoff.seal_history_receipt(fork)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "forks or reorders",
            ):
                agent_handoff.validate_prior_handoffs(
                    [first_receipt, fork]
                )

            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "contiguous",
            ):
                agent_handoff.validate_prior_handoffs(
                    [second_receipt, first_receipt]
                )

            current_authority = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                200,
            )
            owner = AUTHORITY_OWNERS[str(root)]
            reference = agent_handoff.history_authority_ref(178, 200)
            fork_commit = owner_create_authority_commit(
                owner,
                {
                    "schema_version": 1,
                    "repository": "example/workflow",
                    "issue": 178,
                    "pull_request": 200,
                    "sequence": 2,
                    "head_seal": second_receipt["seal"],
                    "previous_object_id": genesis["object_id"],
                },
                genesis["object_id"],
            )
            git(
                owner,
                "push",
                "-q",
                (
                    f"--force-with-lease={reference}:"
                    f"{current_authority['object_id']}"
                ),
                "origin",
                f"{fork_commit}:{reference}",
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "replays or gaps sequence",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    178,
                    200,
                )

    def test_local_history_ref_cannot_forge_remote_genesis(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            reference = agent_handoff.history_authority_ref(178, 200)
            clone_root = root.parent / "normal-clone"
            subprocess.run(
                [
                    reporter.trusted_git_executable(),
                    "clone",
                    "--quiet",
                    "--no-local",
                    str(root),
                    str(clone_root),
                ],
                env=reporter.git_environment(offline=True),
                check=True,
                capture_output=True,
            )
            remote_url = git(root, "remote", "get-url", "origin")
            git(clone_root, "remote", "set-url", "origin", remote_url)
            before_fetch = subprocess.run(
                reporter.git_command(
                    clone_root,
                    "cat-file",
                    "-e",
                    document["history_authority"]["object_id"],
                ),
                cwd=clone_root,
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(before_fetch.returncode, 0)
            fetched = agent_handoff.read_history_authority(
                clone_root,
                "example/workflow",
                178,
                200,
            )
            self.assertEqual(
                fetched["object_id"],
                document["history_authority"]["object_id"],
            )

            forged = owner_create_authority_commit(
                root,
                {
                    "schema_version": 1,
                    "repository": "example/workflow",
                    "issue": 178,
                    "pull_request": 200,
                    "sequence": 0,
                    "head_seal": None,
                    "previous_object_id": None,
                },
            )
            git(
                root,
                "update-ref",
                reference,
                forged,
            )
            report = agent_handoff.validate_document(document, root)
            self.assertTrue(report["summary"]["trusted_push_eligible"])

            owner = AUTHORITY_OWNERS[str(root)]
            git(owner, "push", "-q", "origin", f":{reference}")
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "genesis is unknown",
            ):
                agent_handoff.validate_document(document, root)

    def test_watcher_timeout_defers_to_authoritative_success(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            add_run(document, result, process_result="timeout")
            report = agent_handoff.validate_document(document, root)

        self.assertTrue(report["summary"]["trusted_push_eligible"])
        self.assertFalse(report["summary"]["delivery_eligible"])
        self.assertEqual(
            report["watchers"],
            [
                {
                    "run_id": 9001,
                    "head_sha": result,
                    "watcher_process_result": "timeout",
                    "authoritative_outcome": "success",
                    "reconciled": True,
                }
            ],
        )

    def test_watcher_identity_and_observation_must_match_authority(self):
        with handoff_repository() as (root, _base, parent, result):
            wrong_head = handoff_document(root, parent, result)
            add_run(wrong_head, result)
            wrong_head["watchers"][0]["head_sha"] = parent
            report = agent_handoff.validate_document(wrong_head, root)
            self.assertIn(
                "watcher-run-mismatch",
                report["summary"]["rejection_codes"],
            )

            stale_observation = handoff_document(root, parent, result)
            add_run(stale_observation, result)
            stale_observation["workflow_runs"][0][
                "observed_at"
            ] = "2026-01-01T01:14:00Z"
            report = agent_handoff.validate_document(stale_observation, root)
            self.assertIn(
                "watcher-authority-stale",
                report["summary"]["rejection_codes"],
            )

    def test_true_failed_authoritative_run_stays_failed(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            add_run(document, result, conclusion="failure", process_result="error")
            report = agent_handoff.validate_document(document, root)

        self.assertTrue(report["summary"]["trusted_push_eligible"])
        self.assertFalse(report["summary"]["delivery_eligible"])
        self.assertIn(
            "authoritative-run-failed",
            report["summary"]["rejection_codes"],
        )
        self.assertEqual(
            report["watchers"][0]["authoritative_outcome"],
            "failure",
        )

    def test_sigkill_oom_preserves_worktree_and_assigns_one_replacement(self):
        with handoff_repository() as (root, _base, _parent, result):
            preserved = root / "scripts" / "workflow_pilot" / "recovery.py"
            preserved.write_text("RECOVER = True\n", encoding="utf-8")
            document = handoff_document(root, result, result)
            interrupted = document["handoffs"][0]
            interrupted["result"] = None
            interrupted["states"] = timestamped_states()[:3] + [
                {"state": "interrupted", "at": "2026-01-01T01:03:30Z"}
            ]
            interrupted["evidence"] = evidence("incomplete")
            interrupted["required_checks"][0]["receipt_id"] = None
            interrupted["check_receipts"] = []
            interrupted["interruption"] = {
                "kind": "sigkill_oom",
                "signal": 9,
                "occurred_at": "2026-01-01T01:03:30Z",
                "kernel_evidence": (
                    "Fixture: kernel reports Out of memory and killed process."
                ),
                "interrupted_check_ids": ["focused-module"],
                "preserved_paths": ["scripts/workflow_pilot/recovery.py"],
                "recovery_minutes": 7,
                "replacement_handoff_id": "issue-178-round-1-replacement",
                "host_process_actions": [],
            }

            replacement = copy.deepcopy(interrupted)
            replacement["id"] = "issue-178-round-1-replacement"
            replacement["owner_id"] = "owner-2"
            replacement["replaces_handoff_id"] = interrupted["id"]
            replacement["states"] = [
                {
                    "state": "assignment_sent",
                    "at": "2026-01-01T01:04:00Z",
                },
                {
                    "state": "assignment_received",
                    "at": "2026-01-01T01:05:00Z",
                },
            ]
            replacement["evidence"] = []
            replacement["interruption"] = None
            document["handoffs"].append(replacement)
            primary_task = next(
                task
                for task in document["delivery_graph"]["tasks"]
                if task["id"] == "child-implement"
            )
            primary_task["status"] = "blocked"
            primary_task["status_reason"] = "owner_interrupted"
            replacement_task = copy.deepcopy(primary_task)
            replacement_task["id"] = "child-implement-replacement"
            replacement_task["status"] = "pending"
            replacement_task["status_reason"] = None
            replacement_task["handoff_id"] = replacement["id"]
            document["delivery_graph"]["tasks"].append(replacement_task)
            replacement_relationship = copy.deepcopy(
                document["delivery_graph"]["relationships"][0]
            )
            replacement_relationship["handoff_id"] = replacement["id"]
            document["delivery_graph"]["relationships"].append(
                replacement_relationship
            )
            document["delivery_graph"]["dependencies"].append(
                {
                    "task": replacement_task["id"],
                    "depends_on": "parent-merge",
                    "type": "code_contract",
                }
            )
            report = agent_handoff.validate_document(document, root)

            for replacement_at in (
                "2026-01-01T01:03:30Z",
                "2026-01-01T01:03:00Z",
            ):
                with self.subTest(replacement_at=replacement_at):
                    noncausal = copy.deepcopy(document)
                    noncausal["handoffs"][1]["states"][0][
                        "at"
                    ] = replacement_at
                    noncausal_report = agent_handoff.validate_document(
                        noncausal,
                        root,
                    )
                    self.assertIn(
                        "replacement-assignment-not-causal",
                        noncausal_report["summary"]["rejection_codes"],
                    )

            multiple = copy.deepcopy(document)
            extra = copy.deepcopy(multiple["handoffs"][1])
            extra["id"] = "issue-178-round-1-extra-replacement"
            extra["owner_id"] = "owner-3"
            multiple["handoffs"].append(extra)
            extra_task = copy.deepcopy(replacement_task)
            extra_task["id"] = "child-implement-extra-replacement"
            extra_task["handoff_id"] = extra["id"]
            multiple["delivery_graph"]["tasks"].append(extra_task)
            extra_relationship = copy.deepcopy(replacement_relationship)
            extra_relationship["handoff_id"] = extra["id"]
            multiple["delivery_graph"]["relationships"].append(
                extra_relationship
            )
            multiple["delivery_graph"]["dependencies"].append(
                {
                    "task": extra_task["id"],
                    "depends_on": "parent-merge",
                    "type": "code_contract",
                }
            )
            multiple_report = agent_handoff.validate_document(multiple, root)
            self.assertIn(
                "replacement-owner-count",
                multiple_report["summary"]["rejection_codes"],
            )

        self.assertEqual(report["summary"]["recovery_count"], 1)
        self.assertEqual(report["summary"]["recovery_minutes"], 7)
        self.assertEqual(report["handoffs"][0]["outcome"], "interrupted")
        self.assertEqual(report["handoffs"][1]["outcome"], "in_progress")
        self.assertEqual(report["handoffs"][1]["rejection_codes"], [])
        self.assertFalse(report["summary"]["trusted_push_eligible"])
        self.assertFalse(report["summary"]["delivery_eligible"])
        self.assertNotIn(
            "incomplete-lifecycle",
            report["handoffs"][1]["rejection_codes"],
        )
        self.assertNotIn(
            "oom-worktree-not-preserved",
            report["handoffs"][0]["rejection_codes"],
        )
        self.assertNotIn(
            "replacement-owner-count",
            report["handoffs"][0]["rejection_codes"],
        )

    def test_hibernated_local_coordinator_fails_closed(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document["coordinators"][0]["availability"] = {
                "mode": "local",
                "autostop_enabled": True,
                "stop_on_disconnect": True,
                "evaluation_source": "coordinator-runtime",
                "evaluated_at": "2026-08-31T03:00:00Z",
                "unattended_until": "2026-09-01T03:00:00Z",
                "plan": None,
            }
            report = agent_handoff.validate_document(document, root)

            self.assertFalse(report["summary"]["trusted_push_eligible"])
            self.assertIn(
                "coordinator-unavailable",
                report["summary"]["rejection_codes"],
            )

            document["coordinators"][0]["availability"]["plan"] = {
                "kind": "disable_triggers",
                "available_until": "2026-01-31T03:00:00Z",
                "evidence": {
                    "source": "coordinator-runtime",
                    "observed_at": "2026-08-31T03:00:00Z",
                    "autostop_enabled": False,
                    "stop_on_disconnect": False,
                },
            }
            expired = agent_handoff.validate_document(document, root)
            self.assertIn(
                "coordinator-unavailable",
                expired["summary"]["rejection_codes"],
            )

            document["coordinators"][0]["availability"]["plan"] = {
                "kind": "disable_triggers",
                "available_until": "2026-09-02T03:00:00Z",
                "evidence": {
                    "source": "coordinator-runtime",
                    "observed_at": "2026-08-31T03:00:00Z",
                    "autostop_enabled": True,
                    "stop_on_disconnect": False,
                },
            }
            ineffective = agent_handoff.validate_document(document, root)
            self.assertIn(
                "coordinator-unavailable",
                ineffective["summary"]["rejection_codes"],
            )

            document["coordinators"][0]["availability"]["plan"][
                "evidence"
            ]["autostop_enabled"] = False
            available = agent_handoff.validate_document(document, root)
            self.assertTrue(available["summary"]["trusted_push_eligible"])

    def test_duplicate_json_keys_and_non_exact_remote_boundary_fail_schema(self):
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "duplicate JSON key 'schema_version'",
        ):
            reporter.parse_json(
                '{"schema_version":1,"schema_version":1}',
                "handoff",
            )

        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document["handoffs"][0]["prohibited_remote_actions"].pop()
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must exactly cover",
            ):
                agent_handoff.validate_document(document, root)


class ReporterHandoffExtensionTests(unittest.TestCase):
    def test_version_two_fixture_reports_sealed_handoff_metrics(self):
        with handoff_repository() as (root, _base, parent, result):
            accepted_document = handoff_document(root, parent, result)
            accepted_result = agent_handoff.validate_document(
                accepted_document,
                root,
            )
            accepted = agent_handoff.reporter_record(
                accepted_document,
                accepted_result,
            )

            stale_document = handoff_document(root, parent, result)
            stale_handoff = stale_document["handoffs"][0]
            stale_handoff["id"] = "issue-178-stale"
            stale_handoff["owner_id"] = "owner-2"
            stale_relationship = stale_document["delivery_graph"][
                "relationships"
            ][0]
            stale_relationship["handoff_id"] = stale_handoff["id"]
            stale_task = next(
                task
                for task in stale_document["delivery_graph"]["tasks"]
                if task["phase"] == "implementation"
            )
            stale_task["handoff_id"] = stale_handoff["id"]
            stale_handoff["result"]["sha"] = parent
            stale_result = agent_handoff.validate_document(
                stale_document,
                root,
            )
            stale = agent_handoff.reporter_record(
                stale_document,
                stale_result,
            )

            fixture = test_reporter.minimal_fixture()
            fixture["schema_version"] = reporter.HANDOFF_FIXTURE_SCHEMA_VERSION
            fixture["lifecycle_as_of"] = "2026-09-02T00:00:00Z"
            fixture["review_thread_event_source"][
                "coverage_end"
            ] = fixture["lifecycle_as_of"]
            fixture["implementation_handoffs"] = [accepted, stale]
            decisions = test_reporter.minimal_decisions()
            with test_reporter.git_authority(fixture) as (
                authoritative_fixture,
                authority_root,
            ):
                report = reporter.build_report(
                    authoritative_fixture,
                    decisions,
                    authority_root,
                )
                decisions_path = (
                    authority_root / ".github" / "workflow-pilot-decisions.json"
                )
                decisions_path.parent.mkdir(parents=True)
                decisions_path.write_text(
                    json.dumps(decisions),
                    encoding="utf-8",
                )
                fixture_path = authority_root / "operational.json"
                fixture_path.write_text(
                    json.dumps(authoritative_fixture),
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.workflow_pilot.reporter",
                        "--repository-root",
                        str(authority_root),
                        "--fixture",
                        str(fixture_path),
                        "--decisions",
                        str(decisions_path),
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr.decode(),
                )
                self.assertEqual(
                    completed.stdout,
                    reporter.normalized_json(report),
                )

        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(
            report["identities"]["implementation_handoffs"],
            ["issue-178-round-1", "issue-178-stale"],
        )
        expected_lifetime = max(
            item["lifetime_seconds"]
            for item in (
                accepted_result["handoffs"][0],
                stale_result["handoffs"][0],
            )
        )
        self.assertEqual(
            report["implementation_handoffs"],
            {
                "records": 2,
                "accepted": 1,
                "rejected": 1,
                "interrupted": 0,
                "in_progress": 0,
                "stale_responses": 1,
                "max_lifetime_seconds": expected_lifetime,
                "max_peak_rss_bytes": 134217728,
                "coordination_turns": 4,
                "recovery_minutes": 0,
            },
        )
        tampered = copy.deepcopy(fixture)
        tampered["implementation_handoffs"][0]["result"]["summary"][
            "accepted_handoffs"
        ] = 99
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "result seal does not verify",
        ):
            reporter.validate_fixture(tampered)

    def test_frozen_version_one_schema_remains_closed_and_unchanged(self):
        baseline = reporter.load_json(test_reporter.BASELINE)
        data = reporter.validate_fixture(baseline)
        self.assertEqual(baseline["schema_version"], 1)
        self.assertNotIn("implementation_handoffs", baseline)
        self.assertEqual(data["implementation_handoffs"], {})
        self.assertEqual(data["implementation_handoff_bundles"], {})

        changed = copy.deepcopy(baseline)
        changed["implementation_handoffs"] = []
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "unknown fields",
        ):
            reporter.validate_fixture(changed)

    def test_handoff_reporter_schema_rejects_unknown_or_incoherent_records(self):
        fixture = test_reporter.minimal_fixture()
        fixture["schema_version"] = reporter.HANDOFF_FIXTURE_SCHEMA_VERSION
        fixture["implementation_handoffs"] = [
            {
                "id": "bad",
                "owner_id": "owner-a",
                "assigned_at": "2026-01-01T01:00:00Z",
                "closed_at": "2026-01-01T01:05:00Z",
                "outcome": "accepted",
                "rejection_codes": ["stale-result"],
                "peak_rss_bytes": 1,
                "coordination_turns": 1,
                "recovery_minutes": 0,
            }
        ]
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "is missing fields",
        ):
            reporter.validate_fixture(fixture)


if __name__ == "__main__":
    unittest.main()
