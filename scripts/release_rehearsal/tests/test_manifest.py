"""Manifest checks for issue #29's candidate-tree provenance boundary."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "modernize"))

from scripts.release_rehearsal import git_source as gs
from scripts.release_rehearsal import manifest as rm


class CandidateTreeManifestTests(unittest.TestCase):
    def test_exact_membership_and_modes_come_from_staged_candidate_tree(self):
        target_tree = gs.write_index_tree(ROOT)
        report = rm.check_allowlist_exact(ROOT, target_tree)
        self.assertTrue(report["ok"], report["errors"])
        self.assertIn("mgfembp", report["modes"])
        self.assertEqual(report["modes"]["mgfembp"], "160000")

    def test_source_guard_accepts_the_staged_candidate_tree(self):
        target_tree = gs.write_index_tree(ROOT)
        report = rm.check_source_guard(ROOT, target_tree)
        self.assertEqual(report["status"], "pass", report["violations"])

    def test_manifest_reports_candidate_tree_not_removed_allowlist(self):
        manifest = rm.build_manifest(ROOT, "release", "aapcs", "16M")
        self.assertIn("candidate_tree", manifest)
        self.assertNotIn("allowlist", manifest)
        self.assertNotIn("tree_coverage", manifest)


class TargetShaTests(unittest.TestCase):
    def test_target_override_requires_exact_commit_sha(self):
        with self.assertRaises(rm.ManifestError):
            rm.resolve_target_sha(ROOT, "deadbeef")


if __name__ == "__main__":
    unittest.main()
