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
        result = self.run_report(changed_paths=("Makefile",))
        self.assertFalse(result["policy"]["narrowing_authorized"])
        self.assertEqual(result["coverage"]["tracked_paths"], result["coverage"]["owned_paths"])
        self.assertEqual(result["measurement"]["false_positive_selections"], 0)
        self.assertEqual(result["measurement"]["false_negative_selections"], 0)
        self.assertEqual(len(result["artifact"]["executable_lifecycle"]), 3)
        self.assertTrue(all(item["removal"] == "fail" and item["restoration"] == "pass"
                            for item in result["artifact"]["executable_lifecycle"]))
        self.assertEqual(result["resolutions"][0]["path"], "Makefile")
        self.assertGreater(result["execution"]["runs"], 0)

    def test_current_and_base_models_share_report_and_invalidate_real_case_change(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        self.fixture.add("docs/test-cases/registry.json", json.dumps({
            "cases": [{"id": "TC-WORKFLOW-GATE-OWNERSHIP-001", "title": "Changed real case"}],
        }))
        self.fixture.commit("Change case semantics")
        result = self.run_report(base_revision=base)
        self.assertTrue(result["review_invalidation"]["invalidated"])
        self.assertIn("source.adversarial-control", result["review_invalidation"]["changed_edge_ids"])

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
