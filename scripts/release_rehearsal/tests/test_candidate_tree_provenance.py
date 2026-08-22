"""Issue #29 candidate-tree provenance controls."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.release_rehearsal import candidate_tree as ct
from scripts.release_rehearsal import git_source as gs
from scripts.release_rehearsal import provenance as prov


class CandidateTreeProvenanceTests(unittest.TestCase):
    def test_metadata_is_human_facts_without_identity_snapshots(self):
        data = json.loads((ROOT / "docs/release_data/provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertNotIn("generation_basis_sha", data)
        self.assertEqual(
            len(data["entries"]),
            len(ct.load(ROOT, gs.write_index_tree(ROOT)).entries),
        )
        self.assertTrue(data["facts"])
        for fact in data["facts"].values():
            self.assertFalse(prov.FORBIDDEN_KEYS & set(fact))

    def test_missing_or_stale_exact_path_metadata_fails_closed(self):
        tree = ct.CandidateTree(
            "0" * 40,
            (gs.GitEntry("src/example.c", "100644", "blob", "1" * 40),),
        )
        missing = prov.check_candidate_tree([], tree)
        self.assertEqual(missing, ["missing human provenance metadata for src/example.c"])
        stale = prov.check_candidate_tree(
            [{
                "path": "old/example.c",
                "category": "code",
                "author": "NOASSERTION",
                "rightsholder": "NOASSERTION",
                "license": "NOASSERTION",
                "redistribution_approved": False,
                "reviewer": None,
                "notes": "fixture",
            }],
            tree,
        )
        self.assertEqual(
            stale,
            [
                "missing human provenance metadata for src/example.c",
                "stale human provenance metadata for old/example.c",
            ],
        )

    def test_gitlink_requires_submodule_human_fact(self):
        tree = ct.CandidateTree(
            "0" * 40,
            (gs.GitEntry("vendor", "160000", "commit", "2" * 40),),
        )
        reasons = prov.check_candidate_tree(
            [{
                "path": "vendor",
                "category": "code",
                "author": "NOASSERTION",
                "rightsholder": "NOASSERTION",
                "license": "NOASSERTION",
                "redistribution_approved": False,
                "reviewer": None,
                "notes": "fixture",
            }],
            tree,
        )
        self.assertEqual(reasons, ["vendor: gitlink requires submodule human provenance metadata"])


if __name__ == "__main__":
    unittest.main()
