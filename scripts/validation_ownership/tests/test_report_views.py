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
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
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

    def registry(self, source, *, pattern=None):
        resolver = (
            " def load_records(self,source):\n"
            "  path=Path(source)\n"
            "  return {'source_paths':[str(path)],"
            "'records':[json.loads(path.read_text())]}\n"
            " def manifest_record_count(self,records): return len(records['records'])\n"
        ) if pattern is None else (
            f" def source_paths(self,source): return sorted(Path(source).glob({pattern!r}))\n"
            " def load_records(self,source):\n"
            "  paths=self.source_paths(source)\n"
            "  return {'source_paths':[str(path) for path in paths],"
            "'records':[json.loads(path.read_text()) for path in paths]}\n"
            " def manifest_record_count(self,records): return len(records['records'])\n"
        )
        self.add("scripts/generated_data/registry.py", (
            "from pathlib import Path\nimport json\n"
            "class Schema:\n"
            " name='table'\n"
            " version=1\n"
            f" default_source={source!r}\n"
            " default_hand_source=None\n"
            " default_output_name='table.c'\n"
            " default_inventory_path=None\n"
            " def dependencies(self): return ()\n"
            " def dependency_tables(self): return ()\n"
            + resolver +
            "class Registry:\n"
            " def all_names(self): return ('table',)\n"
            " def resolve(self,name):\n"
            "  assert name=='table'\n"
            "  return Schema()\n"
            "REGISTRY=Registry()\n"
        ))

    def test_directory_registry_resolves_only_real_consumed_members_in_both_views(self):
        self.registry("src/data", pattern="*_bundle.json")
        self.add("src/data/old_bundle.json", '{"version":1}\n')
        self.add("src/data/shared_bundle.json", '{"version":1}\n')
        self.add("src/data/unrelated.json", '{"unrelated":true}\n')
        base = self.capture()
        (self.root / "src/data/old_bundle.json").unlink()
        self.add("src/data/new_bundle.json", '{"version":2}\n')
        current = self.capture()
        with ProbeSession(
            current, scratch_root=self.root / "build/probe", budget=self.budget,
        ) as probe:
            _, paths = reporter._generated_registry_records(current, session=probe)
            self.assertEqual(paths, {"src/data/new_bundle.json", "src/data/shared_bundle.json"})
            with probe.select_view(base):
                _, old_paths = reporter._generated_registry_records(base, session=probe)
                self.assertEqual(old_paths, {"src/data/old_bundle.json", "src/data/shared_bundle.json"})
            _, restored = reporter._generated_registry_records(current, session=probe)
            self.assertEqual(restored, paths)
            self.assertNotIn("src/data/unrelated.json", paths | old_paths)
        self.assertIsNone(probe.base)
        self.assertFalse(self.budget.children)

    def test_file_backed_registry_loads_records_and_reports_source_paths(self):
        self.registry("src/data/table.json")
        self.add("src/data/table.json", '{"value":1}\n')
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            records, paths = reporter._generated_registry_records(loader, session=probe)
            self.assertEqual(paths, {"src/data/table.json"})
            self.assertEqual(records[0]["source_paths"], ["src/data/table.json"])
        self.assertFalse(self.budget.children)

    def test_registry_declarations_do_not_grant_unrelated_test_code(self):
        from scripts.validation_ownership.graph_registry import observe_declarations

        self.registry("src/data/table.json")
        self.add("src/data/table.json", '{"value":1}\n')
        self.add("scripts/generated_data/tests/unrelated.py", "VALUE=1\n")
        path = self.root / "scripts/generated_data/registry.py"
        path.write_text(
            path.read_text()
            + "\nPath('scripts/generated_data/tests/unrelated.py').read_text()\n"
        )
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            with self.assertRaises(MakeProbeError):
                observe_declarations(loader, probe)
        self.assertIsNone(probe.base)
        self.assertFalse(self.budget.children)

    def test_directory_discovery_cannot_read_member_contents(self):
        self.registry("src/data", pattern="*_bundle.json")
        self.add("src/data/one_bundle.json", '{"version":1}\n')
        path = self.root / "scripts/generated_data/registry.py"
        path.write_text(path.read_text() + (
            "\noriginal_paths=Schema.source_paths\n"
            "def read_during_discovery(self,source):\n"
            " paths=original_paths(self,source)\n"
            " paths[0].read_bytes()\n"
            " return paths\n"
            "Schema.source_paths=read_during_discovery\n"
        ))
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            with self.assertRaisesRegex(reporter.OwnershipError, "undeclared source read"):
                reporter._generated_registry_records(loader, session=probe)
        self.assertFalse(self.budget.children)

    def test_directory_loader_cannot_read_an_unreported_member(self):
        self.registry("src/data", pattern="*_bundle.json")
        self.add("src/data/one_bundle.json", '{"version":1}\n')
        self.add("src/data/unreported.json", '{"hidden":true}\n')
        path = self.root / "scripts/generated_data/registry.py"
        path.write_text(path.read_text() + (
            "\noriginal_load=Schema.load_records\n"
            "def read_unreported(self,source):\n"
            " (Path(source)/'unreported.json').read_bytes()\n"
            " return original_load(self,source)\n"
            "Schema.load_records=read_unreported\n"
        ))
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            with self.assertRaisesRegex(reporter.OwnershipError, "undeclared source read"):
                reporter._generated_registry_records(loader, session=probe)
        self.assertFalse(self.budget.children)

    def test_file_backed_loader_cannot_read_an_unreported_companion(self):
        self.registry("src/data/table.json")
        self.add("src/data/table.json", '{"version":1}\n')
        self.add("src/data/extra.json", '{"hidden":true}\n')
        path = self.root / "scripts/generated_data/registry.py"
        path.write_text(path.read_text() + (
            "\noriginal_load=Schema.load_records\n"
            "def read_extra(self,source):\n"
            " Path(source).with_name('extra.json').read_bytes()\n"
            " return original_load(self,source)\n"
            "Schema.load_records=read_extra\n"
        ))
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            with self.assertRaisesRegex(reporter.OwnershipError, "undeclared source read"):
                reporter._generated_registry_records(loader, session=probe)
        self.assertFalse(self.budget.children)

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

    def test_registry_import_enumerates_complete_view_without_reading_extra_members(self):
        self.registry("src/data/table.json")
        self.add("src/data/table.json", "{}\n")
        self.add("hidden.txt", "ungranted member bytes\n")
        path = self.root / "scripts/generated_data/registry.py"
        path.write_text(
            "import os\n"
            "assert 'hidden.txt' in os.listdir('/repo')\n"
            "assert 'registry.py' in os.listdir('/repo/scripts/generated_data')\n"
            + path.read_text()
        )
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            records, paths = reporter._generated_registry_records(loader, session=probe)
            self.assertEqual(records[0]["name"], "table")
            self.assertEqual(paths, {"src/data/table.json"})

    def test_registry_directory_grant_does_not_grant_member_content(self):
        self.registry("src/data/table.json")
        self.add("src/data/table.json", "{}\n")
        self.add("hidden.txt", "ungranted member bytes\n")
        path = self.root / "scripts/generated_data/registry.py"
        path.write_text(
            "import os\n"
            "assert 'hidden.txt' in os.listdir('/repo')\n"
            "open('/repo/hidden.txt').read()\n"
            + path.read_text()
        )
        loader = self.capture()
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=self.budget) as probe:
            with self.assertRaisesRegex(reporter.OwnershipError, "undeclared source read"):
                reporter._generated_registry_records(loader, session=probe)


if __name__ == "__main__":
    unittest.main()
