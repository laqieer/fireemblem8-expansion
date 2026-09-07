import copy
import json
import unittest

from scripts.validation_ownership import reporter
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_report import check
from .report_fixture import ReportFixture


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


if __name__ == "__main__":
    unittest.main()
