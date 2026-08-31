import copy
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from scripts.workflow_pilot import agent_handoff, reporter
from scripts.workflow_pilot.tests import test_reporter


ROOT = Path(__file__).resolve().parents[3]
TEST_ARTIFACTS = ROOT / "build" / "test-artifacts"
TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)


def git(repository_root, *arguments):
    return subprocess.run(
        reporter.git_command(repository_root, *arguments),
        cwd=repository_root,
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@contextmanager
def handoff_repository():
    with tempfile.TemporaryDirectory(
        prefix="agent-handoff-",
        dir=TEST_ARTIFACTS,
    ) as temporary:
        repository_root = Path(temporary)
        git(repository_root, "init", "-q", "-b", "master")
        git(repository_root, "config", "user.name", "Handoff Test")
        git(repository_root, "config", "user.email", "handoff@example.invalid")
        git(
            repository_root,
            "remote",
            "add",
            "origin",
            "https://github.com/example/workflow.git",
        )
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
        yield repository_root, base_sha, parent_sha, result_sha


def timestamped_states():
    return [
        {"state": "assignment_sent", "at": "2026-01-01T01:00:00Z"},
        {"state": "assignment_received", "at": "2026-01-01T01:01:00Z"},
        {"state": "progressing", "at": "2026-01-01T01:02:00Z"},
        {"state": "committed", "at": "2026-01-01T01:04:00Z"},
        {"state": "handed_off", "at": "2026-01-01T01:05:00Z"},
    ]


def evidence(status="passed"):
    exit_code = 0 if status == "passed" else None
    return [
        {
            "id": "acceptance",
            "kind": "acceptance",
            "status": status,
            "exit_code": exit_code,
            "completed_at": "2026-01-01T01:03:00Z",
            "detail": "Acceptance criteria were exercised.",
        },
        {
            "id": "focused-check",
            "kind": "check",
            "status": status,
            "exit_code": exit_code,
            "completed_at": "2026-01-01T01:03:00Z",
            "detail": "The focused module passed.",
        },
        {
            "id": "budget-lines",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": "2026-01-01T01:03:00Z",
            "detail": "Git provided the changed-line count.",
        },
        {
            "id": "budget-rom",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": "2026-01-01T01:03:00Z",
            "detail": "No ROM-producing path changed.",
        },
        {
            "id": "budget-ram",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": "2026-01-01T01:03:00Z",
            "detail": "No RAM-owning path changed.",
        },
        {
            "id": "budget-protocol",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": "2026-01-01T01:03:00Z",
            "detail": "The one admitted protocol change is versioned.",
        },
    ]


def delivery_graph(
    *,
    merge_status="done",
    build_status="in_progress",
    remote_status="pending",
    watcher_process="running",
    watcher_status="in_progress",
    watcher_conclusion=None,
    recovery_status="not_required",
):
    return {
        "relationships": [
            {
                "child_issue": 178,
                "parent_issue": 176,
                "type": "code_contract",
            }
        ],
        "tasks": [
            {
                "id": "parent-merge",
                "issue": 176,
                "phase": "merge",
                "status": merge_status,
            },
            {
                "id": "parent-post-merge-build",
                "issue": 176,
                "phase": "post_merge_build",
                "status": build_status,
            },
            {
                "id": "parent-completion",
                "issue": 176,
                "phase": "completion",
                "status": "pending",
            },
            {
                "id": "parent-closure",
                "issue": 176,
                "phase": "closure",
                "status": "pending",
            },
            {
                "id": "parent-remote",
                "issue": 176,
                "phase": "remote_completion",
                "status": remote_status,
            },
            {
                "id": "parent-recovery",
                "issue": 176,
                "phase": "fix_forward_revert",
                "status": recovery_status,
            },
            {
                "id": "child-implement",
                "issue": 178,
                "phase": "implementation",
                "status": "pending",
            },
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
        "watchers": (
            []
            if watcher_process is None
            else [
                {
                    "id": "parent-master-watcher",
                    "run_task": "parent-post-merge-build",
                    "process_state": watcher_process,
                    "authoritative_status": watcher_status,
                    "conclusion": watcher_conclusion,
                }
            ]
        ),
    }


def handoff_document(repository_root, parent_sha, result_sha):
    return {
        "schema_version": 1,
        "repository": "example/workflow",
        "delivery_graph": delivery_graph(),
        "coordinators": [
            {
                "id": "coordinator-1",
                "availability": {
                    "mode": "always_on",
                    "autostop_enabled": False,
                    "stop_on_disconnect": False,
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
                        "command": (
                            "python3 -m unittest "
                            "scripts.workflow_pilot.tests.test_agent_handoff -v"
                        ),
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
                "states": timestamped_states(),
                "evidence": evidence(),
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
                "type": "code_contract",
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
                    "run_task": "parent-post-merge-build",
                    "process_state": "running",
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
        self.assertTrue(report["summary"]["delivery_eligible"])
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
            )
            report = agent_handoff.validate_document(document, root)

        self.assertFalse(report["summary"]["trusted_push_eligible"])
        self.assertIn(
            "code-contract-not-merged",
            report["handoffs"][0]["rejection_codes"],
        )

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
            incomplete["handoffs"][0]["states"] = timestamped_states()[:3]
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
            document = handoff_document(root, parent, result)
            document["remote_actions"] = [
                {
                    "id": "remote:push",
                    "handoff_id": "issue-178-round-1",
                    "actor_id": "owner-1",
                    "action": "push",
                    "occurred_at": "2026-01-01T01:06:00Z",
                }
            ]
            report = agent_handoff.validate_document(document, root)

        self.assertIn(
            "implementation-owner-remote-action",
            report["summary"]["rejection_codes"],
        )

    def test_watcher_timeout_defers_to_authoritative_success(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            add_run(document, result, process_result="timeout")
            report = agent_handoff.validate_document(document, root)

        self.assertTrue(report["summary"]["trusted_push_eligible"])
        self.assertTrue(report["summary"]["delivery_eligible"])
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
            replacement["states"] = timestamped_states()[:2]
            replacement["evidence"] = []
            replacement["interruption"] = None
            document["handoffs"].append(replacement)
            report = agent_handoff.validate_document(document, root)

        self.assertEqual(report["summary"]["recovery_count"], 1)
        self.assertEqual(report["summary"]["recovery_minutes"], 7)
        self.assertEqual(report["handoffs"][0]["outcome"], "interrupted")
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
                "plan": None,
            }
            report = agent_handoff.validate_document(document, root)

            self.assertFalse(report["summary"]["trusted_push_eligible"])
            self.assertIn(
                "coordinator-unavailable",
                report["summary"]["rejection_codes"],
            )

            document["coordinators"][0]["availability"]["plan"] = {
                "kind": "always_on_takeover",
                "available_until": "2026-01-01T02:00:00Z",
            }
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
            accepted_result = agent_handoff.validate_document(
                handoff_document(root, parent, result),
                root,
            )
        accepted = agent_handoff.reporter_records(accepted_result)[0]
        accepted["id"] = "accepted"
        accepted["owner_id"] = "owner-a"
        stale = copy.deepcopy(accepted)
        stale.update(
            {
                "id": "stale",
                "owner_id": "owner-b",
                "closed_at": "2026-01-01T01:06:00Z",
                "outcome": "rejected",
                "rejection_codes": ["stale-result"],
                "peak_rss_bytes": 268435456,
                "coordination_turns": 3,
                "recovery_minutes": 4,
            }
        )
        fixture = test_reporter.minimal_fixture()
        fixture["schema_version"] = reporter.HANDOFF_FIXTURE_SCHEMA_VERSION
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
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertEqual(completed.stdout, reporter.normalized_json(report))

            version_one = copy.deepcopy(authoritative_fixture)
            version_one["schema_version"] = 1
            del version_one["implementation_handoffs"]
            version_one_path = authority_root / "version-one.json"
            version_one_path.write_text(
                json.dumps(version_one),
                encoding="utf-8",
            )
            missing_expected = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.workflow_pilot.reporter",
                    "--repository-root",
                    str(authority_root),
                    "--fixture",
                    str(version_one_path),
                    "--decisions",
                    str(decisions_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(missing_expected.returncode, 2)
            self.assertIn(
                b"--expected is required for frozen schema version 1",
                missing_expected.stderr,
            )

        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(
            report["identities"]["implementation_handoffs"],
            ["accepted", "stale"],
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
                "max_lifetime_seconds": 360,
                "max_peak_rss_bytes": 268435456,
                "coordination_turns": 5,
                "recovery_minutes": 4,
            },
        )
        changed = copy.deepcopy(fixture)
        changed["implementation_handoffs"][1]["coordination_turns"] = 4
        self.assertNotEqual(
            reporter.cohort_identity_seal(reporter.validate_fixture(fixture)),
            reporter.cohort_identity_seal(reporter.validate_fixture(changed)),
        )

    def test_frozen_version_one_schema_remains_closed_and_unchanged(self):
        baseline = reporter.load_json(test_reporter.BASELINE)
        data = reporter.validate_fixture(baseline)
        self.assertEqual(baseline["schema_version"], 1)
        self.assertNotIn("implementation_handoffs", baseline)
        self.assertEqual(data["implementation_handoffs"], {})

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
            "accepted outcome requires closure without rejections",
        ):
            reporter.validate_fixture(fixture)

        fixture["implementation_handoffs"][0]["outcome"] = "rejected"
        fixture["implementation_handoffs"][0]["rejection_codes"] = ["unknown"]
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "must be one of",
        ):
            reporter.validate_fixture(fixture)


if __name__ == "__main__":
    unittest.main()
