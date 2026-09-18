import copy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import shlex
import unittest
from pathlib import Path
import tempfile
import subprocess
import tarfile
from types import SimpleNamespace
from unittest import mock

from scripts.validation_ownership import graph_lifecycle, reporter
from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
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
        checks = {}
        lifecycle_check = graph_lifecycle.check

        def observe_removal(path, target):
            if Path(target).name == "graph.backup":
                removed.append(path)
            return replace(path, target)

        def observe_check(artifact_root, check_id, **arguments):
            present = (artifact_root / reporter.GRAPH_PATH).is_file()
            outcome = "fail"
            if not checks:
                with self.assertRaisesRegex(MakeProbeError, "check is not allowlisted"):
                    lifecycle_check(artifact_root, "not-a-declared-lifecycle-route", **arguments)
            try:
                result = lifecycle_check(artifact_root, check_id, **arguments)
                outcome = "pass"
                return result
            finally:
                checks.setdefault(artifact_root, []).append((check_id, present, outcome))

        with mock.patch.object(Path, "replace", new=observe_removal), \
             mock.patch.object(graph_lifecycle, "check", new=observe_check):
            result = self.run_report(changed_paths=("Makefile",))
        self.assertFalse(result["policy"]["narrowing_authorized"])
        self.assertEqual(result["coverage"]["tracked_paths"], result["coverage"]["owned_paths"])
        self.assertEqual(result["measurement"]["false_positive_selections"], 0)
        self.assertEqual(result["measurement"]["false_negative_selections"], 0)
        self.assertEqual(len(result["artifact"]["executable_lifecycle"]), 3)
        self.assertEqual(len(removed), len(result["artifact"]["executable_lifecycle"]))
        self.assertTrue(all(item["removal"] == "fail" and item["restoration"] == "pass"
                            for item in result["artifact"]["executable_lifecycle"]))
        artifact = json.loads((self.fixture.root / reporter.GRAPH_PATH).read_text())["artifact"]
        self.assertEqual(len(checks), len(removed))
        for observations in checks.values():
            for check_id in (artifact["executable_consumer"], artifact["consistency_check"]):
                self.assertEqual(
                    [(present, outcome) for route, present, outcome in observations
                     if route == check_id],
                    [(True, "pass"), (False, "fail"), (True, "pass")],
                )
        self.assertEqual(result["resolutions"][0]["path"], "Makefile")
        self.assertGreater(result["execution"]["runs"], 0)
        for proof in result["artifact"]["executable_lifecycle"]:
            self.assertEqual(proof["semantics"], "verified-dispatch-and-shared-checker")
            self.assertEqual(proof["verified_routes"], [
                artifact["executable_consumer"], artifact["consistency_check"],
            ])

    def test_both_declared_lifecycle_routes_require_removal_failure_and_restoration(self):
        artifact = json.loads((self.fixture.root / reporter.GRAPH_PATH).read_text())["artifact"]
        lifecycle_check = graph_lifecycle.check
        for selected in (artifact["executable_consumer"], artifact["consistency_check"]):
            for phase in ("removal", "restoration"):
                with self.subTest(check=selected, phase=phase):
                    absent = set()

                    def broken_route(artifact_root, check_id, **arguments):
                        if check_id == selected:
                            if not (artifact_root / reporter.GRAPH_PATH).is_file():
                                absent.add(artifact_root)
                                if phase == "removal":
                                    return 0
                            elif artifact_root in absent and phase == "restoration":
                                raise MakeProbeError("controlled restored artifact failure")
                        return lifecycle_check(artifact_root, check_id, **arguments)

                    expected = ("artifact removal did not fail" if phase == "removal"
                                else "controlled restored artifact failure")
                    with mock.patch.object(graph_lifecycle, "check", new=broken_route):
                        with self.assertRaisesRegex(MakeProbeError, expected):
                            self.run_report()
                    self.assertTrue(absent)

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
        registry = json.loads((self.fixture.root / reporter.TEST_CASE_REGISTRY_PATH).read_text())
        registry["cases"][0]["title"] = "Changed real case"
        self.fixture.add(reporter.TEST_CASE_REGISTRY_PATH, json.dumps(registry))
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

    def test_added_removed_and_changed_exclusions_invalidate_all_edges(self):
        graph_path = self.fixture.root / reporter.GRAPH_PATH
        path = "external-policy.txt"
        exclusion = {
            "id": "exclude.external-policy",
            "include": [{"kind": "exact", "path": path}],
            "reason": "Controlled external enforcement exclusion",
            "fail_closed": True,
            "applies_to": "external-enforcement",
        }

        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        self.fixture.add(path, "External enforcement policy\n")
        graph = json.loads(graph_path.read_text())
        graph["exclusions"].append(exclusion)
        graph_path.write_text(json.dumps(graph))
        self.fixture.commit("Add external exclusion")
        self.assert_all_edges_invalidated(base)

        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        (self.fixture.root / path).unlink()
        graph = json.loads(graph_path.read_text())
        graph["exclusions"] = []
        graph_path.write_text(json.dumps(graph))
        self.fixture.commit("Remove external exclusion")
        self.assert_all_edges_invalidated(base)

        self.fixture.add(path, "External enforcement policy\n")
        graph["exclusions"] = [exclusion]
        graph_path.write_text(json.dumps(graph))
        self.fixture.commit("Restore external exclusion")
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        graph["exclusions"][0]["reason"] = "Changed external enforcement reason"
        graph_path.write_text(json.dumps(graph))
        self.fixture.commit("Change external exclusion")
        self.assert_all_edges_invalidated(base)

    def test_exclusion_and_selector_reordering_does_not_invalidate(self):
        graph_path = self.fixture.root / reporter.GRAPH_PATH
        for path in ("external-a.txt", "external-b.txt", "external-c.txt"):
            self.fixture.add(path, path + "\n")
        graph = json.loads(graph_path.read_text())
        graph["exclusions"] = [
            {
                "id": "exclude.external-ab",
                "include": [
                    {"kind": "exact", "path": "external-a.txt"},
                    {"kind": "exact", "path": "external-b.txt"},
                ],
                "reason": "Controlled paired external exclusion",
                "fail_closed": True,
                "applies_to": "external-enforcement",
            },
            {
                "id": "exclude.external-c",
                "include": [{"kind": "exact", "path": "external-c.txt"}],
                "reason": "Controlled single external exclusion",
                "fail_closed": True,
                "applies_to": "external-enforcement",
            },
        ]
        graph_path.write_text(json.dumps(graph))
        base = self.fixture.commit("Add reorderable exclusions")
        graph["exclusions"].reverse()
        graph["exclusions"][1]["include"].reverse()
        graph_path.write_text(json.dumps(graph, indent=1, sort_keys=True))
        self.fixture.commit("Reorder exclusion serialization")
        result = self.run_report(base_revision=base, lifecycle=False)
        self.assertFalse(result["review_invalidation"]["invalidated"])
        self.assertEqual(result["review_invalidation"]["changed_edge_ids"], [])

    def test_artifact_routes_and_lifecycle_authority_invalidate_all_edges(self):
        path = self.fixture.root / reporter.GRAPH_PATH
        graph = json.loads(path.read_text())
        self.fixture.add(
            "Makefile",
            report_fixture.consuming_makefile("validation-ownership-check", "alternate-ownership-check"),
        )
        graph["nodes"].extend((
            {"id": "owner.alternate-make", "kind": "evidence", "label": "Alternate existing consumer",
             "evidence_type": "host",
             "authority": {"kind": "make-target", "target": "alternate-ownership-check"}},
            {"id": "owner.alternate-case", "kind": "evidence", "label": "Alternate existing case",
             "evidence_type": "host",
             "authority": {"kind": "tester-case", "case_id": "TC-WORKFLOW-ALTERNATE-001"}},
        ))
        registry_path = self.fixture.root / reporter.TEST_CASE_REGISTRY_PATH
        registry = json.loads(registry_path.read_text())
        alternate = copy.deepcopy(registry["cases"][0])
        alternate["id"] = "TC-WORKFLOW-ALTERNATE-001"
        registry["cases"].append(alternate)
        registry_path.write_text(json.dumps(registry))
        path.write_text(json.dumps(graph))
        base = self.fixture.commit("Provide valid alternate lifecycle authorities")
        variants = []
        for key, value in (
            ("executable_consumer", "alternate-ownership-check"),
            ("consistency_check", "TC-WORKFLOW-ALTERNATE-001"),
            ("owner", "changed-workflow-owner"),
        ):
            changed = copy.deepcopy(graph)
            changed["artifact"][key] = value
            variants.append((key, changed))
        changed = copy.deepcopy(graph)
        event = next(item for item in changed["lifecycle_events"]
                     if item["type"] == "artifact_checkpoint")
        event["occurred_at"] = (
            datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
            + timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        variants.append(("lifecycle", changed))
        for label, changed in variants:
            with self.subTest(authority=label):
                path.write_text(json.dumps(changed))
                self.fixture.commit("Change valid " + label + " authority")
                self.assert_all_edges_invalidated(base)

    def test_lifecycle_event_and_artifact_key_reordering_does_not_invalidate(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        path = self.fixture.root / reporter.GRAPH_PATH
        graph = json.loads(path.read_text())
        graph["lifecycle_events"].reverse()
        path.write_text(json.dumps(graph, indent=1, sort_keys=True))
        self.fixture.commit("Reorder equivalent lifecycle serialization")
        result = self.run_report(base_revision=base, lifecycle=False)
        self.assertFalse(result["review_invalidation"]["invalidated"])
        self.assertEqual(result["review_invalidation"]["changed_edge_ids"], [])

    def test_current_expiry_repair_can_compare_expired_historical_base(self):
        path = self.fixture.root / reporter.GRAPH_PATH
        graph = json.loads(path.read_text())
        graph["artifact"]["expires_at"] = "2029-12-31T00:00:00Z"
        path.write_text(json.dumps(graph))
        base = self.fixture.commit("Record a previously valid expiring artifact")
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        bind = graph_lifecycle.bind
        for expiry in ("2031-01-01T00:00:00Z", None):
            with self.subTest(current_expiry=expiry):
                graph["artifact"]["expires_at"] = expiry
                path.write_text(json.dumps(graph))
                self.fixture.commit("Repair CURRENT artifact expiry")
                bound = []

                def observe_binding(current, **arguments):
                    bound.append(current["artifact"]["expires_at"])
                    return bind(current, **arguments)

                with mock.patch.object(reporter, "datetime") as clock, \
                     mock.patch.object(graph_lifecycle, "bind", side_effect=observe_binding):
                    clock.now.return_value = now
                    result = self.run_report(base_revision=base, lifecycle=False)
                self.assertEqual(result["coverage"]["tracked_paths"], result["coverage"]["owned_paths"])
                self.assertEqual(result["measurement"]["false_positive_selections"], 0)
                self.assertEqual(result["measurement"]["false_negative_selections"], 0)
                self.assertTrue(result["review_invalidation"]["invalidated"])
                self.assertTrue(bound)
                self.assertTrue(all(value == expiry for value in bound))
        graph["artifact"]["expires_at"] = "2029-12-31T00:00:00Z"
        path.write_text(json.dumps(graph))
        expired = self.fixture.commit("Restore expired CURRENT as a rejection control")
        with mock.patch.object(reporter, "datetime") as clock:
            clock.now.return_value = now
            with self.assertRaisesRegex(MakeProbeError, "expired artifact"):
                self.run_report(base_revision=expired, lifecycle=False)

    def test_artifact_authority_change_requires_reviewed_verifier_mode(self):
        base = self.fixture.git("rev-parse", "HEAD").decode().strip()
        graph_path = self.fixture.root / reporter.GRAPH_PATH
        graph = json.loads(graph_path.read_text())
        graph["artifact"]["owner"] = "changed-workflow-owner"
        graph_path.write_text(json.dumps(graph))
        head = self.fixture.commit("Change artifact authority without changing edges")
        edges = sorted(edge["id"] for edge in graph["edges"])
        for mode, revision in (("exact-base-pinned", base), ("reviewed-evolution", head)):
            trusted = self.fixture.directory / ("artifact-verifier-" + mode)
            trusted.mkdir()
            with tarfile.open(fileobj=BytesIO(self.fixture.git("archive", revision))) as archive:
                archive.extractall(trusted, filter="data")
            command = [
                "/usr/bin/python3", "-I", "-S", "-B",
                str(trusted / "scripts/validation_ownership/ci_verifier.py"),
                "--trusted-root", str(trusted), "--repository-root", str(self.fixture.root),
                "--base-sha", base, "--candidate-sha", head,
                "--trusted-sha", revision, "--expected-mode", mode,
            ]
            if mode == "reviewed-evolution":
                command.extend((
                    "--reviewed-repository", "owner/repository",
                    "--reviewed-pull-request", "186",
                    "--reviewed-path", reporter.GRAPH_PATH.as_posix(),
                    *(item for edge in edges for item in ("--reviewed-edge", edge)),
                    "--reviewed-consumer", "surface.schema",
                    "--reviewed-consumer", "surface.source",
                ))
            result = subprocess.run(
                command, cwd=trusted, env=ENVIRONMENT, capture_output=True, text=True, timeout=120,
            )
            with self.subTest(mode=mode):
                if mode == "exact-base-pinned":
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("retargets exact-base oracle authority", result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    observed = json.loads(result.stdout)
                    self.assertEqual(len(observed["lifecycle"]), 3)
                    self.assertTrue(all(
                        proof["semantics"] == "verified-dispatch-and-shared-checker"
                        for proof in observed["lifecycle"]
                    ))
                    self.assertEqual(observed["review_invalidation"]["changed_edge_ids"], edges)

    def test_unknown_current_path_cannot_be_admitted_by_prefix(self):
        self.fixture.add("src/foo.c", "int unknown;\n")
        self.fixture.commit("Add unadmitted source")
        with self.assertRaisesRegex(MakeProbeError, "semantic admission"):
            self.run_report()

    def test_stale_exact_include_and_exclude_require_current_membership(self):
        for role in ("include", "exclude"):
            with self.subTest(role=role):
                fixture = ReportFixture()
                self.addCleanup(fixture.close)
                path = f"docs/exact-{role}/unprobed.md"
                fixture.add(path, role + " exact selector\n")
                graph_path = fixture.root / reporter.GRAPH_PATH
                graph = json.loads(graph_path.read_text())
                source = next(rule for rule in graph["path_rules"] if rule["id"] == "paths.source")
                if role == "include":
                    source["include"].append({"kind": "exact", "path": path})
                else:
                    source["exclude"].append({"kind": "exact", "path": path})
                    schema = next(rule for rule in graph["path_rules"] if rule["id"] == "paths.schema")
                    schema["include"].append({"kind": "exact", "path": path})
                    graph["path_rules"].sort(key=lambda rule: rule["id"] != "paths.source")
                graph_path.write_text(json.dumps(graph))
                base = fixture.commit("Add real unprobed exact selector")

                budget = ProbeBudget()
                try:
                    check(fixture.root, budget=budget, runtime_files=(), lifecycle=False)
                finally:
                    budget.close()
                (fixture.root / path).unlink()
                fixture.commit("Delete exact-selected source only")
                budget = ProbeBudget()
                try:
                    with self.assertRaisesRegex(MakeProbeError, f"stale exact {role}"):
                        check(
                            fixture.root,
                            budget=budget,
                            base_revision=base,
                            runtime_files=(),
                            lifecycle=False,
                        )
                finally:
                    budget.close()

                graph = json.loads(graph_path.read_text())
                source = next(rule for rule in graph["path_rules"] if rule["id"] == "paths.source")
                source[role] = [
                    selector for selector in source[role]
                    if selector != {"kind": "exact", "path": path}
                ]
                if role == "exclude":
                    schema = next(rule for rule in graph["path_rules"] if rule["id"] == "paths.schema")
                    schema["include"] = [
                        selector for selector in schema["include"]
                        if selector != {"kind": "exact", "path": path}
                    ]
                graph_path.write_text(json.dumps(graph))
                fixture.commit("Remove stale exact selector")
                budget = ProbeBudget()
                try:
                    result = check(
                        fixture.root,
                        budget=budget,
                        base_revision=base,
                        changed_paths=(path,),
                        runtime_files=(),
                        lifecycle=False,
                    )
                finally:
                    budget.close()
                resolution = next(item for item in result["resolutions"] if item["path"] == path)
                self.assertEqual(resolution["admission"], "selected-base-tree")
                self.assertEqual(
                    resolution["surface"],
                    "surface.source" if role == "include" else "surface.schema",
                )
                self.assertTrue(resolution["owners"])
                self.assertTrue(all(owner["reason"] for owner in resolution["owners"]))

    def test_edge_loss_and_owner_redirection_reject(self):
        path = self.fixture.root / reporter.GRAPH_PATH
        graph = json.loads(path.read_text())
        graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "source.owns-test"]
        path.write_text(json.dumps(graph))
        self.fixture.commit("Remove required owner")
        with self.assertRaisesRegex(MakeProbeError, "missing owner"):
            self.run_report()

    def test_stale_external_exclusion_rejects_and_base_remains_fail_closed(self):
        case = report_fixture.reviewed_exclusion_case(self.fixture)
        base = case["head"]
        self.run_report(lifecycle=False)
        (self.fixture.root / "external-policy.txt").unlink()
        self.fixture.commit("Delete the excluded current source only")
        with self.assertRaisesRegex(MakeProbeError, "stale exact exclusion"):
            self.run_report(base_revision=base, lifecycle=False)
        path = self.fixture.root / reporter.GRAPH_PATH
        graph = json.loads(path.read_text())
        graph["exclusions"] = []
        path.write_text(json.dumps(graph))
        self.fixture.commit("Remove the stale exclusion")
        self.run_report(base_revision=base, lifecycle=False)
        with self.assertRaisesRegex(MakeProbeError, "fail-closed external enforcement"):
            self.run_report(
                base_revision=base, changed_paths=("external-policy.txt",), lifecycle=False,
            )


class LifecycleBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ReportFixture()
        self.addCleanup(self.fixture.close)

    def report(self, **arguments):
        budget = ProbeBudget(Limits(seconds=90))
        try:
            return check(self.fixture.root, budget=budget, runtime_files=(), **arguments)
        finally:
            budget.close()
            self.assertFalse(budget.children)
            self.assertFalse(budget.producer_waiters)
            scratch = self.fixture.root / "build/test-artifacts/validation-ownership"
            self.assertFalse(scratch.exists())

    def case_command(self, command):
        registry = json.loads((self.fixture.root / reporter.TEST_CASE_REGISTRY_PATH).read_text())
        registry["cases"][0]["automation"][0]["command"] = command
        self.fixture.add(reporter.TEST_CASE_REGISTRY_PATH, json.dumps(registry))

    def test_real_make_noop_cannot_receive_a_lifecycle_proof(self):
        self.fixture.add("Makefile", "validation-ownership-check:\n\t@true\n")
        self.fixture.commit("Make the actual consumer a no-op")
        direct = self.fixture.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaises(MakeProbeError):
            result = self.report()
            self.assertEqual(len(result["artifact"]["executable_lifecycle"]), 3)
        self.assertEqual(self.fixture.git("rev-parse", "HEAD").decode().strip(), direct)

    def test_real_consistency_noop_cannot_receive_a_lifecycle_proof(self):
        self.case_command("/usr/bin/true")
        self.fixture.commit("Make the actual consistency automation a no-op")
        with self.assertRaises(MakeProbeError):
            result = self.report()
            self.assertEqual(len(result["artifact"]["executable_lifecycle"]), 3)

    def test_conditional_help_wrong_root_and_ignored_make_routes_reject(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        valid = report_fixture.CHECK_COMMAND
        cases = (
            ("conditional", "false && " + valid),
            ("make-conditional", "$(if enabled," + valid + ",true)"),
            ("help", valid + " --help"),
            ("wrong-root", valid.replace("--repository-root .", "--repository-root /elsewhere")),
            ("different-entry", valid.replace("isolated_launcher.py", "reporter.py")),
            ("ignore-prefix", "-" + valid),
            ("global-ignore", valid),
        )
        for label, command in cases:
            with self.subTest(route=label):
                self.fixture.git("switch", "--detach", baseline)
                self.fixture.add("Makefile", (
                    (".IGNORE:\n" if label == "global-ignore" else "")
                    + ".PHONY: validation-ownership-check\nvalidation-ownership-check:\n\t@"
                    + command + "\n"
                ))
                self.fixture.commit("Unproven " + label + " dispatch")
                with self.assertRaises(MakeProbeError):
                    self.report()

    def test_conditional_help_redirected_and_missing_case_routes_reject(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        valid = report_fixture.CHECK_COMMAND
        for label, command in (
            ("conditional", "false && " + valid),
            ("help", valid + " --help"),
            ("wrong-root", valid.replace("--repository-root .", "--repository-root /elsewhere")),
            ("guest-root", valid.replace("--repository-root .", "--repository-root /repo")),
            ("different-entry", valid.replace("isolated_launcher.py", "reporter.py")),
            ("missing", None),
        ):
            with self.subTest(route=label):
                self.fixture.git("switch", "--detach", baseline)
                if command is None:
                    registry = json.loads((self.fixture.root / reporter.TEST_CASE_REGISTRY_PATH).read_text())
                    registry["cases"][0].pop("automation")
                    self.fixture.add(reporter.TEST_CASE_REGISTRY_PATH, json.dumps(registry))
                else:
                    self.case_command(command)
                self.fixture.commit("Unproven consistency " + label)
                with self.assertRaises(MakeProbeError):
                    self.report()

    def test_literal_redirection_arguments_reject_through_both_real_routes(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        literals = ("'>'", '">"', r"\>", "'>'\"\"", r"''\>")
        for role in ("make", "make-direct", "case"):
            for literal in (("'>'", r"\>") if role == "make-direct" else literals):
                with self.subTest(role=role, literal=literal):
                    self.fixture.git("switch", "--detach", baseline)
                    command = report_fixture.CHECK_COMMAND + " " + literal + " /dev/null"
                    if role.startswith("make"):
                        prefix = "SHELL := /bin/bash\n" if role == "make" else ""
                        self.fixture.add("Makefile", prefix
                                         + ".PHONY: validation-ownership-check\nvalidation-ownership-check:\n\t@"
                                         + command + "\n")
                        argv = ["/usr/bin/make", "-f", "Makefile", "validation-ownership-check"]
                    else:
                        self.case_command(command)
                        argv = ["/bin/sh", "-c", command]
                    self.fixture.commit("Literal redirection argument")
                    budget = ProbeBudget(Limits(seconds=90))
                    try:
                        actual = budget.run(argv, cwd=self.fixture.root, env=ENVIRONMENT)
                        self.assertEqual(actual.returncode, 2)
                        self.assertIn(b"unrecognized arguments: > /dev/null", actual.stderr)
                    finally:
                        budget.close()
                        self.assertFalse(budget.children)
                    with mock.patch.object(graph_lifecycle, "prove", wraps=graph_lifecycle.prove) as proof:
                        with self.assertRaises(MakeProbeError) as failure:
                            self.report()
                        self.assertIn("unrecognized arguments: > /dev/null", str(failure.exception.__cause__))
                        proof.assert_not_called()

    def test_real_stdout_redirection_preserves_quoted_destinations_and_roots(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        for suffix in ("> /dev/null", "> '/dev/null'", '>"/dev/null"', ">''/dev/null"):
            with self.subTest(redirection=suffix):
                self.fixture.git("switch", "--detach", baseline)
                command = report_fixture.CHECK_COMMAND.replace(
                    "--repository-root .", "--repository-root '.'",
                ) + " " + suffix + " # ordinary comment"
                self.fixture.add("Makefile", ".PHONY: validation-ownership-check\nvalidation-ownership-check:\n\t@"
                                 + command + "\n")
                self.case_command(command)
                self.fixture.commit("Real stdout redirection with equivalent quoting")
                self.assertEqual(len(self.report()["artifact"]["executable_lifecycle"]), 3)
                budget = ProbeBudget(Limits(seconds=120))
                try:
                    for argv in (
                        ["/usr/bin/make", "-f", "Makefile", "validation-ownership-check"],
                        ["/bin/sh", "-c", command],
                    ):
                        actual = budget.run(argv, cwd=self.fixture.root, env=ENVIRONMENT)
                        self.assertEqual(actual.returncode, 0, actual.stderr)
                        self.assertEqual(actual.stdout, b"")
                finally:
                    budget.close()
                    self.assertFalse(budget.children)

    def test_shell_comment_boundary_matches_real_launcher_and_cannot_hide_failure(self):
        valid = report_fixture.CHECK_COMMAND
        for suffix in (" # ordinary comment", ".#missing || true"):
            with self.subTest(suffix=suffix):
                command = valid + suffix if suffix.startswith(" ") else valid[:-1] + suffix
                self.case_command(command)
                self.fixture.commit("Real shell comment boundary")
                budget = ProbeBudget(Limits(seconds=90))
                try:
                    actual = budget.run(["/bin/sh", "-c", command], cwd=self.fixture.root, env=ENVIRONMENT)
                    self.assertEqual(actual.returncode, 0, actual.stderr)
                    if suffix.startswith(" "):
                        self.assertEqual(len(json.loads(actual.stdout)["artifact"]["executable_lifecycle"]), 3)
                        self.assertEqual(len(self.report()["artifact"]["executable_lifecycle"]), 3)
                    else:
                        self.assertEqual(actual.stdout, b"")
                        self.assertIn(b".#missing", actual.stderr)
                        with mock.patch.object(graph_lifecycle, "prove", wraps=graph_lifecycle.prove) as proof:
                            with self.assertRaises(MakeProbeError):
                                self.report()
                            proof.assert_not_called()
                finally:
                    budget.close()
                    self.assertFalse(budget.children)

    def test_carriage_returns_do_not_become_valid_lifecycle_routes(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        for role in ("case-trailing", "make-leading", "make-argument"):
            with self.subTest(role=role):
                self.fixture.git("switch", "--detach", baseline)
                if role == "case-trailing":
                    command = report_fixture.CHECK_COMMAND + "\r"
                    self.case_command(command)
                    argv = ["/bin/sh", "-c", command]
                else:
                    command = (
                        "\r" + report_fixture.CHECK_COMMAND if role == "make-leading"
                        else report_fixture.CHECK_COMMAND + "\r # CR is argument data"
                    )
                    self.fixture.add("Makefile", "SHELL := /bin/bash\n.PHONY: validation-ownership-check\n"
                                     "validation-ownership-check:\n\t@" + command + "\n")
                    argv = ["/usr/bin/make", "-f", "Makefile", "validation-ownership-check"]
                self.fixture.commit("Actual CR command data")
                budget = ProbeBudget(Limits(seconds=90))
                try:
                    actual = budget.run(argv, cwd=self.fixture.root, env=ENVIRONMENT)
                    self.assertEqual(actual.returncode, 2)
                    self.assertIn(b"No such file or directory", actual.stderr)
                finally:
                    budget.close()
                    self.assertFalse(budget.children)
                seen = []
                decode = graph_lifecycle._command_words

                def observe(command):
                    seen.append(command)
                    return decode(command)

                with mock.patch.object(graph_lifecycle, "_command_words", observe), \
                     mock.patch.object(graph_lifecycle, "prove", wraps=graph_lifecycle.prove) as proof:
                    with self.assertRaises(MakeProbeError):
                        self.report()
                    proof.assert_not_called()
                if role == "make-leading":
                    self.assertTrue(seen[0].startswith("\r"))

    def test_case_lf_continuation_at_eof_keeps_real_checker_execution(self):
        command = report_fixture.CHECK_COMMAND + "\\\n"
        self.case_command(command)
        self.fixture.commit("Real LF continuation at EOF")
        self.assertEqual(len(self.report()["artifact"]["executable_lifecycle"]), 3)
        budget = ProbeBudget(Limits(seconds=90))
        try:
            actual = budget.run(["/bin/sh", "-c", command], cwd=self.fixture.root, env=ENVIRONMENT)
            self.assertEqual(actual.returncode, 0, actual.stderr)
            self.assertEqual(len(json.loads(actual.stdout)["artifact"]["executable_lifecycle"]), 3)
        finally:
            budget.close()
            self.assertFalse(budget.children)

    def test_original_missing_and_nondirectory_path_components_cannot_receive_proofs(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        valid = report_fixture.CHECK_COMMAND
        cases = (
            ("make-missing", valid.replace("scripts/validation_ownership", "scripts/absent/../validation_ownership")),
            ("make-nondirectory", valid.replace("isolated_launcher.py", "isolated_launcher.py/../isolated_launcher.py")),
            ("case-missing", valid.replace("--repository-root .", "--repository-root missing/..")),
            ("case-nondirectory", valid.replace("--repository-root .", "--repository-root Makefile/..")),
        )
        for label, command in cases:
            with self.subTest(path=label):
                self.fixture.git("switch", "--detach", baseline)
                if label.startswith("make-"):
                    self.fixture.add("Makefile", ".PHONY: validation-ownership-check\nvalidation-ownership-check:\n\t@"
                                     + command + "\n")
                    argv = ["/usr/bin/make", "--no-print-directory", "validation-ownership-check"]
                else:
                    self.case_command(command)
                    argv = ["/bin/sh", "-c", command]
                self.fixture.commit("Real invalid traversal " + label)
                budget = ProbeBudget(Limits(seconds=90))
                try:
                    actual = budget.run(argv, cwd=self.fixture.root, env=ENVIRONMENT)
                    self.assertNotEqual(actual.returncode, 0)
                    self.assertEqual(actual.stdout, b"")
                finally:
                    budget.close()
                    self.assertFalse(budget.children)
                with mock.patch.object(graph_lifecycle, "prove", wraps=graph_lifecycle.prove) as proof:
                    with self.assertRaises(MakeProbeError):
                        self.report()
                    proof.assert_not_called()

    def test_existing_directory_traversals_and_quoted_roots_keep_real_dispatch(self):
        valid = report_fixture.CHECK_COMMAND.replace(
            "scripts/validation_ownership", "scripts/generated_data/../validation_ownership",
        ).replace("--repository-root .", "--repository-root 'scripts/..'")
        self.fixture.add("Makefile", ".PHONY: validation-ownership-check\nvalidation-ownership-check:\n\t@" + valid + "\n")
        self.case_command(valid)
        self.fixture.commit("Actual existing directory traversal")
        budget = ProbeBudget(Limits(seconds=90))
        try:
            actual = budget.run(
                ["/usr/bin/make", "--no-print-directory", "validation-ownership-check"],
                cwd=self.fixture.root, env=ENVIRONMENT,
            )
            self.assertEqual(actual.returncode, 0, actual.stderr)
            self.assertEqual(len(json.loads(actual.stdout)["artifact"]["executable_lifecycle"]), 3)
            self.assertEqual(len(self.report()["artifact"]["executable_lifecycle"]), 3)
        finally:
            budget.close()
            self.assertFalse(budget.children)

    def test_loader_help_and_trace_do_not_prove_artifact_consumption(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        for name, value in (("LD_DEBUG", "help"), ("LD_TRACE_LOADED_OBJECTS", "1")):
            with self.subTest(control=name):
                self.fixture.git("switch", "--detach", baseline)
                self.fixture.add("Makefile", "export " + name + " = " + value
                                 + "\n.PHONY: validation-ownership-check\nvalidation-ownership-check:\n\t@"
                                 + report_fixture.CHECK_COMMAND + "\n")
                selected = self.fixture.commit("Real loader-only startup")
                contexts = []
                startup = graph_lifecycle._startup_environment

                def observe(dispatch, *arguments, **options):
                    contexts.append(dispatch)
                    return startup(dispatch, *arguments, **options)

                with mock.patch.object(graph_lifecycle, "_startup_environment", observe), \
                     mock.patch.object(graph_lifecycle, "prove", wraps=graph_lifecycle.prove) as proof:
                    with self.assertRaisesRegex(MakeProbeError, "unsupported loader"):
                        self.report()
                    proof.assert_not_called()
                self.assertEqual(contexts[0]["environment"][name], value)
                graph = self.fixture.root / reporter.GRAPH_PATH
                budget = ProbeBudget(Limits(seconds=90))
                try:
                    for phase in ("present", "removed", "restored"):
                        if phase == "removed":
                            graph.unlink()
                            self.fixture.commit("Remove the real authoritative graph")
                        elif phase == "restored":
                            self.fixture.git("switch", "--detach", selected)
                        actual = budget.run(
                            ["/usr/bin/make", "-f", "Makefile", "validation-ownership-check"],
                            cwd=self.fixture.root, env=ENVIRONMENT,
                        )
                        self.assertEqual(actual.returncode, 0, actual.stderr)
                        self.assertTrue(actual.stdout)
                finally:
                    budget.close()
                    self.assertFalse(budget.children)

    def test_shell_startup_cannot_skip_the_bound_checker(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        startup_file = self.fixture.directory / "startup.sh"
        startup_file.write_text("exit 0\n")
        for name, value, spelling in (
            ("SHELLOPTS", "noexec", "/usr/bin/python3"),
            ("BASH_ENV", str(startup_file), "/usr/bin/python3"),
            ("BASH_FUNC_python3%%", "() { return 0; }", "python3"),
        ):
            with self.subTest(control=name):
                self.fixture.git("switch", "--detach", baseline)
                command = report_fixture.CHECK_COMMAND.replace("/usr/bin/python3", spelling)
                self.fixture.add("Makefile", "SHELL := /bin/bash\nexport " + name + " = " + value
                                 + "\n.PHONY: validation-ownership-check\nvalidation-ownership-check:\n\t@"
                                 + command + " > /dev/null\n")
                self.fixture.commit("Real shell startup bypass")
                with mock.patch.object(graph_lifecycle, "prove", wraps=graph_lifecycle.prove) as proof:
                    with self.assertRaisesRegex(MakeProbeError, "unsupported shell startup"):
                        self.report()
                    proof.assert_not_called()
                graph = self.fixture.root / reporter.GRAPH_PATH
                graph.unlink()
                self.fixture.commit("Remove the real graph behind the skipped checker")
                budget = ProbeBudget(Limits(seconds=90))
                try:
                    actual = budget.run(
                        ["/usr/bin/make", "-f", "Makefile", "validation-ownership-check"],
                        cwd=self.fixture.root, env=ENVIRONMENT,
                    )
                    self.assertEqual(actual.returncode, 0, actual.stderr)
                    self.assertEqual(actual.stdout, b"")
                finally:
                    budget.close()
                    self.assertFalse(budget.children)

    def test_benign_exports_keep_complete_dispatch_and_real_checker_behavior(self):
        command = report_fixture.CHECK_COMMAND
        self.fixture.add("Makefile", "export PROJECT_LABEL = retained\nexport PYTHONPATH = /not-imported\n"
                         ".PHONY: validation-ownership-check\nvalidation-ownership-check:\n\t@" + command + "\n")
        selected = self.fixture.commit("Benign exact checker exports")
        startup = graph_lifecycle._startup_environment
        observed = []

        def inspect(dispatch, arguments, *, shell):
            self.assertEqual(dispatch["environment"]["PROJECT_LABEL"], "retained")
            self.assertEqual(dispatch["environment"]["PYTHONPATH"], "/not-imported")
            with self.assertRaisesRegex(MakeProbeError, "captured startup environment"):
                startup({key: value for key, value in dispatch.items() if key != "environment"}, arguments, shell=shell)
            if arguments[0] == "/usr/bin/python3":
                with self.assertRaisesRegex(MakeProbeError, "controlled PATH"):
                    startup(
                        {**dispatch, "environment": {**dispatch["environment"], "PATH": "/not-the-runtime"}},
                        ["python3", *arguments[1:]], shell=shell,
                    )
            observed.append(dispatch)
            return startup(dispatch, arguments, shell=shell)

        with mock.patch.object(graph_lifecycle, "_startup_environment", inspect):
            self.assertEqual(len(self.report()["artifact"]["executable_lifecycle"]), 3)
        self.assertEqual(len(observed), 1)
        graph = self.fixture.root / reporter.GRAPH_PATH
        budget = ProbeBudget(Limits(seconds=120))
        try:
            for phase in ("present", "removed", "restored"):
                if phase == "removed":
                    graph.unlink()
                    self.fixture.commit("Remove the actually consumed graph")
                elif phase == "restored":
                    self.fixture.git("switch", "--detach", selected)
                result = budget.run(
                    ["/usr/bin/make", "-f", "Makefile", "validation-ownership-check"],
                    cwd=self.fixture.root, env=ENVIRONMENT,
                )
                if phase == "removed":
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(reporter.LIFECYCLE_FAILURE_REASON.encode(), result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(len(json.loads(result.stdout)["artifact"]["executable_lifecycle"]), 3)
        finally:
            budget.close()
            self.assertFalse(budget.children)

    def test_empty_original_root_rejects_but_actual_absolute_case_root_remains_valid(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        command = report_fixture.CHECK_COMMAND.replace("--repository-root .", "--repository-root ''")
        for route in ("make", "case"):
            with self.subTest(route=route):
                self.fixture.git("switch", "--detach", baseline)
                if route == "make":
                    self.fixture.add("Makefile", "validation-ownership-check:\n\t@" + command + "\n")
                    argv = ["/usr/bin/make", "-f", "Makefile", "validation-ownership-check"]
                else:
                    self.case_command(command)
                    argv = ["/bin/sh", "-c", command]
                self.fixture.commit("Empty original root")
                with self.assertRaisesRegex(MakeProbeError, "empty original spelling"):
                    self.report()
                budget = ProbeBudget(Limits(seconds=90))
                try:
                    actual = budget.run(argv, cwd=self.fixture.root, env=ENVIRONMENT)
                    self.assertNotEqual(actual.returncode, 0)
                    self.assertEqual(actual.stdout, b"")
                finally:
                    budget.close()
                    self.assertFalse(budget.children)
        self.fixture.git("switch", "--detach", baseline)
        self.case_command(report_fixture.CHECK_COMMAND.replace(
            "--repository-root .", "--repository-root " + shlex.quote(str(self.fixture.root)),
        ))
        self.fixture.commit("Actual absolute case root")
        self.assertEqual(len(self.report()["artifact"]["executable_lifecycle"]), 3)

    def test_skipped_up_to_date_consumer_has_no_native_dispatch(self):
        path = self.fixture.root / reporter.GRAPH_PATH
        graph = json.loads(path.read_text())
        target = "src/data/table.json"
        graph["artifact"]["executable_consumer"] = target
        next(node for node in graph["nodes"] if node["id"] == "owner.make")["authority"]["target"] = target
        self.fixture.add(reporter.GRAPH_PATH, json.dumps(graph))
        self.fixture.add("Makefile", target + ":\n\t@" + report_fixture.CHECK_COMMAND + "\n")
        self.fixture.commit("Existing target skips its apparent checker")
        with self.assertRaisesRegex(MakeProbeError, "did not actually dispatch"):
            self.report()

    def test_unrelated_scoped_ignore_preserves_the_real_checker_route(self):
        self.fixture.add("Makefile", ".IGNORE: unrelated-target\n"
                         + report_fixture.consuming_makefile("validation-ownership-check"))
        self.fixture.commit("Unrelated scoped ignore rule")
        self.assertEqual(len(self.report()["artifact"]["executable_lifecycle"]), 3)
        budget = ProbeBudget(Limits(seconds=90))
        try:
            actual = budget.run(
                ["/usr/bin/make", "-f", "Makefile", "validation-ownership-check"],
                cwd=self.fixture.root, env=ENVIRONMENT,
            )
            self.assertEqual(actual.returncode, 0, actual.stderr)
        finally:
            budget.close()
            self.assertFalse(budget.children)

    def test_checker_substitution_is_rejected_even_with_the_right_argv(self):
        self.fixture.add("scripts/validation_ownership/isolated_launcher.py", "raise SystemExit(0)\n")
        self.fixture.commit("Substitute the checker behind its valid pathname")
        with self.assertRaises(MakeProbeError):
            self.report()

    def test_equivalent_quoted_and_alternate_consumers_retain_real_binding(self):
        graph = json.loads((self.fixture.root / reporter.GRAPH_PATH).read_text())
        graph["artifact"]["executable_consumer"] = "alternate-ownership-check"
        next(node for node in graph["nodes"] if node["id"] == "owner.make")["authority"]["target"] = (
            "alternate-ownership-check"
        )
        self.fixture.add(reporter.GRAPH_PATH, json.dumps(graph))
        self.fixture.add("Makefile", report_fixture.consuming_makefile("alternate-ownership-check").replace(
            "-I -S -B", "-BSI",
        ).replace("isolated_launcher.py", "'isolated_launcher.py'"))
        self.case_command(report_fixture.CHECK_COMMAND.replace("-I -S -B", "-B -S -I"))
        self.fixture.commit("Equivalent bound alternate consumer")
        result = self.report()
        for proof in result["artifact"]["executable_lifecycle"]:
            self.assertEqual(proof["verified_routes"][0], "alternate-ownership-check")

    def test_native_curdir_recipe_uses_observed_value_and_actual_dispatch(self):
        from scripts.validation_ownership import graph_probe, graph_report
        from scripts.validation_ownership.make_probe import ProbeSession

        target = "validation-ownership-check"
        self.fixture.add("Makefile", report_fixture.consuming_makefile(target).replace(
            "--repository-root .", '--repository-root "$(CURDIR)"',
        ))
        self.fixture.commit("Native CURDIR checker dispatch")
        budget = ProbeBudget(Limits(seconds=60))
        try:
            loader = graph_report.capture(self.fixture.root, "HEAD", budget)
            graph, _, _ = graph_report.documents(loader)
            cases = reporter._load_test_case_registry(loader)
            with ProbeSession(
                loader, scratch_root=self.fixture.root / "build/probe", budget=budget,
            ) as session:
                actual = graph_probe.run_probe(
                    loader, {target}, {}, {}, session=session,
                    trusted_builtin_names={"CURDIR"}, dispatch_targets={target},
                )
                record = actual[target]["record"]["variants"][0]["record"]
                self.assertEqual(record["files"][0]["variables"]["CURDIR"]["value"], "/repo")
                self.assertEqual(len(record["recipe_dispatches"]), 1)
                routes = graph_lifecycle._consumer_routes(
                    graph, actual, cases, {}, self.fixture.root.as_posix(), session.snapshot,
                )
                self.assertEqual(routes[0][1][0][-2:], ("/repo", "HEAD"))
                self.assertFalse(session.budget.children)
        finally:
            budget.close()
            self.assertFalse(budget.children)
            self.assertFalse(budget.producer_waiters)

    def test_missing_forged_copied_and_mutated_model_bindings_reject(self):
        original = graph_lifecycle.prove
        observed = []

        def inspect(root, graph, **arguments):
            model, session = arguments["model"], arguments["session"]
            binding = model["lifecycle_bindings"]
            for replacement in (None, True, {"verified": True}, copy.copy(binding)):
                model["lifecycle_bindings"] = replacement
                with self.assertRaisesRegex(MakeProbeError, "verified.*bindings"):
                    original(root, graph, **arguments)
            model["lifecycle_bindings"] = binding
            with self.assertRaisesRegex(MakeProbeError, "active validated graph model"):
                graph_lifecycle.bind(
                    graph, session=session, model=dict(model),
                    make_authorities=model["lifecycle_authorities"][0],
                    tester_cases=model["lifecycle_authorities"][1],
                )
            with self.assertRaisesRegex(MakeProbeError, "verified.*bindings"):
                original(root, graph, **{**arguments, "model": dict(model)})
            changed = copy.deepcopy(graph)
            changed["artifact"]["owner"] += "-unbound"
            with self.assertRaisesRegex(MakeProbeError, "verified.*bindings"):
                original(root, changed, **arguments)
            with self.assertRaisesRegex(MakeProbeError, "verified.*bindings"):
                original(root, graph, **{**arguments, "session": copy.copy(session)})
            owner = model["authorities"]["owner.make"]
            fingerprint = owner["fingerprint"]
            owner["fingerprint"] = "changed"
            with self.assertRaisesRegex(MakeProbeError, "verified.*bindings"):
                original(root, graph, **arguments)
            owner["fingerprint"] = fingerprint
            observed.append((session, model, graph))
            return original(root, graph, **arguments)

        with mock.patch.object(graph_lifecycle, "prove", inspect):
            self.report()
        session, model, graph = observed[0]
        self.assertEqual(reporter._binding_models, {})
        with self.assertRaises(MakeProbeError):
            graph_lifecycle.check(
                self.fixture.root, graph["artifact"]["executable_consumer"],
                session=session, graph=graph, schema={}, oracle={}, model=model,
            )

    def test_actual_declared_routes_consume_authoritative_artifact_before_and_after_removal(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        graph_path = self.fixture.root / reporter.GRAPH_PATH
        registry = json.loads((self.fixture.root / reporter.TEST_CASE_REGISTRY_PATH).read_text())
        commands = (
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "validation-ownership-check"],
            shlex.split(registry["cases"][0]["automation"][0]["command"]),
        )
        outcomes = []
        budget = ProbeBudget(Limits(seconds=180))
        try:
            for phase in ("present", "removed", "restored"):
                if phase == "removed":
                    graph_path.unlink()
                    self.fixture.commit("Remove authoritative graph input")
                elif phase == "restored":
                    self.fixture.git("switch", "--detach", baseline)
                for argv in commands:
                    result = budget.run(argv, cwd=self.fixture.root, env=ENVIRONMENT)
                    outcomes.append((phase, result.returncode))
                    if phase == "removed":
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(reporter.LIFECYCLE_FAILURE_REASON.encode(), result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([code for phase, code in outcomes if phase != "removed"], [0, 0, 0, 0])
        finally:
            budget.close()
            self.assertFalse(budget.children)
            self.assertFalse(budget.producer_waiters)
        self.assertTrue(graph_path.is_file())

    def test_public_lifecycle_uses_bound_routes_without_recursive_proof(self):
        graph = json.loads((self.fixture.root / reporter.GRAPH_PATH).read_text())
        parent = self.fixture.root / "build/test-artifacts/validation-ownership"
        parent.mkdir(parents=True)
        budget = ProbeBudget(Limits(seconds=180))
        try:
            with tempfile.TemporaryDirectory(prefix="public-lifecycle-", dir=parent) as directory:
                artifact_root = Path(directory)
                artifact = artifact_root / reporter.GRAPH_PATH
                artifact.parent.mkdir()
                artifact.write_text(json.dumps(graph))
                backup = artifact_root / "backup"
                for phase in ("present", "removed", "restored"):
                    if phase == "removed":
                        artifact.replace(backup)
                    elif phase == "restored":
                        backup.replace(artifact)
                    for route in (graph["artifact"]["executable_consumer"], graph["artifact"]["consistency_check"]):
                        result = budget.run([
                            "/usr/bin/python3", "-I", "-S", "-B",
                            "scripts/validation_ownership/isolated_launcher.py", "lifecycle-check",
                            "--artifact-root", str(artifact_root),
                            "--authority-root", str(self.fixture.root), "--check", route,
                        ], cwd=self.fixture.root, env=ENVIRONMENT)
                        if phase == "removed":
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn(reporter.LIFECYCLE_FAILURE_REASON.encode(), result.stderr)
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(backup.exists())
        finally:
            budget.close()
            self.assertFalse(budget.children)
            self.assertFalse(budget.producer_waiters)

    def test_actual_trusted_verifier_rejects_a_noop_consumer(self):
        baseline = self.fixture.git("rev-parse", "HEAD").decode().strip()
        trusted = self.fixture.extract_revision(baseline, "trusted-lifecycle")
        budget = ProbeBudget(Limits(seconds=180))
        try:
            for noop in (False, True):
                if noop:
                    self.fixture.add("Makefile", "validation-ownership-check:\n\t@true\n")
                    self.fixture.commit("No-op consumer with unchanged checker implementation")
                head = self.fixture.git("rev-parse", "HEAD").decode().strip()
                result = budget.run([
                    "/usr/bin/python3", "-I", "-S", "-B",
                    str(trusted / "scripts/validation_ownership/ci_verifier.py"),
                    "--trusted-root", str(trusted), "--repository-root", str(self.fixture.root),
                    "--base-sha", baseline, "--candidate-sha", head,
                    "--trusted-sha", baseline, "--expected-mode", "exact-base-pinned",
                ], cwd=trusted, env=ENVIRONMENT)
                if noop:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn(b'"lifecycle":', result.stdout)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertEqual(len(report["lifecycle"]), 3)
                    self.assertTrue(all(row["semantics"] == "verified-dispatch-and-shared-checker"
                                        for row in report["lifecycle"]))
                self.assertFalse((trusted / ".validation-ownership-runtime").exists())
        finally:
            budget.close()
            self.assertFalse(budget.children)
            self.assertFalse(budget.producer_waiters)


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
