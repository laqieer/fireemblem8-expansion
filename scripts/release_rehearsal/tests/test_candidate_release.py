"""Issue #29 release validation without provenance/legal-review metadata."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.release_rehearsal import candidate_tree as ct
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

    def test_removed_metadata_is_not_tracked(self):
        tracked = subprocess.check_output(
            ["git", "ls-files", "--cached", "docs/release_data/provenance.json"],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(tracked, "")


if __name__ == "__main__":
    unittest.main()
