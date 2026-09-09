import copy
import json
import unittest
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
from unittest import mock

from scripts.validation_ownership import reporter
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_report import check
from .report_fixture import ReportFixture
from . import report_fixture


class GraphReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ReportFixture()
        self.addCleanup(self.fixture.close)

    def run_report(self, **arguments):
        budget = ProbeBudget()
        try:
            result = check(self.fixture.root, budget=budget, runtime_files=(), **arguments)
            self.assertFalse(budget.children)
            return result
        finally:
            budget.close()

    def test_real_complete_report_partition_oracle_and_lifecycle(self):
        removed = []
        replace = Path.replace

        def observe_removal(path, target):
            if Path(target).name == "graph.backup":
                removed.append(path)
            return replace(path, target)

        with mock.patch.object(Path, "replace", new=observe_removal):
            result = self.run_report(changed_paths=("Makefile",))
        self.assertFalse(result["policy"]["narrowing_authorized"])
        self.assertEqual(result["coverage"]["tracked_paths"], result["coverage"]["owned_paths"])
        self.assertEqual(result["measurement"]["false_positive_selections"], 0)
        self.assertEqual(result["measurement"]["false_negative_selections"], 0)
        self.assertEqual(len(result["artifact"]["executable_lifecycle"]), 3)
        self.assertEqual(len(removed), len(result["artifact"]["executable_lifecycle"]))
        self.assertTrue(all(item["removal"] == "fail" and item["restoration"] == "pass"
                            for item in result["artifact"]["executable_lifecycle"]))
        self.assertEqual(result["resolutions"][0]["path"], "Makefile")
        self.assertGreater(result["execution"]["runs"], 0)

    def test_each_lifecycle_trigger_requires_its_actual_removal_failure(self):
        replace = Path.replace
        removals = []

        def skip_one_removal(path, target):
            if Path(target).name == "graph.backup":
                removals.append(path)
                if len(removals) == 2:
                    Path(target).write_bytes(path.read_bytes())
                    return Path(target)
            return replace(path, target)

        with mock.patch.object(Path, "replace", new=skip_one_removal):
            with self.assertRaisesRegex(MakeProbeError, "artifact removal did not fail"):
                self.run_report()
        self.assertEqual(len(removals), 2)

    def test_current_and_base_models_share_report_and_invalidate_real_case_change(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        self.fixture.add("docs/test-cases/registry.json", json.dumps({
            "cases": [{"id": "TC-WORKFLOW-GATE-OWNERSHIP-001", "title": "Changed real case"}],
        }))
        self.fixture.commit("Change case semantics")
        result = self.run_report(base_revision=base)
        self.assertTrue(result["review_invalidation"]["invalidated"])
        self.assertIn("source.adversarial-control", result["review_invalidation"]["changed_edge_ids"])

    def assert_all_edges_invalidated(self, base):
        result = self.run_report(base_revision=base, lifecycle=False)
        graph = json.loads((self.fixture.root / reporter.GRAPH_PATH).read_text())
        self.assertTrue(result["review_invalidation"]["invalidated"])
        self.assertEqual(
            set(result["review_invalidation"]["changed_edge_ids"]),
            {edge["id"] for edge in graph["edges"]},
        )

    def test_valid_schema_constraint_change_invalidates_all_edges(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        path = self.fixture.root / reporter.SCHEMA_PATH
        schema = json.loads(path.read_text())
        self.assertEqual(schema["$defs"]["nonempty"]["minLength"], 1)
        schema["$defs"]["nonempty"]["minLength"] = 2
        path.write_text(json.dumps(schema))
        self.fixture.commit("Tighten a valid graph schema constraint")
        self.assert_all_edges_invalidated(base)

    def test_valid_oracle_coverage_change_invalidates_all_edges(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        path = self.fixture.root / reporter.PROBE_ORACLE_PATH
        oracle = json.loads(path.read_text())
        probe = copy.deepcopy(oracle["probes"][0])
        probe["path"] = "src/data/table.json"
        oracle["probes"].append(probe)
        oracle["seal"] = reporter._sha256(
            reporter.PROBE_SEAL_DOMAIN, reporter.canonical_probe_oracle_payload(oracle),
        )
        path.write_text(json.dumps(oracle))
        self.fixture.commit("Extend valid oracle coverage without changing owners")
        self.assert_all_edges_invalidated(base)

    def test_document_serialization_without_semantic_change_does_not_invalidate(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        for name in (reporter.SCHEMA_PATH, reporter.PROBE_ORACLE_PATH):
            path = self.fixture.root / name
            path.write_text(json.dumps(json.loads(path.read_text()), indent=1, sort_keys=True))
        self.fixture.commit("Change only ownership document serialization")
        result = self.run_report(base_revision=base, lifecycle=False)
        self.assertFalse(result["review_invalidation"]["invalidated"])
        self.assertEqual(result["review_invalidation"]["changed_edge_ids"], [])

    def test_unknown_current_path_cannot_be_admitted_by_prefix(self):
        self.fixture.add("src/foo.c", "int unknown;\n")
        self.fixture.commit("Add unadmitted source")
        with self.assertRaisesRegex(MakeProbeError, "semantic admission"):
            self.run_report()

    def test_edge_loss_and_owner_redirection_reject(self):
        path = self.fixture.root / reporter.GRAPH_PATH
        graph = json.loads(path.read_text())
        graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "source.owns-test"]
        path.write_text(json.dumps(graph))
        self.fixture.commit("Remove required owner")
        with self.assertRaisesRegex(MakeProbeError, "missing owner"):
            self.run_report()


class GitFixtureTests(unittest.TestCase):
    def test_fixture_git_never_starts_automatic_maintenance_even_if_locally_enabled(self):
        parent = report_fixture.ROOT / "build/test-artifacts/fixture-maintenance"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            directory = Path(directory)
            root = directory / "repo"
            root.mkdir()
            context = SimpleNamespace(root=root, directory=directory)
            ReportFixture.git(context, "init", "--quiet")
            for name, value in (
                ("maintenance.auto", "true"), ("gc.auto", "1"),
                ("gc.autoDetach", "false"), ("maintenance.autoDetach", "false"),
            ):
                ReportFixture.git(context, "config", "--local", name, value)
            environment = {
                **report_fixture.ENVIRONMENT,
                "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                "TMPDIR": str(directory),
            }
            ordinary_trace = directory / "ordinary.json"
            subprocess.run(
                ["/usr/bin/git", "-C", str(root), "commit", "--allow-empty", "-q", "-m", "ordinary"],
                env={**environment, "GIT_TRACE2_EVENT": str(ordinary_trace)},
                capture_output=True, check=True, timeout=15,
            )
            def automatic_children(path):
                return [event for line in path.read_text().splitlines()
                        for event in (json.loads(line),)
                        if event.get("event") == "child_start"
                        and any(arg in {"maintenance", "gc"} for arg in event.get("argv", []))]
            self.assertTrue(automatic_children(ordinary_trace))
            controlled_trace = directory / "controlled.json"
            with mock.patch.dict(report_fixture.ENVIRONMENT, {"GIT_TRACE2_EVENT": str(controlled_trace)}):
                ReportFixture.git(context, "commit", "--allow-empty", "-q", "-m", "controlled")
            self.assertEqual(automatic_children(controlled_trace), [])
            self.assertEqual(ReportFixture.git(context, "rev-list", "--count", "HEAD").strip(), b"2")

if __name__ == "__main__":
    unittest.main()
