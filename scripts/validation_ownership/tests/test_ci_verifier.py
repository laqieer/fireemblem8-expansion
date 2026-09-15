import copy
from contextlib import contextmanager
import errno
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.validation_ownership import budget as budget_module, ci_verifier, reporter
from scripts.validation_ownership.authority import (
    AuthorityLoader, ENVIRONMENT, GitlinkSource, GitTreeEntries, Snapshot, git_tree_entries,
)
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from .report_fixture import (
    ReportFixture, reviewed_code_evolution_case, reviewed_evolution_case, reviewed_exclusion_case,
)


@contextmanager
def protected_launches():
    launching = budget_module.subprocess.Popen
    children = []

    def launch(argv, *args, **kwargs):
        child = launching(argv, *args, **kwargs)
        children.append(child)
        return child

    with patch.object(budget_module.subprocess, "Popen", side_effect=launch):
        yield children
    for child in children:
        if child.poll() is None or not all(
            stream.closed for stream in (child.stdin, child.stdout, child.stderr)
        ):
            raise AssertionError("protected Git launch was not reaped and closed")


class ImmutableBlobBatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = (
            Path(__file__).resolve().parents[3] / "build/test-artifacts/verifier-git-batch"
            / secrets.token_hex(12)
        )
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.directory)
        self.git("init", "--quiet")

    def git(self, *args, root=None):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(root or self.root),
             "-c", "core.hooksPath=/dev/null", "-c", "gc.auto=0", *args],
            env={**ENVIRONMENT, "TMPDIR": str(self.directory),
                 "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                 "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"},
            check=True, capture_output=True, timeout=15,
        ).stdout

    def add(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def capture(self, *, budget=None, revision=None, gitlinks=()):
        budget = budget or ProbeBudget()
        self.addCleanup(budget.close)
        if revision is None:
            self.git("add", "-A")
            revision = self.git("write-tree").decode().strip()
        return AuthorityLoader(
            self.root, git_tree_entries(self.root, revision, budget=budget, gitlinks=gitlinks),
            revision, budget=budget,
        )

    def inputs(self):
        expected = {"empty": b"", "directory/with space": b"\0\xff\nbinary\n", "caf\u00e9.py": b"VALUE = 1\n"}
        for name, data in expected.items():
            self.add(name, data)
        (self.root / "caf\u00e9.py").chmod(0o755)
        return expected

    def frame(self, loader, expected):
        return b"".join(
            f"{loader.entries[name].object_id} blob {len(data)}\n".encode() + data + b"\n"
            for name, data in expected.items()
        )

    def test_selected_bytes_and_snapshot_share_parser_and_actual_accounting(self):
        expected = self.inputs()
        loader = self.capture()
        budget = loader.budget
        clock = budget.started, budget.deadline
        output, pending = budget.bytes["output"], budget.bytes["pending"]
        with protected_launches() as children:
            self.assertEqual(loader.read_blobs(expected, "selected"), expected)
        self.assertEqual(len(children), 1)
        self.assertIn("--batch", children[0].args)
        self.assertIn("lifecycle.py", Path(children[0].args[4]).name)
        self.assertEqual(
            budget.bytes["output"] - output,
            len(self.frame(loader, expected)) + sum(map(len, expected.values())),
        )
        self.assertEqual(
            budget.bytes["pending"] - pending,
            sum(len(loader.entries[name].object_id) + 1 for name in expected)
            + sum(len(os.fsencode(arg)) + 1 for arg in children[0].args),
        )
        self.assertEqual((budget.started, budget.deadline), clock)
        self.assertFalse(budget.children)
        self.assertNotIn("cache", budget.bytes)
        for name, data in expected.items():
            self.assertEqual(loader.read_blob(name, "single-file control"), data)
        snapshot_before = budget.bytes.get("snapshot", 0)
        with protected_launches() as children:
            snapshot = Snapshot(loader, budget)
        self.assertEqual(len(children), 1)
        self.assertEqual(snapshot.files, expected)
        self.assertEqual(snapshot.modes["caf\u00e9.py"], "100755")
        self.assertEqual(
            budget.bytes["snapshot"] - snapshot_before,
            len(self.frame(loader, expected)) + sum(map(len, expected.values()))
            + sum(len(name.encode()) + 64 for name in expected),
        )

    def test_per_file_restoration_loses_batching_without_changing_bytes(self):
        expected = self.inputs()
        loader = self.capture()
        with protected_launches() as batched:
            self.assertEqual(loader.read_blobs(expected, "batch control"), expected)
        with protected_launches() as original:
            self.assertEqual(
                {name: loader.read_blob(name, "restored single-file control") for name in expected},
                expected,
            )
        self.assertEqual(len(batched), 1)
        self.assertEqual(len(original), len(expected))

    def test_selected_reads_do_not_capture_unrequested_large_blobs(self):
        self.add("selected", b"owned")
        self.add("unrequested", b"x" * 4096)
        loader = self.capture(budget=ProbeBudget(Limits(file_bytes=1024)))
        self.assertEqual(loader.read_blobs(["selected"], "selected"), {"selected": b"owned"})
        for read in (lambda: loader.read_blobs(["unrequested"], "large"),
                     lambda: Snapshot(loader, loader.budget)):
            with self.assertRaisesRegex(MakeProbeError, "file bound"):
                read()
            self.assertFalse(loader.budget.children)

    def test_combined_stream_is_not_a_file_and_aggregate_copies_still_count(self):
        expected = {"one": b"a" * 1024, "two": b"b" * 1024}
        for name, data in expected.items():
            self.add(name, data)
        loader = self.capture(budget=ProbeBudget(Limits(file_bytes=1024)))
        self.assertGreater(len(self.frame(loader, expected)), loader.budget.limits.file_bytes)
        self.assertEqual(loader.read_blobs(expected, "combined"), expected)
        cap = loader.budget.bytes["output"]
        for adjustment in (0, -1):
            bounded = self.capture(budget=ProbeBudget(Limits(file_bytes=1024, output_bytes=cap + adjustment)))
            with protected_launches():
                if adjustment == 0:
                    self.assertEqual(bounded.read_blobs(expected, "exact output bound"), expected)
                else:
                    with self.assertRaisesRegex(MakeProbeError, "aggregate output byte"):
                        bounded.read_blobs(expected, "copied payload over bound")
            self.assertFalse(bounded.budget.children)
        for category in ("total_bytes", "snapshot_bytes"):
            bounded = self.capture(budget=ProbeBudget(Limits(**{category: 4096})))
            with protected_launches(), self.assertRaisesRegex(MakeProbeError, "aggregate .*byte"):
                Snapshot(bounded, bounded.budget)
            self.assertFalse(bounded.budget.children)
        self.assertEqual(Snapshot(loader, loader.budget).files, expected)

    def test_every_context_spends_its_own_budget_and_keeps_capture_identity(self):
        expected = self.inputs()
        original = self.capture()
        same_budget = self.capture(budget=original.budget, revision=original.revision)
        independent = self.capture(revision=original.revision)
        for loader in (original, original, same_budget, independent):
            before = loader.budget.runs, loader.budget.bytes["output"]
            clock = loader.budget.started, loader.budget.deadline
            with protected_launches() as children:
                self.assertEqual(loader.read_blobs(expected, "context"), expected)
            self.assertEqual(len(children), 1)
            self.assertEqual(loader.budget.runs, before[0] + 1)
            self.assertGreater(loader.budget.bytes["output"], before[1])
            self.assertEqual((loader.budget.started, loader.budget.deadline), clock)
        for root, revision in ((self.directory, original.revision), (self.root, "0" * 40)):
            with self.assertRaisesRegex(MakeProbeError, "captured repository/revision"):
                AuthorityLoader(root, original.entries, revision, budget=original.budget)
        for entries in (dict(original.entries), original.entries.copy()):
            with self.assertRaisesRegex(MakeProbeError, "capture's report budget"):
                AuthorityLoader(self.root, entries, original.revision, budget=original.budget)
        with self.assertRaisesRegex(MakeProbeError, "capture's report budget"):
            AuthorityLoader(self.root, original.entries, original.revision, budget=independent.budget)
        detached = AuthorityLoader(
            self.root, GitTreeEntries(original.entries, budget=original.budget),
            original.revision, budget=original.budget,
        )
        live = AuthorityLoader(self.root, original.entries, budget=original.budget)
        for invalid in (detached, live):
            with protected_launches() as children, self.assertRaisesRegex(MakeProbeError, "actual immutable capture"):
                invalid.read_blobs(expected, "not immutable")
            self.assertEqual(len(children), 0)
        self.add("empty", b"changed")
        self.assertEqual(live.read_blob("empty", "live read"), b"changed")
        self.assertEqual(original.read_blobs(["empty"], "captured read"), {"empty": b""})
        changed = self.capture(budget=original.budget)
        self.assertNotEqual(changed.revision, original.revision)
        self.assertEqual(changed.read_blobs(["empty"], "changed capture"), {"empty": b"changed"})
        self.assertEqual(original.read_blobs(["empty"], "original capture"), {"empty": b""})
        original.budget.close()
        with protected_launches() as children, self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
            same_budget.read_blobs(expected, "closed capture")
        self.assertEqual(len(children), 0)
        self.assertEqual(independent.read_blobs(expected, "independent lifetime"), expected)

    def test_entry_and_pending_limits_reject_before_launch(self):
        expected = self.inputs()
        (self.root / "link").symlink_to("empty")
        loader = self.capture()
        for selected in (["missing"], ["link"], ["../empty"]):
            with protected_launches() as children, self.assertRaises(MakeProbeError):
                loader.read_blobs(selected, "invalid selected path")
            self.assertEqual(len(children), 0)
        for category, amount in (("entries", 4), ("pending_bytes", 4096)):
            bounded = self.capture(budget=ProbeBudget(Limits(**{category: amount})))
            if category == "pending_bytes":
                bounded.budget.charge("pending", amount - bounded.budget.bytes["pending"])
            with protected_launches() as children, self.assertRaisesRegex(MakeProbeError, "entry bound|pending byte"):
                bounded.read_blobs(list(expected) * 2 if category == "entries" else expected, "bounded")
            self.assertEqual(len(children), 0)
            self.assertTrue(bounded.budget.failed)
        bounded = self.capture(budget=ProbeBudget(Limits(runs=1)))
        with protected_launches() as children, self.assertRaisesRegex(MakeProbeError, "process-launch budget"):
            bounded.read_blobs(expected, "capture already spent the run")
        self.assertEqual(len(children), 0)
        self.assertTrue(bounded.budget.failed)

    def test_gitlink_batches_keep_database_and_recorded_revision(self):
        self.add("same", b"superproject")
        module = self.directory / "module"
        module.mkdir()
        self.git("init", "--quiet", root=module)
        (module / "same").write_bytes(b"pinned")
        self.git("add", "same", root=module)
        self.git("commit", "--quiet", "-m", "Pinned module", root=module)
        pin = self.git("rev-parse", "HEAD", root=module).decode().strip()
        self.git("add", "same")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{pin},module")
        revision = self.git("write-tree").decode().strip()
        (module / "same").write_bytes(b"later")
        self.git("commit", "--quiet", "-am", "Later module", root=module)
        loader = self.capture(
            revision=revision, gitlinks=(GitlinkSource("module", module / ".git"),),
        )
        expected = {"same": b"superproject", "module/same": b"pinned"}
        with protected_launches() as children:
            self.assertEqual(loader.read_blobs(expected, "gitlink"), expected)
        self.assertEqual(len(children), 2)
        self.assertEqual(sum("--git-dir" in child.args for child in children), 1)
        self.assertIn(str(module / ".git"), children[1].args)
        self.assertEqual(Snapshot(loader, loader.budget).files, expected)
        with self.assertRaisesRegex(MakeProbeError, "regular Git blob"):
            loader.read_blobs(["module"], "gitlink is not a blob")

    def test_malformed_streams_reject_for_both_consumers_after_real_git_cleanup(self):
        self.add("owned", b"payload")
        loader = self.capture()
        raw = self.frame(loader, {"owned": b"payload"})
        _, payload = raw.split(b"\n", 1)
        oid = loader.entries["owned"].object_id.encode()
        mutations = {
            "missing": oid + b" missing\n",
            "header": b"",
            "malformed": b"not a header\n",
            "wrong-oid": raw.replace(oid, b"0" * len(oid), 1),
            "wrong-type": raw.replace(b" blob ", b" tree ", 1),
            "size": oid + b" blob -1\n" + payload,
            "huge-size": oid + b" blob " + b"9" * 5000 + b"\n",
            "truncated": raw[:-2],
            "terminator": raw[:-1] + b"x",
            "trailing": raw + b"x",
            "duplicate": raw + raw,
        }
        for name, malformed in mutations.items():
            for snapshot in (False, True):
                with self.subTest(mutation=name, snapshot=snapshot):
                    bounded = self.capture(revision=loader.revision)
                    running = bounded.budget.run

                    def corrupt(*args, **kwargs):
                        result = running(*args, **kwargs)
                        self.assertEqual(result.stdout, raw)
                        return subprocess.CompletedProcess(result.args, result.returncode, malformed, result.stderr)

                    with protected_launches() as children, patch.object(bounded.budget, "run", side_effect=corrupt):
                        with self.assertRaises(MakeProbeError):
                            if snapshot:
                                Snapshot(bounded, bounded.budget)
                            else:
                                bounded.read_blobs(["owned"], "corrupt")
                    self.assertEqual(len(children), 1)
                    self.assertFalse(bounded.budget.children)
        self.assertEqual(loader.read_blobs(["owned"], "restored"), {"owned": b"payload"})

    def test_real_missing_object_failed_git_and_stream_overflow_close_children(self):
        self.add("owned", b"x" * 4096)
        for failure in ("missing-object", "failed-git", "stream-overflow"):
            with self.subTest(failure=failure):
                loader = self.capture(budget=ProbeBudget(Limits(file_bytes=1024, output_bytes=2048)))
                target = None
                if failure == "missing-object":
                    oid = loader.entries["owned"].object_id
                    target = self.root / ".git/objects" / oid[:2] / oid[2:]
                elif failure == "failed-git":
                    target = self.root / ".git/HEAD"
                saved = target.read_bytes() if target else None
                try:
                    if target:
                        target.unlink()
                    with protected_launches(), self.assertRaises(MakeProbeError):
                        loader.read_blobs(["owned"], failure)
                    self.assertFalse(loader.budget.children)
                finally:
                    if target:
                        target.write_bytes(saved)

    def test_loaded_module_checks_keep_each_real_source_and_reject_drift(self):
        self.add("scripts/owned.py", b"VALUE = 1\n")
        loader = self.capture()
        trusted = self.directory / "trusted"
        (trusted / "scripts").mkdir(parents=True)
        path = trusted / "scripts/owned.py"
        path.write_bytes(b"VALUE = 1\n")
        spec = importlib.util.spec_from_file_location("scripts.owned", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules = {"scripts.owned": module, "scripts.alias": module}
        with patch.object(ci_verifier.sys, "modules", modules):
            control_before = loader.budget.bytes["control"]
            with protected_launches() as children:
                self.assertEqual(ci_verifier._verify_loaded_modules(trusted, loader), ["scripts/owned.py"] * 2)
            self.assertEqual(len(children), 1)
            self.assertEqual(loader.budget.bytes["control"] - control_before, 2 * path.stat().st_size)
            for mutation in ("bytes", "missing", "extra", "foreign", "non-python"):
                with self.subTest(mutation=mutation):
                    source = path
                    if mutation == "bytes":
                        path.write_bytes(b"VALUE = 2\n")
                    elif mutation == "missing":
                        path.unlink()
                    else:
                        source = (
                            self.directory / "foreign.py" if mutation == "foreign"
                            else trusted / ("scripts/extra.py" if mutation == "extra" else "scripts/owned.txt")
                        )
                        source.write_bytes(b"VALUE = 1\n")
                    module.__file__ = str(source)
                    with self.assertRaises((MakeProbeError, FileNotFoundError)):
                        ci_verifier._verify_loaded_modules(trusted, loader)
                    path.write_bytes(b"VALUE = 1\n")
                    module.__file__ = str(path)
            self.assertEqual(ci_verifier._verify_loaded_modules(trusted, loader), ["scripts/owned.py"] * 2)
        self.assertFalse(loader.budget.children)


class BatchedVerifierSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ReportFixture()
        self.addCleanup(self.fixture.close)
        self.revision = self.fixture.git("rev-parse", "HEAD").decode().strip()

    def loader(self):
        budget = ProbeBudget()
        self.addCleanup(budget.close)
        return ci_verifier.capture(self.fixture.root, self.revision, budget)

    def test_each_verifier_stage_batches_actual_processes_without_skipping_sources(self):
        trusted = self.fixture.extract_revision(self.revision, "trusted")
        loader = self.loader()
        with protected_launches() as children:
            paths = ci_verifier._trusted_paths(trusted, loader)
        self.assertEqual(paths, ci_verifier._trusted_namespace(loader))
        self.assertGreater(len(paths), 1)
        self.assertEqual(len(children), 1)
        for _ in range(2):
            with protected_launches() as children:
                modules = ci_verifier._verify_loaded_modules(ci_verifier.TRUSTED_ROOT, loader)
            self.assertIn(ci_verifier.CI_VERIFIER_PATH, modules)
            self.assertIn("scripts/check_docs.py", modules)
            self.assertGreater(len(modules), 1)
            self.assertEqual(len(children), 1)
        self.assertFalse(loader.budget.children)

    def test_trusted_worktree_drift_still_rejects_all_selected_files(self):
        for mutation in ("bytes", "missing", "symlink", "directory", "extra"):
            with self.subTest(mutation=mutation):
                trusted = self.fixture.extract_revision(self.revision, "trusted-" + mutation)
                target = trusted / ci_verifier.CI_VERIFIER_PATH
                if mutation == "bytes":
                    target.write_bytes(target.read_bytes() + b"\n# changed\n")
                elif mutation == "extra":
                    target.with_name("unadmitted.py").write_bytes(b"VALUE = 1\n")
                else:
                    target.unlink()
                    if mutation == "directory":
                        target.mkdir()
                    elif mutation == "symlink":
                        target.symlink_to("reporter.py")
                loader = self.loader()
                with self.assertRaises(MakeProbeError):
                    ci_verifier._trusted_paths(trusted, loader)
                self.assertFalse(loader.budget.children)


class BasePinnedVerifierTests(unittest.TestCase):
    def runtime_root(self):
        root = (
            Path(__file__).resolve().parents[3]
            / "build/test-artifacts/verifier-runtime-cleanup"
            / secrets.token_hex(12)
        )
        root.mkdir(parents=True)
        self.addCleanup(lambda: root.exists() and shutil.rmtree(root))
        return root

    def test_runtime_root_repeats_and_rejects_preexisting_content(self):
        trusted = self.runtime_root()
        for _ in range(2):
            owned = ci_verifier._prepare_trusted_runtime_root(trusted)
            self.assertTrue(owned.path.is_dir())
            ci_verifier._cleanup_trusted_runtime_root(owned)
            self.assertFalse(owned.path.exists())
        runtime = trusted / ".validation-ownership-runtime"
        runtime.mkdir()
        marker = runtime / "unknown"
        marker.write_text("retain")
        with self.assertRaisesRegex(
            reporter.OwnershipError, "cannot create trusted verifier runtime root",
        ):
            ci_verifier._prepare_trusted_runtime_root(trusted)
        self.assertEqual(marker.read_text(), "retain")

    def test_runtime_root_cleanup_rejects_substitution_and_residual_work(self):
        for replacement in ("directory", "symlink", "residual"):
            with self.subTest(replacement=replacement):
                trusted = self.runtime_root()
                owned = ci_verifier._prepare_trusted_runtime_root(trusted)
                if replacement == "residual":
                    marker = owned.path / "unknown"
                    marker.write_text("retain")
                    with self.assertRaisesRegex(
                        reporter.OwnershipError, "cannot remove trusted verifier runtime root",
                    ):
                        ci_verifier._cleanup_trusted_runtime_root(owned)
                    self.assertEqual(marker.read_text(), "retain")
                    continue
                displaced = trusted / "owned-displaced"
                owned.path.rename(displaced)
                marker = trusted / "replacement-marker"
                marker.write_text("retain")
                if replacement == "directory":
                    owned.path.mkdir()
                    retained = owned.path / "unknown"
                    retained.write_text("retain")
                else:
                    owned.path.symlink_to(marker)
                with self.assertRaisesRegex(
                    reporter.OwnershipError, "identity changed before cleanup",
                ):
                    ci_verifier._cleanup_trusted_runtime_root(owned)
                self.assertTrue(displaced.is_dir())
                if replacement == "directory":
                    self.assertEqual(retained.read_text(), "retain")
                else:
                    self.assertTrue(owned.path.is_symlink())
                    self.assertEqual(marker.read_text(), "retain")

    def test_runtime_root_partial_setup_closes_created_workspace(self):
        trusted = self.runtime_root()
        runtime = trusted / ".validation-ownership-runtime"
        opening = os.open
        failed = False

        def fail_once(path, flags, *arguments, **options):
            nonlocal failed
            if (
                path == runtime.name
                and options.get("dir_fd") is not None
                and not failed
            ):
                failed = True
                raise OSError(errno.EIO, "controlled runtime open failure")
            return opening(path, flags, *arguments, **options)

        with patch.object(ci_verifier.os, "open", side_effect=fail_once):
            with self.assertRaisesRegex(
                reporter.OwnershipError, "cannot create trusted verifier runtime root",
            ):
                ci_verifier._prepare_trusted_runtime_root(trusted)
        self.assertTrue(failed)
        self.assertFalse(runtime.exists())

    def test_runtime_root_is_retained_when_owned_termination_is_unconfirmed(self):
        trusted = self.runtime_root()
        owned = []
        budgets = []
        child = subprocess.Popen(
            ["/usr/bin/python3", "-I", "-S", "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        def fail_with_owned_work(*arguments, budget, runtime_owner, **options):
            workspace = ci_verifier._prepare_trusted_runtime_root(trusted)
            runtime_owner.append(workspace)
            owned.append(workspace)
            budgets.append(budget)
            budget.children[child] = False
            raise reporter.OwnershipError("controlled primary verifier failure")

        try:
            with (
                patch.object(ci_verifier, "_verify", side_effect=fail_with_owned_work),
                patch.object(ci_verifier.ProbeBudget, "_terminate",
                             side_effect=OSError("unconfirmed termination")),
            ):
                with self.assertRaisesRegex(
                    reporter.OwnershipError, "controlled primary verifier failure",
                ) as caught:
                    ci_verifier.verify(trusted, trusted / "candidate", "1" * 40, "2" * 40)
            self.assertIsNone(child.poll())
            self.assertIn(child, budgets[0].children)
            self.assertTrue(owned[0].path.is_dir())
            self.assertTrue(caught.exception.cleanup_errors)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=15)
            for budget in budgets:
                budget.children.pop(child, None)
                budget.close()

    def test_main_reports_cleanup_failure_without_replacing_primary_failure(self):
        arguments = SimpleNamespace(
            trusted_root=Path("/trusted"),
            repository_root=Path("/candidate"),
            base_sha="1" * 40,
            candidate_sha="2" * 40,
            trusted_sha=None,
            expected_mode="exact-base-pinned",
            reviewed_repository=None,
            reviewed_pull_request=None,
            reviewed_paths=None,
            reviewed_edge_ids=None,
            reviewed_consumers=None,
        )
        error = reporter.OwnershipError("primary verifier failure")
        error.cleanup_errors = (
            "after owned cleanup: OwnershipError: retained unknown workspace",
        )
        stderr = StringIO()
        with (
            patch.object(ci_verifier, "parse_args", return_value=arguments),
            patch.object(ci_verifier, "verify", side_effect=error),
            patch("sys.stderr", stderr),
        ):
            self.assertEqual(ci_verifier.main(), 1)
        self.assertIn("primary verifier failure", stderr.getvalue())
        self.assertIn("retained unknown workspace", stderr.getvalue())

    def test_base_step_guard_rejects_inert_and_duplicate_decoys(self):
        root = Path(__file__).resolve().parents[3]
        text = (root / reporter.BUILD_WORKFLOW_PATH).read_text()
        marker = ci_verifier.BASE_STEP_MARKER
        start = text.index(marker)
        end = text.index("\n    - name:", start + len(marker))
        step = text[start:end + 1]
        ci_verifier._base_step(text)
        disabled = step.replace(marker, marker + "      if: false\n", 1)
        decoy = "  ownership-decoy:\n    if: false\n    runs-on: ubuntu-latest\n    steps:\n" + step
        for candidate in (
            text.replace(step, disabled, 1).replace("  host-tests:\n", decoy + "  host-tests:\n", 1),
            text.replace(step, step + step, 1),
        ):
            with self.subTest(candidate=candidate[:120]), self.assertRaises(MakeProbeError):
                ci_verifier._base_step(candidate)

    def test_base_step_guard_ignores_nonsemantic_yaml_comments(self):
        root = Path(__file__).resolve().parents[3]
        text = (root / reporter.BUILD_WORKFLOW_PATH).read_text()
        marker = ci_verifier.BASE_STEP_MARKER
        changed = text.replace(marker, marker + "      # Same executed step mapping.\n", 1)
        self.assertEqual(ci_verifier._base_step(text), ci_verifier._base_step(changed))

    def test_scanner_build_contract_preserves_make_semantics_and_rejects_redirects(self):
        root = Path(__file__).resolve().parents[3]
        original = (root / ci_verifier.SCANINC_MAKEFILE).read_text()

        def make_dry_run(source):
            fixture = self.runtime_root()
            (fixture / "Makefile").write_text(source)
            for path in ci_verifier.SCANINC_SOURCES:
                (fixture / Path(path).name).touch()
            return subprocess.run(
                ["make", "--no-print-directory", "-n"], cwd=fixture,
                env=ENVIRONMENT, capture_output=True, text=True, check=True, timeout=15,
            ).stdout

        lines = original.splitlines()
        assignments = [line for line in lines if "=" in line]
        changed = "\n".join([
            "# Same supported scanner build\n", *reversed(assignments),
            *(line for line in lines if line not in assignments),
        ]).replace("$(CXX)", "${CXX}").replace(
            "-Wall -Werror -std=c++11 -O2", "-O2 -std=c++11 -Werror -Wall",
        )
        commands = []
        for source in (original, changed):
            ci_verifier._scaninc_build_contract(source)
            argv = make_dry_run(source).split()
            commands.append((argv[0], sorted(argv[1:-2]), argv[-2:]))
        self.assertEqual(commands[0], commands[1])
        safe_comment = "# Unicode remains inert after the ASCII comment boundary: café\u2028still comment\n"
        ci_verifier._scaninc_build_contract(safe_comment + original)
        self.assertEqual(make_dry_run(safe_comment + original), make_dry_run(original))
        for name, mutated in {
            "nbsp-assignment": original.replace("CXXFLAGS =", "CXXFLAGS\u00a0="),
            "vt-assignment": original.replace("-Wall -Werror", "-Wall\v-Werror"),
            "nbsp-recipe": original.replace("$(CXX) $(CXXFLAGS)", "$(CXX)\u00a0$(CXXFLAGS)"),
            "vt-recipe": original.replace("$(CXX) $(CXXFLAGS)", "$(CXX)\v$(CXXFLAGS)"),
            "vt-comment-assignment": original.replace("CXXFLAGS =", "# comment\vCXXFLAGS ="),
        }.items():
            with self.subTest(unicode_separator=name):
                self.assertNotEqual(make_dry_run(mutated), make_dry_run(original))
                with self.assertRaisesRegex(MakeProbeError, "build contract"):
                    ci_verifier._scaninc_build_contract(mutated)
        for old, new in (
            ("CXX = g++", "CXX = clang++"),
            ("CXX = g++", "CXX := $(shell printf g++)"),
            ("CXX = g++", "CXX = g++\0"),
            ("CXX = g++", "CXX = g++\r"),
            ("-O2", "-O2 -DUNAPPROVED"),
            ("-O2", "-O2 -include injected.h"),
            ("asm_file.cpp", "unlisted.cpp"),
            ("asm_file.h", "unlisted.h"),
            ("$(SRCS) $(HEADERS)", "$(SRCS)"),
            ("-o $@", "-o $@ -fplugin=unlisted.so"),
            ("\t$(CXX)", "\t-$(CXX)"),
        ):
            with self.subTest(replacement=new), self.assertRaisesRegex(MakeProbeError, "build contract"):
                ci_verifier._scaninc_build_contract(original.replace(old, new))

    def test_base_mode_distinguishes_bootstrap_foundation_and_partial_authority(self):
        self.assertEqual(ci_verifier._base_authority_mode({}), "bootstrap-not-authoritative")
        foundation = {
            "scripts/validation_ownership/" + name: None
            for name in ("authority.py", "budget.py", "make_probe.py", "syscall_guard.py",
                         "sandbox_exec.py", "shell_interceptor.c", "make_observer.c", "lifecycle.py")
        }
        self.assertEqual(ci_verifier._base_authority_mode(foundation), "foundation-introduction")
        with self.assertRaisesRegex(MakeProbeError, "incomplete"):
            ci_verifier._base_authority_mode({**foundation, reporter.GRAPH_PATH.as_posix(): None})
        self.assertEqual(
            ci_verifier._base_authority_mode(dict.fromkeys(ci_verifier.BASE_AUTHORITY_PATHS)),
            "exact-base-pinned",
        )

    def test_exact_owner_pair_authority_comparison_rejects_redirects(self):
        graph = {
            "nodes": [
                {"id": "surface", "kind": "surface", "surface_type": "source",
                 "requirements": ["positive"], "dependencies": []},
                {"id": "owner", "kind": "evidence", "evidence_type": "host",
                 "authority": {"kind": "make-target", "target": "all"}},
            ],
            "edges": [{"id": "edge", "type": "owns-test", "source": "surface",
                       "target": "owner", "reason": "Actual owner"}],
            "path_rules": [{"id": "paths", "surface": "surface",
                            "include": [{"kind": "exact", "path": "source.c"}], "exclude": []}],
            "exclusions": [],
            "artifact": {"estimated_maintenance_minutes": 1, "max_maintenance_minutes": 5},
        }
        entry = reporter.GitTreeEntry("source.c", "100644", "blob", "0" * 40)
        model = {
            "entries": {"source.c": entry}, "generated_paths": set(),
            "surfaces": {"surface": graph["nodes"][0]}, "evidence": {"owner": graph["nodes"][1]},
            "outgoing": {"surface": graph["edges"]}, "authorities": {"owner": {"display": "make all", "fingerprint": "one"}},
            "admission_sources": {"generated-source-registry": set(), "initial-graph-cohort": {"source.c"},
                                  "verifier-runtime-registry": set()},
        }
        oracle = {"source_case": "TC-WORKFLOW-GATE-OWNERSHIP-001", "seal": "controlled",
                  "probes": [{"path": "source.c", "expected_surface": "surface",
                              "expected_owners": [{"edge_type": "owns-test", "evidence_id": "owner"}]}]}
        pairs, authorities = ci_verifier._verify_oracle_pairs(oracle, graph, model, graph, model)
        self.assertEqual(len(pairs), 64)
        self.assertEqual(len(authorities), 64)
        redirected = copy.deepcopy(model)
        redirected["authorities"]["owner"]["fingerprint"] = "different observed semantics"
        with self.assertRaisesRegex(MakeProbeError, "retargets"):
            ci_verifier._verify_oracle_pairs(oracle, graph, redirected, graph, model)
        changed = copy.deepcopy(graph)
        changed["nodes"][1]["authority"]["target"] = "different"
        with self.assertRaisesRegex(MakeProbeError, "retargets"):
            ci_verifier._verify_oracle_pairs(oracle, changed, model, graph, model)


class ReviewedEvolutionVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ReportFixture()
        self.addCleanup(self.fixture.close)

    def trusted_root(self, revision, label=""):
        return self.fixture.extract_revision(revision, "trusted-" + revision[:12] + label)

    def verify(self, trusted, *arguments, stop_at_session=False):
        entry = [str(trusted / "scripts/validation_ownership/ci_verifier.py")]
        if stop_at_session:
            entry = ["-c", (
                "import sys\n"
                "sys.path.insert(0, sys.argv.pop(1))\n"
                "from scripts.validation_ownership import ci_verifier\n"
                "def entered(*args, **kwargs):\n"
                "    raise RuntimeError('controlled scanner session entry')\n"
                "ci_verifier.ProbeSession = entered\n"
                "raise SystemExit(ci_verifier.main())\n"
            ), str(trusted)]
        return subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                *entry,
                "--trusted-root",
                str(trusted),
                "--repository-root",
                str(self.fixture.root),
                *arguments,
            ],
            cwd=trusted,
            env=ENVIRONMENT,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def exact_arguments(self, base, candidate):
        return (
            "--base-sha", base,
            "--candidate-sha", candidate,
            "--trusted-sha", base,
            "--expected-mode", "exact-base-pinned",
        )

    def reviewed_arguments(self, case):
        return (
            "--base-sha", case["base"],
            "--candidate-sha", case["head"],
            "--trusted-sha", case.get("trusted_sha", case["head"]),
            "--expected-mode", "reviewed-evolution",
            "--reviewed-repository", "owner/repository",
            "--reviewed-pull-request", "186",
            *(item for path in case["reviewed_paths"] for item in ("--reviewed-path", path)),
            *(item for edge in case["reviewed_edges"] for item in ("--reviewed-edge", edge)),
            *(item for consumer in case["affected_consumers"]
              for item in ("--reviewed-consumer", consumer)),
        )

    def test_trusted_sources_reject_candidate_drift_before_session(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        trusted = self.trusted_root(base)
        reached = self.verify(trusted, *self.exact_arguments(base, base), stop_at_session=True)
        self.assertIn("controlled scanner session entry", reached.stderr)
        for relative in (
            *ci_verifier.SCANINC_SOURCES, ci_verifier.SCANINC_WRAPPER, ci_verifier.SCANINC_MAKEFILE,
            ci_verifier.CI_VERIFIER_PATH, "scripts/validation_ownership/reporter.py", "scripts/check_docs.py",
        ):
            for mutation in ("bytes", "missing", "mode", "symlink"):
                with self.subTest(path=relative, mutation=mutation):
                    self.fixture.git("switch", "--detach", base)
                    path = self.fixture.root / relative
                    if mutation == "bytes":
                        path.write_text("#error unapproved scanner\n")
                    elif mutation == "mode":
                        path.chmod(0o755)
                    else:
                        path.unlink()
                        if mutation == "symlink":
                            path.symlink_to("not-a-scanner-input")
                    head = self.fixture.commit("Change scanner " + mutation)
                    failed = self.verify(
                        trusted, *self.exact_arguments(base, head), stop_at_session=True,
                    )
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn("candidate trusted sources differ", failed.stderr)
                    self.assertNotIn("controlled scanner session entry", failed.stderr)
                    self.assertFalse((trusted / ".validation-ownership-runtime").exists())

    def test_code_only_evolution_executes_selected_code_and_requires_an_actual_boundary(self):
        case = reviewed_code_evolution_case(self.fixture)
        base_trusted = self.trusted_root(case["base"])
        trusted = self.trusted_root(case["head"])
        exact = self.verify(base_trusted, *self.exact_arguments(case["base"], case["head"]))
        self.assertNotEqual(exact.returncode, 0, exact.stdout)
        self.assertIn("candidate trusted sources differ", exact.stderr)
        completed = self.verify(trusted, *self.reviewed_arguments(case))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["reviewed_code_executed"])
        self.assertEqual(result["trusted_source_changes"], case["reviewed_paths"])
        self.assertFalse(result["review_invalidation"]["invalidated"])
        self.assertEqual(result["review_invalidation"]["changed_edge_ids"], [])

        extra = "scripts/validation_ownership/unreviewed.py"
        self.fixture.add(extra, "VALUE = 1\n")
        later = self.fixture.commit("Add unselected verifier code")
        drift = {**case, "head": later, "trusted_sha": case["head"],
                 "reviewed_paths": sorted([*case["reviewed_paths"], extra])}
        rejected = self.verify(trusted, *self.reviewed_arguments(drift), stop_at_session=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("candidate trusted sources differ", rejected.stderr)
        self.assertNotIn("controlled scanner session entry", rejected.stderr)

        self.fixture.git("switch", "--detach", case["base"])
        self.fixture.add("src/data/table.json", '{"version":2}\n')
        unchanged = self.fixture.commit("Change data without verifier or graph authority")
        no_boundary = {**case, "head": unchanged, "reviewed_paths": ["src/data/table.json"]}
        rejected = self.verify(self.trusted_root(unchanged), *self.reviewed_arguments(no_boundary))
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("actual graph or trusted source change", rejected.stderr)

    def test_complete_direct_verifier_needs_used_authority_not_auxiliary_make_gate(self):
        (self.fixture.root / "scripts/validation_ownership/ci_gate.mk").unlink(missing_ok=True)
        self.fixture.add("src/data/table.json", '{"version":2}\n')
        revision = self.fixture.commit("Exercise direct verifier without auxiliary Make gate")
        for relative in ("scripts/bash_parser.py",
                         "tools/scaninc/c_file.cpp",
                         "scripts/validation_ownership/metadata_transport.py",
                         "scripts/validation_ownership/reporter.py"):
            with self.subTest(dependency=relative):
                self.fixture.git("switch", "--detach", revision)
                trusted = self.trusted_root(revision, Path(relative).stem)
                wrong = trusted.with_name(trusted.name + "-wrong")
                staged = trusted.with_name(trusted.name + "-staged")
                for root in (wrong, staged):
                    shutil.copytree(trusted, root)
                for root in (trusted, wrong, staged):
                    self.addCleanup(lambda path=root: path.exists() and shutil.rmtree(path))
                completed = self.verify(trusted, *self.exact_arguments(revision, revision))
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["mode"], "exact-base-pinned")

                (trusted / relative).unlink()
                wrong_path = wrong / relative
                wrong_path.write_text(wrong_path.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
                failures = [
                    self.verify(trusted, *self.exact_arguments(revision, revision)),
                    self.verify(wrong, *self.exact_arguments(revision, revision)),
                ]
                self.fixture.add(relative, "# candidate-only dependency\n")
                staged_candidate = self.fixture.commit("Stage candidate-only dependency drift")
                (staged / relative).unlink()
                failures.append(self.verify(staged, *self.exact_arguments(revision, staged_candidate)))
                for failed in failures:
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertFalse(failed.stdout.strip())
                    self.assertIn(Path(relative).stem, failed.stderr)

    def test_exact_verifier_reuses_one_trusted_tree_for_two_actual_captures(self):
        revision = self.fixture.git("rev-parse", "HEAD").decode().strip()
        trusted = self.trusted_root(revision)
        runtime = trusted / ".validation-ownership-runtime"
        results = []
        for _ in range(2):
            completed = self.verify(trusted, *self.exact_arguments(revision, revision))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(runtime.exists())
            results.append(json.loads(completed.stdout))
        self.assertEqual(results[0]["oracle_pairs_sha256"],
                         results[1]["oracle_pairs_sha256"])
        self.assertEqual(results[0]["oracle_authority_sha256"],
                         results[1]["oracle_authority_sha256"])

    def test_failed_actual_capture_cleans_before_valid_retry(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        trusted = self.trusted_root(base)
        runtime = trusted / ".validation-ownership-runtime"
        workflow = (self.fixture.root / reporter.BUILD_WORKFLOW_PATH).read_text()
        self.fixture.add(
            reporter.BUILD_WORKFLOW_PATH.as_posix(),
            workflow.replace(
                ci_verifier.BASE_STEP_NAME,
                "Disabled ownership exact-base verifier",
                1,
            ),
        )
        failed_head = self.fixture.commit("Break exact-base verifier step")
        failed = self.verify(trusted, *self.exact_arguments(base, failed_head))
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "Build verifier staging authority is invalid",
            failed.stderr,
        )
        first_failure = failed.stderr
        self.assertFalse(runtime.exists())
        self.fixture.add(reporter.BUILD_WORKFLOW_PATH.as_posix(), workflow)
        restored = self.fixture.commit("Restore exact-base verifier step")
        retried = self.verify(trusted, *self.exact_arguments(base, restored))
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertFalse(runtime.exists())
        self.assertIn("verifier", first_failure)

    def test_reviewed_evolution_accepts_exact_new_surface_and_authority_change(self):
        case = reviewed_evolution_case(self.fixture)
        relative = "tools/scaninc/c_file.cpp"
        self.fixture.add(relative, (self.fixture.root / relative).read_text().replace(
            "std::rewind(fp);", "std::fseek(fp, 0, SEEK_SET);",
        ))
        case["head"] = self.fixture.commit("Review scanner evolution with graph authority")
        case["reviewed_paths"] = sorted([*case["reviewed_paths"], relative])
        trusted = self.trusted_root(case["head"])
        completed = self.verify(trusted, *self.reviewed_arguments(case))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["authority"], "reviewed-evolution")
        self.assertEqual(result["mode"], "reviewed-evolution")
        self.assertEqual(result["trusted_sha"], case["head"])
        self.assertEqual(result["candidate_changed_paths"], case["reviewed_paths"])
        self.assertEqual(result["reviewed_evolution"]["repository"], "owner/repository")
        self.assertEqual(result["reviewed_evolution"]["pull_request"], 186)
        self.assertEqual(
            result["reviewed_evolution"]["affected_consumers"],
            case["affected_consumers"],
        )
        self.assertIn("docs-source.depends", result["reviewed_evolution"]["changed_edge_ids"])
        self.assertEqual(result["review_invalidation"], {
            "invalidated": True,
            "reason": "authoritative-graph-edge-change",
            "changed_edge_ids": case["reviewed_edges"],
        })

    def test_exact_base_and_mismatched_reviewed_scope_reject_evolution(self):
        case = reviewed_evolution_case(self.fixture)
        trusted = self.trusted_root(case["head"])
        base_trusted = self.trusted_root(case["base"])
        strict = self.verify(base_trusted, *self.exact_arguments(case["base"], case["head"]))
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("candidate trusted sources differ", strict.stderr)
        wrong_paths = self.verify(
            trusted, *self.reviewed_arguments({**case, "reviewed_paths": case["reviewed_paths"][1:]}),
        )
        self.assertNotEqual(wrong_paths.returncode, 0)
        self.assertIn("path scope differs", wrong_paths.stderr)
        wrong_edges = self.verify(
            trusted, *self.reviewed_arguments({**case, "reviewed_edges": case["reviewed_edges"][:-1]}),
        )
        self.assertNotEqual(wrong_edges.returncode, 0)
        self.assertIn("relationship scope differs", wrong_edges.stderr)
        consumer_trusted = self.trusted_root(case["head"], "-consumers")
        wrong_consumers = self.verify(
            consumer_trusted,
            *self.reviewed_arguments({**case, "affected_consumers": case["affected_consumers"][:-1]}),
        )
        self.assertNotEqual(wrong_consumers.returncode, 0)
        self.assertIn("consumer scope differs", wrong_consumers.stderr)

    def test_exclusion_evolution_rejects_strict_default_and_invalidates_all_edges(self):
        case = reviewed_exclusion_case(self.fixture)
        base_trusted = self.trusted_root(case["base"])
        reviewed_trusted = self.trusted_root(case["head"], "-exclusion")
        strict = self.verify(base_trusted, *self.exact_arguments(case["base"], case["head"]))
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("retargets exact-base oracle authority", strict.stderr)
        reviewed = self.verify(reviewed_trusted, *self.reviewed_arguments(case))
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        result = json.loads(reviewed.stdout)
        self.assertEqual(result["review_invalidation"]["changed_edge_ids"], case["reviewed_edges"])


if __name__ == "__main__":
    unittest.main()
