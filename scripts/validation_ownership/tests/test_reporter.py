from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.validation_ownership import reporter


ROOT = Path(__file__).resolve().parents[3]
GRAPH_PATH = ROOT / reporter.GRAPH_PATH
SCHEMA_PATH = ROOT / reporter.SCHEMA_PATH
SCRATCH_ROOT = ROOT / "build" / "test-artifacts" / "validation-ownership"


class OwnershipGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = reporter.load_json(GRAPH_PATH)
        cls.schema = reporter.load_json(SCHEMA_PATH)
        cls.tracked = reporter.tracked_paths(ROOT)
        cls.fixture_paths = tuple(
            probe["path"] for probe in cls.graph["measurement"]["probes"]
        ) + ("mgfembp",)
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)

    def validate(self, graph=None, paths=None):
        return reporter.validate_graph(
            self.graph if graph is None else graph,
            self.schema,
            ROOT,
            self.fixture_paths if paths is None else paths,
        )

    def test_whole_repository_has_exact_coverage(self):
        model = self.validate(paths=self.tracked)
        self.assertEqual(len(model["coverage"]), len(self.tracked))
        self.assertEqual(
            [path for path, record in model["coverage"].items() if record["kind"] == "excluded"],
            ["mgfembp"],
        )

    def test_representative_surface_resolutions_and_measurement(self):
        report = reporter.build_report(
            self.graph,
            self.schema,
            ROOT,
            self.tracked,
            (probe["path"] for probe in self.graph["measurement"]["probes"]),
        )
        self.assertEqual(report["measurement"]["false_positive_selections"], 0)
        self.assertEqual(report["measurement"]["false_negative_selections"], 0)
        actual = {
            record["path"]: record["surface"] for record in report["resolutions"]
        }
        expected = {
            probe["path"]: probe["expected_surface"]
            for probe in self.graph["measurement"]["probes"]
        }
        self.assertEqual(actual, expected)
        for resolution in report["resolutions"]:
            self.assertTrue(resolution["owners"])
            self.assertTrue(
                all(owner["reason"] and owner["gate"] for owner in resolution["owners"])
            )

    def test_generated_paths_come_from_typed_registry(self):
        model = self.validate(paths=self.tracked)
        generated = {
            path
            for path, record in model["coverage"].items()
            if record.get("surface") == "surface.generated"
        }
        _, registry_paths = reporter._generated_registry_records(ROOT)
        self.assertTrue(registry_paths)
        self.assertLessEqual(registry_paths, generated)
        self.assertIn("src/data/items.json", generated)

    def test_unknown_path_and_fail_closed_exclusion_reject(self):
        model = self.validate()
        with self.assertRaisesRegex(reporter.OwnershipError, "no ownership contract"):
            reporter._resolve_path("unowned/new.c", self.graph, model)
        with self.assertRaisesRegex(reporter.OwnershipError, "fail-closed exclusion"):
            reporter._resolve_path("mgfembp", self.graph, model)
        with self.assertRaisesRegex(reporter.OwnershipError, "no ownership contract"):
            self.validate(paths=self.fixture_paths + ("unowned/new.c",))

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
            ROOT, ".github/manual-testing-handoff.json"
        )
        self.assertFalse(handoff["eligibility"]["deterministic_criteria"])
        self.assertTrue(handoff["pre_handoff"]["semantic_assertions_primary"])

    def test_stale_make_workflow_case_and_generated_targets_reject(self):
        mutations = (
            ("owner.compile-modern", "target", "missing-target", "stale Make target"),
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

    def test_make_and_workflow_content_drift_changes_edge_authority(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            make_path = scratch / "Makefile"
            workflow_path = scratch / "build.yml"
            make_path.write_bytes((ROOT / "Makefile").read_bytes())
            workflow_path.write_bytes((ROOT / reporter.BUILD_WORKFLOW_PATH).read_bytes())
            before_make = reporter.digest_paths(
                scratch,
                ("Makefile",),
                b"make-fixture\0",
            )
            before_workflow = reporter.digest_paths(
                scratch,
                ("build.yml",),
                b"workflow-fixture\0",
            )
            make_path.write_bytes(make_path.read_bytes() + b"\n# target drift\n")
            workflow_path.write_bytes(
                workflow_path.read_bytes() + b"\n# workflow target drift\n"
            )
            after_make = reporter.digest_paths(
                scratch,
                ("Makefile",),
                b"make-fixture\0",
            )
            after_workflow = reporter.digest_paths(
                scratch,
                ("build.yml",),
                b"workflow-fixture\0",
            )
        self.assertNotEqual(before_make, after_make)
        self.assertNotEqual(before_workflow, after_workflow)
        first = reporter._sha256(
            reporter.EDGE_SEAL_DOMAIN,
            {"authority": before_make},
        )
        second = reporter._sha256(
            reporter.EDGE_SEAL_DOMAIN,
            {"authority": after_make},
        )
        self.assertNotEqual(first, second)

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
                graph["artifact"]["lifecycle_proofs"] = [
                    proof
                    for proof in graph["artifact"]["lifecycle_proofs"]
                    if proof["kind"] != kind
                ]
                with self.assertRaisesRegex(reporter.OwnershipError, "lifecycle"):
                    self.validate(graph)

        graph = copy.deepcopy(self.graph)
        graph["artifact"]["lifecycle_proofs"][0]["restored_result"] = "fail"
        with self.assertRaisesRegex(reporter.OwnershipError, "did not restore"):
            self.validate(graph)

        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            copy_path = Path(directory) / GRAPH_PATH.name
            original = GRAPH_PATH.read_bytes()
            copy_path.write_bytes(original)
            copy_path.unlink()
            with self.assertRaisesRegex(reporter.OwnershipError, "cannot read"):
                reporter.load_json(copy_path)
            copy_path.write_bytes(original)
            self.assertEqual(reporter.load_json(copy_path), self.graph)
        self.assertEqual(GRAPH_PATH.read_bytes(), original)

    def test_strict_schema_rejects_unknown_keys_and_boolean_integers(self):
        graph = copy.deepcopy(self.graph)
        graph["guessed"] = True
        with self.assertRaisesRegex(reporter.OwnershipError, "unknown keys"):
            self.validate(graph)

        graph = copy.deepcopy(self.graph)
        graph["artifact"]["estimated_maintenance_minutes"] = False
        with self.assertRaisesRegex(reporter.OwnershipError, "must have type integer"):
            self.validate(graph)

    def test_report_is_canonical_report_only_and_preserves_git_state(self):
        before = reporter.repository_status(ROOT)
        command = [
            "/usr/bin/python3",
            "-I",
            "scripts/validation_ownership/isolated_launcher.py",
            "resolve",
            "--repository-root",
            str(ROOT),
            "--changed",
            "src/bm.c",
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(
            completed.stdout,
            reporter.normalized_json(report),
        )
        self.assertEqual(report["policy"]["validation_effect"], "report-only")
        self.assertFalse(report["policy"]["narrowing_authorized"])
        self.assertTrue(report["selected_gates"])
        self.assertEqual(reporter.repository_status(ROOT), before)

        original_graph = GRAPH_PATH.read_bytes()
        mutated_graph = copy.deepcopy(self.graph)
        mutated_graph["edges"][0]["reason"] = (
            "deterministic working-tree review invalidation fixture"
        )
        try:
            GRAPH_PATH.write_bytes(reporter.normalized_json(mutated_graph))
            completed = subprocess.run(
                command + ["--base-revision", "HEAD"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
        finally:
            GRAPH_PATH.write_bytes(original_graph)
        comparison = json.loads(completed.stdout)["review_invalidation"]
        self.assertTrue(comparison["invalidated"])
        self.assertEqual(comparison["reason"], "authoritative-graph-edge-change")
        self.assertEqual(
            comparison["changed_edge_ids"],
            [self.graph["edges"][0]["id"]],
        )
        self.assertEqual(reporter.repository_status(ROOT), before)


if __name__ == "__main__":
    unittest.main()
