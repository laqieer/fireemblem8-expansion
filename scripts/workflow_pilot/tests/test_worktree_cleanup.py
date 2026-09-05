"""TC-WORKFLOW-WORKTREE-CLEANUP-001: real Git, deterministic GitHub, no live deletion."""

from contextlib import redirect_stdout
import copy
import io
import json
import os
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


@unittest.skipUnless(
    sys.platform == "linux" and Path("/proc/self/mountinfo").is_file(),
    "live cleanup requires Linux /proc process and mount visibility",
)
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

    def command(self, root, *args, input=None):
        self.assertTrue(Path(root).resolve().is_relative_to(self.sandbox))
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(root), *args],
            input=input, env=env, capture_output=True, timeout=60, check=True,
        )
        return os.fsdecode(result.stdout)

    def private_gitdir(self):
        return Path(self.command(self.target, "rev-parse", "--absolute-git-dir").strip())

    def dangling_commit(self):
        tree = self.command(self.root, "rev-parse", "HEAD^{tree}").strip()
        return self.command(self.root, "commit-tree", tree, "-p", self.base,
                            "-m", "recoverable " + uuid.uuid4().hex).strip()

    @staticmethod
    def snapshot(path):
        return {str(item.relative_to(path)): item.read_bytes()
                for item in path.rglob("*") if item.is_file()}

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

    def test_nested_ignored_bare_repository_keeps_its_unique_commit(self):
        nested = self.target / "build" / "archive"
        nested.mkdir(parents=True)
        self.command(nested, "init", "--bare", "-q")
        self.command(nested, "config", "user.name", "Bare fixture")
        self.command(nested, "config", "user.email", "bare@example.invalid")
        tree = self.command(nested, "mktree", input=b"").strip()
        unique = self.command(nested, "commit-tree", tree, "-m", "only in bare repo").strip()
        self.command(nested, "update-ref", "refs/heads/recovery", unique)
        self.assertFalse((nested / ".git").exists())
        with self.assertRaises(subprocess.CalledProcessError):
            self.command(self.root, "cat-file", "-e", unique)
        before = self.snapshot(nested)
        self.held("nested Git")
        self.assertEqual(self.snapshot(nested), before)
        self.assertEqual(self.command(nested, "cat-file", "-t", unique).strip(), "commit")

    def test_detached_commit_in_private_head_reflog_is_preserved(self):
        self.command(self.target, "checkout", "-q", "--detach")
        (self.target / "tracked").write_text("only recoverable from this worktree\n")
        self.command(self.target, "commit", "-qam", "detached recovery")
        unique = self.command(self.target, "rev-parse", "HEAD").strip()
        self.command(self.target, "checkout", "-q", "topic")
        self.assertEqual(self.command(self.target, "status", "--porcelain"), "")
        self.assertEqual(self.command(self.root, "branch", "--contains", unique).strip(), "")
        self.assertNotIn(unique, self.command(self.root, "reflog", "show", "HEAD"))
        before = self.snapshot(self.private_gitdir())
        self.held("private Git recovery")
        self.assertEqual(self.snapshot(self.private_gitdir()), before)
        self.assertIn(unique, self.command(self.target, "reflog", "show", "--format=%H", "HEAD"))

    def test_every_private_reflog_includes_old_and_new_objects(self):
        unique = self.dangling_commit()
        log = self.private_gitdir() / "logs" / "refs" / "worktree" / "recovery"
        log.parent.mkdir(parents=True)
        for old, new in ((unique, self.head), (self.head, unique)):
            with self.subTest(old=old, new=new):
                log.write_text(f"{old} {new} Fixture <fixture@example.invalid> 1 +0000\trecovery\n")
                self.assertEqual(self.result()["decision"], "retained")
                self.held("private Git recovery")
                self.assertTrue(log.exists())

    def test_orig_head_and_every_fetch_head_entry_are_preserved(self):
        unique = self.dangling_commit()
        for name, content in (
            ("ORIG_HEAD", unique + "\n"),
            ("FETCH_HEAD", f"{self.head}\t\tbranch 'topic'\n"
                           f"{unique}\tnot-for-merge\tbranch 'recovery'\n"),
            ("OTHER_RECOVERY", unique + "\n"),
        ):
            with self.subTest(name=name):
                marker = self.private_gitdir() / name
                marker.write_text(content)
                try:
                    self.assertEqual(self.result()["decision"], "retained")
                    self.held("private Git recovery")
                    self.assertEqual(marker.read_text(), content)
                finally:
                    marker.unlink(missing_ok=True)

    def test_git_created_pseudorefs_preserve_unique_recovery(self):
        unique = self.dangling_commit()
        for name in ("ORIG_HEAD", "AUTO_MERGE"):
            with self.subTest(name=name):
                self.command(self.target, "update-ref", name, unique)
                marker = self.private_gitdir() / name
                self.assertEqual(marker.read_text().strip(), unique)
                try:
                    self.assertEqual(self.result()["decision"], "retained")
                    self.held("private Git recovery")
                finally:
                    marker.unlink(missing_ok=True)

    def test_shared_refs_not_common_reflogs_supply_durable_recovery(self):
        unique = self.dangling_commit()
        log = self.repo.common / "logs" / "HEAD"
        with log.open("a") as output:
            output.write(f"{unique} {self.merge} Fixture <fixture@example.invalid> 1 +0000\trecovery\n")
        marker = self.private_gitdir() / "ORIG_HEAD"
        marker.write_text(unique + "\n")
        self.held("private Git recovery")
        tree = self.command(self.root, "rev-parse", "HEAD^{tree}").strip()
        descendant = self.command(self.root, "commit-tree", tree, "-p", unique,
                                  "-m", "durable descendant").strip()
        self.command(self.root, "update-ref", "refs/heads/saved-recovery", descendant)
        self.assertEqual(self.result(apply=True)["decision"], "removed")
        self.assertEqual(self.command(self.root, "rev-parse", "saved-recovery").strip(), descendant)
        self.command(self.root, "merge-base", "--is-ancestor", unique, descendant)

    def test_noncommit_recovery_objects_need_durable_shared_refs_too(self):
        blob = self.command(self.root, "hash-object", "-w", "--stdin", input=b"recovery data").strip()
        (self.private_gitdir() / "AUTO_MERGE").write_text(blob + "\n")
        self.held("private Git recovery")
        self.command(self.root, "update-ref", "refs/tags/saved-object", blob)
        self.assertEqual(self.result(apply=True)["decision"], "removed")
        self.assertEqual(self.command(self.root, "cat-file", "blob", blob), "recovery data")

    def test_malformed_symlinked_or_missing_recovery_evidence_is_retained(self):
        marker = self.private_gitdir() / "ORIG_HEAD"
        for data in ("not a ref\n", "f" * 40 + "\n", self.head + "\n" + self.base + "\n"):
            with self.subTest(data=data):
                marker.write_text(data)
                self.held()
        marker.unlink()
        outside = self.sandbox / "outside-recovery"
        outside.write_text(self.head + "\n")
        marker.symlink_to(outside)
        self.held("not a regular file")
        self.assertEqual(outside.read_text(), self.head + "\n")
        marker.unlink()
        marker.write_text("ref: refs/heads/topic\n")
        self.assertEqual(self.result()["decision"], "eligible")
        marker.unlink()
        fetch = self.private_gitdir() / "FETCH_HEAD"
        fetch.write_text(f"{self.head}\t\tvalid\nmalformed second entry\n")
        self.held("malformed private Git recovery FETCH_HEAD")

    def test_recovery_changes_are_checked_after_plan_and_immediately_before_remove(self):
        unique = self.dangling_commit()
        marker = self.private_gitdir() / "FETCH_HEAD"
        for stage in (2, 3):
            with self.subTest(stage=stage):
                original = self.repo.local_state
                calls = 0

                def change(path):
                    nonlocal calls
                    calls += 1
                    if calls == stage:
                        marker.write_text(f"{self.head}\t\tcompleted\n"
                                          f"{unique}\tnot-for-merge\trecoverable\n")
                    return original(path)

                with patch.object(self.repo, "local_state", side_effect=change):
                    self.held("private Git recovery")
                self.assertEqual(calls, stage)
                self.assertIn(unique, marker.read_text())
                marker.unlink()

    def test_nested_git_metadata_arriving_after_fresh_assessment_is_retained(self):
        original = self.repo.local_state
        calls = 0
        nested = self.target / "build" / "late-bare"

        def change(path):
            nonlocal calls
            calls += 1
            if calls == 3:
                nested.mkdir(parents=True)
                self.command(nested, "init", "--bare", "-q")
            return original(path)

        with patch.object(self.repo, "local_state", side_effect=change):
            self.held("nested Git")
        self.assertEqual(calls, 3)
        self.assertTrue((nested / "HEAD").exists())

    def test_promisor_dry_run_never_fetches_missing_proof_objects(self):
        remote = self.sandbox / "promisor.git"
        self.command(self.root, "clone", "--bare", "--no-local", "-q", str(self.root), str(remote))
        self.command(remote, "config", "user.name", "Promisor fixture")
        self.command(remote, "config", "user.email", "promisor@example.invalid")
        self.command(remote, "config", "uploadpack.allowFilter", "true")
        tree = self.command(remote, "rev-parse", "HEAD^{tree}").strip()
        proof = self.command(remote, "commit-tree", tree, "-p", self.merge, "-m", "later proof").strip()
        self.command(remote, "update-ref", "refs/heads/master", proof)
        with self.assertRaises(subprocess.CalledProcessError):
            self.command(self.root, "cat-file", "-e", proof)
        self.command(self.root, "remote", "add", "local-promisor", remote.as_uri())
        self.command(self.root, "config", "remote.local-promisor.promisor", "true")
        self.command(self.root, "config", "remote.local-promisor.partialclonefilter", "blob:none")
        self.api.data["/git/ref/heads/master"]["object"]["sha"] = proof
        self.api.run["head_sha"] = self.api.check["head_sha"] = proof
        for suffix in ("check-runs", "statuses"):
            self.api.data[f"/commits/{proof}/{suffix}"] = self.api.data[f"/commits/{self.merge}/{suffix}"]
        before = self.snapshot(self.repo.common)
        row = self.result(proof=proof)
        self.assertEqual(self.snapshot(self.repo.common), before,
                         "dry-run changed shared objects, pack/index/promisor files, refs, or metadata")
        self.assertEqual(row["decision"], "retained", row)
        self.assertIn("promisor", " ".join(row["reasons"]))
        self.assertTrue(self.target.exists())

    def test_promisor_settings_are_rejected_before_initial_or_later_git_commands(self):
        for key, value in (("remote.local.promisor", "true"),
                           ("remote.local.promisor", "false"),
                           ("remote.local.partialclonefilter", "blob:none"),
                           ("extensions.partialClone", "local")):
            with self.subTest(key=key, value=value):
                self.command(self.root, "config", key, value)
                before = self.snapshot(self.repo.common)
                try:
                    with self.assertRaisesRegex(cleanup.Retain, "promisor"):
                        cleanup.Repository(self.root, [self.root])
                    with self.assertRaisesRegex(cleanup.Retain, "promisor"):
                        cleanup.git(self.root, "cat-file", "-t", self.head)
                    self.held("promisor")
                    self.assertEqual(self.snapshot(self.repo.common), before)
                finally:
                    self.command(self.root, "config", "--unset", key)

        included = self.sandbox / "partial-config"
        included.write_text('[remote "local"]\n\tpromisor = true\n')
        self.command(self.root, "config", "include.path", str(included))
        self.held("promisor")
        self.command(self.root, "config", "--unset", "include.path")
        self.command(self.root, "config", "extensions.worktreeConfig", "true")
        self.command(self.target, "config", "--worktree", "remote.local.promisor", "true")
        self.held("promisor")

    def test_promisor_configuration_arriving_after_plan_is_retained(self):
        original = self.repo.local_state
        calls = 0

        def change(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.command(self.root, "config", "remote.local.promisor", "true")
            return original(path)

        with patch.object(self.repo, "local_state", side_effect=change):
            self.held("promisor")
        self.assertEqual(calls, 2)

    def test_missing_nonpromisor_history_does_not_mutate_git_metadata(self):
        self.api.data["/git/ref/heads/master"]["object"]["sha"] = "f" * 40
        before = self.snapshot(self.repo.common)
        self.held("exit 128")
        self.assertEqual(self.snapshot(self.repo.common), before)
        with self.assertRaisesRegex(cleanup.Retain, "exit 1.*no stderr output"):
            cleanup.git(self.root, "merge-base", "--is-ancestor", self.merge, self.base)

    def test_common_generated_graphics_are_source_backed_not_suffix_wildcards(self):
        source = self.target / "graphics" / "fixture.png"
        source.parent.mkdir()
        source.write_bytes(b"tracked source for the generated-path contract")
        with (self.target / ".gitignore").open("a") as output:
            output.write("*.4bpp\n*.fk\n*.lz\n*.feimg*.bin\n*.fetsa*.bin\n")
        self.command(self.target, "add", ".")
        self.command(self.target, "commit", "-qm", "asset source")
        self.head = self.command(self.target, "rev-parse", "HEAD").strip()
        self.command(self.root, "merge", "-q", "--no-ff", "topic", "-m", "merge assets")
        self.merge = self.command(self.root, "rev-parse", "HEAD").strip()
        self.api = Responses(self.root, self.head, self.merge)
        products = ["fixture.4bpp.fk", "fixture.4bpp.fk.lz"]
        products += [f"fixture.{kind}{number}.bin{compression}"
                     for kind in ("feimg", "fetsa") for number in range(1, 5)
                     for compression in ("", ".lz", ".fk")]
        for name in products:
            (source.parent / name).write_bytes(b"disposable output")
        row = self.result()
        self.assertEqual(row["decision"], "eligible", row)
        for name in ("unknown.4bpp.fk", "unknown.feimg1.bin", "fixture.feimg0.bin",
                     "fixture.feimg5.bin", "fixture.fetsa01.bin", "notes.fk"):
            with self.subTest(name=name):
                unknown = source.parent / name
                unknown.write_bytes(b"not a proven build output")
                self.held("ignored non-build/local data")
                self.assertTrue(unknown.exists())
                unknown.unlink()
        self.assertEqual(self.result(apply=True)["decision"], "removed")

    def test_mounts_and_uninspectable_processes_block_removal(self):
        with patch.object(cleanup, "mount_paths", return_value=[self.target / "build"]):
            self.held("mount")
        with patch.object(cleanup, "process_cwds", side_effect=cleanup.Retain("process visibility incomplete")):
            self.held("process visibility")
        with patch.object(cleanup, "mount_paths", return_value=[self.private_gitdir() / "logs"]):
            self.held("mounted private Git metadata")

    def test_uninitialized_submodule_index_is_retained_without_running_its_git(self):
        self.command(self.target, "update-index", "--add", "--cacheinfo",
                     "160000," + self.base + ",uninitialized-submodule")
        self.command(self.target, "commit", "-qm", "gitlink")
        self.held("submodule index")

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

    def test_cli_accepts_uppercase_proof_without_weakening_remote_identities(self):
        with patch.object(cleanup, "GitHub", return_value=self.api):
            output = io.StringIO()
            with redirect_stdout(output):
                code = cleanup.main(["--repository-root", str(self.root), "--target", str(self.target),
                                     "--proof-sha", self.merge.upper()])
        self.assertEqual(code, 0)
        row = json.loads(output.getvalue())["results"][0]
        self.assertEqual(row["decision"], "eligible", row)
        self.assertEqual(row["proof"]["ci"]["sha"], self.merge)
        self.api.data["/git/ref/heads/master"]["object"]["sha"] = self.merge.upper()
        self.held("invalid commit identity")
        with self.assertRaises(cleanup.Retain):
            cleanup.cleanup(self.repo, self.api, [self.target], proof_sha=self.merge.upper())


class CleanupCommandTests(unittest.TestCase):
    def test_unsupported_platform_checks_fail_closed(self):
        with patch.object(cleanup.sys, "platform", "darwin"):
            for check in (cleanup.process_cwds, cleanup.mount_paths):
                with self.subTest(check=check.__name__), self.assertRaisesRegex(cleanup.Retain, "Linux /proc"):
                    check()

    def test_command_failure_reports_exit_code_when_stderr_is_empty(self):
        with self.assertRaisesRegex(cleanup.Retain, "exit 7.*no stderr output"):
            cleanup.execute([sys.executable, "-c", "raise SystemExit(7)"], ROOT)

    def test_git_environment_cannot_reenable_mutating_defaults(self):
        with patch.dict(os.environ, {"GIT_DIR": "/not-the-repository", "GIT_NO_LAZY_FETCH": "0",
                                    "GIT_OPTIONAL_LOCKS": "1", "GIT_NO_REPLACE_OBJECTS": "0"}):
            result = cleanup.execute(
                [sys.executable, "-c", "import json, os; print(json.dumps("
                 "{k: v for k, v in os.environ.items() if k.startswith('GIT_')}))"], ROOT
            )
        self.assertEqual(json.loads(result), {
            "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0",
        })


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
