import copy
import errno
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.validation_ownership import ci_verifier, reporter
from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import MakeProbeError
from .report_fixture import ReportFixture, reviewed_evolution_case, reviewed_exclusion_case


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

    def exact_arguments(self, base, candidate):
        return (
            "--base-sha", base,
            "--candidate-sha", candidate,
            "--trusted-sha", base,
            "--expected-mode", "exact-base-pinned",
        )

    def test_exact_verifier_reuses_one_trusted_tree_for_two_actual_captures(self):
        revision = self.fixture.git("rev-parse", "HEAD").decode().strip()
        trusted = self.trusted_root(revision)
        self.addCleanup(lambda: trusted.exists() and shutil.rmtree(trusted))
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
        self.addCleanup(lambda: trusted.exists() and shutil.rmtree(trusted))
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

    def test_exclusion_evolution_rejects_strict_default_and_invalidates_all_edges(self):
        case = reviewed_exclusion_case(self.fixture)
        base_trusted = self.trusted_root(case["base"])
        reviewed_trusted = self.fixture.directory / ("trusted-exclusion-" + case["head"][:12])
        reviewed_trusted.mkdir()
        with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", case["head"]))) as archive:
            archive.extractall(reviewed_trusted, filter="data")
        self.addCleanup(lambda: base_trusted.exists() and shutil.rmtree(base_trusted))
        self.addCleanup(lambda: reviewed_trusted.exists() and shutil.rmtree(reviewed_trusted))
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
        self.assertIn("retargets exact-base oracle authority", strict.stderr)
        reviewed = self.verify(
            reviewed_trusted,
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
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        result = json.loads(reviewed.stdout)
        self.assertEqual(result["review_invalidation"]["changed_edge_ids"], case["reviewed_edges"])


if __name__ == "__main__":
    unittest.main()
