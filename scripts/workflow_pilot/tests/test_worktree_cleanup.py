"""TC-WORKFLOW-WORKTREE-CLEANUP-001: real Git, deterministic GitHub, no live deletion."""

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs
import uuid

from scripts.workflow_pilot import worktree_cleanup as cleanup


ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = "example/worktree-cleanup"
REPO = {"id": 7, "full_name": REPOSITORY}
PREFIX = "repos/" + REPOSITORY


class Responses(cleanup.GitHub):
    def __init__(self, root, head, merge):
        super().__init__(root)
        self.requests = []
        self.pr = {
            "id": 1, "number": 1, "state": "closed", "merged": True,
            "merged_at": "2026-01-01T00:00:00Z", "merge_commit_sha": merge,
            "head": {"ref": "topic", "sha": head, "repo": dict(REPO)},
            "base": {"ref": "master", "sha": merge, "repo": dict(REPO)},
        }
        self.run = {
            "id": 10, "workflow_id": 2, "run_number": 10, "run_attempt": 1,
            "check_suite_id": 20, "path": cleanup.BUILD_PATH, "event": "push",
            "head_branch": "master", "head_sha": merge,
            "repository": dict(REPO), "head_repository": dict(REPO),
            "status": "completed", "conclusion": "success",
            "created_at": "2026-01-01T00:00:01Z", "updated_at": "2026-01-01T00:01:00Z",
        }
        self.check = {
            "id": 30, "head_sha": merge, "check_suite": {"id": 20},
            "app": {"id": 40, "slug": "github-actions"}, "name": "summary",
            "status": "completed", "conclusion": "success",
        }
        self.data = {
            "": {**REPO, "default_branch": "master"},
            "/git/ref/heads/master": {"ref": "refs/heads/master",
                                     "object": {"type": "commit", "sha": merge}},
            "/pulls": [self.pr], "/pulls/1": self.pr,
            "/actions/workflows/build.yml": {"id": 2, "path": cleanup.BUILD_PATH},
            "/actions/runs": {"workflow_runs": [self.run]},
            "/actions/runs/10": self.run,
            f"/commits/{merge}/check-runs": {"check_runs": [self.check]},
            f"/commits/{merge}/statuses": [],
        }

    def request(self, endpoint):
        self.requests.append(endpoint)
        path, _, query = endpoint.partition("?")
        if not path.startswith(PREFIX):
            raise AssertionError("unexpected repository request")
        data = copy.deepcopy(self.data[path.removeprefix(PREFIX)])
        args = parse_qs(query)
        if "page" in args:
            offset = (int(args["page"][0]) - 1) * cleanup.PAGE_SIZE
            if isinstance(data, list):
                return data[offset:offset + cleanup.PAGE_SIZE]
            key = next(key for key in ("workflow_runs", "check_runs") if key in data)
            rows = data[key]
            return {"total_count": len(rows), key: rows[offset:offset + cleanup.PAGE_SIZE]}
        return data

    def add_run(self, **changes):
        run = {**self.run, "id": 11, "run_number": 11, "check_suite_id": 21,
               "updated_at": "2026-01-01T00:02:00Z", **changes}
        self.data["/actions/runs"]["workflow_runs"].append(run)
        self.data[f"/actions/runs/{run['id']}"] = run
        checks = self.data[f"/commits/{run['head_sha']}/check-runs"]["check_runs"]
        checks.append({**self.check, "id": run["id"] + 100,
                       "check_suite": {"id": run["check_suite_id"]}})
        return run


class CleanupTests(unittest.TestCase):
    def setUp(self):
        # Exclusively test-owned directories, underneath the source checkout, never /tmp.
        self.sandbox = ROOT / "build" / ("worktree-cleanup-" + uuid.uuid4().hex)
        self.sandbox.mkdir(parents=True)
        self.addCleanup(self.remove_fixture)
        self.root = self.sandbox / "repo"
        self.target = self.sandbox / "completed"
        self.root.mkdir()
        self.command(self.root, "init", "-q", "-b", "master")
        self.command(self.root, "config", "user.email", "cleanup-test@example.invalid")
        self.command(self.root, "config", "user.name", "Cleanup test")
        self.command(self.root, "config", "commit.gpgsign", "false")
        self.command(self.root, "config", "core.autocrlf", "false")
        (self.root / "tracked").write_text("base\n")
        (self.root / ".gitignore").write_text("build/\n*.sav\n*.o\ntools/scaninc/scaninc\n")
        (self.root / "tools" / "scaninc").mkdir(parents=True)
        (self.root / "tools" / "scaninc" / "Makefile").write_text("scaninc: scaninc.cpp\n")
        self.command(self.root, "add", ".")
        self.command(self.root, "commit", "-qm", "base")
        self.base = self.command(self.root, "rev-parse", "HEAD").strip()
        self.command(self.root, "worktree", "add", "-qb", "topic", str(self.target))
        (self.target / "tracked").write_text("completed feature\n")
        self.command(self.target, "commit", "-qam", "feature")
        self.head = self.command(self.target, "rev-parse", "HEAD").strip()
        self.command(self.root, "merge", "-q", "--no-ff", "topic", "-m", "merge")
        self.merge = self.command(self.root, "rev-parse", "HEAD").strip()
        self.command(self.root, "remote", "add", "origin", "https://github.com/" + REPOSITORY + ".git")
        self.repo = cleanup.Repository(self.root, [self.root])
        self.api = Responses(self.root, self.head, self.merge)

    def remove_fixture(self):
        if self.sandbox.parent == ROOT / "build" and self.sandbox.name.startswith("worktree-cleanup-"):
            shutil.rmtree(self.sandbox)

    @staticmethod
    def command(root, *args):
        return cleanup.git(root, *args)

    def result(self, apply=False, target=None, proof=None):
        return cleanup.cleanup(
            self.repo, self.api, [target or self.target], apply=apply, proof_sha=proof
        )["results"][0]

    def held(self, reason=None, **arguments):
        row = self.result(apply=True, **arguments)
        self.assertEqual(row["decision"], "retained", row)
        if reason:
            self.assertIn(reason, " ".join(row["reasons"]))
        self.assertTrue(self.target.is_dir())
        return row

    def test_dry_run_is_inert_including_index_and_registrations(self):
        before = cleanup.inventory(self.root)
        gitdir = Path(self.command(self.target, "rev-parse", "--absolute-git-dir").strip())
        index = (gitdir / "index").read_bytes()
        row = self.result()
        self.assertEqual(row["decision"], "eligible", row)
        self.assertGreater(row["allocated_bytes"], 0)
        self.assertEqual(cleanup.inventory(self.root), before)
        self.assertEqual((gitdir / "index").read_bytes(), index)
        self.assertTrue(self.target.exists())

    def test_normal_apply_removes_only_completed_tree_and_keeps_branch(self):
        output = self.target / "build" / "output"
        output.parent.mkdir()
        output.write_bytes(b"a" * 8192)
        (self.target / "tools" / "scaninc" / "scaninc").write_bytes(b"generated host tool")
        outside = self.sandbox / "user-file"
        outside.write_text("retain this\n")
        (output.parent / "outside-link").symlink_to(outside)
        expected_size = cleanup.allocated_size(self.target)
        row = self.result(apply=True)
        self.assertEqual(row["decision"], "removed", row)
        self.assertEqual(row["allocated_bytes"], expected_size)
        self.assertFalse(self.target.exists())
        self.assertNotIn(str(self.target), cleanup.inventory(self.root))
        self.assertEqual(self.command(self.root, "rev-parse", "topic").strip(), self.head)
        self.assertEqual(outside.read_text(), "retain this\n")
        self.assertTrue(self.root.is_dir())

    def test_current_master_and_ancestor_roots_remain(self):
        for path in (self.root, self.sandbox, Path.home(), Path("/")):
            with self.subTest(path=path):
                self.held(target=path)
        self.command(self.root, "checkout", "-qb", "coordinator")
        master_tree = self.sandbox / "master"
        self.command(self.root, "worktree", "add", "-q", str(master_tree), "master")
        self.held("master worktree", target=master_tree)
        self.repo = cleanup.Repository(self.target, [self.root])
        self.held("current")

    def test_locked_worktree_remains_locked(self):
        self.command(self.root, "worktree", "lock", str(self.target))
        self.held("locked")
        self.assertIn("locked", cleanup.inventory(self.root)[str(self.target)])

    def test_tracked_staged_and_untracked_data_are_preserved(self):
        for mode in ("tracked", "staged", "untracked"):
            with self.subTest(mode=mode):
                file = self.target / ("notes" if mode == "untracked" else "tracked")
                file.write_text("unique local data\n")
                if mode == "staged":
                    self.command(self.target, "add", "tracked")
                self.held("dirty")
                self.command(self.target, "reset", "-q", "HEAD")
                if mode == "untracked":
                    file.unlink()
                else:
                    self.command(self.target, "checkout", "--", "tracked")

    def test_ignored_save_is_not_treated_as_generated_output(self):
        (self.target / "precious.sav").write_text("original user data")
        self.held("ignored non-build/local data")

    def test_hidden_index_changes_and_in_progress_operations_are_held(self):
        self.command(self.target, "update-index", "--assume-unchanged", "tracked")
        (self.target / "tracked").write_text("hidden edit\n")
        self.held("assume-unchanged")
        self.command(self.target, "update-index", "--no-assume-unchanged", "tracked")
        self.command(self.target, "checkout", "--", "tracked")
        gitdir = Path(self.command(self.target, "rev-parse", "--absolute-git-dir").strip())
        (gitdir / "MERGE_HEAD").write_text(self.merge + "\n")
        self.held("unfinished Git operation")

    def test_unique_and_unpushed_commits_are_not_inferred_complete(self):
        (self.target / "tracked").write_text("not delivered\n")
        self.command(self.target, "commit", "-qam", "unique")
        self.held("unique/unpushed")
        self.command(self.root, "update-ref", "refs/remotes/origin/topic", self.head)
        self.command(self.target, "branch", "--set-upstream-to=origin/topic")
        self.held("unpushed or divergent upstream")

    def test_unmerged_head_cannot_borrow_another_merge_proof(self):
        (self.target / "tracked").write_text("unpushed new change\n")
        self.command(self.target, "commit", "-qam", "unmerged")
        self.api.pr["head"]["sha"] = self.command(self.target, "rev-parse", "HEAD").strip()
        self.held("unique/unmerged")

    def test_detached_and_unassociated_new_branches_are_held(self):
        self.command(self.target, "checkout", "-q", "--detach")
        self.held("detached")
        self.command(self.target, "checkout", "-qb", "new-work")
        self.held("unknown/unpushed")

    def test_missing_registration_is_retained_without_global_prune(self):
        moved = self.sandbox / "moved-user-work"
        self.target.rename(moved)
        row = self.result(apply=True)
        self.assertEqual(row["decision"], "retained")
        self.assertIn(str(self.target), cleanup.inventory(self.root))
        self.assertTrue(moved.exists())

    def test_foreign_unregistered_and_substituted_git_common_dirs_are_held(self):
        foreign = self.sandbox / "foreign"
        foreign.mkdir()
        self.command(foreign, "init", "-q")
        self.held("not an exact registered", target=foreign)
        (self.target / ".git").write_text("gitdir: " + str(foreign / ".git") + "\n")
        self.held("foreign Git common")

    def test_active_preserved_paths_cover_agents_between_commands(self):
        for path in (self.target, self.target / "future-subdir", self.sandbox):
            with self.subTest(path=path):
                self.repo = cleanup.Repository(self.root, [path])
                self.held("explicitly preserved")

    def test_real_active_process_cwd_is_protected(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], cwd=self.target
        )
        try:
            self.assertIsNone(process.poll())
            self.held("active process")
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_nested_ignored_git_repository_is_not_generated_trash(self):
        nested = self.target / "build" / "user-repo"
        nested.mkdir(parents=True)
        self.command(nested, "init", "-q")
        self.held("nested Git repository")

    def test_mounts_and_uninspectable_processes_block_removal(self):
        with patch.object(cleanup, "mount_paths", return_value=[self.target / "build"]):
            self.held("mount")
        with patch.object(cleanup, "process_cwds", side_effect=cleanup.Retain("process visibility incomplete")):
            self.held("process visibility")

    def test_symlink_target_and_duplicate_targets_are_not_explicit_authority(self):
        alias = self.sandbox / "alias"
        alias.symlink_to(self.target, target_is_directory=True)
        self.held("symlink target", target=alias)
        with self.assertRaises(cleanup.Retain):
            cleanup.cleanup(self.repo, self.api, [self.target, self.target], apply=True)

    def test_open_pr_by_branch_or_head_is_preserved(self):
        self.api.pr["state"] = "open"
        self.held("open PR")
        self.api.pr["head"]["ref"] = "another-branch"
        self.held("open PR")

    def test_closed_unmerged_wrong_base_and_reused_branch_are_held(self):
        self.api.pr["merged"] = False
        self.held("not merged")
        self.api.pr["merged"] = True
        self.api.pr["base"]["ref"] = "integration"
        self.held("not merged into master")
        self.api.pr["base"]["ref"] = "master"
        reused = copy.deepcopy(self.api.pr)
        reused.update(id=2, number=2)
        reused["head"]["sha"] = self.base
        self.api.data["/pulls"].append(reused)
        self.held("branch reused")

    def test_pending_failed_and_latest_rerun_not_hidden_by_success(self):
        for status, conclusion in (("queued", None), ("in_progress", None),
                                   ("completed", "failure"), ("completed", "cancelled")):
            with self.subTest(status=status, conclusion=conclusion):
                self.api.run.update(status=status, conclusion=conclusion, run_attempt=2)
                self.held("latest master workflow")
        self.api.run.update(status="completed", conclusion="success", run_attempt=1)
        newer = self.api.add_run(conclusion="failure")
        self.held("latest master workflow")
        newer["conclusion"] = "success"
        self.api.run.update(run_attempt=3, conclusion="failure",
                            updated_at="2026-01-01T00:03:00Z")
        self.held("attempt 3")

    def test_missing_stale_candidate_only_manual_and_wrong_workflow_proof(self):
        for key, value in (("event", "pull_request"), ("event", "workflow_dispatch"),
                           ("head_branch", "topic"), ("head_sha", self.head),
                           ("workflow_id", 999), ("path", ".github/workflows/other.yml")):
            with self.subTest(key=key, value=value):
                original = self.api.run[key]
                self.api.run[key] = value
                self.held()
                self.api.run[key] = original
        self.api.data["/actions/runs"]["workflow_runs"] = []
        self.held("missing successful automatic master Build")

    def test_manual_success_never_hides_failed_automatic_build(self):
        self.api.add_run(event="workflow_dispatch")
        self.assertEqual(self.result()["decision"], "eligible")
        self.api.run["conclusion"] = "failure"
        self.held("latest master workflow")

    def test_exact_commit_additional_workflow_checks_and_statuses_must_pass(self):
        extra = self.api.add_run(workflow_id=3, path=".github/workflows/extra.yml",
                                 conclusion="failure")
        self.held("latest master workflow")
        extra["conclusion"] = "success"
        check = {**self.api.check, "id": 222, "name": "external",
                 "check_suite": {"id": 222}, "app": {"id": 99, "slug": "external"},
                 "conclusion": "failure"}
        self.api.data[f"/commits/{self.merge}/check-runs"]["check_runs"].append(check)
        self.held("latest check external")
        check["conclusion"] = "success"
        status = {"id": 333, "url": f"https://api.github.com/{PREFIX}/statuses/{self.merge}",
                  "context": "external-status", "state": "pending"}
        self.api.data[f"/commits/{self.merge}/statuses"].append(status)
        self.held("external commit status")
        self.api.data[f"/commits/{self.merge}/statuses"].append({**status, "id": 334, "state": "success"})
        self.assertEqual(self.result()["decision"], "eligible")

    def test_missing_stale_malformed_and_failed_checks_hold(self):
        for key, value in (("head_sha", self.head), ("status", "queued"),
                           ("conclusion", "failure"), ("app", {}),
                           ("check_suite", {"id": 999})):
            with self.subTest(key=key):
                original = self.api.check[key]
                self.api.check[key] = value
                self.held()
                self.api.check[key] = original
        self.api.data[f"/commits/{self.merge}/check-runs"]["check_runs"] = []
        self.held("missing exact master workflow checks")

    def test_foreign_repository_response_and_workflow_detail_drift_are_held(self):
        self.api.data[""]["full_name"] = "another/repository"
        self.held("foreign")
        self.api.data[""]["full_name"] = REPOSITORY
        self.api.data["/actions/runs/10"] = {**self.api.run, "run_attempt": 2}
        self.held("workflow changed")

    def test_historical_green_proof_does_not_depend_on_newer_master_ci(self):
        (self.root / "tracked").write_text("new unrelated master change\n")
        self.command(self.root, "commit", "-qam", "later master")
        newer = self.command(self.root, "rev-parse", "HEAD").strip()
        self.api.data["/git/ref/heads/master"]["object"]["sha"] = newer
        self.assertEqual(self.result(apply=True)["decision"], "removed")
        self.assertFalse(any(newer in request for request in self.api.requests))

    def test_explicit_descendant_proof_and_unrelated_proof_rejection(self):
        (self.root / "tracked").write_text("fix master CI\n")
        self.command(self.root, "commit", "-qam", "fixforward")
        proof = self.command(self.root, "rev-parse", "HEAD").strip()
        self.api.data["/git/ref/heads/master"]["object"]["sha"] = proof
        for suffix in ("check-runs", "statuses"):
            self.api.data[f"/commits/{proof}/{suffix}"] = self.api.data[f"/commits/{self.merge}/{suffix}"]
        self.api.run["head_sha"] = self.api.check["head_sha"] = proof
        self.assertEqual(self.result(proof=proof)["decision"], "eligible")
        self.held("proof not on master", proof=self.base)

    def test_pagination_beyond_first_page_and_duplicate_rejection(self):
        for number in range(2, 105):
            pr = copy.deepcopy(self.api.pr)
            pr.update(id=number, number=number)
            pr["head"] = {"ref": f"old-{number}", "sha": self.base,
                          "repo": {"id": 123, "full_name": "another/fork"}}
            self.api.data["/pulls"].append(pr)
        self.assertEqual(self.result()["decision"], "eligible")
        self.assertTrue(any("/pulls?" in request and "page=2" in request for request in self.api.requests))
        self.api.data["/pulls"].append(copy.deepcopy(self.api.pr))
        self.held("duplicate")

    def test_pagination_shape_total_bound_and_incomplete_pages_fail_closed(self):
        endpoint = PREFIX + "/actions/runs"
        cases = (
            {"total_count": 1, "workflow_runs": []},
            {"total_count": cleanup.MAX_RECORDS + 1, "workflow_runs": []},
            {"total_count": True, "workflow_runs": []},
            {"total_count": 1, "workflow_runs": [{"id": True}]},
            {"workflow_runs": []},
        )
        for response in cases:
            with self.subTest(response=response), patch.object(self.api, "request", return_value=response):
                self.api.clear()
                with self.assertRaises(cleanup.Retain):
                    self.api.pages(endpoint, "workflow_runs")

    def test_apply_revalidates_live_remote_state_not_cached_plan(self):
        clear = self.api.clear
        calls = 0

        def change_after_plan():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.api.run.update(status="queued", conclusion=None, run_attempt=2)
            clear()

        with patch.object(self.api, "clear", side_effect=change_after_plan):
            self.held("latest master workflow")
        self.assertEqual(calls, 2)

    def test_reopened_pr_after_planning_is_preserved(self):
        clear = self.api.clear
        calls = 0

        def reopen():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.api.pr["state"] = "open"
            clear()

        with patch.object(self.api, "clear", side_effect=reopen):
            self.held("open PR")

    def test_even_new_green_rerun_requires_a_fresh_plan(self):
        clear = self.api.clear
        calls = 0

        def rerun():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.api.run["run_attempt"] = 2
            clear()

        with patch.object(self.api, "clear", side_effect=rerun):
            self.held("evidence changed since planning")

    def test_last_moment_dirty_lock_head_and_active_drift_are_rejected(self):
        for drift in ("dirty", "lock", "head", "active"):
            with self.subTest(drift=drift):
                original = self.repo.local_state
                calls = 0
                active = []

                def change_before_remove(path):
                    nonlocal calls
                    calls += 1
                    if calls == 3:
                        if drift == "dirty":
                            (self.target / "notes").write_text("last-moment data\n")
                        elif drift == "lock":
                            self.command(self.root, "worktree", "lock", str(self.target))
                        elif drift == "head":
                            self.command(self.target, "checkout", "-q", "--detach", self.base)
                        else:
                            active.append((987654, self.target))
                    return original(path)

                with patch.object(self.repo, "local_state", side_effect=change_before_remove), \
                     patch.object(cleanup, "process_cwds", side_effect=lambda: list(active)):
                    self.held()
                # Only resetting this test's own fixture; no product path unlocks or forces removal.
                if drift == "dirty":
                    (self.target / "notes").unlink()
                elif drift == "lock":
                    gitdir = Path(self.command(self.target, "rev-parse", "--absolute-git-dir").strip())
                    (gitdir / "locked").unlink()
                elif drift == "head":
                    self.command(self.target, "checkout", "-q", "topic")

    def test_apply_requires_explicit_targets_and_active_workspace_inventory(self):
        for targets, preserve in (([], [self.root]), ([self.target], [])):
            with self.subTest(targets=targets):
                repo = cleanup.Repository(self.root, preserve)
                with self.assertRaises(cleanup.Retain):
                    cleanup.cleanup(repo, self.api, targets, apply=True)
                self.assertTrue(self.target.exists())

    def test_cli_defaults_to_plan_and_reports_retention_exit_status(self):
        with patch.object(cleanup, "GitHub", return_value=self.api):
            output = io.StringIO()
            with redirect_stdout(output):
                code = cleanup.main(["--repository-root", str(self.root), "--target", str(self.target)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["mode"], "dry-run")
            self.api.run["conclusion"] = "failure"
            with redirect_stdout(io.StringIO()):
                code = cleanup.main(["--repository-root", str(self.root), "--target", str(self.target),
                                     "--preserve", str(self.root), "--apply"])
            self.assertEqual(code, 1)
            self.assertTrue(self.target.exists())


class CleanupCatalogTests(unittest.TestCase):
    def test_indexed_case_resolves_to_behavioral_suite_and_procedure(self):
        from scripts.check_docs import _check_registry_document, parse_test_case_registry

        registry, errors = parse_test_case_registry(ROOT)
        self.assertEqual(errors, [])
        case_id = "TC-WORKFLOW-WORKTREE-CLEANUP-001"
        cases = [row for row in registry["cases"] if row["id"] == case_id]
        self.assertEqual(len(cases), 1)
        case = cases[0]
        feature = next(row for row in registry["features"] if row["id"] == case["feature_id"])
        self.assertIn(case_id, feature["required_cases"])
        self.assertIn("https://github.com/laqieer/fireemblem8-expansion/issues/208", case["issue_urls"])
        self.assertEqual(_check_registry_document(ROOT, case["document"], case["anchor"], case_id), [])
        self.assertEqual(_check_registry_document(
            ROOT, "docs/workflow-pilot.md", "completed-worktree-cleanup", case_id
        ), [])
        evidence = case["automation"][0]
        self.assertEqual((ROOT / evidence["evidence"]).resolve(), Path(__file__).resolve())
        self.assertEqual(evidence["command"],
                         "python3 -m unittest scripts.workflow_pilot.tests.test_worktree_cleanup -v")
        for field in ("profiles", "purpose", "prerequisites", "actions", "expected_result",
                      "negative_control", "interactions", "save_compatibility", "cleanup", "limitations"):
            self.assertTrue(case[field], field)


if __name__ == "__main__":
    unittest.main()
