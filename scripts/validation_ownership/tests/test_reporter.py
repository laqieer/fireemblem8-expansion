from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.validation_ownership import reporter


ROOT = Path(__file__).resolve().parents[3]
GRAPH_PATH = ROOT / reporter.GRAPH_PATH
SCHEMA_PATH = ROOT / reporter.SCHEMA_PATH
ORACLE_PATH = ROOT / reporter.PROBE_ORACLE_PATH
SCRATCH_ROOT = ROOT / "build" / "test-artifacts" / "validation-ownership"



class OwnershipGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = reporter.load_json(GRAPH_PATH)
        cls.schema = reporter.load_json(SCHEMA_PATH)
        cls.oracle = reporter.load_json(ORACLE_PATH)
        cls.entries = reporter.git_tree_entries(ROOT)
        for path in (
            "scripts/validation_ownership/ci_gate.mk",
            "scripts/validation_ownership/ci_verifier.py",
            "scripts/validation_ownership/make_probe.py",
            "scripts/validation_ownership/sandbox_exec.py",
            "scripts/validation_ownership/shell_interceptor.c",
            "scripts/validation_ownership/tests/test_make_probe.py",
            "scripts/validation_ownership/tests/test_ci_verifier.py",
        ):
            if path not in cls.entries and (ROOT / path).is_file():
                cls.entries[path] = reporter.GitTreeEntry(
                    path,
                    "100644",
                    "blob",
                    "0" * 40,
                )
        cls.loader = reporter.AuthorityLoader(ROOT, cls.entries)
        cls.fixture_entries = {
            path: cls.entries[path]
            for path in {
                *(probe["path"] for probe in cls.oracle["probes"]),
                "mgfembp",
            }
        }
        cls.source_status = reporter.repository_status(ROOT)
        cls.protected_source_bytes = {
            path: (ROOT / path).read_bytes()
            for path in (
                "Makefile",
                reporter.BUILD_WORKFLOW_PATH.as_posix(),
                reporter.GRAPH_PATH.as_posix(),
                reporter.PROBE_ORACLE_PATH.as_posix(),
                reporter.MAKE_DYNAMIC_PATH.as_posix(),
            )
        }
        cls.scratch = reporter.prepare_validation_scratch(ROOT)
        cls.fixture_container = tempfile.TemporaryDirectory(
            prefix="authority-checkout-",
            dir=cls.scratch.path,
        )
        cls.fixture_root = Path(cls.fixture_container.name) / "checkout"
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--no-hardlinks",
                "--no-recurse-submodules",
                str(ROOT),
                str(cls.fixture_root),
            ],
            check=True,
        )
        common_git = Path(
            subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        fixture_module = (
            cls.fixture_root / ".git" / "modules" / "mgfembp"
        )
        fixture_module.parent.mkdir(parents=True)
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--bare",
                "--no-hardlinks",
                str(common_git / "modules" / "mgfembp"),
                str(fixture_module),
            ],
            check=True,
        )
        changed = [
            path
            for path in subprocess.run(
            ["git", "diff", "--name-only", "-z", "HEAD", "--"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            ).stdout.decode("utf-8").split("\0")
            if path
        ]
        changed.extend(
            path
            for path in subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", "-z"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout.decode("utf-8").split("\0")
            if path
        )
        changed = list(dict.fromkeys(changed))
        for relative in changed:
            source = ROOT / relative
            if source.is_file() and not source.is_symlink():
                target = cls.fixture_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        if changed:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=cls.fixture_root,
                check=True,
            )
            environment = {
                "GIT_AUTHOR_NAME": "Ownership Fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "Ownership Fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                "PATH": os.environ.get("PATH", ""),
            }
            subprocess.run(
                ["git", "commit", "-q", "-m", "ownership fixture"],
                cwd=cls.fixture_root,
                env=environment,
                check=True,
            )

    @classmethod
    def tearDownClass(cls):
        try:
            current = {
                path: (ROOT / path).read_bytes()
                for path in cls.protected_source_bytes
            }
            if current != cls.protected_source_bytes:
                raise AssertionError("ownership tests changed protected source authority")
            if reporter.repository_status(ROOT) != cls.source_status:
                raise AssertionError("ownership tests changed source repository status")
        finally:
            cls.fixture_container.cleanup()
            reporter.cleanup_validation_scratch(cls.scratch)

    def validate(self, graph=None, entries=None):
        return reporter.validate_graph(
            self.graph if graph is None else graph,
            self.schema,
            self.loader,
            self.fixture_entries if entries is None else entries,
        )

    def fixture_authority(self):
        entries = reporter.git_tree_entries(self.fixture_root)
        loader = reporter.AuthorityLoader(self.fixture_root, entries)
        graph = loader.read_json(reporter.GRAPH_PATH, "fixture graph")
        schema = loader.read_json(reporter.SCHEMA_PATH, "fixture schema")
        return entries, loader, graph, schema

    def test_fixture_exception_never_changes_source_authority(self):
        source_before = {
            path: (ROOT / path).read_bytes()
            for path in self.protected_source_bytes
        }
        status_before = reporter.repository_status(ROOT)
        graph_path = self.fixture_root / reporter.GRAPH_PATH
        fixture_before = graph_path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            try:
                graph_path.write_bytes(b"{}\n")
                raise RuntimeError("simulated fixture failure")
            finally:
                graph_path.write_bytes(fixture_before)
        self.assertEqual(
            {
                path: (ROOT / path).read_bytes()
                for path in self.protected_source_bytes
            },
            source_before,
        )
        self.assertEqual(reporter.repository_status(ROOT), status_before)

    def test_whole_repository_has_exact_coverage(self):
        model = self.validate(entries=self.entries)
        self.assertEqual(len(model["coverage"]), len(self.entries))
        self.assertEqual(
            [path for path, record in model["coverage"].items() if record["kind"] == "excluded"],
            [".github/CODEOWNERS", "mgfembp"],
        )

    def test_representative_surface_resolutions_and_measurement(self):
        report = reporter.build_report(
            self.graph,
            self.schema,
            self.oracle,
            self.loader,
            self.entries,
            (
                probe["path"]
                for probe in self.oracle["probes"]
                if "expected_exclusion" not in probe
            ),
        )
        self.assertEqual(report["measurement"]["false_positive_selections"], 0)
        self.assertEqual(report["measurement"]["false_negative_selections"], 0)
        actual = {
            record["path"]: record["surface"] for record in report["resolutions"]
        }
        expected = {
            probe["path"]: probe["expected_surface"]
            for probe in self.oracle["probes"]
            if "expected_surface" in probe
        }
        self.assertEqual(actual, expected)
        for resolution in report["resolutions"]:
            self.assertTrue(resolution["owners"])
            self.assertTrue(
                all(owner["reason"] and owner["gate"] for owner in resolution["owners"])
            )
        codeowners = next(
            record
            for record in report["measurement"]["probes"]
            if record["path"] == ".github/CODEOWNERS"
        )
        self.assertEqual(
            codeowners["exclusion"],
            "exclude.codeowners-external-enforcement",
        )
        workflow = next(
            record
            for record in report["resolutions"]
            if record["path"] == ".github/workflows/build.yml"
        )
        self.assertIn(
            "Run workflow contract test suite",
            {owner["gate"].rsplit(":", 1)[-1] for owner in workflow["owners"]},
        )
        pr_template = next(
            record
            for record in report["resolutions"]
            if record["path"] == ".github/PULL_REQUEST_TEMPLATE.md"
        )
        self.assertIn(
            "Check documentation (issues #7/#17)",
            {owner["gate"].rsplit(":", 1)[-1] for owner in pr_template["owners"]},
        )

    def test_generated_paths_come_from_typed_registry(self):
        model = self.validate(entries=self.entries)
        generated = {
            path
            for path, record in model["coverage"].items()
            if record.get("surface") == "surface.generated"
        }
        _, registry_paths = reporter._generated_registry_records(self.loader)
        self.assertTrue(registry_paths)
        self.assertLessEqual(registry_paths, generated)
        self.assertIn("src/data/items.json", generated)

    def test_unknown_path_and_fail_closed_exclusion_reject(self):
        model = self.validate()
        with self.assertRaisesRegex(reporter.OwnershipError, "absent from"):
            reporter._resolve_path("unowned/new.c", self.graph, model)
        with self.assertRaisesRegex(reporter.OwnershipError, "fail-closed.*exclusion"):
            reporter._resolve_path("mgfembp", self.graph, model)
        with self.assertRaisesRegex(
            reporter.OwnershipError,
            "fail-closed external enforcement",
        ):
            reporter._resolve_path(".github/CODEOWNERS", self.graph, model)
        with self.assertRaisesRegex(reporter.OwnershipError, "no ownership contract"):
            entries = dict(self.fixture_entries)
            entries["unowned/new.c"] = reporter.GitTreeEntry(
                "unowned/new.c",
                "100644",
                "blob",
                "0" * 40,
            )
            self.validate(entries=entries)

    def test_git_modes_and_changed_path_provenance_fail_closed(self):
        entries = dict(self.fixture_entries)
        entries["include/global.h"] = reporter.GitTreeEntry(
            "include/global.h",
            reporter.SYMLINK_MODE,
            "blob",
            "0" * 40,
        )
        with self.assertRaisesRegex(reporter.OwnershipError, "120000"):
            self.validate(entries=entries)

        entries = dict(self.fixture_entries)
        entries["scripts/check_docs.py"] = reporter.GitTreeEntry(
            "scripts/check_docs.py",
            reporter.GITLINK_MODE,
            "commit",
            "0" * 40,
        )
        with self.assertRaisesRegex(reporter.OwnershipError, "gitlink.*exclusion"):
            self.validate(entries=entries)

        model = self.validate()
        with self.assertRaisesRegex(reporter.OwnershipError, "absent from"):
            reporter._resolve_path("src/new.c", self.graph, model)
        with self.assertRaisesRegex(reporter.OwnershipError, "absent from"):
            reporter._resolve_path("build/ignored.c", self.graph, model)

        base_entries = {
            "src/removed.c": reporter.GitTreeEntry(
                "src/removed.c",
                "100644",
                "blob",
                "1" * 40,
            ),
        }
        resolution = reporter._resolve_path(
            "src/removed.c",
            self.graph,
            model,
            base_entries,
        )
        self.assertEqual(resolution["surface"], "surface.runtime")

        base_entries["include/global.h"] = reporter.GitTreeEntry(
            "include/global.h",
            "100755",
            "blob",
            "2" * 40,
        )
        with self.assertRaisesRegex(reporter.OwnershipError, "changes Git mode"):
            reporter._resolve_path(
                "include/global.h",
                self.graph,
                model,
                base_entries,
            )

    def test_authority_loader_rejects_symlink_escape_and_nonblob_mode(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            outside = SCRATCH_ROOT / "outside-authority.json"
            outside.write_text("{}\n", encoding="ascii")
            link = scratch / "authority.json"
            link.symlink_to(outside)
            entries = {
                "authority.json": reporter.GitTreeEntry(
                    "authority.json",
                    "100644",
                    "blob",
                    "0" * 40,
                )
            }
            loader = reporter.AuthorityLoader(scratch, entries)
            with self.assertRaisesRegex(reporter.OwnershipError, "regular file"):
                loader.read_blob("authority.json", "fixture authority")
            entries["authority.json"] = reporter.GitTreeEntry(
                "authority.json",
                reporter.SYMLINK_MODE,
                "blob",
                "0" * 40,
            )
            with self.assertRaisesRegex(reporter.OwnershipError, "regular blob"):
                loader.read_blob("authority.json", "fixture authority")
            outside.unlink()

    def test_scratch_components_reject_symlinks_without_outside_writes(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            base = Path(directory)
            outside = base / "outside"
            outside.mkdir()
            root_link = base / "repo"
            root_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "authority root must be a non-symlink directory",
            ):
                reporter.prepare_validation_scratch(root_link)
            self.assertEqual(list(outside.iterdir()), [])

        for symlink_component in ("build", "test-artifacts", "validation-ownership"):
            with self.subTest(component=symlink_component):
                with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
                    base = Path(directory)
                    root = base / "repo"
                    outside = base / "outside"
                    root.mkdir()
                    outside.mkdir()
                    if symlink_component == "build":
                        (root / "build").symlink_to(outside, target_is_directory=True)
                    elif symlink_component == "test-artifacts":
                        (root / "build").mkdir()
                        (root / "build" / "test-artifacts").symlink_to(
                            outside,
                            target_is_directory=True,
                        )
                    else:
                        (root / "build" / "test-artifacts").mkdir(parents=True)
                        (
                            root
                            / "build"
                            / "test-artifacts"
                            / "validation-ownership"
                        ).symlink_to(outside, target_is_directory=True)
                    with self.assertRaisesRegex(
                        reporter.OwnershipError,
                        "non-symlink directory|open scratch component",
                    ):
                        reporter.prepare_validation_scratch(root)
                    self.assertEqual(list(outside.iterdir()), [])

    def test_overlap_and_duplicate_rule_reject(self):
        graph = copy.deepcopy(self.graph)
        graph["path_rules"].append(
            {
                "id": "paths.ambiguous-runtime",
                "surface": "surface.runtime",
                "include": [{"kind": "prefix", "path": "src/"}],
                "exclude": [],
            }
        )
        with self.assertRaisesRegex(reporter.OwnershipError, "ambiguous ownership"):
            self.validate(graph)

        graph = copy.deepcopy(self.graph)
        graph["path_rules"].append(copy.deepcopy(graph["path_rules"][0]))
        with self.assertRaisesRegex(reporter.OwnershipError, "duplicate path rule"):
            self.validate(graph)

    def test_unknown_edge_type_is_schema_error(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["type"] = "guessed-owner"
        with self.assertRaisesRegex(reporter.OwnershipError, "unknown value"):
            self.validate(graph)

    def test_removing_or_redirecting_every_owner_edge_family_fails(self):
        families = {
            "owns-test",
            "adversarial-control",
            "compile-owner",
            "link-owner",
            "target-scenario",
            "generated-by",
            "drift-check",
            "generated-consumer",
            "dependent-profile",
            "negative-control",
            "manual-handoff",
            "depends-on",
        }
        for family in sorted(families):
            with self.subTest(family=family, mutation="remove"):
                graph = copy.deepcopy(self.graph)
                removed = next(edge for edge in graph["edges"] if edge["type"] == family)
                graph["edges"].remove(removed)
                expected = (
                    "dependency edges"
                    if family == "depends-on"
                    else "missing owner edges"
                )
                with self.assertRaisesRegex(reporter.OwnershipError, expected):
                    self.validate(graph)
            with self.subTest(family=family, mutation="redirect"):
                graph = copy.deepcopy(self.graph)
                edge = next(edge for edge in graph["edges"] if edge["type"] == family)
                edge["target"] = "owner.missing"
                with self.assertRaisesRegex(reporter.OwnershipError, "missing target"):
                    self.validate(graph)

    def test_ambiguous_and_duplicate_owners_reject(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"].append(
            {
                "id": "runtime.second-owner",
                "type": "owns-test",
                "source": "surface.runtime",
                "target": "owner.host-build",
                "reason": "ambiguous fixture",
            }
        )
        with self.assertRaisesRegex(reporter.OwnershipError, "ambiguous owners"):
            self.validate(graph)

        graph = copy.deepcopy(self.graph)
        node = copy.deepcopy(
            next(item for item in graph["nodes"] if item["id"] == "owner.host-generated")
        )
        node["id"] = "owner.duplicate-generated"
        graph["nodes"].append(node)
        with self.assertRaisesRegex(reporter.OwnershipError, "duplicates authority"):
            self.validate(graph)

    def test_cycle_and_missing_dependent_reject(self):
        graph = copy.deepcopy(self.graph)
        next(
            node for node in graph["nodes"] if node["id"] == "surface.runtime"
        )["dependencies"].append("surface.generated")
        next(
            node for node in graph["nodes"] if node["id"] == "surface.generated"
        )["dependencies"].append("surface.runtime")
        graph["edges"].extend(
            [
                {
                    "id": "cycle.runtime-generated",
                    "type": "depends-on",
                    "source": "surface.runtime",
                    "target": "surface.generated",
                    "reason": "cycle fixture",
                },
                {
                    "id": "cycle.generated-runtime",
                    "type": "depends-on",
                    "source": "surface.generated",
                    "target": "surface.runtime",
                    "reason": "cycle fixture",
                },
            ]
        )
        with self.assertRaisesRegex(reporter.OwnershipError, "cycle has no unique owner"):
            self.validate(graph)

        for family in ("dependent-profile", "negative-control"):
            with self.subTest(family=family):
                graph = copy.deepcopy(self.graph)
                graph["edges"] = [
                    edge
                    for edge in graph["edges"]
                    if not (
                        edge["source"] == "surface.configuration"
                        and edge["type"] == family
                    )
                ]
                with self.assertRaisesRegex(reporter.OwnershipError, "missing owner edges"):
                    self.validate(graph)

    def test_manual_handoff_never_replaces_deterministic_evidence(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"] = [
            edge for edge in graph["edges"] if edge["id"] != "manual.compile"
        ]
        with self.assertRaisesRegex(reporter.OwnershipError, "missing owner edges"):
            self.validate(graph)
        handoff = reporter._manual_handoff_record(
            self.loader, ".github/manual-testing-handoff.json"
        )
        self.assertFalse(handoff["eligibility"]["deterministic_criteria"])
        self.assertTrue(handoff["pre_handoff"]["semantic_assertions_primary"])

        graph = copy.deepcopy(self.graph)
        manual = next(
            node
            for node in graph["nodes"]
            if node["id"] == "owner.manual-handoff"
        )
        manual["authority"]["path"] = ".github/CODEOWNERS"
        with self.assertRaisesRegex(reporter.OwnershipError, "must be exactly"):
            self.validate(graph)

    def test_stale_make_workflow_case_and_generated_targets_reject(self):
        mutations = (
            ("owner.host-runtime", "step", "Missing workflow step", "stale workflow step"),
            ("owner.case", "case_id", "TC-WORKFLOW-MISSING-001", "stale tester case"),
        )
        for node_id, field, value, message in mutations:
            with self.subTest(node=node_id):
                graph = copy.deepcopy(self.graph)
                node = next(item for item in graph["nodes"] if item["id"] == node_id)
                node["authority"][field] = value
                with self.assertRaisesRegex(reporter.OwnershipError, message):
                    self.validate(graph)

    def test_workflow_content_drift_changes_exact_step_authority(self):
        workflow = (
            "name: Fixture\non: push\npermissions: read-all\njobs:\n"
            "  host-tests:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "    - name: Alpha\n"
            "      run: echo alpha\n"
            "    - name: Beta\n"
            "      run: echo beta\n"
        )
        before_jobs, before_steps = reporter._generic_workflow_authorities(workflow)
        commented_jobs, commented_steps = reporter._generic_workflow_authorities(
            workflow.replace("jobs:\n", "jobs:\n  # comment-only change\n")
        )
        self.assertEqual(before_jobs, commented_jobs)
        self.assertEqual(before_steps, commented_steps)
        changed_jobs, changed_steps = reporter._generic_workflow_authorities(
            workflow.replace("echo beta", "echo changed-beta")
        )
        self.assertEqual(
            before_steps[("host-tests", "Alpha")],
            changed_steps[("host-tests", "Alpha")],
        )
        self.assertNotEqual(
            before_steps[("host-tests", "Beta")],
            changed_steps[("host-tests", "Beta")],
        )
        self.assertNotEqual(
            before_jobs["host-tests"],
            changed_jobs["host-tests"],
        )


    def test_workflow_invalidation_is_step_specific(self):
        entries, _, graph, schema = self.fixture_authority()
        workflow = self.fixture_root / reporter.BUILD_WORKFLOW_PATH
        original = workflow.read_bytes()
        base_loader = reporter.AuthorityLoader(
            self.fixture_root, entries, "HEAD"
        )
        prior_graph = reporter._prior_graph(base_loader)
        self.assertIsNotNone(prior_graph)

        def changed_edges():
            loader = reporter.AuthorityLoader(self.fixture_root, entries)
            model = reporter.validate_graph(
                graph,
                schema,
                loader,
                entries,
            )
            return reporter._authority_changed_edges(
                graph,
                prior_graph,
                model,
                loader,
                base_loader,
            )

        try:
            workflow.write_bytes(original + b"\n# comment-only fixture\n")
            self.assertEqual(changed_edges(), set())

            workflow.write_bytes(
                original.replace(
                    b"make codeql-alerts-test CODEQL_REQUIRE_FANALYZER=1",
                    b"make codeql-alerts-test CODEQL_REQUIRE_FANALYZER=1 EXTRA=1",
                )
            )
            self.assertEqual(changed_edges(), set())

            workflow.write_bytes(
                original.replace(
                    b'python3 -m unittest discover -s tests/workflows -p "test_*.py" -v',
                    b'python3 -m unittest discover -s tests/workflows -p "test_*.py" -q',
                )
            )
            self.assertEqual(
                changed_edges(),
                {
                    "docs.adversarial",
                    "host.adversarial",
                    "host.owns-test",
                    "localization.adversarial",
                    "manual.adversarial",
                    "repo-config.owns-test",
                    "runtime.adversarial",
                    "templates.owns-test",
                    "workflow.owns-test",
                },
            )
        finally:
            workflow.write_bytes(original)

    def test_review_invalidation_is_derived_from_edge_authority(self):
        unchanged = reporter.compare_graph_edges(self.graph, copy.deepcopy(self.graph))
        self.assertFalse(unchanged["invalidated"])

        def assert_invalidated(graph, edge_id):
            changed = reporter.compare_graph_edges(graph, self.graph)
            self.assertTrue(changed["invalidated"])
            self.assertIn(edge_id, changed["changed_edge_ids"])

        cases = {
            "edge-reason": (
                "runtime.owns-test",
                lambda graph: next(
                    edge
                    for edge in graph["edges"]
                    if edge["id"] == "runtime.owns-test"
                ).update(reason="changed semantic reason"),
            ),
            "edge-source-endpoint": (
                "runtime.owns-test",
                lambda graph: next(
                    edge
                    for edge in graph["edges"]
                    if edge["id"] == "runtime.owns-test"
                ).update(source="surface.host"),
            ),
            "edge-owner-endpoint": (
                "runtime.owns-test",
                lambda graph: next(
                    edge
                    for edge in graph["edges"]
                    if edge["id"] == "runtime.owns-test"
                ).update(target="owner.host-build"),
            ),
            "edge-type": (
                "runtime.owns-test",
                lambda graph: next(
                    edge
                    for edge in graph["edges"]
                    if edge["id"] == "runtime.owns-test"
                ).update(type="adversarial-control"),
            ),
            "owner-evidence-type": (
                "runtime.target",
                lambda graph: next(
                    node
                    for node in graph["nodes"]
                    if node["id"] == "owner.runtime-modern"
                ).update(evidence_type="link"),
            ),
            "authority-target": (
                "runtime.target",
                lambda graph: next(
                    node
                    for node in graph["nodes"]
                    if node["id"] == "owner.runtime-modern"
                )["authority"].update(target="expansion-modern-title-check"),
            ),
        }
        for label, (edge_id, mutate) in cases.items():
            with self.subTest(label=label):
                graph = copy.deepcopy(self.graph)
                mutate(graph)
                assert_invalidated(graph, edge_id)

        graph = copy.deepcopy(self.graph)
        graph["nodes"][0]["label"] = "non-authoritative presentation change"
        self.assertFalse(reporter.compare_graph_edges(graph, self.graph)["invalidated"])

        graph = copy.deepcopy(self.graph)
        graph["path_rules"][0]["include"][0]["path"] = "src/runtime/"
        changed = reporter.compare_graph_edges(graph, self.graph)
        self.assertTrue(changed["invalidated"])
        self.assertIn("runtime.target", changed["changed_edge_ids"])

        changed = reporter.compare_graph_edges(
            self.graph,
            self.graph,
            {"runtime.target"},
        )
        self.assertTrue(changed["invalidated"])
        self.assertEqual(changed["changed_edge_ids"], ["runtime.target"])

    def test_artifact_lifecycle_mutation_and_deletion_are_non_destructive(self):
        for kind in sorted(reporter.REQUIRED_PROOF_KINDS):
            with self.subTest(kind=kind):
                graph = copy.deepcopy(self.graph)
                trigger_id = next(
                    event["id"]
                    for event in graph["lifecycle_events"]
                    if event["type"] == kind
                )
                graph["lifecycle_events"] = [
                    event
                    for event in graph["lifecycle_events"]
                    if event["id"] != trigger_id
                ]
                with self.assertRaisesRegex(
                    reporter.OwnershipError,
                    "authoritative trigger|lifecycle",
                ):
                    self.validate(graph)

        graph = copy.deepcopy(self.graph)
        proof = next(
            event
            for event in graph["lifecycle_events"]
            if event["type"] == "deletion_proof"
        )
        proof["restored_result"] = "fail"
        with self.assertRaisesRegex(reporter.OwnershipError, "did not restore"):
            self.validate(graph)

        graph = copy.deepcopy(self.graph)
        proof = next(
            event
            for event in graph["lifecycle_events"]
            if event["type"] == "deletion_proof"
        )
        proof["reason"] = "self-declared consistent string"
        with self.assertRaisesRegex(reporter.OwnershipError, "executable failure reason"):
            self.validate(graph)

        graph = copy.deepcopy(self.graph)
        proof = next(
            event
            for event in graph["lifecycle_events"]
            if event["type"] == "deletion_proof"
        )
        trigger = next(
            event
            for event in graph["lifecycle_events"]
            if event["id"] == proof["trigger_event_id"]
        )
        proof["occurred_at"] = trigger["occurred_at"]
        with self.assertRaisesRegex(reporter.OwnershipError, "strictly follow"):
            self.validate(graph)

        graph = copy.deepcopy(self.graph)
        trigger = next(
            event
            for event in graph["lifecycle_events"]
            if event["type"] == "dependency_changed"
        )
        trigger["authority"] = "edge:fabricated"
        with self.assertRaisesRegex(reporter.OwnershipError, "fabricated authority"):
            self.validate(graph)

        before = GRAPH_PATH.read_bytes()
        results = reporter.validate_executable_lifecycle(ROOT, self.graph)
        self.assertEqual(len(results), len(reporter.REQUIRED_PROOF_KINDS))
        self.assertEqual(
            {result["trigger_type"] for result in results},
            reporter.REQUIRED_PROOF_KINDS,
        )
        self.assertTrue(
            all(
                result["removal"] == "fail"
                and result["restoration"] == "pass"
                and result["reason"] == reporter.LIFECYCLE_FAILURE_REASON
                for result in results
            )
        )
        self.assertEqual(GRAPH_PATH.read_bytes(), before)

    def test_strict_schema_rejects_unknown_keys_and_boolean_integers(self):
        graph = copy.deepcopy(self.graph)
        graph["guessed"] = True
        with self.assertRaisesRegex(reporter.OwnershipError, "unknown keys"):
            self.validate(graph)

        graph = copy.deepcopy(self.graph)
        graph["artifact"]["estimated_maintenance_minutes"] = False
        with self.assertRaisesRegex(reporter.OwnershipError, "must have type integer"):
            self.validate(graph)

    def test_independent_probe_oracle_is_sealed_and_mismatch_fails(self):
        def reseal(oracle):
            oracle["seal"] = reporter._sha256(
                reporter.PROBE_SEAL_DOMAIN,
                reporter.canonical_probe_oracle_payload(oracle),
            )

        reporter.validate_probe_oracle(
            self.oracle,
            self.graph,
            self.entries,
        )

        oracle = copy.deepcopy(self.oracle)
        oracle["seal"] = "0" * 64
        with self.assertRaisesRegex(reporter.OwnershipError, "seal does not match"):
            reporter.validate_probe_oracle(oracle, self.graph, self.entries)

        oracle = copy.deepcopy(self.oracle)
        oracle["probes"][0]["expected_owners"][0]["edge_type"] = "guessed-family"
        reseal(oracle)
        with self.assertRaisesRegex(reporter.OwnershipError, "unknown edge family"):
            reporter.validate_probe_oracle(oracle, self.graph, self.entries)

        oracle = copy.deepcopy(self.oracle)
        oracle["probes"][0]["expected_surface"] = "surface.missing"
        reseal(oracle)
        with self.assertRaisesRegex(reporter.OwnershipError, "unknown surface"):
            reporter.validate_probe_oracle(oracle, self.graph, self.entries)

        oracle = copy.deepcopy(self.oracle)
        oracle["probes"][0]["expected_owners"].pop()
        reseal(oracle)
        with self.assertRaisesRegex(reporter.OwnershipError, "selection mismatch"):
            reporter.build_report(
                self.graph,
                self.schema,
                oracle,
                self.loader,
                self.entries,
            )

        oracle = copy.deepcopy(self.oracle)
        oracle["probes"][0]["expected_owners"].append(
            copy.deepcopy(oracle["probes"][0]["expected_owners"][0])
        )
        reseal(oracle)
        with self.assertRaisesRegex(reporter.OwnershipError, "duplicates an exact owner pair"):
            reporter.validate_probe_oracle(oracle, self.graph, self.entries)

        oracle = copy.deepcopy(self.oracle)
        oracle["probes"][0]["expected_owners"].reverse()
        reporter.validate_probe_oracle(oracle, self.graph, self.entries)
        self.assertEqual(oracle["seal"], self.oracle["seal"])

        graph = copy.deepcopy(self.graph)
        edge = next(
            item
            for item in graph["edges"]
            if item["id"] == "workflow.owns-test"
        )
        edge["target"] = "owner.host-build"
        with self.assertRaisesRegex(
            reporter.OwnershipError,
            r"false_positive=1, false_negative=1",
        ):
            reporter.build_report(
                graph,
                self.schema,
                self.oracle,
                self.loader,
                self.entries,
            )

        graph = copy.deepcopy(self.graph)
        owns = next(
            item for item in graph["edges"] if item["id"] == "workflow.owns-test"
        )
        adversarial = next(
            item
            for item in graph["edges"]
            if item["id"] == "workflow.adversarial"
        )
        owns["target"], adversarial["target"] = (
            adversarial["target"],
            owns["target"],
        )
        with self.assertRaisesRegex(
            reporter.OwnershipError,
            r"false_positive=2, false_negative=2",
        ):
            reporter.build_report(
                graph,
                self.schema,
                self.oracle,
                self.loader,
                self.entries,
            )


    def test_mixed_make_goal_is_rejected_before_dependency_suppression(self):
        before = reporter.repository_status(self.fixture_root)
        completed = subprocess.run(
            ["make", "-n", "validation-ownership-check", "compare"],
            cwd=self.fixture_root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "validation-ownership-check must be invoked as the sole Make goal",
            completed.stderr,
        )
        self.assertEqual(reporter.repository_status(self.fixture_root), before)

    def test_public_make_gate_rejects_execution_control_bypasses(self):
        before = reporter.repository_status(self.fixture_root)
        cases = (
            (["make", "-n", "validation-ownership-check"], {}),
            (["make", "--just-print", "validation-ownership-check"], {}),
            (["make", "-t", "validation-ownership-check"], {}),
            (["make", "-q", "validation-ownership-check"], {}),
            (["make", "-s", "validation-ownership-check"], {}),
            (["make", "-i", "validation-ownership-check"], {}),
            (
                ["make", "validation-ownership-check"],
                {"MAKEFLAGS": "-n"},
            ),
            (
                ["make", "validation-ownership-check"],
                {"GNUMAKEFLAGS": "-n"},
            ),
            (
                ["make", "MFLAGS=-n", "validation-ownership-check"],
                {},
            ),
            (
                ["make", "validation-ownership-check"],
                {"MAKEOVERRIDES": "PYTHON=hostile"},
            ),
            (
                [
                    "make",
                    "MAKEOVERRIDES=",
                    "validation-ownership-check",
                ],
                {},
            ),
            (
                [
                    "make",
                    "MAKECMDGOALS=",
                    "-n",
                    "validation-ownership-check",
                ],
                {},
            ),
            (
                [
                    "make",
                    "AUTOTOOLS_CONFIG_MK=/dev/stdin",
                    "SHELL=printf injected",
                    "validation-ownership-check",
                ],
                {},
            ),
        )
        for command, hostile_environment in cases:
            with self.subTest(command=command, env=hostile_environment):
                environment = dict(os.environ)
                environment.update(hostile_environment)
                completed = subprocess.run(
                    command,
                    cwd=self.fixture_root,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertTrue(
                    "validation-ownership-check rejects Make"
                    in completed.stderr
                    or "MAKECMDGOALS must remain owned by GNU Make"
                    in completed.stderr,
                    completed.stderr,
                )
        graph_path = self.fixture_root / reporter.GRAPH_PATH
        original_graph = graph_path.read_bytes()
        broken_graph = json.loads(original_graph)
        broken_graph["seal"] = "0" * 64
        try:
            graph_path.write_bytes(reporter.normalized_json(broken_graph))
            safe = subprocess.run(
                [
                    "make",
                    "-j2",
                    "--no-print-directory",
                    "validation-ownership-check",
                ],
                cwd=self.fixture_root,
                env={
                    **os.environ,
                    "GNUMAKEFLAGS": "",
                    "MAKEFLAGS": "",
                    "MAKEOVERRIDES": "",
                    "MFLAGS": "",
                },
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            graph_path.write_bytes(original_graph)
        self.assertNotEqual(safe.returncode, 0)
        self.assertNotIn(
            "validation-ownership-check rejects Make",
            safe.stderr,
        )
        self.assertIn("seal", safe.stderr)
        self.assertEqual(reporter.repository_status(self.fixture_root), before)



if __name__ == "__main__":
    unittest.main()
