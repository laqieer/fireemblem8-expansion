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
            include_path = scratch / "rules.mk"
            make_source = (
                "include rules.mk\n"
                "alpha: first\n"
                "alpha: second\n"
                "\t@echo $(ALPHA)\n"
                "beta:\n"
                "\t@echo beta\n"
            )
            make_path.write_text(make_source, encoding="ascii")
            include_path.write_text("ALPHA := value\n", encoding="ascii")
            entries = {
                path: reporter.GitTreeEntry(path, "100644", "blob", "0" * 40)
                for path in ("Makefile", "rules.mk")
            }
            loader = reporter.AuthorityLoader(scratch, entries)
            before_make = reporter._parse_make_authorities(loader)

            make_path.write_text(
                "# comment-only change\n"
                "include   rules.mk\n"
                "alpha :  first\n"
                "alpha:   second\n"
                "\t@echo $(ALPHA)\n"
                "beta:\n"
                "\t@echo beta\n",
                encoding="ascii",
            )
            normalized_make = reporter._parse_make_authorities(loader)
            self.assertEqual(before_make, normalized_make)

            make_path.write_text(
                "# declaration-order fixture\n"
                "include rules.mk\n"
                "alpha: second\n"
                "alpha: first\n"
                "\t@echo $(ALPHA)\n"
                "beta:\n"
                "\t@echo beta\n",
                encoding="ascii",
            )
            reordered_make = reporter._parse_make_authorities(loader)
            self.assertNotEqual(before_make["alpha"], reordered_make["alpha"])

            make_path.write_text(
                make_source.replace("@echo beta", "@echo changed-beta"),
                encoding="ascii",
            )
            changed_make = reporter._parse_make_authorities(loader)
            self.assertEqual(before_make["alpha"], changed_make["alpha"])
            self.assertNotEqual(before_make["beta"], changed_make["beta"])

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

    def test_make_fingerprints_track_actual_gnu_make_behavior(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            makefile = scratch / "Makefile"
            entry = reporter.GitTreeEntry("Makefile", "100644", "blob", "0" * 40)
            loader = reporter.AuthorityLoader(scratch, {"Makefile": entry})

            def parse(text, target):
                makefile.write_text(text, encoding="ascii")
                return reporter._parse_make_authorities(loader).get(target)

            def run(text, target):
                makefile.write_text(text, encoding="ascii")
                return subprocess.run(
                    ["make", "--no-print-directory", "-s", target],
                    cwd=scratch,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            assignment_a = (
                "VALUE := first\nVALUE := second\n"
                "assignment:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            assignment_b = (
                "VALUE := second\nVALUE := first\n"
                "assignment:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            self.assertNotEqual(
                run(assignment_a, "assignment").stdout,
                run(assignment_b, "assignment").stdout,
            )
            self.assertNotEqual(
                parse(assignment_a, "assignment"),
                parse(assignment_b, "assignment"),
            )

            target_assignment_a = (
                "target-assignment: VALUE := first\n"
                "target-assignment: VALUE += second\n"
                "target-assignment:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            target_assignment_b = (
                "target-assignment: VALUE := second\n"
                "target-assignment: VALUE += first\n"
                "target-assignment:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            self.assertNotEqual(
                run(target_assignment_a, "target-assignment").stdout,
                run(target_assignment_b, "target-assignment").stdout,
            )
            self.assertNotEqual(
                parse(target_assignment_a, "target-assignment"),
                parse(target_assignment_b, "target-assignment"),
            )

            recursive = (
                "A = one\nVALUE = $(A)\nA = two\n"
                "flavor:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            immediate = recursive.replace("VALUE = $(A)", "VALUE := $(A)")
            self.assertNotEqual(
                run(recursive, "flavor").stdout,
                run(immediate, "flavor").stdout,
            )
            self.assertNotEqual(
                parse(recursive, "flavor"),
                parse(immediate, "flavor"),
            )

            immediate_old = (
                "BASE = old\n"
                "OBJECT := $(BASE)\n"
                "BASE = new\n"
                "all:\n\t@printf '%s\\n' '$(OBJECT)'\n"
            )
            immediate_later_change = immediate_old.replace(
                "BASE = new", "BASE = later"
            )
            immediate_earlier_change = immediate_old.replace(
                "BASE = old", "BASE = earlier"
            )
            self.assertEqual(run(immediate_old, "all").stdout, "old\n")
            self.assertEqual(run(immediate_later_change, "all").stdout, "old\n")
            self.assertEqual(
                parse(immediate_old, "all"),
                parse(immediate_later_change, "all"),
            )
            self.assertNotEqual(
                run(immediate_old, "all").stdout,
                run(immediate_earlier_change, "all").stdout,
            )
            self.assertNotEqual(
                parse(immediate_old, "all"),
                parse(immediate_earlier_change, "all"),
            )
            immediate_posix = immediate_old.replace(
                "OBJECT :=", "OBJECT ::="
            )
            self.assertEqual(
                run(immediate_old, "all").stdout,
                run(immediate_posix, "all").stdout,
            )
            self.assertNotEqual(
                parse(immediate_old, "all"),
                parse(immediate_posix, "all"),
            )

            recursive_old = immediate_old.replace("OBJECT :=", "OBJECT =")
            recursive_later_change = immediate_later_change.replace(
                "OBJECT :=", "OBJECT ="
            )
            self.assertNotEqual(
                run(recursive_old, "all").stdout,
                run(recursive_later_change, "all").stdout,
            )
            self.assertNotEqual(
                parse(recursive_old, "all"),
                parse(recursive_later_change, "all"),
            )

            target_immediate = (
                "BASE = old\n"
                "all: OBJECT := $(BASE)\n"
                "BASE = new\n"
                "all:\n\t@printf '%s\\n' '$(OBJECT)'\n"
            )
            target_later_change = target_immediate.replace(
                "BASE = new", "BASE = later"
            )
            self.assertEqual(run(target_immediate, "all").stdout, "old\n")
            self.assertEqual(run(target_later_change, "all").stdout, "old\n")
            self.assertEqual(
                parse(target_immediate, "all"),
                parse(target_later_change, "all"),
            )
            target_recursive = target_immediate.replace(
                "all: OBJECT :=", "all: OBJECT ="
            )
            target_recursive_later = target_later_change.replace(
                "all: OBJECT :=", "all: OBJECT ="
            )
            self.assertEqual(run(target_recursive, "all").stdout, "new\n")
            self.assertEqual(run(target_recursive_later, "all").stdout, "later\n")
            self.assertNotEqual(
                parse(target_recursive, "all"),
                parse(target_recursive_later, "all"),
            )

            target_simple_append = (
                "BASE = old\n"
                "all: VALUE := $(BASE)\n"
                "all: VALUE += $(BASE)\n"
                "BASE = new\n"
                "all:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            target_recursive_append = target_simple_append.replace(
                "VALUE :=", "VALUE ="
            )
            self.assertEqual(
                run(target_simple_append, "all").stdout,
                "old old\n",
            )
            self.assertEqual(
                run(target_recursive_append, "all").stdout,
                "new new\n",
            )
            self.assertNotEqual(
                parse(target_simple_append, "all"),
                parse(target_recursive_append, "all"),
            )

            inherited = (
                "BASE = old\n"
                "all: VALUE = $(BASE)\n"
                "all: child\n"
                "BASE = new\n"
                "child:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            inherited_later = inherited.replace("BASE = new", "BASE = later")
            self.assertEqual(run(inherited, "all").stdout, "new\n")
            self.assertEqual(run(inherited_later, "all").stdout, "later\n")
            self.assertNotEqual(
                parse(inherited, "all"),
                parse(inherited_later, "all"),
            )

            secondary = (
                ".SECONDEXPANSION:\n"
                "BASE = old\n"
                "all: DEPS = $(BASE)\n"
                "all: $$(DEPS)\n"
                "BASE = child\n"
                "child:\n\t@printf 'secondary\\n'\n"
            )
            self.assertEqual(run(secondary, "all").stdout, "secondary\n")
            self.assertIn(
                "child",
                {
                    item["target"]
                    for item in parse(secondary, "all")["transitive"]
                },
            )

            target_conditional_default = (
                "VALUE = global-old\n"
                "all: VALUE ?= target\n"
                "VALUE = global-new\n"
                "all:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            target_conditional_later = target_conditional_default.replace(
                "VALUE = global-new", "VALUE = global-later"
            )
            self.assertEqual(
                run(target_conditional_default, "all").stdout,
                "global-new\n",
            )
            self.assertEqual(
                run(target_conditional_later, "all").stdout,
                "global-later\n",
            )
            self.assertNotEqual(
                parse(target_conditional_default, "all"),
                parse(target_conditional_later, "all"),
            )

            conditional_old = (
                "BASE = old\n"
                "ifeq ($(BASE),old)\n"
                "OBJECT := enabled\n"
                "else\n"
                "OBJECT := disabled\n"
                "endif\n"
                "all:\n\t@printf '%s\\n' '$(OBJECT)'\n"
            )
            conditional_new = conditional_old.replace(
                "BASE = old", "BASE = new"
            )
            self.assertEqual(run(conditional_old, "all").stdout, "enabled\n")
            self.assertEqual(run(conditional_new, "all").stdout, "disabled\n")
            self.assertNotEqual(
                parse(conditional_old, "all"),
                parse(conditional_new, "all"),
            )

            prerequisites_a = (
                "first second:\n\t@:\n"
                "ordered: first second\n\t@printf '%s\\n' '$<'\n"
            )
            prerequisites_b = prerequisites_a.replace(
                "ordered: first second", "ordered: second first"
            )
            self.assertNotEqual(
                run(prerequisites_a, "ordered").stdout,
                run(prerequisites_b, "ordered").stdout,
            )
            self.assertNotEqual(
                parse(prerequisites_a, "ordered"),
                parse(prerequisites_b, "ordered"),
            )

            conditional_true = (
                "ifeq (1,1)\nconditional:\n\t@printf 'yes\\n'\nendif\n"
            )
            conditional_false = conditional_true.replace("ifeq (1,1)", "ifeq (1,0)")
            self.assertEqual(run(conditional_true, "conditional").returncode, 0)
            self.assertNotEqual(run(conditional_false, "conditional").returncode, 0)
            self.assertNotEqual(
                parse(conditional_true, "conditional"),
                parse(conditional_false, "conditional"),
            )

            assignment_true = (
                "ifeq (1,1)\nVALUE := enabled\nendif\n"
                "conditional-assignment:\n\t@printf '%s\\n' '$(VALUE)'\n"
            )
            assignment_false = assignment_true.replace("ifeq (1,1)", "ifeq (1,0)")
            self.assertNotEqual(
                run(assignment_true, "conditional-assignment").stdout,
                run(assignment_false, "conditional-assignment").stdout,
            )
            self.assertNotEqual(
                parse(assignment_true, "conditional-assignment"),
                parse(assignment_false, "conditional-assignment"),
            )

            aggregate_one = (
                "all: child\n"
                "child:\n\t@printf 'one\\n'\n"
                "unrelated:\n\t@printf 'stable\\n'\n"
            )
            aggregate_two = aggregate_one.replace("printf 'one", "printf 'two")
            self.assertEqual(run(aggregate_one, "all").stdout, "one\n")
            self.assertEqual(run(aggregate_two, "all").stdout, "two\n")
            parsed_one = parse(aggregate_one, "all")
            parsed_two = parse(aggregate_two, "all")
            self.assertNotEqual(parsed_one, parsed_two)
            makefile.write_text(aggregate_one, encoding="ascii")
            all_one = reporter._parse_make_authorities(loader)
            makefile.write_text(aggregate_two, encoding="ascii")
            all_two = reporter._parse_make_authorities(loader)
            self.assertEqual(all_one["unrelated"], all_two["unrelated"])

            child_contract_one = (
                "first second:\n\t@:\n"
                "all: child\n"
                "child: VALUE := one\n"
                "child: first second\n"
                "\t@printf '%s %s\\n' '$(VALUE)' '$<'\n"
            )
            child_contract_two = child_contract_one.replace(
                "VALUE := one", "VALUE += two"
            ).replace("child: first second", "child: second first")
            self.assertNotEqual(
                run(child_contract_one, "all").stdout,
                run(child_contract_two, "all").stdout,
            )
            self.assertNotEqual(
                parse(child_contract_one, "all"),
                parse(child_contract_two, "all"),
            )

            target_prerequisite_one = (
                "all: child\n"
                "child: DEPS := grand\n"
                "child: $(DEPS)\n"
                "grand:\n\t@printf 'grand-one\\n'\n"
            )
            target_prerequisite_two = target_prerequisite_one.replace(
                "grand-one", "grand-two"
            )
            self.assertNotEqual(
                parse(target_prerequisite_one, "all"),
                parse(target_prerequisite_two, "all"),
            )
            self.assertIn(
                "grand",
                {
                    item["target"]
                    for item in parse(target_prerequisite_one, "all")["transitive"]
                },
            )

            pattern_one = (
                "all: sample.out\n"
                "%.out: %.in\n\t@printf 'pattern-one\\n'\n"
            )
            pattern_two = pattern_one.replace("pattern-one", "pattern-two")
            self.assertNotEqual(
                parse(pattern_one, "all"),
                parse(pattern_two, "all"),
            )
            self.assertIn(
                "%.out",
                {
                    item["target"]
                    for item in parse(pattern_one, "all")["transitive"]
                },
            )

            (scratch / "foo.c").write_text("int value;\n", encoding="ascii")
            addprefix_one = (
                "OBJECTS := $(addprefix build/,foo.o)\n"
                "all: $(OBJECTS)\n"
                "build/%.o: %.c\n"
                "\t@mkdir -p $(@D)\n"
                "\t@printf 'one\\n'\n"
            )
            addprefix_two = addprefix_one.replace("printf 'one", "printf 'two")
            self.assertEqual(run(addprefix_one, "all").stdout, "one\n")
            shutil.rmtree(scratch / "build")
            self.assertEqual(run(addprefix_two, "all").stdout, "two\n")
            self.assertNotEqual(
                parse(addprefix_one, "all"),
                parse(addprefix_two, "all"),
            )
            self.assertIn(
                "build/%.o",
                {
                    item["target"]
                    for item in parse(addprefix_one, "all")["transitive"]
                },
            )

            cycle = "all: child\nchild: all\n\t@true\n"
            cycle_record = parse(cycle, "all")
            self.assertEqual(cycle_record, parse(cycle, "all"))
            self.assertTrue(cycle_record["cycles"])

    def test_make_define_and_call_semantics_match_gnu_make(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            makefile = scratch / "Makefile"
            loader = reporter.AuthorityLoader(
                scratch,
                {
                    "Makefile": reporter.GitTreeEntry(
                        "Makefile", "100644", "blob", "0" * 40
                    )
                },
            )

            def parse(text, target="all"):
                makefile.write_text(text, encoding="ascii")
                return reporter._parse_make_authorities(loader, {target})[target]

            def run(text, target="all"):
                makefile.write_text(text, encoding="ascii")
                return subprocess.run(
                    ["make", "--no-print-directory", "-s", target],
                    cwd=scratch,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            define_one = (
                "define emit\n"
                "@printf '%s' '$(1)'\n"
                "@printf '%s\\n' '-one'\n"
                "endef\n"
                "all:\n"
                "\t$(call emit,arg)\n"
                "unrelated:\n"
                "\t@true\n"
            )
            define_two = define_one.replace("'-one'", "'-two'")
            self.assertEqual(run(define_one).stdout, "arg-one\n")
            self.assertEqual(run(define_two).stdout, "arg-two\n")
            parsed_one = parse(define_one)
            parsed_two = parse(define_two)
            self.assertNotEqual(parsed_one, parsed_two)
            self.assertEqual(
                parsed_one,
                parse("# unrelated comment\n" + define_one),
            )
            self.assertEqual(
                parsed_one["record"]["recipe_calls"][0]["macros"]["emit"][0][
                    "value"
                ],
                "@printf '%s' '$(1)'\n@printf '%s\\n' '-one'",
            )
            makefile.write_text(define_one, encoding="ascii")
            all_one = reporter._parse_make_authorities(loader)
            makefile.write_text(define_two, encoding="ascii")
            all_two = reporter._parse_make_authorities(loader)
            self.assertEqual(all_one["unrelated"], all_two["unrelated"])

            recursive = (
                "BASE = old\n"
                "define VALUE\n"
                "$(BASE)\n"
                "endef\n"
                "BASE = new\n"
                "all:\n"
                "\t@printf '%s\\n' '$(VALUE)'\n"
            )
            recursive_later = recursive.replace("BASE = new", "BASE = later")
            immediate = recursive.replace("define VALUE", "define VALUE :=")
            immediate_later = recursive_later.replace(
                "define VALUE",
                "define VALUE :=",
            )
            immediate_posix = recursive.replace(
                "define VALUE",
                "define VALUE ::=",
            )
            self.assertEqual(run(recursive).stdout, "new\n")
            self.assertEqual(run(recursive_later).stdout, "later\n")
            self.assertNotEqual(parse(recursive), parse(recursive_later))
            self.assertEqual(run(immediate).stdout, "old\n")
            self.assertEqual(run(immediate_later).stdout, "old\n")
            self.assertEqual(parse(immediate), parse(immediate_later))
            self.assertEqual(run(immediate_posix).stdout, "old\n")

            nested = (
                "define inner\n"
                "@printf '%s\\n' '$(0):$(1):$(2)'\n"
                "endef\n"
                "define outer\n"
                "$(call inner,$(1),tail)\n"
                "endef\n"
                "all:\n"
                "\t$(call outer,head)\n"
            )
            self.assertEqual(run(nested).stdout, "inner:head:tail\n")
            nested_authority = parse(nested)
            self.assertEqual(
                nested_authority["record"]["recipe_calls"][0][
                    "effective_values"
                ],
                ["@printf '%s\\n' 'inner:head:tail'"],
            )
            self.assertEqual(
                nested_authority["record"]["expanded_recipes"][0]["expanded"],
                "@printf '%s\\n' 'inner:head:tail'",
            )

            append_default = (
                "DEFAULT = kept\n"
                "define VALUE\n"
                "one\n"
                "endef\n"
                "define VALUE +=\n"
                "two\n"
                "endef\n"
                "define DEFAULT ?=\n"
                "replaced\n"
                "endef\n"
                "all:\n"
                "\t@printf '%s|%s\\n' '$(VALUE)' '$(DEFAULT)'\n"
            )
            self.assertEqual(run(append_default).stdout, "one two|kept\n")
            append_record = parse(append_default)["record"]["recipe_variables"]
            self.assertEqual(append_record["VALUE"]["effective_values"], ["one two"])
            self.assertEqual(append_record["DEFAULT"]["effective_values"], ["kept"])

            prerequisite_one = (
                "define dependency\n"
                "child\n"
                "endef\n"
                "all: $(call dependency)\n"
                "child:\n"
                "\t@printf 'one\\n'\n"
            )
            prerequisite_two = prerequisite_one.replace(
                "define dependency\nchild",
                "define dependency\nother",
            ).replace(
                "child:\n\t@printf 'one",
                "other:\n\t@printf 'two",
            )
            self.assertEqual(run(prerequisite_one).stdout, "one\n")
            self.assertEqual(run(prerequisite_two).stdout, "two\n")
            self.assertNotEqual(
                parse(prerequisite_one),
                parse(prerequisite_two),
            )
            self.assertEqual(
                {
                    item["target"]
                    for item in parse(prerequisite_one)["transitive"]
                },
                {"child"},
            )

    def test_make_define_and_call_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            makefile = scratch / "Makefile"
            loader = reporter.AuthorityLoader(
                scratch,
                {
                    "Makefile": reporter.GitTreeEntry(
                        "Makefile", "100644", "blob", "0" * 40
                    )
                },
            )

            malformed = {
                "missing-name": "define\nvalue\nendef\nall:\n\t@true\n",
                "unsupported-operator": (
                    "define VALUE !=\nvalue\nendef\nall:\n\t@true\n"
                ),
                "unsupported-modifier": (
                    "override define VALUE\nvalue\nendef\nall:\n\t@true\n"
                ),
                "nested": (
                    "define OUTER\ndefine INNER\nvalue\nendef\nendef\n"
                    "all:\n\t@true\n"
                ),
                "unmatched": "endef\nall:\n\t@true\n",
                "unclosed": "define VALUE\nvalue\n",
            }
            for label, text in malformed.items():
                with self.subTest(definition=label):
                    makefile.write_text(text, encoding="ascii")
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._parse_make_authorities(loader, {"all"})

            cyclic = (
                "define A\n$(call B)\nendef\n"
                "define B\n$(call A)\nendef\n"
                "all:\n\t$(call A)\n"
            )
            makefile.write_text(cyclic, encoding="ascii")
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "cyclic dynamic Make prerequisite variables",
            ):
                reporter._parse_make_authorities(loader, {"all"})

            for label, text, error in (
                (
                    "undefined",
                    "all:\n\t$(call MISSING)\n",
                    "undefined macro",
                ),
                (
                    "unsupported-body",
                    "define MACRO\n$(unsupported value)\nendef\n"
                    "all:\n\t$(call MACRO)\n",
                    "unsupported dynamic Make prerequisite function",
                ),
            ):
                with self.subTest(reachable_call=label):
                    makefile.write_text(text, encoding="ascii")
                    with self.assertRaisesRegex(
                        reporter.OwnershipError,
                        error,
                    ):
                        reporter._parse_make_authorities(loader, {"all"})

            def call_chain(length):
                definitions = []
                for index in range(1, length + 1):
                    body = (
                        "@true"
                        if index == length
                        else f"$(call M{index + 1})"
                    )
                    definitions.append(
                        f"define M{index}\n{body}\nendef\n"
                    )
                return "".join(definitions) + "all:\n\t$(call M1)\n"

            makefile.write_text(call_chain(64), encoding="ascii")
            self.assertIn(
                "all",
                reporter._parse_make_authorities(loader, {"all"}),
            )
            makefile.write_text(call_chain(65), encoding="ascii")
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "expansion exceeds depth bound",
            ):
                reporter._parse_make_authorities(loader, {"all"})

            expander = reporter.SafeMakeExpander(
                loader,
                {
                    "MACRO": [
                        {
                            "operator": "=",
                            "value": "one two three",
                            "context": (),
                            "syntax": "define",
                            "_sequence": 0,
                        }
                    ]
                },
            )
            expander.MAX_WORDS = 2
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "word bound",
            ):
                expander.expand("$(call MACRO)")
            expander.MAX_WORDS = reporter.SafeMakeExpander.MAX_WORDS
            expander.MAX_VARIANTS = 0
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "variant bound",
            ):
                expander.expand("$(call MACRO)")

    def test_make_global_and_target_modifiers_match_gnu_make(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            makefile = scratch / "Makefile"
            loader = reporter.AuthorityLoader(
                scratch,
                {
                    "Makefile": reporter.GitTreeEntry(
                        "Makefile", "100644", "blob", "0" * 40
                    )
                },
            )

            def parse(text, target="all"):
                makefile.write_text(text, encoding="ascii")
                return reporter._parse_make_authorities(loader, {target})[target]

            def run(text, target="all", *arguments, env=None):
                makefile.write_text(text, encoding="ascii")
                return subprocess.run(
                    [
                        "make",
                        "--no-print-directory",
                        "-s",
                        *arguments,
                        target,
                    ],
                    cwd=scratch,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            exported_one = (
                "export VALUE := one\n"
                "all:\n"
                "\t@printf '%s|%s\\n' '$(VALUE)' \"$$VALUE\"\n"
            )
            exported_two = exported_one.replace("one", "two")
            self.assertEqual(run(exported_one).stdout, "one|one\n")
            self.assertEqual(run(exported_two).stdout, "two|two\n")
            one_authority = parse(exported_one)
            two_authority = parse(exported_two)
            self.assertNotEqual(one_authority, two_authority)
            self.assertIn(
                "one",
                one_authority["record"]["expanded_recipes"][0]["expanded"],
            )
            self.assertEqual(
                one_authority["effective_exported_environment"]["VALUE"][
                    "effective_values"
                ],
                ["one"],
            )

            bare_export = (
                "VALUE := one\n"
                "export VALUE\n"
                "all:\n"
                "\t@printf '%s|%s\\n' '$(VALUE)' \"$$VALUE\"\n"
            )
            unexported = bare_export.replace("export VALUE", "unexport VALUE")
            self.assertEqual(run(bare_export).stdout, "one|one\n")
            self.assertEqual(run(unexported).stdout, "one|\n")
            self.assertIn(
                "VALUE",
                parse(bare_export)["effective_exported_environment"],
            )
            self.assertNotIn(
                "VALUE",
                parse(unexported)["effective_exported_environment"],
            )
            ambient_export = (
                "export VALUE\n"
                "all:\n"
                "\t@printf '%s\\n' \"$$VALUE\"\n"
            )
            self.assertEqual(
                run(
                    ambient_export,
                    env={**os.environ, "VALUE": "ambient"},
                ).stdout,
                "ambient\n",
            )
            self.assertEqual(
                parse(ambient_export)["effective_exported_environment"][
                    "VALUE"
                ]["effective_values"],
                ["<ambient-environment:VALUE>"],
            )

            export_all = bare_export.replace("export VALUE", "export")
            cancel_export_all = export_all.replace(
                "export\n",
                "export\nunexport\n",
            )
            self.assertEqual(run(export_all).stdout, "one|one\n")
            self.assertEqual(run(cancel_export_all).stdout, "one|\n")
            self.assertIn(
                "VALUE",
                parse(export_all)["effective_exported_environment"],
            )
            self.assertEqual(
                parse(export_all)["record"]["export_policy"][
                    "ambient_environment"
                ],
                "all",
            )
            self.assertNotIn(
                "VALUE",
                parse(cancel_export_all)["effective_exported_environment"],
            )

            override_one = (
                "export override VALUE := one\n"
                "all:\n"
                "\t@printf '%s|%s\\n' '$(VALUE)' \"$$VALUE\"\n"
            )
            override_two = override_one.replace("one", "two")
            self.assertEqual(
                run(override_one, "all", "VALUE=command").stdout,
                "one|one\n",
            )
            self.assertEqual(
                run(override_two, "all", "VALUE=command").stdout,
                "two|two\n",
            )
            self.assertNotEqual(parse(override_one), parse(override_two))
            self.assertEqual(
                parse(override_one)["record"]["recipe_variables"]["VALUE"][
                    "assignments"
                ][0]["modifiers"],
                ("override", "export"),
            )
            self.assertEqual(
                parse(override_one)["record"]["recipe_variables"]["VALUE"][
                    "external_precedence"
                ],
                "override",
            )

            target_normal = (
                "all: VALUE := target\n"
                "all:\n"
                "\t@printf '%s\\n' '$(VALUE)'\n"
            )
            target_override = target_normal.replace(
                "all: VALUE",
                "all: override VALUE",
            )
            self.assertEqual(
                run(target_normal, "all", "VALUE=command").stdout,
                "command\n",
            )
            self.assertEqual(
                run(target_override, "all", "VALUE=command").stdout,
                "target\n",
            )
            self.assertNotEqual(
                parse(target_normal),
                parse(target_override),
            )

            target_export = (
                "all: export VALUE := parent\n"
                "all: child\n"
                "child:\n"
                "\t@printf '%s|%s\\n' '$(VALUE)' \"$$VALUE\"\n"
            )
            self.assertEqual(run(target_export).stdout, "parent|parent\n")
            exported_child = next(
                item
                for item in parse(target_export)["transitive"]
                if item["target"] == "child"
            )
            self.assertEqual(
                exported_child["inherited_target_variables"]["VALUE"][
                    "effective_value"
                ],
                "parent",
            )
            self.assertEqual(
                exported_child["effective_exported_environment"]["VALUE"][
                    "effective_values"
                ],
                ["parent"],
            )

            target_private = (
                "VALUE := global\n"
                "all: private export VALUE := parent\n"
                "all: child\n"
                "all:\n"
                "\t@printf 'all=%s|%s\\n' '$(VALUE)' \"$$VALUE\"\n"
                "child:\n"
                "\t@printf 'child=%s|%s\\n' '$(VALUE)' \"$${VALUE-}\"\n"
            )
            self.assertEqual(
                run(target_private).stdout,
                "child=global|parent\nall=parent|parent\n",
            )
            private_authority = parse(target_private)
            self.assertEqual(
                private_authority["effective_exported_environment"]["VALUE"][
                    "effective_values"
                ],
                ["parent"],
            )
            private_child = next(
                item
                for item in private_authority["transitive"]
                if item["target"] == "child"
            )
            self.assertNotIn(
                "VALUE",
                private_child["inherited_target_variables"],
            )
            self.assertEqual(
                private_child["effective_exported_environment"]["VALUE"][
                    "effective_values"
                ],
                ["parent"],
            )

            malformed = (
                "export export VALUE := bad\nall:\n\t@true\n",
                "override override VALUE := bad\nall:\n\t@true\n",
                "private VALUE := bad\nall:\n\t@true\n",
                "all: private private VALUE := bad\nall:\n\t@true\n",
                "unexport VALUE := bad\nall:\n\t@true\n",
            )
            for text in malformed:
                with self.subTest(modifiers=text.splitlines()[0]):
                    makefile.write_text(text, encoding="ascii")
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._parse_make_authorities(loader, {"all"})

    def test_defined_recipe_macros_require_registered_expansion(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            makefile = scratch / "Makefile"
            tool = scratch / "tool.py"
            input_file = scratch / "input.txt"
            registry_path = scratch / reporter.MAKE_DYNAMIC_PATH
            registry_path.parent.mkdir(parents=True)
            marker = scratch / "shell-executed"
            tool.write_text(
                "from pathlib import Path\n"
                "Path('shell-executed').write_text('bad')\n"
                "print('dynamic')\n",
                encoding="ascii",
            )
            input_file.write_text("one\n", encoding="ascii")
            expression = "$(shell python3 tool.py)"
            direct = (
                "define MACRO\n"
                f"{expression}\n"
                "endef\n"
                "all:\n"
                "\t@printf '%s\\n' '$(MACRO)'\n"
            )
            called = direct.replace("$(MACRO)'", "$(call MACRO)'")
            ordinary_direct = (
                f"MACRO = {expression}\n"
                "all:\n"
                "\t@printf '%s\\n' '$(MACRO)'\n"
            )
            ordinary_called = ordinary_direct.replace(
                "$(MACRO)'",
                "$(call MACRO)'",
            )
            basic_entries = {
                "Makefile": reporter.GitTreeEntry(
                    "Makefile", "100644", "blob", "0" * 40
                )
            }
            for text in (
                direct,
                called,
                ordinary_direct,
                ordinary_called,
            ):
                makefile.write_text(text, encoding="ascii")
                with self.assertRaisesRegex(
                    reporter.OwnershipError,
                    "defined recipe macro|unsupported dynamic",
                ):
                    reporter._parse_make_authorities(
                        reporter.AuthorityLoader(scratch, basic_entries),
                        {"all"},
                    )
                self.assertFalse(marker.exists())

            nested = "value"
            for _ in range(65):
                nested = "$(strip " + nested + ")"
            ordinary_failures = {
                "unsupported": (
                    "MACRO = $(unsupported value)\n"
                    "all:\n\t@echo $(MACRO)\n"
                ),
                "cycle": (
                    "MACRO = $(OTHER)\n"
                    "OTHER = $(MACRO)\n"
                    "all:\n\t@echo $(MACRO)\n"
                ),
                "undefined-call": (
                    "MACRO = $(call MISSING)\n"
                    "all:\n\t@echo $(MACRO)\n"
                ),
                "depth": f"MACRO = {nested}\nall:\n\t@echo $(MACRO)\n",
            }
            for label, text in ordinary_failures.items():
                with self.subTest(ordinary_macro=label):
                    makefile.write_text(text, encoding="ascii")
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._parse_make_authorities(
                            reporter.AuthorityLoader(
                                scratch,
                                basic_entries,
                            ),
                            {"all"},
                        )

            contract = {
                "schema_version": 1,
                "contracts": [
                    {
                        "id": "synthetic-recipe-macro",
                        "expression": expression,
                        "tool": "tool.py",
                        "input_files": ["input.txt"],
                        "input_variables": [],
                        "automatic_inputs": [],
                        "resolved_value": "dynamic",
                        "owning_evidence_ids": ["owner.synthetic"],
                    }
                ],
                "seal": "",
            }
            contract["seal"] = reporter._sha256(
                reporter.MAKE_DYNAMIC_SEAL_DOMAIN,
                reporter.canonical_make_dynamic_payload(contract),
            )
            registry_path.write_bytes(reporter.normalized_json(contract))
            entries = {
                path: reporter.GitTreeEntry(
                    path, "100644", "blob", "0" * 40
                )
                for path in (
                    "Makefile",
                    "tool.py",
                    "input.txt",
                    reporter.MAKE_DYNAMIC_PATH.as_posix(),
                )
            }
            for text in (
                direct,
                called,
                ordinary_direct,
                ordinary_called,
            ):
                makefile.write_text(text, encoding="ascii")
                authority = reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, entries),
                    {"all"},
                    require_dynamic_contracts=True,
                )["all"]
                self.assertEqual(
                    {
                        item["id"]
                        for item in authority["dynamic_dependencies"]
                    },
                    {"synthetic-recipe-macro"},
                )
                self.assertIn(
                    "dynamic",
                    authority["record"]["expanded_recipes"][0]["expanded"],
                )
                self.assertFalse(marker.exists())

            makefile.write_text(ordinary_direct, encoding="ascii")
            before = reporter._parse_make_authorities(
                reporter.AuthorityLoader(scratch, entries),
                {"all"},
                require_dynamic_contracts=True,
            )["all"]
            tool.write_text(
                "from pathlib import Path\n"
                "Path('shell-executed').write_text('worse')\n"
                "print('dynamic')\n",
                encoding="ascii",
            )
            after = reporter._parse_make_authorities(
                reporter.AuthorityLoader(scratch, entries),
                {"all"},
                require_dynamic_contracts=True,
            )["all"]
            self.assertNotEqual(before, after)
            self.assertFalse(marker.exists())

    def test_make_defaults_bind_declared_ambient_variants(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            makefile = scratch / "Makefile"
            registry_path = scratch / reporter.MAKE_DYNAMIC_PATH
            registry_path.parent.mkdir(parents=True)

            def write_registry(names):
                registry = {
                    "schema_version": 2,
                    "contracts": [],
                    "ambient_inputs": {
                        "allowed_names": sorted(names),
                        "allowed_sources": [
                            "command-line",
                            "process-environment",
                        ],
                        "value_policy": "symbolic-no-host-value",
                        "provenance": "gnu-make-import-before-default",
                        "evidence_binding": "consuming-make-target",
                    },
                    "seal": "",
                }
                registry["seal"] = reporter._sha256(
                    reporter.MAKE_DYNAMIC_SEAL_DOMAIN,
                    reporter.canonical_make_dynamic_payload(registry),
                )
                registry_path.write_bytes(reporter.normalized_json(registry))
                return registry

            write_registry({"VALUE"})
            entries = {
                path: reporter.GitTreeEntry(
                    path, "100644", "blob", "0" * 40
                )
                for path in (
                    "Makefile",
                    reporter.MAKE_DYNAMIC_PATH.as_posix(),
                )
            }

            def parse(text):
                makefile.write_text(text, encoding="ascii")
                return reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, entries),
                    {"all"},
                    require_dynamic_contracts=True,
                )["all"]

            def run(text, env, *arguments):
                makefile.write_text(text, encoding="ascii")
                return subprocess.run(
                    [
                        "make",
                        "--no-print-directory",
                        "-s",
                        *arguments,
                        "all",
                    ],
                    cwd=scratch,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            defaulted = (
                "VALUE ?= fallback\n"
                "all:\n"
                "\t@printf 'make=%s env=%s\\n' '$(VALUE)' \"$${VALUE-}\"\n"
            )
            clean_environment = dict(os.environ)
            clean_environment.pop("VALUE", None)
            ambient_environment = {
                **clean_environment,
                "VALUE": "ambient",
            }
            self.assertEqual(
                run(defaulted, clean_environment).stdout,
                "make=fallback env=\n",
            )
            self.assertEqual(
                run(defaulted, ambient_environment).stdout,
                "make=ambient env=ambient\n",
            )
            self.assertEqual(
                run(defaulted, clean_environment, "VALUE=command").stdout,
                "make=command env=command\n",
            )
            clean_authority = parse(defaulted)
            with mock.patch.dict(os.environ, {"VALUE": "host-secret"}):
                ambient_authority = parse(defaulted)
            self.assertEqual(clean_authority, ambient_authority)
            semantics = clean_authority["record"]["recipe_variables"]["VALUE"]
            self.assertEqual(
                [item["source"] for item in semantics["authority_variants"]],
                [
                    "command-line",
                    "process-environment",
                    "tracked-fallback",
                ],
            )
            self.assertEqual(
                {
                    item["source"]
                    for item in clean_authority["record"][
                        "expanded_recipes"
                    ][0]["expanded_variants"]
                },
                {
                    "tracked",
                    "command-line:VALUE",
                    "process-environment:VALUE",
                },
            )
            self.assertEqual(
                {
                    item["source"]: item["present"]
                    for item in clean_authority[
                        "effective_exported_environment"
                    ]["VALUE"]["variants"]
                },
                {
                    "command-line": True,
                    "process-environment": True,
                    "tracked-fallback": False,
                },
            )

            overridden_default = defaulted.replace(
                "VALUE ?=",
                "override VALUE ?=",
            )
            self.assertEqual(
                run(overridden_default, ambient_environment).stdout,
                "make=ambient env=ambient\n",
            )
            self.assertEqual(
                run(
                    overridden_default,
                    clean_environment,
                    "VALUE=command",
                ).stdout,
                "make=command env=command\n",
            )
            self.assertTrue(
                parse(overridden_default)["record"]["recipe_variables"][
                    "VALUE"
                ]["attributes"]["override"]
            )

            prior_definition = (
                "VALUE := fixed\n"
                "VALUE ?= fallback\n"
                "all:\n"
                "\t@printf '%s\\n' '$(VALUE)'\n"
            )
            write_registry(set())
            prior_semantics = parse(prior_definition)["record"][
                "recipe_variables"
            ]["VALUE"]
            self.assertEqual(prior_semantics["effective_values"], ["fixed"])
            self.assertEqual(
                prior_semantics["ambient_input_contracts"],
                [],
            )

            write_registry({"VALUE"})
            makefile.write_text(
                "OTHER ?= fallback\nall:\n\t@echo $(OTHER)\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "ambient input registry does not match",
            ):
                reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, entries),
                    {"all"},
                    require_dynamic_contracts=True,
                )

            for label, field, value, error in (
                (
                    "sources",
                    "allowed_sources",
                    ["process-environment"],
                    "sources must be",
                ),
                (
                    "value-policy",
                    "value_policy",
                    "capture-host-value",
                    "value policy",
                ),
                (
                    "provenance",
                    "provenance",
                    "ambient-process",
                    "provenance",
                ),
                (
                    "evidence",
                    "evidence_binding",
                    "unbound",
                    "evidence binding",
                ),
            ):
                with self.subTest(ambient_contract=label):
                    registry = write_registry({"VALUE"})
                    registry["ambient_inputs"][field] = value
                    registry["seal"] = reporter._sha256(
                        reporter.MAKE_DYNAMIC_SEAL_DOMAIN,
                        reporter.canonical_make_dynamic_payload(registry),
                    )
                    registry_path.write_bytes(
                        reporter.normalized_json(registry)
                    )
                    with self.assertRaisesRegex(
                        reporter.OwnershipError,
                        error,
                    ):
                        reporter._parse_make_authorities(
                            reporter.AuthorityLoader(scratch, entries),
                            {"all"},
                            require_dynamic_contracts=True,
                        )
            registry = write_registry({"VALUE"})
            registry["seal"] = "0" * 64
            registry_path.write_bytes(reporter.normalized_json(registry))
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "seal does not match",
            ):
                reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, entries),
                    {"all"},
                    require_dynamic_contracts=True,
                )

    def test_make_expansion_rejects_unsupported_cycles_and_bounds(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            makefile = scratch / "Makefile"
            loader = reporter.AuthorityLoader(
                scratch,
                {
                    "Makefile": reporter.GitTreeEntry(
                        "Makefile", "100644", "blob", "0" * 40
                    )
                },
            )
            outside = scratch / "shell-must-not-run"
            makefile.write_text(
                "all: $(shell touch shell-must-not-run)\n\t@true\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "unregistered dynamic prerequisite.*function 'shell'",
            ):
                reporter._parse_make_authorities(loader, {"all"})
            self.assertFalse(outside.exists())

            makefile.write_text(
                "A = $(B)\nB = $(A)\nall: $(A)\n\t@true\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "cyclic dynamic Make prerequisite variables",
            ):
                reporter._parse_make_authorities(loader, {"all"})

            for assignment, error in (
                ("private private VALUE = hidden", "repeats"),
                ("override override VALUE = forced", "repeats"),
                ("export export VALUE = public", "repeats"),
                ("VALUE != printf shell", "target-specific shell assignment"),
            ):
                with self.subTest(target_assignment=assignment):
                    makefile.write_text(
                        f"all: {assignment}\nall:\n\t@true\n",
                        encoding="ascii",
                    )
                    with self.assertRaisesRegex(
                        reporter.OwnershipError,
                        error,
                    ):
                        reporter._parse_make_authorities(loader, {"all"})

            expander = reporter.SafeMakeExpander(
                loader,
                {
                    "WORDS": [
                        {
                            "operator": ":=",
                            "value": "one two three",
                            "context": (),
                            "_sequence": 0,
                        }
                    ]
                },
            )
            expander.MAX_WORDS = 2
            with self.assertRaisesRegex(reporter.OwnershipError, "word bound"):
                expander.expand("$(WORDS)")
            expander.MAX_WORDS = reporter.SafeMakeExpander.MAX_WORDS
            expander.MAX_VARIANTS = 2
            with self.assertRaisesRegex(reporter.OwnershipError, "variant bound"):
                expander._bounded(("one", "two", "three"), "fixture")

            expression = "value"
            for _ in range(64):
                expression = "$(strip " + expression + ")"
            self.assertEqual(expander.expand(expression), ["value"])
            expression = "$(strip " + expression + ")"
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "expression exceeds depth bound",
            ):
                expander.expand(expression)

    def test_dynamic_target_declarations_are_registered_or_rejected(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            makefile = scratch / "Makefile"
            tool = scratch / "tool.py"
            input_file = scratch / "input.txt"
            registry_path = scratch / reporter.MAKE_DYNAMIC_PATH
            registry_path.parent.mkdir(parents=True)
            tool.write_text(
                "from pathlib import Path\n"
                "Path('shell-executed').write_text('bad')\n"
                "print('child')\n",
                encoding="ascii",
            )
            input_file.write_text("authority\n", encoding="ascii")
            dynamic_expression = "$(shell python3 tool.py)"
            make_one = (
                "all: child\n"
                f"{dynamic_expression}:\n"
                "\t@printf 'one\\n'\n"
            )
            makefile.write_text(make_one, encoding="ascii")
            basic_entries = {
                "Makefile": reporter.GitTreeEntry(
                    "Makefile", "100644", "blob", "0" * 40
                )
            }
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "unregistered dynamic target declaration",
            ):
                reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, basic_entries),
                    {"all"},
                )
            self.assertFalse((scratch / "shell-executed").exists())

            contract = {
                "schema_version": 1,
                "contracts": [
                    {
                        "id": "synthetic-target",
                        "expression": dynamic_expression,
                        "tool": "tool.py",
                        "input_files": ["input.txt"],
                        "input_variables": [],
                        "automatic_inputs": [],
                        "resolved_value": "child",
                        "owning_evidence_ids": ["owner.synthetic"],
                    }
                ],
                "seal": "",
            }
            contract["seal"] = reporter._sha256(
                reporter.MAKE_DYNAMIC_SEAL_DOMAIN,
                reporter.canonical_make_dynamic_payload(contract),
            )
            registry_path.write_bytes(reporter.normalized_json(contract))
            entries = {
                path: reporter.GitTreeEntry(
                    path, "100644", "blob", "0" * 40
                )
                for path in (
                    "Makefile",
                    "tool.py",
                    "input.txt",
                    reporter.MAKE_DYNAMIC_PATH.as_posix(),
                )
            }
            loader = reporter.AuthorityLoader(scratch, entries)
            one = reporter._parse_make_authorities(
                loader,
                {"all"},
                require_dynamic_contracts=True,
            )["all"]
            self.assertFalse((scratch / "shell-executed").exists())
            self.assertIn(
                "child",
                {item["target"] for item in one["transitive"]},
            )
            self.assertEqual(
                {item["id"] for item in one["dynamic_dependencies"]},
                {"synthetic-target"},
            )

            makefile.write_text(make_one.replace("printf 'one", "printf 'two"), encoding="ascii")
            two = reporter._parse_make_authorities(
                reporter.AuthorityLoader(scratch, entries),
                {"all"},
                require_dynamic_contracts=True,
            )["all"]
            self.assertNotEqual(one, two)
            self.assertFalse((scratch / "shell-executed").exists())

            makefile.write_text(
                "TARGET = $(shell python3 tool.py)\n"
                "all: child\n"
                "$(TARGET):\n"
                "\t@printf 'nested\\n'\n",
                encoding="ascii",
            )
            nested = reporter._parse_make_authorities(
                reporter.AuthorityLoader(scratch, entries),
                {"all"},
                require_dynamic_contracts=True,
            )["all"]
            self.assertEqual(
                {item["id"] for item in nested["dynamic_dependencies"]},
                {"synthetic-target"},
            )
            self.assertFalse((scratch / "shell-executed").exists())

            makefile.write_text(
                "all:\n\t@true\n$(unsupported value):\n\t@true\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "unregistered dynamic target declaration",
            ):
                reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, entries),
                    {"all"},
                    require_dynamic_contracts=True,
                )

            makefile.write_text(
                "all:\n\t@true\n$@:\n\t@true\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "dynamic target declaration",
            ):
                reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, entries),
                    {"all"},
                    require_dynamic_contracts=True,
                )

            makefile.write_text(
                "A = $(B)\nB = $(A)\nall:\n\t@true\n$(A):\n\t@true\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "cyclic dynamic Make prerequisite variables",
            ):
                reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, entries),
                    {"all"},
                    require_dynamic_contracts=True,
                )

            expression = "child"
            for _ in range(65):
                expression = "$(strip " + expression + ")"
            makefile.write_text(
                "all:\n\t@true\n" + expression + ":\n\t@true\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reporter.OwnershipError,
                "expression exceeds depth bound",
            ):
                reporter._parse_make_authorities(
                    reporter.AuthorityLoader(scratch, entries),
                    {"all"},
                    require_dynamic_contracts=True,
                )

    def test_live_linker_dynamics_are_registered_and_complete(self):
        entries, loader, graph, schema = self.fixture_authority()
        model = reporter.validate_graph(graph, schema, loader, entries)
        target = next(
            node["authority"]["target"]
            for node in graph["nodes"]
            if node["id"] == "owner.link-modern"
        )
        authority = reporter._parse_make_authorities(
            loader,
            {target},
            require_dynamic_contracts=True,
        )[target]
        self.assertEqual(authority["unknown_dynamic_prerequisites"], [])
        for name in ("PYTHON", "MODERN_CONFIG", "MODERN_ABI"):
            self.assertEqual(
                [
                    item["name"]
                    for item in authority["record"]["recipe_variables"][name][
                        "ambient_input_contracts"
                    ]
                ],
                [name],
            )
        path_semantics = authority["effective_exported_environment"]["PATH"]
        self.assertEqual(
            {
                item["name"]
                for item in (
                    *path_semantics["ambient_input_contracts"],
                    *path_semantics["fallback_ambient_inputs"],
                )
            },
            {"DEVKITARM", "PATH", "TOOLCHAIN"},
        )
        self.assertEqual(
            {item["id"] for item in authority["dynamic_dependencies"]},
            {
                "banim-compressing-linker-inputs",
                "banim-scaninc-inputs",
                "generated-chapter-objectives-enablement",
                "generated-item-cap-resolution",
                "modern-libc-directory",
                "modern-libgcc-directory",
            },
        )
        self.assertEqual(
            model["authorities"]["owner.link-modern"]["fingerprint"],
            reporter._sha256(
                b"validation-ownership-make-target-v1\0",
                {"target": target, "record": authority},
            ),
        )

    def test_dynamic_input_change_invalidates_exact_owners(self):
        entries, _, graph, schema = self.fixture_authority()
        input_path = self.fixture_root / "linker_script_banim.txt"
        original = input_path.read_bytes()
        base_loader = reporter.AuthorityLoader(
            self.fixture_root,
            entries,
            "HEAD",
        )
        prior_graph = reporter._prior_graph(base_loader)
        self.assertIsNotNone(prior_graph)
        try:
            input_path.write_bytes(original + b"\nFIXTURE_DYNAMIC_INPUT\n")
            loader = reporter.AuthorityLoader(self.fixture_root, entries)
            model = reporter.validate_graph(graph, schema, loader, entries)
            self.assertEqual(
                reporter._authority_changed_edges(
                    graph,
                    prior_graph,
                    model,
                    loader,
                    base_loader,
                ),
                {
                    "configuration.link",
                    "configuration.target",
                    "generated-schema.link",
                    "generated-schema.target",
                    "generated.link",
                    "generated.target",
                    "localization.consumer",
                    "localization.link",
                    "localization.negative",
                    "manual.link",
                    "runtime.link",
                    "runtime.target",
                },
            )
        finally:
            input_path.write_bytes(original)

    def test_registered_recipe_dynamic_invalidates_exact_owners(self):
        entries, _, graph, schema = self.fixture_authority()
        tool_path = (
            self.fixture_root
            / "scripts/generated_data/chapterobjectives/enabled.py"
        )
        original = tool_path.read_bytes()
        base_loader = reporter.AuthorityLoader(
            self.fixture_root,
            entries,
            "HEAD",
        )
        prior_graph = reporter._prior_graph(base_loader)
        self.assertIsNotNone(prior_graph)
        try:
            tool_path.write_bytes(
                original + b"\nFIXTURE_RECIPE_DYNAMIC = True\n"
            )
            loader = reporter.AuthorityLoader(self.fixture_root, entries)
            model = reporter.validate_graph(graph, schema, loader, entries)
            self.assertEqual(
                reporter._authority_changed_edges(
                    graph,
                    prior_graph,
                    model,
                    loader,
                    base_loader,
                ),
                {
                    "configuration.compile",
                    "configuration.link",
                    "configuration.target",
                    "generated-schema.compile",
                    "generated-schema.adversarial",
                    "generated-schema.link",
                    "generated-schema.target",
                    "generated.compile",
                    "generated.adversarial",
                    "generated.link",
                    "generated.target",
                    "localization.compile",
                    "localization.consumer",
                    "localization.link",
                    "localization.negative",
                    "manual.compile",
                    "manual.link",
                    "runtime.compile",
                    "runtime.link",
                    "runtime.target",
                },
            )
        finally:
            tool_path.write_bytes(original)

    def test_recursive_make_authority_rejects_untracked_include(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            scratch = Path(directory)
            (scratch / "Makefile").write_text(
                "include untracked.mk\nowned:\n\t@true\n",
                encoding="ascii",
            )
            loader = reporter.AuthorityLoader(
                scratch,
                {
                    "Makefile": reporter.GitTreeEntry(
                        "Makefile", "100644", "blob", "0" * 40
                    )
                },
            )
            with self.assertRaisesRegex(reporter.OwnershipError, "not tracked"):
                reporter._parse_make_authorities(loader)

    def test_make_invalidation_is_target_specific(self):
        entries, _, graph, schema = self.fixture_authority()
        makefile = self.fixture_root / "Makefile"
        original = makefile.read_bytes()
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
            makefile.write_bytes(original + b"\n# comment-only fixture\n")
            self.assertEqual(changed_edges(), set())

            makefile.write_bytes(
                original.replace(
                    b'echo "The legacy comparison target has been removed',
                    b'echo "The obsolete comparison target has been removed',
                )
            )
            self.assertEqual(changed_edges(), set())

            makefile.write_bytes(
                original.replace(
                    b'check --repository-root "$(CURDIR)" > /dev/null',
                    b'check --repository-root "$(CURDIR)" > /dev/null; true',
                )
            )
            self.assertEqual(
                changed_edges(),
                {"configuration.owns-test"},
            )
        finally:
            makefile.write_bytes(original)

    def test_real_linker_child_mutation_invalidates_exact_parent_edges(self):
        entries, _, graph, schema = self.fixture_authority()
        modern_mk = self.fixture_root / "modern.mk"
        original = modern_mk.read_bytes()
        base_loader = reporter.AuthorityLoader(
            self.fixture_root,
            entries,
            "HEAD",
        )
        prior_graph = reporter._prior_graph(base_loader)
        self.assertIsNotNone(prior_graph)
        loader = reporter.AuthorityLoader(self.fixture_root, entries)
        before = reporter._parse_make_authorities(
            loader,
            {
                "expansion-modern-linker-check",
                "expansion-modern-all",
            },
        )
        try:
            mutated = original.replace(
                b"--check --validate-elf \\\n"
                b"\t\t--require-positive-headroom ewram \\\n"
                b"\t\t--require-positive-headroom iwram\n",
                b"--check --validate-elf \\\n"
                b"\t\t--require-positive-headroom ewram \\\n"
                b"\t\t--require-positive-headroom iwram --fixture-child-change\n",
                1,
            )
            self.assertNotEqual(mutated, original)
            modern_mk.write_bytes(mutated)
            loader = reporter.AuthorityLoader(self.fixture_root, entries)
            after = reporter._parse_make_authorities(
                loader,
                {
                    "expansion-modern-linker-check",
                    "expansion-modern-all",
                },
            )
            self.assertNotEqual(
                before["expansion-modern-linker-check"],
                after["expansion-modern-linker-check"],
            )
            self.assertEqual(
                before["expansion-modern-all"],
                after["expansion-modern-all"],
            )
            model = reporter.validate_graph(
                graph,
                schema,
                loader,
                entries,
            )
            self.assertEqual(
                reporter._authority_changed_edges(
                    graph,
                    prior_graph,
                    model,
                    loader,
                    base_loader,
                ),
                {
                    "configuration.link",
                    "generated-schema.link",
                    "generated.link",
                    "localization.consumer",
                    "localization.link",
                    "manual.link",
                    "runtime.link",
                },
            )
        finally:
            modern_mk.write_bytes(original)

    def test_real_starter_macro_mutation_invalidates_exact_link_edges(self):
        entries, _, graph, schema = self.fixture_authority()
        modern_mk = self.fixture_root / "modern.mk"
        original = modern_mk.read_bytes()
        base_loader = reporter.AuthorityLoader(
            self.fixture_root,
            entries,
            "HEAD",
        )
        prior_graph = reporter._prior_graph(base_loader)
        self.assertIsNotNone(prior_graph)
        loader = reporter.AuthorityLoader(self.fixture_root, entries)
        requested = {
            "expansion-modern-all",
            "expansion-modern-linker-check",
            "expansion-modern-starter-hook-check",
        }
        before = reporter._parse_make_authorities(loader, requested)

        def transitive_record(authority, target):
            return next(
                item["record"]
                for item in authority["transitive"]
                if item["target"] == target
            )

        before_starter = transitive_record(
            before["expansion-modern-linker-check"],
            "expansion-modern-starter-hook-check",
        )
        macro_records = before_starter["recipe_variables"][
            "modern_starter_content_disabled_negative"
        ]["assignments"]
        self.assertEqual(len(macro_records), 1)
        for required_check in (
            "ExpansionStarterContentCharmEvade",
            "grep -a -q",
            "gItemData",
        ):
            self.assertIn(required_check, macro_records[0]["value"])
        invocation = b"\t$(modern_starter_content_disabled_negative)\n"
        self.assertEqual(original.count(invocation), 2)
        start = b"define modern_starter_content_disabled_negative\n"
        end = b"\nendef\n\n# Fail loudly"
        prefix, separator, remainder = original.partition(start)
        self.assertEqual(separator, start)
        _, separator, suffix = remainder.partition(end)
        self.assertEqual(separator, end)
        mutated = (
            prefix
            + start
            + b"\t@printf 'fixture disables content artifact checks\\n'"
            + end
            + suffix
        )
        self.assertEqual(mutated.count(invocation), 2)
        mutated_body = mutated.partition(start)[2].partition(end)[0]
        for disabled_check in (
            b"ExpansionStarterContentCharmEvade",
            b"grep -a -q",
            b"gItemData",
        ):
            self.assertNotIn(disabled_check, mutated_body)
        try:
            modern_mk.write_bytes(mutated)
            loader = reporter.AuthorityLoader(self.fixture_root, entries)
            after = reporter._parse_make_authorities(loader, requested)
            self.assertNotEqual(
                before["expansion-modern-starter-hook-check"],
                after["expansion-modern-starter-hook-check"],
            )
            self.assertNotEqual(
                before["expansion-modern-linker-check"],
                after["expansion-modern-linker-check"],
            )
            self.assertNotEqual(
                before_starter,
                transitive_record(
                    after["expansion-modern-linker-check"],
                    "expansion-modern-starter-hook-check",
                ),
            )
            self.assertEqual(
                before["expansion-modern-all"],
                after["expansion-modern-all"],
            )
            model = reporter.validate_graph(
                graph,
                schema,
                loader,
                entries,
            )
            self.assertEqual(
                reporter._authority_changed_edges(
                    graph,
                    prior_graph,
                    model,
                    loader,
                    base_loader,
                ),
                {
                    "configuration.link",
                    "generated-schema.link",
                    "generated.link",
                    "localization.consumer",
                    "localization.link",
                    "manual.link",
                    "runtime.link",
                },
            )
        finally:
            modern_mk.write_bytes(original)

    def test_real_export_mutations_change_child_command_authority(self):
        entries, _, _, _ = self.fixture_authority()
        makefile = self.fixture_root / "Makefile"
        modern_mk = self.fixture_root / "modern.mk"
        original_makefile = makefile.read_bytes()
        original_modern = modern_mk.read_bytes()
        requested = {
            "expansion-modern-linker-check",
            "validation-ownership-check",
        }

        def parse():
            return reporter._parse_make_authorities(
                reporter.AuthorityLoader(self.fixture_root, entries),
                requested,
            )

        before = parse()
        for target in requested:
            self.assertIn(
                "PATH",
                before[target]["effective_exported_environment"],
            )
            self.assertEqual(
                before[target]["effective_exported_environment"]["PATH"][
                    "ambient_inputs"
                ],
                ["PATH"],
            )
            self.assertIn(
                "FE8_ITEM_ID_CAP",
                before[target]["effective_exported_environment"],
            )
            self.assertEqual(
                before[target]["effective_exported_environment"][
                    "FE8_ITEM_ID_CAP"
                ]["effective_values"],
                ["<ambient-environment:FE8_ITEM_ID_CAP>"],
            )
        try:
            changed_path = original_makefile.replace(
                b"export PATH := $(TOOLCHAIN)/bin:$(PATH)",
                b"export PATH := $(TOOLCHAIN)/fixture-bin:$(PATH)",
                1,
            )
            self.assertNotEqual(changed_path, original_makefile)
            makefile.write_bytes(changed_path)
            after_path = parse()
            for target in requested:
                self.assertNotEqual(before[target], after_path[target])
                self.assertNotEqual(
                    before[target]["effective_exported_environment"]["PATH"],
                    after_path[target]["effective_exported_environment"]["PATH"],
                )

            makefile.write_bytes(
                original_makefile.replace(
                    b"export FE8_ITEM_ID_CAP",
                    b"unexport FE8_ITEM_ID_CAP",
                    1,
                )
            )
            after_cap = parse()
            for target in requested:
                self.assertNotEqual(before[target], after_cap[target])
                self.assertNotIn(
                    "FE8_ITEM_ID_CAP",
                    after_cap[target]["effective_exported_environment"],
                )

            makefile.write_bytes(original_makefile)
            changed_nm = original_modern.replace(
                b"export MODERN_NM",
                b"unexport MODERN_NM",
                1,
            )
            self.assertNotEqual(changed_nm, original_modern)
            modern_mk.write_bytes(changed_nm)
            after_nm = parse()
            self.assertNotEqual(
                before["expansion-modern-linker-check"],
                after_nm["expansion-modern-linker-check"],
            )
        finally:
            makefile.write_bytes(original_makefile)
            modern_mk.write_bytes(original_modern)

    def test_real_compile_recipe_mutations_invalidate_exact_compile_edges(self):
        entries, _, graph, schema = self.fixture_authority()
        modern_mk = self.fixture_root / "modern.mk"
        original = modern_mk.read_bytes()
        base_loader = reporter.AuthorityLoader(
            self.fixture_root,
            entries,
            "HEAD",
        )
        prior_graph = reporter._prior_graph(base_loader)
        self.assertIsNotNone(prior_graph)
        loader = reporter.AuthorityLoader(self.fixture_root, entries)
        before = reporter._parse_make_authorities(
            loader,
            {"expansion-modern-all", "validation-ownership-check"},
        )
        self.assertTrue(
            any(
                "%" in item["target"]
                for item in before["expansion-modern-all"]["transitive"]
            )
        )
        mutations = {
            "c": (
                b'$(MODERN_OUTPUT_DIR)/%.o: %.c\n'
                b'\t@mkdir -p "$(@D)"\n'
                b'\t"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD -MP -MF "$(@:.o=.d)" '
                b'-MQ "$@" -c "$<" -o "$@"',
                b'$(MODERN_OUTPUT_DIR)/%.o: %.c\n'
                b'\t@mkdir -p "$(@D)"\n'
                b'\t"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD -MP -MF "$(@:.o=.d)" '
                b'-MQ "$@" -DRECIPE_FIXTURE=1 -c "$<" -o "$@"',
            ),
            "data": (
                b'$(MODERN_ALL_DATA_OBJECTS): $(MODERN_OUTPUT_DIR)/%.o: '
                b'$(MODERN_OUTPUT_DIR)/%.pre.c\n'
                b'\t@mkdir -p "$(@D)"\n'
                b'\t"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD -MP -MF "$(@:.o=.d)" '
                b'-MQ "$@" -c "$<" -o "$@"',
                b'$(MODERN_ALL_DATA_OBJECTS): $(MODERN_OUTPUT_DIR)/%.o: '
                b'$(MODERN_OUTPUT_DIR)/%.pre.c\n'
                b'\t@mkdir -p "$(@D)"\n'
                b'\t"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD -MP -MF "$(@:.o=.d)" '
                b'-MQ "$@" -DRECIPE_FIXTURE=1 -c "$<" -o "$@"',
            ),
            "assembly": (
                b'$(MODERN_OUTPUT_DIR)/%.o: %.s\n'
                b'\t@mkdir -p "$(@D)"\n'
                b'\t"$(MODERN_CC)" $(MODERN_ASFLAGS) -Wa,--MD,"$(@:.o=.d)" '
                b'-c "$<" -o "$@"',
                b'$(MODERN_OUTPUT_DIR)/%.o: %.s\n'
                b'\t@mkdir -p "$(@D)"\n'
                b'\t"$(MODERN_CC)" $(MODERN_ASFLAGS) -Wa,--MD,"$(@:.o=.d)" '
                b'-DRECIPE_FIXTURE=1 -c "$<" -o "$@"',
            ),
        }
        expected_edges = {
            "configuration.compile",
            "configuration.link",
            "configuration.target",
            "generated-schema.compile",
            "generated-schema.link",
            "generated-schema.target",
            "generated.compile",
            "generated.link",
            "generated.target",
            "localization.compile",
            "localization.consumer",
            "localization.link",
            "localization.negative",
            "manual.compile",
            "manual.link",
            "runtime.compile",
            "runtime.link",
            "runtime.target",
        }
        try:
            for label, (old, new) in mutations.items():
                with self.subTest(recipe=label):
                    mutated = original.replace(old, new, 1)
                    self.assertNotEqual(mutated, original)
                    modern_mk.write_bytes(mutated)
                    loader = reporter.AuthorityLoader(self.fixture_root, entries)
                    after = reporter._parse_make_authorities(
                        loader,
                        {"expansion-modern-all", "validation-ownership-check"},
                    )
                    self.assertNotEqual(
                        before["expansion-modern-all"],
                        after["expansion-modern-all"],
                    )
                    self.assertEqual(
                        before["validation-ownership-check"],
                        after["validation-ownership-check"],
                    )
                    model = reporter.validate_graph(
                        graph,
                        schema,
                        loader,
                        entries,
                    )
                    self.assertEqual(
                        reporter._authority_changed_edges(
                            graph,
                            prior_graph,
                            model,
                            loader,
                            base_loader,
                        ),
                        expected_edges,
                    )
                    modern_mk.write_bytes(original)
        finally:
            modern_mk.write_bytes(original)

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

    def test_public_make_gate_surfaces_probe_mismatch(self):
        _, _, graph, _ = self.fixture_authority()
        graph_path = self.fixture_root / reporter.GRAPH_PATH
        original = graph_path.read_bytes()
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
        try:
            graph_path.write_bytes(reporter.normalized_json(graph))
            completed = subprocess.run(
                ["make", "validation-ownership-check"],
                cwd=self.fixture_root,
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            graph_path.write_bytes(original)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "false_positive=2, false_negative=2",
            completed.stderr,
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

    def test_report_is_canonical_report_only_and_preserves_git_state(self):
        _, _, fixture_graph, _ = self.fixture_authority()
        before = reporter.repository_status(self.fixture_root)
        command = [
            "/usr/bin/python3",
            "-I",
            "scripts/validation_ownership/isolated_launcher.py",
            "resolve",
            "--repository-root",
            str(self.fixture_root),
            "--changed",
            "src/bm.c",
        ]
        completed = subprocess.run(
            command,
            cwd=self.fixture_root,
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
        self.assertEqual(reporter.repository_status(self.fixture_root), before)

        graph_path = self.fixture_root / reporter.GRAPH_PATH
        original_graph = graph_path.read_bytes()
        mutated_graph = copy.deepcopy(fixture_graph)
        mutated_graph["edges"][0]["reason"] = (
            "deterministic working-tree review invalidation fixture"
        )
        try:
            graph_path.write_bytes(reporter.normalized_json(mutated_graph))
            completed = subprocess.run(
                command + ["--base-revision", "HEAD"],
                cwd=self.fixture_root,
                check=True,
                capture_output=True,
            )
        finally:
            graph_path.write_bytes(original_graph)
        comparison = json.loads(completed.stdout)["review_invalidation"]
        self.assertTrue(comparison["invalidated"])
        self.assertEqual(comparison["reason"], "authoritative-graph-edge-change")
        self.assertEqual(
            comparison["changed_edge_ids"],
            [fixture_graph["edges"][0]["id"]],
        )
        self.assertEqual(reporter.repository_status(self.fixture_root), before)


if __name__ == "__main__":
    unittest.main()
