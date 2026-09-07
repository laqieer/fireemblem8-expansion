import copy
import unittest

from scripts.validation_ownership import ci_verifier, reporter
from scripts.validation_ownership.budget import MakeProbeError


class BasePinnedVerifierTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
