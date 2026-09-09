import copy
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import unittest

from scripts.validation_ownership import ci_verifier, reporter
from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import MakeProbeError
from .report_fixture import ReportFixture, reviewed_evolution_case


class BasePinnedVerifierTests(unittest.TestCase):
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

    def trusted_root(self, revision):
        trusted = self.fixture.directory / ("trusted-" + revision[:12])
        trusted.mkdir()
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", revision))) as archive:
            archive.extractall(trusted, filter="data")
        return trusted

    def verify(self, trusted, *arguments):
        return subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                str(trusted / "scripts/validation_ownership/ci_verifier.py"),
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

    def test_reviewed_evolution_accepts_exact_new_surface_and_authority_change(self):
        case = reviewed_evolution_case(self.fixture)
        trusted = self.trusted_root(case["head"])
        self.addCleanup(lambda: trusted.exists() and shutil.rmtree(trusted))
        completed = self.verify(
            trusted,
            "--base-sha",
            case["base"],
            "--candidate-sha",
            case["head"],
            "--trusted-sha",
            case["head"],
            "--expected-mode",
            "reviewed-evolution",
            "--reviewed-repository",
            "owner/repository",
            "--reviewed-pull-request",
            "186",
            *(item for path in case["reviewed_paths"] for item in ("--reviewed-path", path)),
            *(item for edge_id in case["reviewed_edges"] for item in ("--reviewed-edge", edge_id)),
            *(item for consumer_id in case["affected_consumers"]
              for item in ("--reviewed-consumer", consumer_id)),
        )
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
        self.addCleanup(lambda: trusted.exists() and shutil.rmtree(trusted))
        self.addCleanup(lambda: base_trusted.exists() and shutil.rmtree(base_trusted))
        strict = self.verify(
            base_trusted,
            "--base-sha",
            case["base"],
            "--candidate-sha",
            case["head"],
            "--trusted-sha",
            case["base"],
            "--expected-mode",
            "exact-base-pinned",
        )
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("leaves graph surfaces unprobed", strict.stderr)
        wrong_paths = self.verify(
            trusted,
            "--base-sha",
            case["base"],
            "--candidate-sha",
            case["head"],
            "--trusted-sha",
            case["head"],
            "--expected-mode",
            "reviewed-evolution",
            "--reviewed-repository",
            "owner/repository",
            "--reviewed-pull-request",
            "186",
            *(item for path in case["reviewed_paths"][1:] for item in ("--reviewed-path", path)),
            *(item for edge_id in case["reviewed_edges"] for item in ("--reviewed-edge", edge_id)),
            *(item for consumer_id in case["affected_consumers"]
              for item in ("--reviewed-consumer", consumer_id)),
        )
        self.assertNotEqual(wrong_paths.returncode, 0)
        self.assertIn("path scope differs", wrong_paths.stderr)
        wrong_edges = self.verify(
            trusted,
            "--base-sha",
            case["base"],
            "--candidate-sha",
            case["head"],
            "--trusted-sha",
            case["head"],
            "--expected-mode",
            "reviewed-evolution",
            "--reviewed-repository",
            "owner/repository",
            "--reviewed-pull-request",
            "186",
            *(item for path in case["reviewed_paths"] for item in ("--reviewed-path", path)),
            *(item for edge_id in case["reviewed_edges"][:-1] for item in ("--reviewed-edge", edge_id)),
            *(item for consumer_id in case["affected_consumers"]
              for item in ("--reviewed-consumer", consumer_id)),
        )
        self.assertNotEqual(wrong_edges.returncode, 0)
        self.assertIn("relationship scope differs", wrong_edges.stderr)
        consumer_trusted = self.fixture.directory / ("trusted-consumers-" + case["head"][:12])
        consumer_trusted.mkdir()
        self.addCleanup(lambda: consumer_trusted.exists() and shutil.rmtree(consumer_trusted))
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", case["head"]))) as archive:
            archive.extractall(consumer_trusted, filter="data")
        wrong_consumers = self.verify(
            consumer_trusted,
            "--base-sha",
            case["base"],
            "--candidate-sha",
            case["head"],
            "--trusted-sha",
            case["head"],
            "--expected-mode",
            "reviewed-evolution",
            "--reviewed-repository",
            "owner/repository",
            "--reviewed-pull-request",
            "186",
            *(item for path in case["reviewed_paths"] for item in ("--reviewed-path", path)),
            *(item for edge_id in case["reviewed_edges"] for item in ("--reviewed-edge", edge_id)),
            *(item for consumer_id in case["affected_consumers"][:-1]
              for item in ("--reviewed-consumer", consumer_id)),
        )
        self.assertNotEqual(wrong_consumers.returncode, 0)
        self.assertIn("consumer scope differs", wrong_consumers.stderr)


if __name__ == "__main__":
    unittest.main()
