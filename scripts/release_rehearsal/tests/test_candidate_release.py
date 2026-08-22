"""Issue #29 release validation without provenance/legal-review metadata."""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.release_rehearsal import candidate_tree as ct
from scripts.release_rehearsal import cli
from scripts.release_rehearsal import git_source as gs
from scripts.release_rehearsal import manifest as rm
from scripts.release_rehearsal import submodule_binding as sb


class CandidateTreeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.target_tree = gs.write_index_tree(ROOT)

    def test_exact_tree_supplies_modes_and_gitlink(self):
        tree = ct.load(ROOT, self.target_tree)
        self.assertIn("mgfembp", tree.modes)
        self.assertEqual(tree.modes["mgfembp"], gs.MODE_GITLINK)
        self.assertNotIn("mgfembp", tree.source_paths)

    def test_tree_coverage_has_no_committed_membership_ledger(self):
        report = rm.check_tree_coverage(ROOT, self.target_tree)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["modes"]["mgfembp"], gs.MODE_GITLINK)

    def test_gitmodules_binds_each_candidate_gitlink(self):
        self.assertEqual(sb.check_submodule_binding(ROOT, self.target_tree), [])

    def test_release_make_exposes_concrete_check_commands(self):
        text = (ROOT / "release.mk").read_text(encoding="utf-8")
        self.assertIn("release-check:", text)
        self.assertIn("release-rehearse:", text)

    def test_candidate_tree_target_runs_only_the_tree_command(self):
        completed = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "release-candidate-tree-check",
                f"RELEASE_TARGET_SHA={self.target_tree}",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn(
            f"scripts.release_rehearsal.cli candidate-tree --target-sha {self.target_tree}",
            completed.stdout,
        )
        self.assertNotIn("scripts.release_rehearsal.cli check", completed.stdout)

    def test_archive_mismatch_is_present_in_json_and_exit_status(self):
        args = SimpleNamespace(
            repo_root=ROOT,
            target_sha="HEAD",
            config="release",
            abi="aapcs",
            rom_size="16M",
            embedded_short_sha=None,
            release_tag_attestation=None,
        )
        tree = SimpleNamespace(source_paths=("README.md",))
        archive = {"match": False}
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli.gs, "is_git_repo", return_value=True),
            mock.patch.object(cli.rm, "resolve_target_sha", return_value="a" * 40),
            mock.patch.object(cli.ct, "load", return_value=tree),
            mock.patch.object(cli.ar, "rehearse_archive_twice", return_value=archive),
            mock.patch.object(cli, "_manifest", return_value={"reasons": []}),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = cli.cmd_rehearse(args)

        self.assertEqual(result, cli.EXIT_TECHNICAL_FAILURE)
        report = json.loads(stdout.getvalue())
        self.assertIn("deterministic archive hashes differ", report["reasons"])
        self.assertIn("deterministic archive hashes differ", stderr.getvalue())

    def test_removed_metadata_is_not_tracked(self):
        tracked = subprocess.check_output(
            ["git", "ls-files", "--cached", "docs/release_data/provenance.json"],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(tracked, "")


if __name__ == "__main__":
    unittest.main()
