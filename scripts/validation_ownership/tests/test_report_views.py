from __future__ import annotations

import copy
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import unittest

from scripts.validation_ownership import reporter
from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, git_tree_entries
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.make_probe import ProbeSession


ROOT = Path(__file__).resolve().parents[3]


class ReportViewTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts/report-view-tests" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.budget = ProbeBudget()
        self.add("Makefile", "all: ;\n")
        self.git("init", "--quiet")

    def tearDown(self):
        self.budget.close()
        shutil.rmtree(self.directory)

    def git(self, *arguments):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(self.root), *arguments],
            env={**ENVIRONMENT, "TMPDIR": str(self.directory)}, capture_output=True,
            check=True, timeout=15,
        ).stdout

    def add(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def registry(self, source):
        self.add("scripts/generated_data/registry.py", (
            "class Schema:\n"
            " version=1\n"
            f" default_source={source!r}\n"
            " default_hand_source=None\n"
            " default_output_name='table.c'\n"
            " default_inventory_path=None\n"
            " def dependencies(self): return ()\n"
            " def dependency_tables(self): return ()\n"
            "class Registry:\n"
            " def all_names(self): return ('table',)\n"
            " def resolve(self,name):\n"
            "  assert name=='table'\n"
            "  return Schema()\n"
            "REGISTRY=Registry()\n"
        ))

    def capture(self):
        self.git("add", "-A", "--", ".")
        revision = self.git("write-tree").decode().strip()
        return AuthorityLoader(
            self.root, git_tree_entries(self.root, revision, budget=self.budget),
            revision, budget=self.budget,
        )

    @staticmethod
    def resolution_model(loader, generated_paths, gate):
        generated = {"kind": "generated-data-registry"}
        graph = {
            "path_rules": [
                {"id": "generated", "surface": "generated", "include": [generated], "exclude": []},
                {"id": "source", "surface": "source",
                 "include": [{"kind": "prefix", "path": "src/data/"}], "exclude": [generated]},
            ],
            "exclusions": [],
        }
        model = {
            "graph": graph, "entries": loader.entries, "generated_paths": generated_paths,
            "surfaces": {
                name: {"id": name, "surface_type": name} for name in ("source", "generated")
            },
            "outgoing": {
                name: [{"id": name + "-check", "type": "owns-test", "target": name,
                        "reason": "controlled typed owner for " + name}]
                for name in ("source", "generated")
            },
            "evidence": {name: {"id": name, "evidence_type": "host"} for name in ("source", "generated")},
            "authorities": {
                "source": {"display": "source-check"}, "generated": {"display": gate},
            },
            "admission_sources": {
                "initial-graph-cohort": set(), "generated-source-registry": generated_paths,
                "verifier-runtime-registry": set(),
            },
        }
        return graph, model

    def test_deleted_path_uses_actual_base_registry_model_and_owner(self):
        old, new = "src/data/old.json", "src/data/new.json"
        self.registry(old)
        self.add(old, '{"version":1}\n')
        base_loader = self.capture()
        self.registry(new)
        (self.root / old).unlink()
        self.add(new, '{"version":2}\n')
        current_loader = self.capture()
        with ProbeSession(
            current_loader, scratch_root=self.root / "build/probe", budget=self.budget,
        ) as probe:
            current_records, current_paths = reporter._generated_registry_records(
                current_loader, session=probe,
            )
            self.assertEqual(current_records[0]["default_source"], new)
            self.assertEqual(current_paths, {new})
            graph, current_model = self.resolution_model(current_loader, current_paths, "current-check")
            graph["path_rules"][0]["id"] = "current-generated"
            deadline, runs = self.budget.deadline, self.budget.runs
            with probe.select_view(base_loader) as selected:
                self.assertIs(selected, probe)
                base_records, base_paths = reporter._generated_registry_records(base_loader, session=probe)
                self.assertEqual(base_records[0]["default_source"], old)
                self.assertEqual(base_paths, {old})
                _, base_model = self.resolution_model(base_loader, base_paths, "base-check")
                base_model["graph"]["path_rules"][0]["id"] = "base-generated"
                self.assertEqual(self.budget.deadline, deadline)
            self.assertGreater(self.budget.runs, runs)
            result = reporter._resolve_path(
                old, graph, current_model, base_loader.entries, base_model=base_model,
            )
            self.assertEqual(result["admission"], "selected-base-tree")
            self.assertEqual(result["surface"], "generated")
            self.assertEqual(result["rule"], "base-generated")
            self.assertEqual(result["owners"][0]["gate"], "base-check")
            self.assertEqual(
                reporter._resolve_path(new, graph, current_model)["owners"][0]["gate"], "current-check",
            )
            with self.assertRaisesRegex(reporter.OwnershipError, "selected BASE ownership model"):
                reporter._resolve_path(old, graph, current_model, base_loader.entries)
            self.resolution_fixture = (old, graph, current_model, base_loader.entries, base_model)
            self.view_evidence = {
                "scope": "registry declarations and controlled resolver, not full graph execution",
                "current_source": current_records[0]["default_source"],
                "base_source": base_records[0]["default_source"],
                "resolution": result,
                "accounting": {
                    "runs": self.budget.runs, "states": self.budget.states,
                    "bytes": dict(self.budget.bytes), "processes": probe.processes_used,
                    "live_process_peak": probe.live_process_peak, "deadline": self.budget.deadline,
                },
            }
        self.assertIsNone(probe.base)
        self.assertFalse(self.budget.children)
        self.view_evidence["cleanup"] = probe.base is None and not self.budget.children

    def test_foreign_selected_loader_rejects_without_executing_registry(self):
        self.registry("src/data/table.json")
        self.add("src/data/table.json", "{}\n")
        first = self.capture()
        self.add("unrelated.txt", "new view\n")
        second = self.capture()
        with ProbeSession(first, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            before = self.budget.runs
            with self.assertRaisesRegex(reporter.OwnershipError, "selected shared report view"):
                reporter._generated_registry_records(second, session=probe)
            self.assertEqual(self.budget.runs, before)

    def test_registry_declarations_reject_stale_sources(self):
        self.registry("src/data/missing.json")
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            with self.assertRaisesRegex(reporter.OwnershipError, "stale default_source"):
                reporter._generated_registry_records(loader, session=probe)

    def test_registry_declarations_reject_boolean_schema_version(self):
        self.registry("src/data/table.json")
        self.add("src/data/table.json", "{}\n")
        path = self.root / "scripts/generated_data/registry.py"
        path.write_text(path.read_text().replace("version=1", "version=True"))
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            with self.assertRaisesRegex(reporter.OwnershipError, "invalid identity fields"):
                reporter._generated_registry_records(loader, session=probe)

    def test_new_source_under_prefix_is_not_admitted_by_base_context(self):
        self.registry("src/data/table.json")
        self.add("src/data/table.json", "{}\n")
        self.add("src/data/unknown.json", "{}\n")
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            _, paths = reporter._generated_registry_records(loader, session=probe)
            graph, model = self.resolution_model(loader, paths, "table-check")
            with self.assertRaisesRegex(reporter.OwnershipError, "lacks semantic admission"):
                reporter._resolve_path(
                    "src/data/unknown.json", graph, model, loader.entries,
                    base_model=copy.deepcopy(model),
                )


if __name__ == "__main__":
    unittest.main()
