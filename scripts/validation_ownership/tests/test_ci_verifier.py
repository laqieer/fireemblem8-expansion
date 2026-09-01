from __future__ import annotations

import copy
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.validation_ownership import ci_verifier, reporter


ROOT = Path(__file__).resolve().parents[3]
SCRATCH_ROOT = ROOT / "build" / "test-artifacts" / "validation-ownership"


class BasePinnedVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = reporter.prepare_validation_scratch(ROOT)

    @classmethod
    def tearDownClass(cls):
        reporter.cleanup_validation_scratch(cls.scratch)

    @staticmethod
    def authority_fixture():
        graph = {
            "nodes": [
                {
                    "id": "surface.validation",
                    "kind": "surface",
                    "surface_type": "host",
                    "requirements": [],
                    "dependencies": [],
                },
                {
                    "id": "owner.validation-check",
                    "kind": "evidence",
                    "evidence_type": "host",
                    "authority": {
                        "kind": "workflow-step",
                        "job": "host-tests",
                        "step": "Validate validation ownership graph (issue #180)",
                    },
                },
                {
                    "id": "owner.host-runtime",
                    "kind": "evidence",
                    "evidence_type": "host",
                    "authority": {
                        "kind": "workflow-step",
                        "job": "host-tests",
                        "step": "Run gba-playtest host test suite",
                    },
                },
            ],
            "edges": [
                {
                    "id": "validation.owns-test",
                    "type": "owns-test",
                    "source": "surface.validation",
                    "target": "owner.validation-check",
                    "reason": "exact ownership fixture",
                },
            ],
            "path_rules": [],
        }
        model = {
            "authorities": {
                "owner.validation-check": {
                    "display": (
                        ".github/workflows/build.yml:host-tests:"
                        "Validate validation ownership graph (issue #180)"
                    ),
                    "fingerprint": "base-validation-fingerprint",
                },
                "owner.host-runtime": {
                    "display": (
                        ".github/workflows/build.yml:host-tests:"
                        "Run gba-playtest host test suite"
                    ),
                    "fingerprint": "gba-playtest-fingerprint",
                },
            }
        }
        return graph, model

    @staticmethod
    def base_entries(paths):
        return {
            path: reporter.GitTreeEntry(
                path,
                "100644",
                "blob",
                "0" * 40,
            )
            for path in paths
        }

    def test_base_mode_distinguishes_introduction_complete_and_partial(self):
        self.assertEqual(
            ci_verifier._base_authority_mode({}),
            "bootstrap-not-authoritative",
        )
        complete = self.base_entries(ci_verifier.BASE_AUTHORITY_PATHS)
        self.assertEqual(
            ci_verifier._base_authority_mode(complete),
            "exact-base-pinned",
        )
        partial = dict(complete)
        partial.pop("scripts/validation_ownership/reporter.py")
        with self.assertRaisesRegex(
            reporter.OwnershipError,
            "incomplete validation authority",
        ):
            ci_verifier._base_authority_mode(partial)
        make_dynamics_only = self.base_entries(
            {reporter.MAKE_DYNAMIC_PATH.as_posix()}
        )
        with self.assertRaisesRegex(
            reporter.OwnershipError,
            "incomplete validation authority",
        ):
            ci_verifier._base_authority_mode(make_dynamics_only)

    def test_introduction_verify_reports_no_authority_without_runtime(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            repository = Path(directory) / "repository"
            subprocess.run(
                ["git", "init", "-q", "-b", "master", str(repository)],
                check=True,
            )
            environment = {
                "GIT_AUTHOR_NAME": "Introduction Fixture",
                "GIT_AUTHOR_EMAIL": "introduction@example.invalid",
                "GIT_COMMITTER_NAME": "Introduction Fixture",
                "GIT_COMMITTER_EMAIL": "introduction@example.invalid",
                "HOME": "/nonexistent",
                "PATH": "/usr/bin:/bin",
            }
            (repository / "base.txt").write_text("base\n", encoding="ascii")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base without verifier"],
                cwd=repository,
                env=environment,
                check=True,
            )
            base_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                text=True,
            ).strip()
            (repository / "candidate.txt").write_text(
                "candidate\n",
                encoding="ascii",
            )
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "candidate"],
                cwd=repository,
                env=environment,
                check=True,
            )
            candidate_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                text=True,
            ).strip()
            result = ci_verifier.verify(
                ROOT,
                repository,
                base_sha,
                candidate_sha,
            )
            self.assertEqual(
                result,
                {
                    "authority": "none",
                    "base_sha": base_sha,
                    "candidate_sha": candidate_sha,
                    "mode": "bootstrap-not-authoritative",
                    "reason": (
                        "exact base predates validation ownership authority"
                    ),
                },
            )
            self.assertFalse(
                (ROOT / ".validation-ownership-runtime").exists()
            )

    def test_candidate_owner_redirect_rejects_exact_base_oracle(self):
        oracle = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "expected_surface": "surface.validation",
                    "expected_owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        matching = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "surface": "surface.validation",
                    "owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        graph, model = self.authority_fixture()
        with mock.patch.object(
            reporter,
            "_measure",
            return_value=matching,
        ):
            ci_verifier._verify_oracle_pairs(
                oracle,
                graph,
                model,
                graph,
                model,
            )

        redirected = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "surface": "surface.validation",
                    "owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-suite",
                        }
                    ],
                }
            ]
        }
        with mock.patch.object(
            reporter,
            "_measure",
            return_value=redirected,
        ), self.assertRaisesRegex(
            reporter.OwnershipError,
            "differ byte-for-byte",
        ):
            ci_verifier._verify_oracle_pairs(
                oracle,
                graph,
                model,
                graph,
                model,
            )

    def test_exact_base_oracle_rejects_authority_retarget_and_fingerprint(self):
        oracle = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "expected_surface": "surface.validation",
                    "expected_owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        measurement = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "surface": "surface.validation",
                    "owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        base_graph, base_model = self.authority_fixture()

        retargeted_graph = copy.deepcopy(base_graph)
        retargeted_node = next(
            node
            for node in retargeted_graph["nodes"]
            if node["id"] == "owner.validation-check"
        )
        runtime_node = next(
            node
            for node in retargeted_graph["nodes"]
            if node["id"] == "owner.host-runtime"
        )
        retargeted_node["authority"], runtime_node["authority"] = (
            runtime_node["authority"],
            retargeted_node["authority"],
        )
        retargeted_model = copy.deepcopy(base_model)
        (
            retargeted_model["authorities"]["owner.validation-check"],
            retargeted_model["authorities"]["owner.host-runtime"],
        ) = (
            retargeted_model["authorities"]["owner.host-runtime"],
            retargeted_model["authorities"]["owner.validation-check"],
        )
        with mock.patch.object(
            reporter,
            "_measure",
            return_value=measurement,
        ), self.assertRaisesRegex(
            reporter.OwnershipError,
            "retargets exact-base oracle authority",
        ):
            ci_verifier._verify_oracle_pairs(
                oracle,
                retargeted_graph,
                retargeted_model,
                base_graph,
                base_model,
            )

        changed_fingerprint = copy.deepcopy(base_model)
        changed_fingerprint["authorities"]["owner.validation-check"][
            "fingerprint"
        ] = "redirected-command-fingerprint"
        with mock.patch.object(
            reporter,
            "_measure",
            return_value=measurement,
        ), self.assertRaisesRegex(
            reporter.OwnershipError,
            "retargets exact-base oracle authority",
        ):
            ci_verifier._verify_oracle_pairs(
                oracle,
                base_graph,
                changed_fingerprint,
                base_graph,
                base_model,
            )

    def test_generated_schema_owner_retarget_is_oracle_backed(self):
        base_graph, base_model = self.authority_fixture()
        surface = next(
            node
            for node in base_graph["nodes"]
            if node["id"] == "surface.validation"
        )
        surface["id"] = "surface.generated-schema"
        edge = base_graph["edges"][0]
        edge["id"] = "generated-schema.owns-test"
        edge["source"] = "surface.generated-schema"
        oracle = {
            "probes": [
                {
                    "path": "scripts/generated_data/registry.py",
                    "expected_surface": "surface.generated-schema",
                    "expected_owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        measurement = {
            "probes": [
                {
                    "path": "scripts/generated_data/registry.py",
                    "surface": "surface.generated-schema",
                    "owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        candidate_graph = copy.deepcopy(base_graph)
        candidate_model = copy.deepcopy(base_model)
        validation_node = next(
            node
            for node in candidate_graph["nodes"]
            if node["id"] == "owner.validation-check"
        )
        runtime_node = next(
            node
            for node in candidate_graph["nodes"]
            if node["id"] == "owner.host-runtime"
        )
        validation_node["authority"], runtime_node["authority"] = (
            runtime_node["authority"],
            validation_node["authority"],
        )
        (
            candidate_model["authorities"]["owner.validation-check"],
            candidate_model["authorities"]["owner.host-runtime"],
        ) = (
            candidate_model["authorities"]["owner.host-runtime"],
            candidate_model["authorities"]["owner.validation-check"],
        )
        with mock.patch.object(
            reporter,
            "_measure",
            return_value=measurement,
        ), self.assertRaisesRegex(
            reporter.OwnershipError,
            "generated-schema.owns-test",
        ):
            ci_verifier._verify_oracle_pairs(
                oracle,
                candidate_graph,
                candidate_model,
                base_graph,
                base_model,
            )

    def test_unprobed_owned_edge_rejects_exact_base(self):
        graph, model = self.authority_fixture()
        graph["edges"].append(
            {
                "id": "generated-schema.owns-test",
                "type": "owns-test",
                "source": "surface.validation",
                "target": "owner.host-runtime",
                "reason": "unprobed owner fixture",
            }
        )
        oracle = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "expected_surface": "surface.validation",
                    "expected_owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        measurement = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "surface": "surface.validation",
                    "owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        with mock.patch.object(
            reporter,
            "_measure",
            return_value=measurement,
        ), self.assertRaisesRegex(
            reporter.OwnershipError,
            "leaves owned edges unprobed.*generated-schema.owns-test",
        ):
            ci_verifier._verify_oracle_pairs(
                oracle,
                graph,
                model,
                graph,
                model,
            )

    def test_exact_base_oracle_allows_unrelated_semantic_stability(self):
        oracle = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "expected_surface": "surface.validation",
                    "expected_owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        measurement = {
            "probes": [
                {
                    "path": "scripts/validation_ownership/reporter.py",
                    "surface": "surface.validation",
                    "owners": [
                        {
                            "edge_type": "owns-test",
                            "evidence_id": "owner.validation-check",
                        }
                    ],
                }
            ]
        }
        base_graph, base_model = self.authority_fixture()
        candidate_graph = copy.deepcopy(base_graph)
        next(
            node
            for node in candidate_graph["nodes"]
            if node["id"] == "owner.host-runtime"
        )["label"] = "Unrelated presentation-only change"
        candidate_model = copy.deepcopy(base_model)
        with mock.patch.object(
            reporter,
            "_measure",
            return_value=measurement,
        ):
            ci_verifier._verify_oracle_pairs(
                oracle,
                candidate_graph,
                candidate_model,
                base_graph,
                base_model,
            )

    def test_base_verifier_step_is_a_pinned_security_boundary(self):
        step = (
            "    - name: Validate ownership with exact PR-base verifier\n"
            "      run: /usr/bin/git archive \"$EXPECTED_BASE_SHA\"\n"
        )
        workflow = "jobs:\n  host:\n    steps:\n" + step + (
            "    - name: Candidate tests\n"
            "      run: true\n"
        )
        self.assertEqual(ci_verifier._base_step(workflow), step)
        changed = workflow.replace(
            "$EXPECTED_BASE_SHA",
            "$EXPECTED_CANDIDATE_SHA",
        )
        self.assertNotEqual(
            ci_verifier._base_step(changed),
            ci_verifier._base_step(workflow),
        )

    def test_trusted_runtime_root_is_external_unique_and_no_follow(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            base = Path(directory)
            trusted = base / "trusted"
            trusted.mkdir(mode=0o700)
            runtime = ci_verifier._prepare_trusted_runtime_root(trusted)
            self.assertEqual(runtime.parent, trusted)
            self.assertTrue(runtime.is_dir())
            self.assertFalse(runtime.is_symlink())
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "cannot create trusted verifier runtime root",
            ):
                ci_verifier._prepare_trusted_runtime_root(trusted)

            outside = base / "outside"
            sentinel = outside / "sentinel"
            outside.mkdir()
            sentinel.write_text("preserve\n", encoding="ascii")
            linked_trusted = base / "linked-trusted"
            linked_trusted.mkdir(mode=0o700)
            (linked_trusted / ".validation-ownership-runtime").symlink_to(
                outside,
                target_is_directory=True,
            )
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "cannot create trusted verifier runtime root",
            ):
                ci_verifier._prepare_trusted_runtime_root(linked_trusted)
            self.assertEqual(
                sentinel.read_text(encoding="ascii"),
                "preserve\n",
            )
            self.assertEqual(
                {item.name for item in outside.iterdir()},
                {"sentinel"},
            )

    def test_candidate_reporter_and_interceptor_cannot_replace_base_authority(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            environment = {
                "GIT_AUTHOR_NAME": "Verifier Test",
                "GIT_AUTHOR_EMAIL": "verifier@example.invalid",
                "GIT_COMMITTER_NAME": "Verifier Test",
                "GIT_COMMITTER_EMAIL": "verifier@example.invalid",
                "HOME": "/nonexistent",
                "PATH": "/usr/bin:/bin",
            }
            trusted = {
                "scripts/validation_ownership/reporter.py": (
                    "def public_report_fixture():\n    return 'base'\n"
                ),
                "scripts/validation_ownership/shell_interceptor.c": (
                    "/* Public base fixture. */\n"
                ),
            }
            for path, content in trusted.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="ascii")
            (root / "candidate.txt").write_text("base\n", encoding="ascii")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base"],
                cwd=root,
                env=environment,
                check=True,
            )
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            for path in trusted:
                (root / path).write_text(
                    "/* Public candidate mutation fixture. */\n",
                    encoding="ascii",
                )
            (root / "candidate.txt").write_text("candidate\n", encoding="ascii")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "candidate"],
                cwd=root,
                env=environment,
                check=True,
            )
            candidate_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            base_entries = reporter.git_tree_entries(root, base_sha)
            base_loader = reporter.AuthorityLoader(
                root,
                base_entries,
                base_sha,
            )
            entries, loader, changes = ci_verifier._pinned_loader(
                root,
                candidate_sha,
                base_loader,
                set(trusted),
            )
            self.assertEqual(changes, sorted(trusted))
            for path, content in trusted.items():
                self.assertEqual(
                    loader.read_blob(path, "trusted test"),
                    content.encode("ascii"),
                )
                self.assertEqual(entries[path], base_entries[path])
            self.assertEqual(
                loader.read_blob("candidate.txt", "candidate test"),
                b"candidate\n",
            )

    def test_base_gate_rejects_candidate_make_controls(self):
        gate = ROOT / "scripts/validation_ownership/ci_gate.mk"
        public_fixture_revision = hashlib.sha1(
            b"public validation ownership fixture",
            usedforsecurity=False,
        ).hexdigest()
        environment = {
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "VO_BASE_SHA": public_fixture_revision,
            "VO_CANDIDATE_SHA": public_fixture_revision,
            "VO_REPOSITORY_ROOT": str(ROOT),
            "VO_TRUSTED_ROOT": str(ROOT),
        }
        cases = (
            ["MAKECMDGOALS=", "-n", "validation-ownership-check"],
            ["SHELL=/bin/false", "validation-ownership-check"],
            ["validation-ownership-check", "other"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    ["/usr/bin/make", "-f", str(gate), *arguments],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
