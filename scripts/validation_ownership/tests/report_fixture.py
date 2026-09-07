"""Owned real-Git graph fixture; no backend or authority substitutions."""

import copy
import json
from pathlib import Path
import secrets
import shutil
import subprocess

from scripts.validation_ownership import reporter
from scripts.validation_ownership.authority import ENVIRONMENT


ROOT = Path(__file__).resolve().parents[3]


class ReportFixture:
    def __init__(self, *, foundation_base=False):
        self.directory = ROOT / "build/test-artifacts/full-report" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        for path in (ROOT / "scripts").rglob("*.py"):
            if "build" not in path.relative_to(ROOT).parts and "__pycache__" not in path.parts:
                self.add(path.relative_to(ROOT).as_posix(), path.read_text())
        for name in ("dispatch.h", "make_observer.c", "shell_interceptor.c",
                     "scaninc_sources.cpp", "graph.schema.json", "ci_gate.mk"):
            self.add("scripts/validation_ownership/" + name,
                     (ROOT / "scripts/validation_ownership" / name).read_text())
        for name in ("scaninc.cpp", "scaninc.h", "source_file.cpp", "source_file.h",
                     "asm_file.cpp", "asm_file.h", "c_file.cpp", "c_file.h"):
            self.add("tools/scaninc/" + name, (ROOT / "tools/scaninc" / name).read_text())
        self.add(".github/workflows/build.yml", (ROOT / ".github/workflows/build.yml").read_text())
        self.add("Makefile", "validation-ownership-check:\n\t@true\n")
        self.add("src/data/table.json", '{"version":1}\n')
        self.add("scripts/generated_data/registry.py", (
            "class Schema:\n"
            " version=1\n default_source='src/data/table.json'\n"
            " default_hand_source=None\n default_inventory_path=None\n default_output_name='table.c'\n"
            " def dependencies(self): return ()\n def dependency_tables(self): return ()\n"
            "class Registry:\n"
            " def all_names(self): return ('table',)\n"
            " def resolve(self,name): return Schema()\n"
            "REGISTRY=Registry()\n"
        ))
        self.add("docs/test-cases/registry.json", json.dumps({
            "cases": [{"id": "TC-WORKFLOW-GATE-OWNERSHIP-001", "title": "Controlled ownership case"}],
        }))
        original = reporter.load_json(ROOT / reporter.GRAPH_PATH)
        graph = {
            "schema_version": original["schema_version"], "policy": original["policy"],
            "artifact": copy.deepcopy(original["artifact"]),
            "lifecycle_events": copy.deepcopy(original["lifecycle_events"]),
            "nodes": [
                {"id": "surface.source", "kind": "surface", "label": "Controlled source",
                 "surface_type": "source", "requirements": ["positive", "adversarial"], "dependencies": []},
                {"id": "surface.schema", "kind": "surface", "label": "Controlled schema",
                 "surface_type": "schema", "requirements": ["positive", "adversarial"],
                 "dependencies": ["surface.source"]},
                {"id": "owner.make", "kind": "evidence", "label": "Actual native Make consumer",
                 "evidence_type": "host",
                 "authority": {"kind": "make-target", "target": "validation-ownership-check"}},
                {"id": "owner.case", "kind": "evidence", "label": "Typed consistency case",
                 "evidence_type": "host",
                 "authority": {"kind": "tester-case", "case_id": "TC-WORKFLOW-GATE-OWNERSHIP-001"}},
            ],
            "edges": [
                {"id": surface + "." + kind, "type": kind, "source": "surface." + surface,
                 "target": owner, "reason": "Controlled complete " + kind + " ownership"}
                for surface in ("source", "schema")
                for kind, owner in (("owns-test", "owner.make"), ("adversarial-control", "owner.case"))
            ] + [{"id": "generated-schema.depends", "type": "depends-on",
                  "source": "surface.schema", "target": "surface.source",
                  "reason": "Schema depends on its measured consumer"}],
            "path_rules": [
                {"id": "paths.schema", "surface": "surface.schema",
                 "include": [{"kind": "exact", "path": "scripts/validation_ownership/graph.schema.json"}],
                 "exclude": []},
                {"id": "paths.source", "surface": "surface.source",
                 "include": [{"kind": "prefix", "path": path} for path in (
                     "scripts/", "tools/", "docs/", "src/", ".github/",
                 )] + [{"kind": "exact", "path": "Makefile"}],
                 "exclude": [{"kind": "exact", "path": "scripts/validation_ownership/graph.schema.json"}]},
            ],
            "exclusions": [],
        }
        self.add(reporter.GRAPH_PATH, json.dumps(graph))
        dynamics = {"schema_version": 1, "contracts": [], "seal": ""}
        dynamics["seal"] = reporter._sha256(
            reporter.MAKE_DYNAMIC_SEAL_DOMAIN, reporter.canonical_make_dynamic_payload(dynamics),
        )
        self.add(reporter.MAKE_DYNAMIC_PATH, json.dumps(dynamics))
        oracle = {
            "schema_version": 1, "source_case": "TC-WORKFLOW-GATE-OWNERSHIP-001",
            "probes": [
                {"path": path, "expected_surface": "surface." + surface,
                 "expected_owners": [
                     {"edge_type": "owns-test", "evidence_id": "owner.make"},
                     {"edge_type": "adversarial-control", "evidence_id": "owner.case"},
                 ]}
                for path, surface in (("Makefile", "source"),
                                      ("scripts/validation_ownership/graph.schema.json", "schema"))
            ], "seal": "",
        }
        oracle["seal"] = reporter._sha256(
            reporter.PROBE_SEAL_DOMAIN, reporter.canonical_probe_oracle_payload(oracle),
        )
        self.add(reporter.PROBE_ORACLE_PATH, json.dumps(oracle))
        self.git("init", "--quiet")
        self.foundation_base = None
        if foundation_base:
            self.git("add", "--", *[
                "scripts/validation_ownership/" + name for name in (
                    "authority.py", "budget.py", "make_probe.py", "syscall_guard.py",
                    "sandbox_exec.py", "shell_interceptor.c", "make_observer.c",
                    "lifecycle.py", "dispatch.h",
                )
            ])
            self.git("commit", "--quiet", "-m", "Actual foundation-only fixture")
            self.foundation_base = self.git("rev-parse", "HEAD").decode().strip()
        self.commit("Initial complete ownership fixture")

    def add(self, path, value):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value)

    def git(self, *args):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(self.root), *args],
            env={**ENVIRONMENT, "TMPDIR": str(self.directory),
                 "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                 "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"},
            check=True, capture_output=True, timeout=30,
        ).stdout

    def commit(self, message):
        self.git("add", "-A", "--", ".")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD").decode().strip()

    def close(self):
        shutil.rmtree(self.directory)
