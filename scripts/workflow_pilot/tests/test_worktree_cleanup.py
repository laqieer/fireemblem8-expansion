"""TC-WORKFLOW-WORKTREE-CLEANUP-001: real Git, deterministic GitHub, no live deletion."""

from contextlib import ExitStack, contextmanager, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
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
        # Cover newer Git's ref-directory layout even on an older fixture host.
        for namespace in ("heads", "tags"):
            (self.private_gitdir() / "refs" / namespace).mkdir(parents=True, exist_ok=True)
        (self.target / "tracked").write_text("completed feature\n")
        self.command(self.target, "commit", "-qam", "feature")
        self.head = self.command(self.target, "rev-parse", "HEAD").strip()
        self.command(self.root, "merge", "-q", "--no-ff", "topic", "-m", "merge")
        self.merge = self.command(self.root, "rev-parse", "HEAD").strip()
        self.command(self.root, "remote", "add", "origin", "https://github.com/" + REPOSITORY + ".git")
        self.repo = cleanup.Repository(self.root, [self.root])
        self.api = Responses(self.root, self.head, self.merge)
        self.process_ids = {os.getpid()}
        iterdir = Path.iterdir

        def owned_processes(path):
            if path == Path("/proc"):
                return iter(path / str(pid) for pid in sorted(self.process_ids))
            return iterdir(path)

        self.proc_inventory = patch.object(Path, "iterdir", owned_processes)
        self.proc_inventory.start()
        self.addCleanup(self.proc_inventory.stop)

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

    def completed_empty_gitlinks(self, *names):
        paths = []
        for number, name in enumerate(names or ("tools/empty-submodule", "vendor/second-submodule")):
            self.command(self.target, "config", "--file", ".gitmodules",
                         f"submodule.fixture-{number}.path", name)
            self.command(self.target, "config", "--file", ".gitmodules",
                         f"submodule.fixture-{number}.url", self.root.as_uri())
            self.command(self.target, "update-index", "--add", "--cacheinfo",
                         "160000", (self.base, self.head)[number % 2], name)
            path = self.target / name
            path.mkdir(parents=True)
            paths.append(path)
        self.command(self.target, "add", ".gitmodules")
        self.command(self.target, "commit", "-qm", "empty gitlink fixture")
        self.head = self.command(self.target, "rev-parse", "HEAD").strip()
        self.command(self.root, "merge", "-q", "--no-ff", "topic", "-m", "merge gitlinks")
        self.merge = self.command(self.root, "rev-parse", "HEAD").strip()
        self.api = Responses(self.root, self.head, self.merge)
        return paths

    def dangling_commit(self):
        tree = self.command(self.root, "rev-parse", "HEAD^{tree}").strip()
        return self.command(self.root, "commit-tree", tree, "-p", self.base,
                            "-m", "recoverable " + uuid.uuid4().hex).strip()

    def resolve_undo(self, blobs=None, mode="100644"):
        if blobs is None:
            blobs = [self.command(self.root, "hash-object", "-w", "--stdin",
                                  input=uuid.uuid4().bytes).strip() for _ in range(3)]
        self.command(self.target, "update-index", "--clear-resolve-undo")
        entries = ["0 " + "0" * 40 + "\ttracked\0"]
        entries += [f"{mode} {oid} {stage}\ttracked\0"
                    for stage, oid in enumerate(blobs, 1)]
        self.command(self.target, "update-index", "-z", "--index-info",
                     input="".join(entries).encode("ascii"))
        self.command(self.target, "add", "tracked")
        self.assertEqual(self.command(self.target, "status", "--porcelain"), "")
        records = self.command(self.target, "ls-files", "--resolve-undo", "-z")
        self.assertTrue(all(oid in records for oid in blobs), records)
        return blobs

    def append_index_extension(self, signature=b"ZRCV", content=b"unique recovery state"):
        index = self.private_gitdir() / "index"
        data = index.read_bytes()[:-20] + signature + len(content).to_bytes(4, "big") + content
        index.write_bytes(data + hashlib.sha1(data, usedforsecurity=False).digest())

    def cli_report(self, *, apply=False):
        arguments = ["--repository-root", str(self.root), "--target", str(self.target),
                     "--preserve", str(self.root)]
        if apply:
            arguments.append("--apply")
        output = io.BytesIO()
        with io.TextIOWrapper(output, encoding="ascii", errors="strict") as text:
            with redirect_stdout(text), patch.object(cleanup, "GitHub", return_value=self.api):
                try:
                    code = cleanup.main(arguments)
                except UnicodeError as error:
                    self.fail(f"filesystem bytes must not crash JSON reporting: {error}")
            text.flush()
            report = json.loads(output.getvalue())
        self.assertEqual(os.fsencode(report["results"][0]["path"]), os.fsencode(self.target))
        return code, report["results"][0]

    def write_mount_records(self, *paths):
        data = []
        for number, path in enumerate(paths, 100):
            field = os.fsencode(path)
            for raw, escaped in ((b"\\", b"\\134"), (b" ", b"\\040"),
                                 (b"\t", b"\\011"), (b"\n", b"\\012")):
                field = field.replace(raw, escaped)
            data.append(f"{number} 1 0:1 / ".encode() + field + b" rw - tmpfs tmpfs rw\n")
        fixture = self.sandbox / "mountinfo"
        fixture.write_bytes(b"".join(data))
        return fixture

    def mount_records(self, *paths):
        fixture = self.write_mount_records(*paths)
        original = Path.open

        def read_mounts(path, *args, **kwargs):
            return original(fixture if path == Path("/proc/self/mountinfo") else path,
                            *args, **kwargs)

        return patch.object(Path, "open", read_mounts)

    @contextmanager
    def external_target_accesses(self, alias, outside):
        accesses = []
        originals = {name: getattr(os, name) for name in ("stat", "lstat", "open", "scandir")}
        readlink = os.readlink

        def pathname(value, dir_fd=None):
            if isinstance(value, int):
                return Path(readlink(f"/proc/self/fd/{value}"))
            value = Path(os.fsdecode(value))
            if not value.is_absolute():
                base = pathname(dir_fd) if dir_fd is not None else Path.cwd()
                value = base / value
            return value

        def observe(name):
            def call(value, *args, **kwargs):
                path = pathname(value, kwargs.get("dir_fd"))
                try:
                    redirected = stat.S_ISLNK(originals["lstat"](alias).st_mode)
                except FileNotFoundError:
                    redirected = False
                if path.is_relative_to(outside) or (
                    redirected and path.is_relative_to(alias) and (
                        path != alias or name == "scandir"
                        or (name == "stat" and kwargs.get("follow_symlinks", True))
                    )
                ):
                    accesses.append((name, str(path)))
                result = originals[name](value, *args, **kwargs)
                if name == "open" and pathname(result).is_relative_to(outside):
                    accesses.append(("opened-external-target", str(pathname(result))))
                return result
            return call

        with ExitStack() as stack:
            for name in originals:
                stack.enter_context(patch.object(os, name, side_effect=observe(name)))
            yield accesses

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
            self.process_ids.add(process.pid)
            self.assertIsNone(process.poll())
            self.held("active process")
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_uninspectable_owned_process_cwd_is_retained(self):
        readlink = Path.readlink

        def inaccessible(path):
            if path == Path("/proc") / str(os.getpid()) / "cwd":
                raise PermissionError("test-owned process is uninspectable")
            return readlink(path)

        with patch.object(Path, "readlink", inaccessible):
            self.held("cannot inspect same-owner process")

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

    def test_every_resolve_undo_stage_needs_durable_shared_reachability(self):
        for missing in range(3):
            blobs = self.resolve_undo()
            reachable = self.command(self.root, "rev-list", "--objects", "--all")
            self.assertTrue(all(oid not in reachable for oid in blobs))
            for stage, oid in enumerate(blobs):
                if stage != missing:
                    self.command(self.root, "update-ref", f"refs/tags/saved-{missing}-{stage}", oid)
            before = self.snapshot(self.private_gitdir())
            row = self.result()
            self.assertEqual(row["decision"], "retained", (missing + 1, row))
            self.held("private Git recovery")
            self.assertEqual(self.snapshot(self.private_gitdir()), before)

    def test_shared_commit_ancestry_preserves_resolve_undo_blobs_after_removal(self):
        blobs = self.resolve_undo()
        entries = "".join(f"100644 blob {oid}\tstage-{stage}\n"
                          for stage, oid in enumerate(blobs, 1))
        tree = self.command(self.root, "mktree", input=entries.encode("ascii")).strip()
        saved = self.command(self.root, "commit-tree", tree, "-p", self.base,
                             "-m", "durable conflict recovery").strip()
        descendant = self.command(self.root, "commit-tree", tree, "-p", saved,
                                  "-m", "recovery descendant").strip()
        self.command(self.root, "update-ref", "refs/heads/saved-recovery", descendant)
        self.assertEqual(self.result()["decision"], "eligible")
        self.assertEqual(self.result(apply=True)["decision"], "removed")
        self.assertFalse(self.target.exists())
        reachable = self.command(self.root, "rev-list", "--objects", "saved-recovery")
        self.assertTrue(all(oid in reachable for oid in blobs))
        self.assertEqual(self.command(self.root, "rev-parse", "topic").strip(), self.head)

    def test_resolve_undo_drift_after_plan_and_before_removal_is_preserved(self):
        for stage in (2, 3):
            original = self.repo.local_state
            calls, observed = 0, {}

            def change(path):
                nonlocal calls
                calls += 1
                if calls == stage:
                    self.resolve_undo()
                    observed.update(self.snapshot(self.private_gitdir()))
                return original(path)

            with patch.object(self.repo, "local_state", side_effect=change):
                self.held("private Git recovery")
            self.assertEqual(calls, stage)
            self.assertEqual(self.snapshot(self.private_gitdir()), observed)
            self.command(self.target, "update-index", "--clear-resolve-undo")

    def test_resolve_undo_mode_drift_is_not_hidden_by_unchanged_object_set(self):
        blobs = [self.command(self.root, "rev-parse", f"{ref}:tracked").strip()
                 for ref in (self.base, self.head, self.merge)]
        for stage in (2, 3):
            self.resolve_undo(blobs)
            original = self.repo.local_state
            calls = 0

            def change(path):
                nonlocal calls
                calls += 1
                if calls == stage:
                    self.resolve_undo(blobs, mode="100755")
                return original(path)

            with patch.object(self.repo, "local_state", side_effect=change):
                self.held("drift" if stage == 3 else "evidence changed")
            self.assertEqual(calls, stage)
            self.assertIn("100755", self.command(self.target, "ls-files", "--resolve-undo"))

    def test_private_configuration_is_preserved_enabled_disabled_and_empty(self):
        self.command(self.root, "config", "extensions.worktreeConfig", "true")
        self.command(self.target, "config", "--worktree", "fixture.unique", "only-local")
        config = self.private_gitdir() / "config.worktree"
        self.assertEqual(self.command(self.target, "config", "--worktree",
                                     "--get", "fixture.unique").strip(), "only-local")
        for state in ("enabled", "disabled", "empty"):
            if state == "disabled":
                self.command(self.root, "config", "--unset", "extensions.worktreeConfig")
            elif state == "empty":
                config.write_bytes(b"")
            before = self.snapshot(self.private_gitdir())
            row = self.result()
            self.assertEqual(row["decision"], "retained", (state, row))
            self.held("config.worktree")
            self.assertEqual(self.snapshot(self.private_gitdir()), before)

    def test_private_metadata_family_and_edit_buffers_are_not_disposable(self):
        gitdir = self.private_gitdir()
        original_message = (gitdir / "COMMIT_EDITMSG").read_bytes()
        for name in ("index.backup", "index.lock", "config.worktree.lock", "description",
                     "info/exclude", "hooks/pre-commit", "rr-cache/conflict/preimage",
                     "COMMIT_EDITMSG", "MERGE_MSG", "SQUASH_MSG", "TAG_EDITMSG", "NOTES_EDITMSG"):
            file = gitdir / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes((gitdir / "index").read_bytes() if name == "index.backup"
                             else b"unique private configuration or recovery data\n")
            before = self.snapshot(gitdir)
            row = self.result()
            self.assertEqual(row["decision"], "retained", (name, row))
            self.held(name.split("/")[0])
            self.assertEqual(self.snapshot(gitdir), before)
            if name == "COMMIT_EDITMSG":
                file.write_bytes(original_message)
            elif "/" in name:
                shutil.rmtree(gitdir / name.split("/")[0])
            else:
                file.unlink()

    def test_empty_private_ref_containers_are_reconstructible(self):
        gitdir = self.private_gitdir()
        (gitdir / "refs" / "worktree" / "empty").mkdir(parents=True)
        before = self.snapshot(gitdir)
        shared = self.command(self.root, "show-ref")
        row = self.result()
        self.assertEqual(row["decision"], "eligible", row)
        self.assertEqual(self.snapshot(gitdir), before)
        self.assertTrue((gitdir / "refs" / "heads").is_dir())
        self.assertTrue((gitdir / "refs" / "tags").is_dir())
        self.assertEqual(self.result(apply=True)["decision"], "removed")
        self.assertEqual(self.command(self.root, "show-ref"), shared)

    def test_private_ref_files_are_preserved_even_when_shared_refs_retain_objects(self):
        gitdir = self.private_gitdir()
        for name in ("heads/local", "tags/local", "worktree/local", "unknown/local"):
            with self.subTest(name=name):
                file = gitdir / "refs" / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(self.head + "\n")
                before = self.snapshot(gitdir)
                self.held("private")
                self.assertEqual(self.snapshot(gitdir), before)
                file.unlink()
        self.assertEqual(self.result()["decision"], "eligible")

    def test_symlinked_private_ref_containers_are_not_followed(self):
        refs = self.private_gitdir() / "refs"
        outside = self.sandbox / "external-refs"
        outside.mkdir()
        (outside / "private-data").write_text("unique data\n")
        before = self.snapshot(outside)
        for name in ("", "heads", "unknown"):
            with self.subTest(name=name):
                path = refs / name if name else refs
                backup = self.sandbox / "fixture-refs-backup"
                existed = path.exists()
                if existed:
                    path.rename(backup)
                path.symlink_to(outside, target_is_directory=True)
                self.held()
                self.assertTrue(path.is_symlink())
                self.assertEqual(self.snapshot(outside), before)
                path.unlink()
                if existed:
                    backup.rename(path)

    def test_private_ref_data_arriving_after_plan_or_before_remove_is_preserved(self):
        file = self.private_gitdir() / "refs" / "heads" / "late"
        for stage in (2, 3):
            with self.subTest(stage=stage):
                original = self.repo.local_state
                calls = 0

                def change(path):
                    nonlocal calls
                    calls += 1
                    if calls == stage:
                        file.write_text(self.head + "\n")
                    return original(path)

                with patch.object(self.repo, "local_state", side_effect=change):
                    self.held("private")
                self.assertEqual(calls, stage)
                self.assertEqual(file.read_text(), self.head + "\n")
                file.unlink()

    def test_overbound_private_ref_container_inventory_is_preserved(self):
        refs = self.private_gitdir() / "refs"
        for number in range(cleanup.MAX_RECORDS):
            (refs / str(number)).mkdir()
        before = set(refs.iterdir())
        self.held("private Git reference inventory exceeds safety bound")
        self.assertEqual(set(refs.iterdir()), before)

    def test_symlinked_private_configuration_and_index_keep_external_data(self):
        gitdir = self.private_gitdir()
        outside = self.sandbox / "private-data"
        outside.write_bytes((gitdir / "index").read_bytes())
        for name in ("config.worktree", "index"):
            file = gitdir / name
            original = file.read_bytes() if file.exists() else None
            if original is not None:
                file.unlink()
            file.symlink_to(outside)
            before = outside.read_bytes()
            row = self.result()
            self.assertEqual(row["decision"], "retained", (name, row))
            self.held()
            self.assertTrue(file.is_symlink())
            self.assertEqual(outside.read_bytes(), before)
            file.unlink()
            if original is not None:
                file.write_bytes(original)

    def test_split_index_base_is_preserved(self):
        self.command(self.target, "update-index", "--split-index")
        gitdir = self.private_gitdir()
        self.assertTrue(list(gitdir.glob("sharedindex.*")))
        before = self.snapshot(gitdir)
        row = self.result()
        self.assertEqual(row["decision"], "retained", row)
        self.held()
        self.assertEqual(self.snapshot(gitdir), before)

    def test_unknown_optional_index_extension_is_not_silently_discarded(self):
        self.append_index_extension()
        self.assertEqual(self.command(self.target, "--no-optional-locks", "status", "--porcelain"), "")
        before = self.snapshot(self.private_gitdir())
        row = self.result()
        self.assertEqual(row["decision"], "retained", row)
        self.held("index extension")
        self.assertEqual(self.snapshot(self.private_gitdir()), before)

    def test_malformed_resolve_undo_metadata_is_not_silently_discarded(self):
        index = self.private_gitdir() / "index"
        original = index.read_bytes()
        for content in (b"tracked\0" + b"100644\0" * 3 + b"\x01" * 59,
                        b"tracked\0" + b"invalid\0" * 3 + b"\x01" * 60,
                        b"unterminated-path"):
            self.append_index_extension(b"REUC", content)
            before = self.snapshot(self.private_gitdir())
            row = self.result()
            self.assertEqual(row["decision"], "retained", row)
            self.held()
            self.assertEqual(self.snapshot(self.private_gitdir()), before)
            index.write_bytes(original)

    def test_index_checksum_and_extension_bounds_are_validated_before_eligibility(self):
        index = self.private_gitdir() / "index"
        original = index.read_bytes()
        malformed = (
            original[:-1] + bytes([original[-1] ^ 0xff]),
            b"DIRC" + (5).to_bytes(4, "big") + original[8:],
            original[:-20] + b"REUC" + (1000).to_bytes(4, "big") + b"x",
            original[:-20] + b"REU",
        )
        for data in malformed:
            if data not in malformed[:2]:
                data += hashlib.sha1(data, usedforsecurity=False).digest()
            index.write_bytes(data)
            before = self.snapshot(self.private_gitdir())
            row = self.result()
            self.assertEqual(row["decision"], "retained", row)
            self.held()
            self.assertEqual(self.snapshot(self.private_gitdir()), before)
        index.write_bytes(original)

    def test_private_metadata_family_drift_after_plan_and_before_removal(self):
        gitdir = self.private_gitdir()
        for name in ("config.worktree", "index.backup", "COMMIT_EDITMSG", "index"):
            for stage in (2, 3):
                file = gitdir / name
                before = file.read_bytes() if file.exists() else None
                original = self.repo.local_state
                calls, observed = 0, {}

                def change(path):
                    nonlocal calls
                    calls += 1
                    if calls == stage:
                        if name == "index":
                            self.append_index_extension()
                        else:
                            file.write_bytes(b"last-moment private data\n")
                        observed.update(self.snapshot(gitdir))
                    return original(path)

                with patch.object(self.repo, "local_state", side_effect=change):
                    self.held()
                self.assertEqual(calls, stage)
                self.assertEqual(self.snapshot(gitdir), observed)
                if before is None:
                    file.unlink()
                else:
                    file.write_bytes(before)

    def test_supported_index_versions_and_reconstructible_caches_remain_eligible(self):
        for name in ("a" * 180, "b" * 180, "prefix-" + "c" * 130 + "-1",
                     "prefix-" + "c" * 130 + "-2", os.fsdecode(b"path-\xff")):
            (self.target / name).write_bytes(b"committed index-path fixture")
        self.command(self.target, "add", ".")
        self.command(self.target, "commit", "-qm", "index paths")
        self.head = self.command(self.target, "rev-parse", "HEAD").strip()
        self.command(self.root, "merge", "-q", "--no-ff", "topic", "-m", "merge index paths")
        self.merge = self.command(self.root, "rev-parse", "HEAD").strip()
        self.api = Responses(self.root, self.head, self.merge)
        for version in (2, 3, 4):
            self.command(self.target, "update-index", "--index-version", str(version))
            self.command(self.target, "update-index", "--untracked-cache")
            if version == 3:
                self.command(self.target, "update-index", "--skip-worktree", "tracked")
                index = (self.private_gitdir() / "index").read_bytes()
                self.assertEqual(int.from_bytes(index[4:8], "big"), 3)
                cleanup.index_extensions(index)
                self.held("skip-worktree")
                self.command(self.target, "update-index", "--no-skip-worktree", "tracked")
            before = self.snapshot(self.private_gitdir())
            row = self.result()
            self.assertEqual(row["decision"], "eligible", (version, row))
            self.assertEqual(self.snapshot(self.private_gitdir()), before)
        self.assertEqual(self.result(apply=True)["decision"], "removed")

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

    def test_graphics_products_require_their_actual_source_format(self):
        graphics = self.target / "graphics"
        graphics.mkdir()
        for name in ("image.png", "palette.pal", "raw-palette.agbpal"):
            (graphics / name).write_bytes(b"tracked source-format ownership fixture")
        with (self.target / ".gitignore").open("a") as output:
            output.write("*.4bpp\n*.8bpp\n*.gbapal\n*.4bpp.h\n*.8bpp.h\n*.fk\n*.lz\n")
        self.command(self.target, "add", ".")
        self.command(self.target, "commit", "-qm", "distinct graphics source formats")
        self.head = self.command(self.target, "rev-parse", "HEAD").strip()
        self.command(self.root, "merge", "-q", "--no-ff", "topic", "-m", "merge formats")
        self.merge = self.command(self.root, "rev-parse", "HEAD").strip()
        self.api = Responses(self.root, self.head, self.merge)
        for name in ("image.4bpp", "image.8bpp", "image.gbapal", "image.4bpp.h", "palette.gbapal"):
            for compression in ("", ".lz", ".fk", ".fk.lz"):
                (graphics / (name + compression)).write_bytes(b"generated graphics")
        self.assertEqual(self.result()["decision"], "eligible")
        for name in ("palette.4bpp", "palette.8bpp", "palette.4bpp.h",
                     "raw-palette.4bpp", "raw-palette.8bpp", "raw-palette.gbapal",
                     "raw-palette.4bpp.h", "image.8bpp.h"):
            for compression in ("", ".lz", ".fk", ".fk.lz"):
                with self.subTest(name=name, compression=compression):
                    file = graphics / (name + compression)
                    file.write_bytes(b"unrelated ignored graphics data")
                    self.held("ignored non-build/local data")
                    self.assertEqual(file.read_bytes(), b"unrelated ignored graphics data")
                    file.unlink()
        self.assertEqual(self.result(apply=True)["decision"], "removed")

    def test_mounts_and_uninspectable_processes_block_removal(self):
        with patch.object(cleanup, "mount_paths", return_value=[self.target / "build"]):
            self.held("mount")
        with patch.object(cleanup, "process_cwds", side_effect=cleanup.Retain("process visibility incomplete")):
            self.held("process visibility")
        with patch.object(cleanup, "mount_paths", return_value=[self.private_gitdir() / "logs"]):
            self.held("mounted private Git metadata")

    def test_non_utf8_target_and_git_backlink_round_trip_through_ascii_json(self):
        target = self.sandbox / os.fsdecode(b"completed-\xff-\xc2\xa0")
        self.command(self.root, "worktree", "move", str(self.target), str(target))
        self.target = target
        backlink = self.private_gitdir() / "gitdir"
        self.assertEqual(backlink.read_bytes(), os.fsencode(target / ".git") + b"\n")
        code, row = self.cli_report()
        self.assertEqual((code, row["decision"]), (0, "eligible"), row)
        code, row = self.cli_report(apply=True)
        self.assertEqual((code, row["decision"]), (0, "removed"), row)
        self.assertFalse(target.exists())

    def test_non_utf8_ignored_filename_is_reported_losslessly_and_preserved(self):
        file = self.target / os.fsdecode(b"notes-\xfe.sav")
        file.write_bytes(b"irreplaceable save")
        code, row = self.cli_report(apply=True)
        self.assertEqual((code, row["decision"]), (1, "retained"), row)
        self.assertIn(os.fsencode(file.name), os.fsencode(row["reasons"][0]))
        self.assertEqual(file.read_bytes(), b"irreplaceable save")

    def test_binary_mount_records_cover_workspace_and_private_metadata_consumers(self):
        unrelated = self.sandbox / os.fsdecode(b"other-\xff-\xc2\xa0")
        with self.mount_records(unrelated):
            code, row = self.cli_report()
            self.assertEqual((code, row["decision"]), (0, "eligible"), row)
            self.assertEqual(cleanup.mount_paths(), [unrelated])
        for mount in (self.target,
                      self.target / os.fsdecode(b"build/mount-\xfe \t\n\\-\xc2\xa0"),
                      self.private_gitdir(),
                      self.private_gitdir() / os.fsdecode(b"logs/mount-\xfd")):
            with self.mount_records(unrelated, mount):
                code, row = self.cli_report(apply=True)
                self.assertEqual((code, row["decision"]), (1, "retained"), row)
                self.assertIn("mount", row["reasons"][0])
                self.assertEqual(cleanup.mount_paths(), [unrelated, mount])
                self.assertTrue(self.target.is_dir())

    def test_binary_mount_inventory_drift_is_checked_on_both_apply_passes(self):
        unrelated = self.sandbox / os.fsdecode(b"other-\xff")
        for mount in (self.target / os.fsdecode(b"build/mount-\xfe"),
                      self.private_gitdir() / os.fsdecode(b"logs/mount-\xfd")):
            for stage in (2, 3):
                original = self.repo.local_state
                calls = 0

                def change(path):
                    nonlocal calls
                    calls += 1
                    if calls == stage:
                        self.write_mount_records(unrelated, mount)
                    return original(path)

                with self.mount_records(unrelated), \
                     patch.object(cleanup.Repository, "local_state", side_effect=change):
                    code, row = self.cli_report(apply=True)
                self.assertEqual((code, row["decision"]), (1, "retained"), row)
                self.assertEqual(calls, stage)
                self.assertIn("mount", row["reasons"][0])
                self.assertTrue(self.target.is_dir())

    def test_non_utf8_backlink_drift_is_preserved_without_crashing(self):
        gitdir = self.private_gitdir()
        backlink = gitdir / "gitdir"
        original_backlink = backlink.read_bytes()
        outside = self.sandbox / os.fsdecode(b"other-\xfe")
        outside.mkdir()
        (outside / ".git").write_bytes(b"gitdir: " + os.fsencode(gitdir) + b"\n")
        for stage in (2, 3):
            original = self.repo.local_state
            calls = 0

            def change(path):
                nonlocal calls
                calls += 1
                if calls == stage:
                    backlink.write_bytes(os.fsencode(outside / ".git") + b"\n")
                return original(path)

            with patch.object(cleanup.Repository, "local_state", side_effect=change):
                code, row = self.cli_report(apply=True)
            self.assertEqual((code, row["decision"]), (1, "retained"), row)
            self.assertEqual(calls, stage)
            self.assertTrue(self.target.is_dir())
            self.assertEqual(backlink.read_bytes(), os.fsencode(outside / ".git") + b"\n")
            backlink.write_bytes(original_backlink)

    def test_malformed_binary_mount_inventory_has_an_explicit_hold_not_fallback(self):
        with self.mount_records(self.target):
            for data in (b"invalid-\xff\n",
                         b"100 1 0:1 / relative-\xff rw - tmpfs tmpfs rw\n",
                         b"100 1 0:1 / /invalid-\\777 rw - tmpfs tmpfs rw\n"):
                (self.sandbox / "mountinfo").write_bytes(data)
                code, row = self.cli_report(apply=True)
                self.assertEqual((code, row["decision"]), (1, "retained"), row)
                self.assertIn("mount inventory", row["reasons"][0])
                self.assertTrue(self.target.is_dir())

    def test_uninitialized_submodule_index_is_retained_without_running_its_git(self):
        self.command(self.target, "update-index", "--add", "--cacheinfo",
                     "160000," + self.base + ",uninitialized-submodule")
        self.command(self.target, "commit", "-qm", "gitlink")
        self.held("missing or ambiguous gitlink directory")

    def test_empty_gitlinks_dry_run_preserves_files_index_and_registrations(self):
        links = self.completed_empty_gitlinks()
        before = self.snapshot(self.repo.common), self.snapshot(self.target)
        registrations = cleanup.inventory(self.root)
        row = self.result()
        self.assertEqual(row["decision"], "eligible", row)
        self.assertGreater(row["allocated_bytes"], 0)
        self.assertEqual((self.snapshot(self.repo.common), self.snapshot(self.target)), before)
        self.assertEqual(cleanup.inventory(self.root), registrations)
        self.assertTrue(all(path.is_dir() and not list(path.iterdir()) for path in links))

    def test_empty_gitlinks_apply_uses_normal_removal_and_keeps_shared_refs(self):
        self.completed_empty_gitlinks()
        shared = self.command(self.root, "show-ref")
        gitdir = self.private_gitdir()
        with patch.object(cleanup, "execute", wraps=cleanup.execute) as commands:
            row = self.result(apply=True)
        self.assertEqual(row["decision"], "removed", row)
        removes = [call.args[0] for call in commands.call_args_list
                   if "worktree" in call.args[0] and "remove" in call.args[0]]
        self.assertEqual([command[command.index("worktree"):] for command in removes],
                         [["worktree", "remove", "--", str(self.target)]])
        self.assertFalse(self.target.exists())
        self.assertFalse(gitdir.exists())
        self.assertNotIn(str(self.target), cleanup.inventory(self.root))
        self.assertEqual(self.command(self.root, "show-ref"), shared)
        self.assertEqual(self.command(self.root, "rev-parse", "topic").strip(), self.head)
        self.assertTrue(self.root.is_dir())

    def test_empty_gitlinks_with_byte_paths_round_trip_and_remove_normally(self):
        self.completed_empty_gitlinks(os.fsdecode(b"vendor/empty-\xff \t\n\\module"))
        code, row = self.cli_report()
        self.assertEqual((code, row["decision"]), (0, "eligible"), row)
        code, row = self.cli_report(apply=True)
        self.assertEqual((code, row["decision"]), (0, "removed"), row)
        self.assertFalse(self.target.exists())

    def test_missing_completed_gitlink_directories_are_not_created_or_removed(self):
        for link in self.completed_empty_gitlinks():
            with self.subTest(path=link):
                link.rmdir()
                before = self.snapshot(self.repo.common), cleanup.inventory(self.root)
                self.held("missing or ambiguous gitlink directory")
                self.assertFalse(link.exists())
                self.assertEqual((self.snapshot(self.repo.common), cleanup.inventory(self.root)), before)
                link.mkdir()

    def test_staged_gitlink_oid_path_mode_and_conflict_changes_are_preserved(self):
        links = self.completed_empty_gitlinks()
        names = [str(link.relative_to(self.target)) for link in links]
        extra = self.target / "vendor" / "new-submodule"
        extra.mkdir()
        index = self.private_gitdir() / "index"
        original = index.read_bytes()
        blob = self.command(self.target, "rev-parse", "HEAD:tracked").strip()
        for change in ("oid", "addition", "deletion", "rename", "mode", "unmerged"):
            with self.subTest(change=change):
                if change in {"deletion", "rename", "unmerged"}:
                    self.command(self.target, "update-index", "--force-remove", names[0])
                if change in {"addition", "rename"}:
                    self.command(self.target, "update-index", "--add", "--cacheinfo",
                                 "160000", self.base, str(extra.relative_to(self.target)))
                elif change in {"oid", "mode"}:
                    self.command(self.target, "update-index", "--cacheinfo",
                                 "160000" if change == "oid" else "100644",
                                 self.head if change == "oid" else blob, names[0])
                elif change == "unmerged":
                    records = "".join(f"160000 {oid} {stage}\t{names[0]}\0"
                                      for stage, oid in enumerate((self.base, self.head, self.merge), 1))
                    self.command(self.target, "update-index", "-z", "--index-info",
                                 input=os.fsencode(records))
                before = self.snapshot(self.repo.common), self.snapshot(self.target)
                self.assertEqual(self.result()["decision"], "retained")
                self.held("unmerged Git index" if change == "unmerged" else "gitlink index/HEAD")
                self.assertEqual((self.snapshot(self.repo.common), self.snapshot(self.target)), before)
                index.write_bytes(original)
        extra.rmdir()
        self.assertEqual(self.result()["decision"], "eligible")

    def test_gitlink_files_hidden_data_and_empty_child_directories_are_preserved(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        for name in ("local-notes", ".hidden", "precious.sav", "build", ".git"):
            with self.subTest(name=name):
                file = link / name
                if name == "build":
                    file.mkdir()
                else:
                    file.write_bytes(b"unique submodule-local data")
                before = self.snapshot(self.repo.common), self.snapshot(link)
                self.held("nonempty submodule/gitlink")
                self.assertEqual((self.snapshot(self.repo.common), self.snapshot(link)), before)
                self.assertTrue(file.exists())
                file.rmdir() if name == "build" else file.unlink()
        link.rmdir()
        link.write_bytes(b"local replacement of a gitlink")
        self.held("not a real directory")
        self.assertEqual(link.read_bytes(), b"local replacement of a gitlink")

    def test_gitlink_initialized_separated_bare_and_nested_repositories_stay_inert(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        metadata = self.sandbox / "separated-metadata"
        for kind in ("initialized", "separated", "bare", "nested"):
            with self.subTest(kind=kind):
                nested = link / "build" / "nested" if kind == "nested" else link
                nested.mkdir(parents=True, exist_ok=True)
                arguments = {"separated": ("--separate-git-dir", str(metadata)),
                             "bare": ("--bare",)}.get(kind, ())
                self.command(nested, "init", "-q", *arguments)
                before = self.snapshot(self.repo.common), self.snapshot(link), self.snapshot(metadata)
                with patch.object(cleanup, "execute", wraps=cleanup.execute) as commands:
                    self.held("nonempty submodule/gitlink")
                self.assertEqual((self.snapshot(self.repo.common), self.snapshot(link),
                                  self.snapshot(metadata)), before)
                self.assertTrue(all(Path(call.args[1]) in {self.root, self.target}
                                    for call in commands.call_args_list))
                shutil.rmtree(link)
                link.mkdir()

    def test_gitlink_symlinks_and_symlinked_parents_preserve_referents(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        outside = self.sandbox / "outside-module"
        outside.mkdir()
        for destination in (outside, self.sandbox / "absent-module", link):
            with self.subTest(destination=destination):
                link.rmdir()
                link.symlink_to(destination, target_is_directory=True)
                before = self.snapshot(self.repo.common), self.snapshot(outside)
                self.held()
                self.assertTrue(link.is_symlink())
                self.assertEqual((self.snapshot(self.repo.common), self.snapshot(outside)), before)
                link.unlink()
                link.mkdir()
        backup = self.sandbox / "real-parent"
        link.parent.rename(backup)
        link.parent.symlink_to(backup, target_is_directory=True)
        self.held("symlinked gitlink")
        self.assertTrue(link.parent.is_symlink())
        self.assertTrue((backup / link.name).is_dir())

    def test_gitlink_symlink_observation_never_accesses_external_target(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        outside = self.sandbox / "external-target"
        (outside / link.name).mkdir(parents=True)
        (outside / "data").write_bytes(b"external data")
        link.parent.rename(self.sandbox / "original-vendor")
        link.parent.symlink_to(outside, target_is_directory=True)
        with self.external_target_accesses(link.parent, outside) as accesses:
            self.held("symlinked gitlink")
        self.assertEqual(accesses, [], "rejection must not first traverse the external target")
        self.assertEqual((outside / "data").read_bytes(), b"external data")

    def test_gitlink_parent_replacement_during_open_never_accesses_external_target(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        parent = link.parent
        retained = self.sandbox / "original-vendor"
        outside = self.sandbox / "external-target"
        (outside / link.name).mkdir(parents=True)
        (outside / "data").write_bytes(b"external data")
        for stage in ("before", "after"):
            with self.subTest(stage=stage):
                changed = False

                def redirect():
                    nonlocal changed
                    changed = True
                    parent.rename(retained)
                    parent.symlink_to(outside, target_is_directory=True)

                with self.external_target_accesses(parent, outside) as accesses:
                    original = os.open

                    def change(value, flags, *args, **kwargs):
                        selected = not changed and (
                            Path(value) == link or
                            (kwargs.get("dir_fd") is not None and Path(value) == Path(parent.name))
                        )
                        if selected and stage == "before":
                            redirect()
                        result = original(value, flags, *args, **kwargs)
                        if selected and stage == "after":
                            redirect()
                        return result

                    with patch.object(os, "open", side_effect=change):
                        self.held()
                self.assertTrue(changed)
                self.assertEqual(accesses, [], "a changing parent must never redirect observation")
                self.assertEqual((outside / "data").read_bytes(), b"external data")
            if changed:
                parent.unlink()
                retained.rename(parent)

    def test_gitlink_scan_parent_replacement_never_accesses_external_target(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        gitlinks = cleanup.gitlink_state(self.target, self.head)
        parent = link.parent
        parent_inode = parent.stat().st_ino
        outside = self.sandbox / "external-target"
        (outside / link.name).mkdir(parents=True)
        (outside / "data").write_bytes(b"external data")
        changed = False
        with self.external_target_accesses(parent, outside) as accesses:
            original = os.scandir

            @contextmanager
            def change(value):
                nonlocal changed
                selected = (os.fstat(value).st_ino == parent_inode
                            if isinstance(value, int) else Path(value) == parent)
                with original(value) as entries:
                    yield entries
                if selected and not changed:
                    changed = True
                    parent.rename(self.sandbox / "original-vendor")
                    parent.symlink_to(outside, target_is_directory=True)

            with patch.object(os, "scandir", side_effect=change):
                with self.assertRaises((cleanup.Retain, OSError)):
                    cleanup.allocated_size(self.target, gitlinks)
        self.assertTrue(changed)
        self.assertEqual(accesses, [], "queued scan paths must not traverse a substituted parent")
        self.assertEqual((outside / "data").read_bytes(), b"external data")

    def test_empty_gitlinks_do_not_bypass_mount_lock_or_active_workspace_guards(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        self.assertEqual(self.result()["decision"], "eligible")
        for mount in (link, link / "nested-mount"):
            with self.mount_records(mount):
                self.held("mount")
        self.repo = cleanup.Repository(self.root, [self.root, link])
        self.held("explicitly preserved")
        self.repo = cleanup.Repository(self.root, [self.root])
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], cwd=link
        )
        try:
            self.process_ids.add(process.pid)
            self.assertIsNone(process.poll())
            self.held("active process")
        finally:
            process.terminate()
            process.wait(timeout=10)
        self.command(self.root, "worktree", "lock", str(self.target))
        self.held("locked")
        self.assertIn("locked", cleanup.inventory(self.root)[str(self.target)])

    def test_gitlink_data_arriving_on_both_apply_passes_is_preserved(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        file = link / "late-data"
        for stage in (2, 3):
            with self.subTest(stage=stage):
                original = self.repo.local_state
                calls = 0

                def change(path):
                    nonlocal calls
                    calls += 1
                    if calls == stage:
                        file.write_bytes(b"data arriving after the empty plan")
                    return original(path)

                with patch.object(self.repo, "local_state", side_effect=change):
                    self.held("nonempty submodule/gitlink")
                self.assertEqual(calls, stage)
                self.assertEqual(file.read_bytes(), b"data arriving after the empty plan")
                file.unlink()

    def test_empty_gitlink_replacement_on_both_apply_passes_invalidates_the_plan(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        backup = self.sandbox / "original-module"
        for stage in (2, 3):
            with self.subTest(stage=stage):
                original = self.repo.local_state
                calls = 0

                def change(path):
                    nonlocal calls
                    calls += 1
                    if calls == stage:
                        link.rename(backup)
                        link.mkdir()
                    return original(path)

                with patch.object(self.repo, "local_state", side_effect=change):
                    self.held("drift" if stage == 3 else "evidence changed")
                self.assertEqual(calls, stage)
                self.assertTrue(link.is_dir() and backup.is_dir())
                link.rmdir()
                backup.rename(link)

    def test_gitlink_data_arriving_during_empty_observation_is_preserved(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        file = link / "during-scan"
        original = os.scandir

        @contextmanager
        def change(descriptor):
            with original(descriptor) as entries:
                yield entries
            if isinstance(descriptor, int):
                file.write_bytes(b"data arriving during empty observation")

        with patch.object(os, "scandir", side_effect=change):
            self.held("gitlink directory changed")
        self.assertEqual(file.read_bytes(), b"data arriving during empty observation")

    def test_zero_byte_gitlink_data_arriving_before_final_size_scan_is_preserved(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        file = link / "late-empty-file"
        original = cleanup.allocated_size
        calls = 0

        def change(path, *args):
            nonlocal calls
            calls += 1
            if calls == 3:
                file.touch()
            return original(path, *args)

        with patch.object(cleanup, "allocated_size", side_effect=change):
            self.held("gitlink")
        self.assertEqual(calls, 3)
        self.assertEqual(file.read_bytes(), b"")

    def test_gitlink_parent_symlink_before_final_size_scan_is_preserved(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        parent = link.parent
        retained = self.sandbox / "retained-vendor"
        padding = self.target / "build" / "padding"
        padding.parent.mkdir()
        padding.touch()
        original = cleanup.allocated_size
        expected_size = original(self.target)
        calls = 0

        def change(path, *args):
            nonlocal calls
            calls += 1
            if calls == 3:
                parent.rename(retained)
                parent.symlink_to(retained, target_is_directory=True)
                difference = expected_size - original(path)
                self.assertGreaterEqual(difference, 0)
                self.assertLessEqual(difference, 1024 * 1024)
                padding.write_bytes(os.urandom(difference))
                self.assertEqual(original(path), expected_size)
            return original(path, *args)

        with patch.object(cleanup, "allocated_size", side_effect=change), \
                patch.object(cleanup, "execute", wraps=cleanup.execute) as commands:
            self.held("gitlink")
        self.assertEqual(calls, 3)
        self.assertTrue(parent.is_symlink())
        self.assertTrue((retained / link.name).is_dir())
        self.assertFalse(any("worktree" in call.args[0] and "remove" in call.args[0]
                             for call in commands.call_args_list))

    def test_submodule_git_and_hooks_do_not_run_even_after_empty_gitlink_observation(self):
        link, = self.completed_empty_gitlinks("vendor/empty-submodule")
        prepared = self.sandbox / "prepared-submodule"
        prepared.mkdir()
        self.command(prepared, "init", "-q")
        self.command(prepared, "config", "user.name", "Submodule fixture")
        self.command(prepared, "config", "user.email", "submodule@example.invalid")
        (prepared / "local-data").write_bytes(b"submodule work to preserve")
        self.command(prepared, "add", ".")
        self.command(prepared, "commit", "-qm", "submodule-only commit")
        marker = self.sandbox / "hook-ran"
        hook = self.sandbox / "submodule-fsmonitor"
        hook.write_text(
            f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
            "sys.stdout.buffer.write(b'fixture-token\\0')\n"
        )
        hook.chmod(0o700)
        self.command(prepared, "config", "core.fsmonitor", str(hook))
        trace = self.sandbox / "git-trace.jsonl"
        run = subprocess.run

        def traced(command, *args, **kwargs):
            kwargs["env"] = {**kwargs["env"], "GIT_TRACE2_EVENT": str(trace)}
            return run(command, *args, **kwargs)

        def submodule_commands():
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertTrue(any(event.get("event") == "start" for event in events))
            return [event for event in events
                    if event.get("event") == "child_start"
                    and event.get("cd") in {str(link), str(link.relative_to(self.target))}]

        link.rmdir()
        prepared.rename(link)
        with patch.object(subprocess, "run", side_effect=traced):
            self.command(self.target, "status", "--porcelain", "--ignore-submodules=none")
        self.assertTrue(submodule_commands(), "the real-Git control must execute nested status")
        self.assertTrue(marker.exists(), "the fixture hook must be executable by unsafe nested status")
        marker.unlink()
        before = self.snapshot(link), self.snapshot(self.repo.common)
        for late in (False, True):
            with self.subTest(late=late):
                trace.unlink()
                if late:
                    link.rename(prepared)
                    link.mkdir()
                original = cleanup.git
                arrived = False

                def arrive(root, *args, **kwargs):
                    nonlocal arrived
                    if late and not arrived and root == self.target and args[0] == "status":
                        link.rmdir()
                        prepared.rename(link)
                        arrived = True
                    return original(root, *args, **kwargs)

                with patch.object(cleanup, "git", side_effect=arrive), \
                     patch.object(subprocess, "run", side_effect=traced):
                    self.held()
                self.assertEqual(arrived, late)
                self.assertEqual(submodule_commands(), [])
                self.assertFalse(marker.exists())
                self.assertEqual((self.snapshot(link), self.snapshot(self.repo.common)), before)

    def test_ambiguous_or_overbound_gitlink_observations_are_retained(self):
        self.completed_empty_gitlinks()
        original = cleanup.git
        for source in ("ls-files", "ls-tree"):
            for malformed in ("truncated", "duplicate"):
                with self.subTest(source=source, malformed=malformed):
                    def change(root, *args, **kwargs):
                        raw = original(root, *args, **kwargs)
                        if args[0] == source and (source == "ls-tree" or "--stage" in args):
                            return raw[:-1] if malformed == "truncated" else raw + raw.split("\0")[0] + "\0"
                        return raw

                    before = self.snapshot(self.repo.common)
                    with patch.object(cleanup, "git", side_effect=change):
                        self.held("Git index/tree")
                    self.assertEqual(self.snapshot(self.repo.common), before)
        with patch.object(cleanup, "MAX_RECORDS", 1):
            self.held("gitlink inventory exceeds safety bound")

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
    def test_graphics_output_admission_uses_per_format_sources(self):
        sources = {
            ".4bpp": {".png"},
            ".8bpp": {".png"},
            ".gbapal": {".png", ".pal"},
            ".4bpp.h": {".png", ".4bpp"},
            ".8bpp.h": set(),
        }
        for output, allowed in sources.items():
            for source in (".png", ".pal", ".agbpal", ".4bpp"):
                if source == output:
                    continue
                for compression in ("", ".lz", ".fk", ".fk.lz"):
                    with self.subTest(output=output, source=source, compression=compression):
                        self.assertEqual(
                            cleanup.generated_ignored("graphics/fixture" + output + compression,
                                                      {"graphics/fixture" + source}),
                            source in allowed,
                        )
        self.assertTrue(cleanup.generated_ignored("graphics/fixture.agbpal.lz",
                                                 {"graphics/fixture.agbpal"}))

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
