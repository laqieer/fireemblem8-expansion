"""Benign data/API controls only: never invoke CURRENT's native/root planner."""

import ast
import builtins
import copy
import dataclasses
import errno
import fcntl
import gc
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import FunctionType, SimpleNamespace
import typing
import unittest
from unittest import mock
import weakref

from scripts.ci_calibration import kernel, policy, root_stage, supervisor, worker


ROOT = Path(__file__).resolve().parents[2]
DRIVER = "/usr/bin/arm-none-eabi-gcc"
FRONTEND = "/usr/lib/gcc/arm-none-eabi/13.2.1/cc1"
ASSEMBLER = "/usr/lib/arm-none-eabi/bin/as"


def toolchain_fixture():
    stages = [
        {"stage": stage, "executed": paths}
        for stage, paths in (
            ("version", [DRIVER]), ("target", [DRIVER]), ("assembler", [DRIVER]),
            ("syntax", [DRIVER, FRONTEND]), ("compile", [DRIVER, FRONTEND, ASSEMBLER]),
        )
    ]
    return {
        "executed": [path for row in stages for path in row["executed"]], "stages": stages,
        "stdin": [
            {"stage": "syntax", "bytes": len('#include "global.h"\n'), "eof": True},
            {"stage": "compile", "bytes": len("void modern_arm7tdmi_thumb_probe(void) {}\n"), "eof": True},
        ],
        "repository_inputs": 2, "sdk_inputs": 3,
    }


def result_fixture():
    return {
        "version": 1, "workload_kind": policy.WORKLOAD_KIND, "fixture_version": policy.FIXTURE_VERSION,
        "source_revision": policy.GRAPH, "base_revision": policy.BASE, "target": policy.ROOT_TARGET,
        "source_phases": True, "root_check_attempts": 1, "root_check_completed": True,
        "graph_check_attempts": 0, "complete_repository_report": False,
        "initial_absence": {"generated_c": True, "public_parents": True},
        "observations": [{
            "state": [], "passes": 2, "analyzed_passes": 2,
            "source_files": ["Makefile", policy.ROOT_HEADER], "source_journal_closed": True,
            "native_jobs": {"text": 1, "toolchain": 2, "header": 5},
            "toolchain": [toolchain_fixture()], "inputs_unchanged": True,
            "generated": [
                {"path": "src/msg_data.c", "mode": 0o644, "size": 123},
                {"path": policy.ROOT_HEADER, "mode": 0o644, "size": 45},
            ],
        }],
        "planner": {"states": [[]], "used_domains": ["MODERN_ALL_C_SOURCES"],
                    "enumerated_domains": ["MODERN_ALL_C_SOURCES"]},
        "cleanup": {"budget_closed": True, "children": 0, "waiters": 0, "retained_owners": 0,
                    "session_base_removed": True, "fixture_removed": True},
    }


def native_command_fixture():
    value = toolchain_fixture()
    observations = []
    for stage in value["stages"]:
        observations.extend(
            {"stage": stage["stage"], "sequence": index + 1, "path": path}
            for index, path in enumerate(stage["executed"])
        )
    observations.extend((
        {"stage": "syntax", "stdin": '#include "global.h"\n', "eof": True},
        {"stage": "compile", "stdin": "void modern_arm7tdmi_thumb_probe(void) {}\n", "eof": True},
    ))
    return {
        "toolchain_check": True, "returncode": 0, "executed": value["executed"],
        "runtime_probes": observations, "inputs": ["unit-1", "unit-2"], "runtime_inputs": ["sdk-1", "sdk-2", "sdk-3"],
    }


class SourceRefusalFixtureError(RuntimeError):
    """Inert canonical-type stand-in; these controls never import the candidate."""


def source_refusal_fixture():
    fact = {
        "bindings": [{"origin": "file", "flavor": "simple"}],
        "binding_status": "stored-original-binding", "binding_version": 0, "mode_version": 0,
        "source_fact_kind": "header-bound", "source_fact_version": 0, "original_input_flags": 0,
        "read_forms": ("MUST_NOT_EXPORT_SOURCE",), "dependency_names": ["SOURCE"],
        "namespace_carrier": False, "unsafe": False, "target_scopes": ["obj/%.o"],
        "snapshot_decision": "exact-original-value-unavailable",
        "assignment_site_status": "unavailable-not-retained-by-global-binding",
    }
    carrier = copy.deepcopy(fact)
    carrier.update(
        bindings=[{"origin": "file", "flavor": "recursive"}], source_fact_kind=None,
        source_fact_version=None, dependency_names=[], namespace_carrier=True,
        snapshot_decision="not-simple",
    )
    return {
        "kind": "source-refusal-attribution-not-a-proof", "pass": 1, "exec": 1,
        "scope": "benign/source",
        "source": {
            "path": "Makefile", "stream_position": 8,
            "site": {"path": "Makefile", "logical": 9, "start": 11, "end": 12},
            "visit": 1, "visit_status": "unique-original-visit",
            "rule_number": 0, "recipe_ordinal": 2, "active": True,
        },
        "condition": "namespace-dependency", "expression": "MUST_NOT_EXPORT_SOURCE",
        "read_form": "MUST_NOT_EXPORT_SOURCE", "unresolved": [],
        "reader_names": ["SNAPSHOT"], "carrier_path": ["SNAPSHOT", "SOURCE"],
        "unknown_writer": False,
        "unsafe_unknown_causes": [
            {"kind": "local-binders", "path": "Makefile", "stream_position": 2,
             "site": ("Makefile", 2, 3, 3), "names": ["LOCAL"]},
        ],
        "snapshot_facts": {"SNAPSHOT": fact, "SOURCE": carrier},
        "use_associations": [
            {"kind": "obligation", "target": "all", "ordinal": 2, "job": None},
            {"kind": "recipe", "target": "all", "ordinal": 2, "job": 9},
            {"kind": "unproved"},
        ],
        "association_status": "recorded",
        "use_kind": "active-source-obligation; actual job status requires an association",
    }


class RootStageControls(unittest.TestCase):
    def setUp(self):
        parent = ROOT / "build/test-artifacts/root20-benign"
        parent.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def current(self, path):
        return subprocess.check_output(
            ["/usr/bin/git", "-C", str(ROOT), "show", policy.GRAPH + ":" + path], timeout=10,
        ).decode("utf-8")

    def logical_chunks(self):
        # Execute only these two pure lexical/data definitions from the pinned tree.
        tree = ast.parse(self.current("scripts/validation_ownership/graph_probe.py"))
        selected = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in {"_LogicalChunk", "_make_logical_chunks"}
        ]
        self.assertEqual(len(selected), 2)
        namespace = {"NamedTuple": typing.NamedTuple}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "<pinned-pure-logical-chunks>", "exec"), namespace)
        return namespace["_make_logical_chunks"]

    def test_first_attempt_identity_names_root_not_full_report(self):
        event = {
            "ref": "refs/heads/" + policy.BRANCH, "before": "0" * 40, "after": "a" * 40,
            "created": True, "deleted": False,
            "repository": {"full_name": policy.REPOSITORY, "private": False}, "sender": {"login": "laqieer"},
        }
        arguments = dict(sha="a" * 40, run_id="123", attempt="1", run_number="1",
                         environment="github-hosted", operating_system="Linux", event_name="push")
        scope = policy.validate_event(event, **arguments)
        self.assertEqual((scope["graph_sha"], scope["base_sha"]),
                         ("61856581bc9859f59fdd938edc219fabc07cd6ef", "ec1dc8553419c8833a687fd8d4a6521a4e29ff7a"))
        self.assertEqual(scope["branch"], "calibration/issue-180-ci-baseline-20")
        self.assertEqual(scope["workload_kind"], "original-root-acceptance")
        self.assertTrue(scope["source_phases"])
        self.assertFalse(scope["production_acceptance"])
        for key, value in (("attempt", "2"), ("run_number", "2"), ("environment", "self-hosted")):
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                policy.validate_event(event, **{**arguments, key: value})
        for branch in (
            "calibration/issue-180-ci-baseline-16", "calibration/issue-180-ci-baseline-17",
            "calibration/issue-180-ci-baseline-18", "master",
        ):
            with self.subTest(branch=branch), self.assertRaises(policy.GuardError):
                policy.validate_event({**event, "ref": "refs/heads/" + branch}, **arguments)

    def test_unguarded_root_cannot_import_candidate_or_build_a_fixture(self):
        original = builtins.__import__
        imported = []
        def record(name, *args, **kwargs):
            if name.startswith("scripts.validation_ownership"):
                imported.append(name)
                raise AssertionError("uncontained candidate import")
            return original(name, *args, **kwargs)
        with mock.patch.object(builtins, "__import__", side_effect=record):
            with self.assertRaises(policy.GuardError):
                worker.root({"guarded": True})
            with self.assertRaises(policy.GuardError):
                root_stage.run(Path("/repo"), SimpleNamespace(deadline=1), {"mode": "root"})
        self.assertEqual(imported, [])

    def test_projection_keeps_real_original_rule_bytes_and_initial_absence(self):
        modern, makefile = self.current("modern.mk"), self.current("Makefile")
        chunks = self.logical_chunks()
        generated = root_stage.fixture_makefile(modern, makefile, chunks)
        original_units = [row.text for row in chunks(modern)]
        output_units = [row.text for row in chunks(generated)]
        prefixes = (
            "MODERN_ALL_C_SOURCES ?=", "MODERN_ALL_C_SOURCES += src/msg_data.c",
            "MODERN_ALL_C_OBJECTS :=", "MODERN_ALL_OBJECTS :=", "MODERN_ALL_C_HEADER_DEPS :=",
            "MODERN_ALL_SOURCE_GOALS :=", "expansion-modern-all: expansion-modern-toolchain-check",
            "$(MODERN_ALL_C_HEADER_DEPS): |", "$(MODERN_OUTPUT_DIR)/%.o: %.c",
        )
        for prefix in prefixes:
            expected, = [row for row in original_units if row.startswith(prefix)]
            self.assertEqual([row for row in output_units if row.startswith(prefix)], [expected])
        for begin, end in (
            ("expansion-modern-toolchain-check:\n", "\nexpansion-modern-cohort:"),
            ("$(MODERN_ALL_C_HEADER_DEPS): $(MODERN_OUTPUT_DIR)/%.headers.d: %.c\n", "\nexpansion-modern-clean:"),
        ):
            block = modern[modern.index(begin):modern.index(end)]
            self.assertEqual(generated.count(block), 1)
        self.assertEqual(root_stage.require_initial_absence(self.root), {"generated_c": True, "public_parents": True})
        for path in ("src/msg_data.c", "build/modern"):
            item = self.root / path
            item.parent.mkdir(parents=True, exist_ok=True)
            item.touch()
            with self.subTest(path=path), self.assertRaises(policy.GuardError):
                root_stage.require_initial_absence(self.root)
            item.unlink()
        missing = modern.replace("$(MODERN_ALL_C_HEADER_DEPS): |", "REMOVED_HEADER_PREREQUISITE: |", 1)
        with self.assertRaises(policy.GuardError):
            root_stage.fixture_makefile(missing, makefile, chunks)

    def test_populate_copies_exact_input_data_without_generating_c_or_parents(self):
        source, destination = self.root / "source", self.root / "projection"
        source.mkdir()
        destination.mkdir()
        (source / "modern.mk").write_text(self.current("modern.mk"))
        (source / "Makefile").write_text(self.current("Makefile"))
        inputs = ("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py",
                  "texts/texts.txt", "texts/textdefs.txt", "include/global.h", "include/gba/types.h")
        for index, name in enumerate(inputs):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"unexecuted copy-only input " + str(index).encode())
        def add(name, data):
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode() if isinstance(data, str) else data)
        result = root_stage.populate_fixture(source, SimpleNamespace(root=destination, add=add), self.logical_chunks())
        self.assertEqual(result, {"generated_c": True, "public_parents": True})
        for name in inputs:
            self.assertEqual((destination / name).read_bytes(), (source / name).read_bytes())
        self.assertFalse((destination / "src/msg_data.c").exists())
        self.assertFalse((destination / "build").exists())

    def test_selected_policies_consume_actual_named_declarations_without_expanding_scope(self):
        data = json.loads(self.current(".github/validation-ownership-make-dynamics.json"))
        metadata = SimpleNamespace(data=data, contracts={row["expression"]: row for row in data["contracts"]})
        budget = object()
        loader = SimpleNamespace(entries={".github/validation-ownership-make-dynamics.json"}, budget=budget)
        names = {
            "load_make_prerequisite_domains", "load_make_ambient_contracts", "load_make_typed_variable_contracts",
        }
        source = ast.parse(self.current("scripts/validation_ownership/reporter.py"))
        selected = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual(len(selected), len(names))
        def select(owner, supplied, *, required):
            self.assertIs(owner, loader)
            self.assertTrue(required)
            self.assertIn(supplied, (None, metadata))
            return metadata
        namespace = {
            "MAKE_DYNAMIC_PATH": Path(".github/validation-ownership-make-dynamics.json"),
            "_selected_make_data": select, "OwnershipError": policy.GuardError,
        }
        prelude = ast.parse("from __future__ import annotations").body
        exec(compile(ast.Module(body=[*prelude, *selected], type_ignores=[]), "<pinned-declaration-readers>", "exec"), namespace)
        reporter = SimpleNamespace(**{name: namespace[name] for name in names}, _selected_make_data=select)
        calls = []
        def capture(root, revision, actual_budget):
            calls.append((root, revision, actual_budget))
            return loader
        api = SimpleNamespace(capture=capture, reporter=reporter)
        domains, contracts, options = root_stage.selected_policies(api, self.root, budget)
        self.assertEqual(domains, {"MODERN_ALL_C_SOURCES": {"kind": "tracked-fallback"}})
        self.assertIs(contracts, metadata.contracts)
        self.assertEqual(calls, [(self.root, policy.GRAPH, budget)])
        self.assertEqual(options["declared_external_names"], {"MODERN_ALL_C_SOURCES"})
        self.assertEqual(options["environment_names"], {"MODERN_ALL_C_SOURCES"})
        self.assertIs(options["source_phases"], True)
        for family, key in (("ambient_inputs", "allowed_names"), ("prerequisite_domains", "tracked_fallback_names")):
            original = metadata.data
            metadata.data = copy.deepcopy(data)
            metadata.data[family][key].remove("MODERN_ALL_C_SOURCES")
            try:
                with self.subTest(family=family), self.assertRaises(policy.GuardError):
                    root_stage.selected_policies(api, self.root, budget)
            finally:
                metadata.data = original

    def test_closed_result_rejects_prefix_only_failed_foreign_and_full_report_claims(self):
        good = result_fixture()
        valid = policy.validate_root_result(good)
        self.assertTrue(valid["root_only"])
        self.assertFalse(valid["production_acceptance"])
        self.assertFalse(valid["complete_repository_report"])
        for key, value in (
            ("version", True), ("source_revision", "b" * 40), ("base_revision", "b" * 40),
            ("source_phases", False), ("root_check_attempts", 0), ("root_check_attempts", 2),
            ("root_check_completed", False), ("graph_check_attempts", 1),
            ("complete_repository_report", True), ("observations", []),
            ("initial_absence", {"generated_c": 1, "public_parents": True}),
            ("extra", "foreign"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(policy.GuardError):
                policy.validate_root_result({**good, key: value})
        for key in good:
            changed = dict(good)
            del changed[key]
            with self.subTest(missing=key), self.assertRaises(policy.GuardError):
                policy.validate_root_result(changed)
        for key, value in (
            ("passes", 1), ("analyzed_passes", 1), ("source_journal_closed", False),
            ("inputs_unchanged", False), ("source_files", ["Makefile", {}]),
            ("toolchain", []), ("generated", []), ("state", [["environment", "FOREIGN", "1"]]),
        ):
            changed = copy.deepcopy(good)
            changed["observations"][0][key] = value
            with self.subTest(observation=key), self.assertRaises(policy.GuardError):
                policy.validate_root_result(changed)
        for key, value in (("children", 1), ("waiters", 1), ("retained_owners", 1),
                           ("session_base_removed", False), ("fixture_removed", False), ("budget_closed", False)):
            changed = copy.deepcopy(good)
            changed["cleanup"][key] = value
            with self.subTest(cleanup=key), self.assertRaises(policy.GuardError):
                policy.validate_root_result(changed)
        with self.assertRaises(policy.GuardError):
            policy.validate_report(good)

    def test_toolchain_summary_consumes_actual_observation_fields_not_declared_intent(self):
        command = native_command_fixture()
        self.assertEqual(root_stage.toolchain_summary(command), toolchain_fixture())
        for kind in ("no-events", "stdin", "sequence", "failed", "foreign-as"):
            changed = copy.deepcopy(command)
            if kind == "no-events":
                changed["runtime_probes"] = []
            elif kind == "stdin":
                changed["runtime_probes"][-1]["stdin"] = "not original"
            elif kind == "sequence":
                changed["runtime_probes"][0]["sequence"] = 2
            elif kind == "failed":
                changed["returncode"] = 1
            else:
                changed["executed"][-1] = "/usr/bin/as"
            with self.subTest(kind=kind), self.assertRaises(policy.GuardError):
                root_stage.toolchain_summary(changed)

    def orchestration(self, *, fault=None):
        directory = self.root / "inert-api-fixture"
        fixture_instances, session_instances, calls = [], [], []
        sentinel = RuntimeError("original inert planner failure")
        class Budget:
            deadline = 123
            closed = False
            children = {}
            producer_waiters = []
            bytes = {}
            def charge(self, category, count):
                self.bytes[category] = self.bytes.get(category, 0) + count
            def read_bytes(self, path, category):
                value = path.read_bytes()
                self.charge(category, len(value))
                return value
            def close(self):
                self.closed = True
        budget = Budget()
        class Fixture:
            def __init__(self):
                self.directory, self.root = directory, directory / "repo"
                self.scratch = self.root / "build/probe"
                self.entries = {}
                fixture_instances.append(self)
            def setUp(self):
                self.root.mkdir(parents=True)
                (self.root / "Makefile").write_bytes(b"unexecuted unit data")
            def tearDown(self):
                if fault in {"fixture-cleanup", "planner-cleanup"}:
                    raise OSError("owned cleanup failure")
                shutil.rmtree(self.directory)
        phases = SimpleNamespace()
        def analyze(owner, observation, target, state, commands, **options):
            if fault == "analysis":
                raise sentinel
            return {}, {"Makefile": "", policy.ROOT_HEADER: ""}, (1, 2), (1, 2)
        phases.analyze = analyze
        class Session:
            def __init__(self, loader, *, scratch_root, budget, runtime_files):
                self.loader, self.budget, self.tree = loader, budget, loader.root
                self.base = self.tree / "owned-base"
                self._file_owners = {}
                self.snapshot = SimpleNamespace(files={"Makefile": b"unexecuted unit data"})
                self.observation = None
                session_instances.append(self)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.budget.close()
                if fault == "retained":
                    self._file_owners["owned"] = SimpleNamespace(retained=True)
                else:
                    self.base = None
            def make(self, target, **keywords):
                if fault in {"planner", "retained", "planner-cleanup"}:
                    raise sentinel
                if fault == "source-mode":
                    (self.tree / "Makefile").chmod(0o600)
                self.observation = SimpleNamespace(
                    source_journal={"closed": True, "scope": "inert-unit"},
                    semantics={
                        "native_dispatches": [
                            {"job": {"target": target_name}}
                            for target_name in ("src/msg_data.c", "expansion-modern-toolchain-check",
                                                *([policy.ROOT_HEADER] * 5))
                        ],
                        "dynamic_commands": [{"command": native_command_fixture()}],
                    },
                    generated=[
                        SimpleNamespace(path="src/msg_data.c", mode=0o644, data=b"x" * 123),
                        SimpleNamespace(path=policy.ROOT_HEADER, mode=0o644, data=b"x" * 45),
                    ],
                )
                return self.observation
            def _original_namespace(self, observation, **keywords):
                if observation is not self.observation:
                    raise policy.GuardError("foreign unit observation")
            def _original_source_archive(self, observation):
                self._original_namespace(observation)
                return SimpleNamespace(scope="inert-unit", passes=(1, 2))
            def _source_phase_images(self, observation):
                self._original_namespace(observation)
                return (1, 2)
        def planner(loader, targets, domains, contracts, *, session, **options):
            calls.append((session.budget, targets, options))
            if fault == "prefix":
                return {policy.ROOT_TARGET: {}}
            observation = session.make(
                policy.ROOT_TARGET, makefile="Makefile", assignments=(),
                observe_source_journal=True, source_journal_mode="prewatched-directories",
            )
            phases.analyze(session, observation, policy.ROOT_TARGET, (), None)
            return {policy.ROOT_TARGET: {
                "record": {"variants": [{"state": []}]},
                "prerequisite_domain_census": {
                    "used": ["MODERN_ALL_C_SOURCES"], "enumerated": ["MODERN_ALL_C_SOURCES"],
                },
            }}
        api = SimpleNamespace(
            root=Path("/repo"), fixture=Fixture, phases=phases,
            graph=SimpleNamespace(run_probe=planner, _make_logical_chunks=None),
            loader=lambda root, entries, budget: SimpleNamespace(root=root, budget=budget),
            entries=lambda entries, budget: entries, session=Session, runtime_files=("unit-only",),
        )
        with mock.patch.object(worker, "require_contained", return_value={}), \
             mock.patch.object(root_stage, "candidate_api", return_value=api), \
             mock.patch.object(root_stage, "selected_policies", return_value=(
                 {"MODERN_ALL_C_SOURCES": {"kind": "tracked-fallback"}}, {}, {"source_phases": True},
             )), mock.patch.object(root_stage, "populate_fixture", return_value={
                 "generated_c": True, "public_parents": True,
             }):
            try:
                result = root_stage.run(Path("/repo"), budget, {"mode": "root", "deadline": 123})
            except BaseException as error:
                return SimpleNamespace(error=error, calls=calls, budget=budget, fixture=fixture_instances[0],
                                       session=session_instances[0], sentinel=sentinel, phases=phases, analyze=analyze)
        return SimpleNamespace(result=result, calls=calls, budget=budget, fixture=fixture_instances[0],
                               session=session_instances[0], phases=phases, analyze=analyze)

    def test_inert_api_composition_uses_one_injected_budget_and_restores_observers(self):
        record = self.orchestration()
        self.assertEqual(len(record.calls), 1)
        self.assertIs(record.calls[0][0], record.budget)
        self.assertEqual(record.calls[0][1], {policy.ROOT_TARGET})
        self.assertIs(record.calls[0][2]["source_phases"], True)
        self.assertIs(record.phases.analyze, record.analyze)
        self.assertNotIn("make", record.session.__dict__)
        self.assertTrue(record.budget.closed)
        self.assertFalse(record.fixture.directory.exists())
        self.assertTrue(record.budget.bytes["control"])
        policy.validate_root_result(record.result)

    def test_inert_failures_keep_first_error_and_do_not_erase_retained_fixture(self):
        for fault in ("planner", "analysis", "prefix", "retained", "fixture-cleanup", "planner-cleanup", "source-mode"):
            with self.subTest(fault=fault):
                record = self.orchestration(fault=fault)
                self.assertEqual(len(record.calls), 1)
                self.assertIs(record.phases.analyze, record.analyze)
                self.assertNotIn("make", record.session.__dict__)
                self.assertTrue(record.budget.closed)
                if fault in {"planner", "analysis", "retained", "planner-cleanup"}:
                    self.assertIs(record.error, record.sentinel)
                else:
                    self.assertIsInstance(record.error, (OSError, policy.GuardError))
                if fault in {"retained", "fixture-cleanup", "planner-cleanup"}:
                    self.assertTrue(record.fixture.directory.exists())
                    if fault == "retained":
                        self.assertEqual(record.error.root_cleanup_state["retained_owners"], 1)
                        self.assertFalse(record.error.root_cleanup_state["fixture_removed"])
                    shutil.rmtree(record.fixture.directory)
                else:
                    self.assertFalse(record.fixture.directory.exists())

    def test_recorder_refuses_foreign_missing_analysis_and_second_invocation(self):
        budget = SimpleNamespace()
        session = SimpleNamespace(budget=budget)
        recorder = root_stage.Recorder(session, budget)
        api = SimpleNamespace(phases=SimpleNamespace(analyze=lambda *args: None))
        with self.assertRaises(policy.GuardError):
            recorder.analyzed(session, object(), policy.ROOT_TARGET, (), ({}, {}, (), ()))
        recorder.attempted = True
        with self.assertRaises(policy.GuardError):
            recorder.invoke(api, None, {}, {}, {"source_phases": True})
        recorder.attempted = False
        with self.assertRaises(policy.GuardError):
            recorder.invoke(api, None, {}, {}, {"source_phases": False})

    def test_root_worker_cleanup_settles_both_resources_and_keeps_first_cause(self):
        first = RuntimeError("sampler cleanup")
        second = RuntimeError("budget cleanup")
        prior = RuntimeError("original root refusal")
        for primary in (None, prior):
            sampler = SimpleNamespace(close=mock.Mock(side_effect=first))
            budget = SimpleNamespace(close=mock.Mock(side_effect=second))
            with mock.patch.object(worker.kernel, "emit") as emitted:
                with self.assertRaises(RuntimeError) as caught:
                    worker.finish_root("unit/root", sampler, budget, primary)
                self.assertIs(caught.exception, first if primary is None else primary)
                sampler.close.assert_called_once_with()
                budget.close.assert_called_once_with()
                self.assertEqual(emitted.call_count, 2)
                self.assertTrue(all(call.args[:2] == ("unit/root", "cleanup-error") for call in emitted.call_args_list))
                self.assertTrue(any("budget cleanup" in message for message in caught.exception.cleanup_errors))

    def assert_broken_cleanup_channel(self, primary=None, *, both_closes_fail=False):
        first = RuntimeError("first sampler cleanup")
        second = ValueError("second budget cleanup")
        reports = []
        sampler = SimpleNamespace(close=mock.Mock(side_effect=first))
        budget = SimpleNamespace(close=mock.Mock(side_effect=second if both_closes_fail else None))
        def emit(*arguments):
            reports.append((arguments, sampler.close.call_count, budget.close.call_count))
            raise BrokenPipeError("actual unit reporting channel closed")
        escaped = None
        with mock.patch.object(worker.kernel, "emit", side_effect=emit):
            try:
                worker.finish_root("unit/root", sampler, budget, primary)
            except BaseException as error:
                escaped = error
        self.assertEqual((sampler.close.call_count, budget.close.call_count), (1, 1))
        self.assertIs(escaped, primary if primary is not None else first)
        self.assertEqual(len(reports), 2 if both_closes_fail else 1)
        self.assertTrue(all(record[1:] == (1, 1) for record in reports))
        self.assertTrue(any("BrokenPipeError" in message for message in escaped.cleanup_errors))
        if both_closes_fail:
            self.assertTrue(any("second budget cleanup" in message for message in escaped.cleanup_errors))
        if primary is not None:
            self.assertTrue(any("first sampler cleanup" in message for message in escaped.cleanup_errors))

    def test_broken_reporting_never_skips_closes_or_replaces_the_primary(self):
        for primary in (None, RuntimeError("original root failure")):
            for both in (False, True):
                with self.subTest(primary=primary, both=both):
                    self.assert_broken_cleanup_channel(primary, both_closes_fail=both)

    def test_original_workload_survives_an_already_failing_error_report(self):
        original = RuntimeError("original workload")
        sampler = SimpleNamespace(close=mock.Mock())
        budget = SimpleNamespace(close=mock.Mock())
        try:
            try:
                raise original
            except RuntimeError:
                raise BrokenPipeError("primary error reporting failed")
        except BrokenPipeError:
            with self.assertRaises(RuntimeError) as caught:
                worker.finish_root("unit/root", sampler, budget, original)
        self.assertIs(caught.exception, original)
        self.assertEqual((sampler.close.call_count, budget.close.call_count), (1, 1))
        self.assertTrue(any("primary error reporting failed" in note for note in original.cleanup_errors))

    @staticmethod
    def qualified_phase_record(mode):
        paths = (
            "/proc/self/oom_score_adj", "/proc/self/oom_adj", "/proc/sys/kernel/kptr_restrict",
            "/proc/sys/vm/overcommit_memory", "/proc/sys/kernel/overflowuid", "/proc/sys/kernel/overflowgid",
        )
        proc = {
            "pid": 1, "proc_pid": 1, "oom_score_adj_after": "0", "proc_mount_flags": 14,
            "denied_writes": {
                path: {"opened": True, "errno": errno.EPERM, "before": "0", "after": "0"} for path in paths
            },
            "sysrq_write_open": {"exposed": True, "errno": errno.EPERM},
        }
        value = {
            "mode": mode,
            "identity": {"uid": 999, "gid": 999, "cgroup": "/unit", "namespaces": {"user": 1}},
            "empty": True, "empty_before_outer_cleanup": True, "watchdog_reaped": True,
            "returncode": 0, "first_cause": None, "probe": {},
            "kernel": {"memory_events": {"oom_kill": 1}},
            "escaped": {"unit": True}, "held_descendants_terminal": 2,
            "caller_lifetime_control_exercised": mode == "lifetime",
        }
        if mode == "identity":
            value["probe"] = {
                "proc_protection": proc,
                "nested": {
                    "uid": 0, "uid_map": ["0", "999", "1"], "gid_map": ["0", "999", "1"],
                    "no_new_privs": "1", "cgroup": "/unit", "namespaces": {"user": 2},
                    "cgroup_control_probe": {"namespace_denied": errno.EPERM}, "proc_protection": proc,
                },
            }
        if mode == "memory":
            value["first_cause"] = {"type": "cgroup-oom", "message": "expected unit memory rejection"}
        if mode == "output":
            value.update(first_cause={"type": "output-bound"}, output_exceeded=True, output_bytes=65537)
        if mode in {"deadline", "lifetime"}:
            value["first_cause"] = {
                "type": "deadline" if mode == "deadline" else "lifetime-eof",
                "message": "Expected caller closure",
            }
        return value

    def supervisor_failure(self, defect, *, output_prefix="issue180-ci-baseline-20-"):
        directory = self.root / defect
        directory.mkdir()
        output = directory / (output_prefix + "123")
        event = {
            "ref": "refs/heads/" + policy.BRANCH, "before": "0" * 40, "after": "a" * 40,
            "created": True, "deleted": False,
            "repository": {"full_name": policy.REPOSITORY, "private": False},
            "sender": {"login": "laqieer"},
        }
        event_path = directory / "event.json"
        event_path.write_text(json.dumps(event))
        args = SimpleNamespace(
            operation="plan", output=str(output), event=str(event_path), sha="a" * 40,
            run_id="123", attempt="1", run_number="1", runner_environment="github-hosted",
            runner_os="Linux", event_name="push", harness="inert-harness", candidate="inert-candidate",
        )
        actual = RuntimeError("INERT_" + defect.upper() + "_FAILURE")
        phase_cause = {"type": "unit-current-phase-error", "error": {"kind": defect}}
        modes, cleanup, prepared, qualified = [], [], [], []
        validate = supervisor.validate_probe
        class Owner:
            def __init__(self, *arguments):
                self.scope = arguments[1]
            def prepare(self):
                prepared.append(True)
            def volume(self, name, size):
                if name == "graph-volume" and defect == "post-qualified-volume":
                    raise actual
                def close():
                    if defect == "post-qualified-close":
                        raise actual
                return SimpleNamespace(close=close)
            def cleanup(self):
                cleanup.append(True)
            def source_status(self, label):
                pass
        def phase(owner, mode, *arguments, **keywords):
            modes.append(mode)
            if mode == "root":
                if defect == "root-before-return":
                    raise actual
                return {"mode": "root", "first_cause": phase_cause, "returncode": 1}
            if mode == "pids" and defect == "preflight-before-return":
                raise actual
            result = self.qualified_phase_record(mode)
            if mode == "pids" and defect == "failing-preflight-result":
                result.update(returncode=1, first_cause=phase_cause)
            return result
        def qualification(result):
            validate(result)
            qualified.append(result["mode"])
        facts = {
            "memory_total": 16 * policy.GIB, "memory_available": 12 * policy.GIB,
            "disk_available": 24 * policy.GIB, "cpus": 4, "threads_max": 100000, "threads_current": 500,
            "cgroup_ancestors": [{"path": "/", "memory_max": None, "memory_current": 0,
                                  "pids_max": None, "pids_current": 0}],
        }
        facade = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), dont_write_bytecode=True)
        with mock.patch.object(supervisor, "arguments", return_value=args), \
             mock.patch.object(supervisor, "sys", facade), mock.patch.object(supervisor.os, "geteuid", return_value=0), \
             mock.patch.object(supervisor.os, "chown"), mock.patch.object(supervisor, "Owner", Owner), \
             mock.patch.object(supervisor, "capacity_facts", return_value=facts), \
             mock.patch.object(supervisor, "phase", side_effect=phase), \
             mock.patch.object(supervisor, "validate_probe", side_effect=qualification):
            self.assertEqual(supervisor.main(), 0)
            args.operation = "run"
            status = supervisor.main()
        result = json.loads((output / "result.json").read_text())
        self.assertEqual(status, 1)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["production_acceptance"])
        self.assertEqual(prepared, [True])
        expected = phase_cause if defect in {"failing-preflight-result", "failing-root-result"} else policy.error_record(actual)
        self.assertEqual(result["first_error"], expected)
        if defect.startswith("post-qualified"):
            self.assertEqual(qualified, ["identity", "memory", "pids", "disk", "output", "deadline", "lifetime"])
            self.assertNotIn("root", modes)
            self.assertEqual(cleanup, [True])
        if defect in {"root-before-return", "failing-root-result"}:
            self.assertEqual(modes.count("root"), 1)
            self.assertEqual(cleanup, [])
            self.assertFalse(result["cleanup_confirmed"])
        return result

    def test_supervisor_setup_and_prereturn_failures_never_borrow_qualified_negative_causes(self):
        for defect in ("post-qualified-volume", "post-qualified-close", "root-before-return", "preflight-before-return"):
            with self.subTest(defect=defect):
                self.supervisor_failure(defect)

    def test_supervisor_preserves_genuinely_failing_current_phase_causes(self):
        for defect in ("failing-preflight-result", "failing-root-result"):
            with self.subTest(defect=defect):
                self.supervisor_failure(defect)

    def old_function(self, module, name):
        path = "scripts/ci_calibration/" + module.__name__.rsplit(".", 1)[-1] + ".py"
        source = subprocess.check_output(
            ["/usr/bin/git", "-C", str(ROOT), "show", supervisor.PREPARATION_SHA + ":" + path],
            text=True, timeout=10,
        )
        node, = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == name]
        namespace = dict(vars(module))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<retained-preparation-control>", "exec"), namespace)
        function = namespace[name]
        return FunctionType(function.__code__, vars(module), name, function.__defaults__, function.__closure__)

    def test_restoring_each_old_failure_path_breaks_its_benign_regression(self):
        with mock.patch.object(worker, "finish_root", self.old_function(worker, "finish_root")):
            with self.assertRaises(AssertionError):
                self.assert_broken_cleanup_channel()
        with mock.patch.object(supervisor, "main", self.old_function(supervisor, "main")):
            with self.assertRaises(AssertionError):
                self.supervisor_failure("post-qualified-volume", output_prefix="issue180-ci-baseline-17-")

    def test_harness_identity_requires_the_exact_five_commit_normal_lineage(self):
        head = "a" * 40
        self.assertEqual(
            (supervisor.REVIEWED_HARNESS_SHA, supervisor.ROOT18_HARNESS_SHA,
             supervisor.RETAINED_HARNESS_SHA, supervisor.PREPARATION_SHA, policy.BASE),
            ("f50cbd175b02aef847e344c84154f6574aa5e457",
             "e4c42d0f831806e4ecf1587ef7cbb977a7ff57e8",
             "1a2d177749cec443c05021855e4f006cdae821f1",
             "4dcbcb7e462a3d0953fea5b54d29c30954193ea7",
             "ec1dc8553419c8833a687fd8d4a6521a4e29ff7a"),
        )
        expected = [
            f"{head} {supervisor.REVIEWED_HARNESS_SHA}",
            f"{supervisor.REVIEWED_HARNESS_SHA} {supervisor.ROOT18_HARNESS_SHA}",
            f"{supervisor.ROOT18_HARNESS_SHA} {supervisor.RETAINED_HARNESS_SHA}",
            f"{supervisor.RETAINED_HARNESS_SHA} {supervisor.PREPARATION_SHA}",
            f"{supervisor.PREPARATION_SHA} {policy.BASE}",
        ]
        supervisor.validate_harness_lineage(expected, head)
        bad = [
            [f"{head} {policy.BASE}"],
            [f"{head} {supervisor.ROOT18_HARNESS_SHA}", *expected[2:]],
            [f"{head} {supervisor.RETAINED_HARNESS_SHA}", *expected[3:]],
            [f"{head} {supervisor.PREPARATION_SHA}", expected[4]],
            expected[:4],
            expected[:3],
            [*expected, f"{policy.BASE} {'b' * 40}"],
        ]
        for index, row in enumerate(expected):
            bad.append([*expected[:index], row + " " + "b" * 40, *expected[index + 1:]])
            child, _ = row.split()
            bad.append([*expected[:index], child + " " + "b" * 40, *expected[index + 1:]])
        for rows in bad:
            with self.subTest(rows=rows), self.assertRaises(policy.GuardError):
                supervisor.validate_harness_lineage(rows, head)

    def test_root_counters_and_supervisor_result_cannot_credit_outer_cleanup_alone(self):
        root = result_fixture()
        counters = {
            "phase": "completed-root-only",
            "budget": {"bytes": {"control": 1}, "total": 1, "runs": 1, "states": 1, "failed": False, "closed": True},
            "semantics": "unit-only scalar contract, not a real native result",
        }
        result = {
            "mode": "root", "returncode": 0, "root_check_attempts": 1, "graph_check_attempts": 0,
            "empty": True, "watchdog_reaped": True, "lifetime_writer_closed": True,
            "worker": {"root": root, "validation": policy.validate_root_result(root),
                       "counters": counters, "root_check_attempts": 1, "root_check_completed": True, "graph_check_attempts": 0},
        }
        supervisor.validate_root_phase(result)
        self.assertFalse(supervisor.root_retention(result))
        for key, value in (("root_check_attempts", 0), ("graph_check_attempts", 1), ("worker", {}),
                           ("empty", False), ("output_exceeded", True), ("cleanup_errors", [{"real": "failure"}])):
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                supervisor.validate_root_phase({**result, key: value})
        for key, value in (("closed", False), ("failed", True), ("total", 0), ("runs", True),
                           ("bytes", {"unclassified": 1})):
            bad = copy.deepcopy(counters)
            bad["budget"][key] = value
            with self.subTest(counter=key), self.assertRaises(policy.GuardError):
                policy.validate_root_counters(bad)
        for cleanup in (None, {**root["cleanup"], "retained_owners": 1},
                        {**root["cleanup"], "fixture_removed": False}):
            error = {} if cleanup is None else {"root_cleanup": cleanup}
            self.assertTrue(supervisor.root_retention({
                "mode": "root", "empty": True, "watchdog_reaped": True,
                "first_cause": {"type": "worker-error", "error": error},
            }))
        with self.assertRaises(policy.GuardError):
            supervisor.root_retention({"mode": "root", "first_cause": {"error": {"root_cleanup": {"unknown": True}}}})
        with self.assertRaises(policy.GuardError):
            supervisor.Artifacts(self.root / "artifacts").write("report.json", root)

    def source_error(self):
        error = SourceRefusalFixtureError(policy.SOURCE_REFUSAL_MESSAGES[0])
        error.source_attribution = source_refusal_fixture()
        return error

    def project_source(self, error, *, revision=None, deadline=None):
        return policy.project_source_refusal(
            error, revision=policy.GRAPH if revision is None else revision,
            deadline=time.monotonic() + 30 if deadline is None else deadline,
        )

    def route_source(self, error, *, revision=None, deadline=None):
        sampler = SimpleNamespace(snapshot=lambda: {
            "phase": "inert-source-failure", "budget": {"closed": True, "bytes": {"cache": 123}},
        })
        return worker.graph_error_record(
            error, sampler, None, source_binding=(
                SourceRefusalFixtureError, policy.GRAPH if revision is None else revision,
                time.monotonic() + 30 if deadline is None else deadline,
            ),
        )

    def assert_source_available(self, error=None):
        error = self.source_error() if error is None else error
        record = self.route_source(error)
        self.assertIn("source_refusal", record)
        member = record["source_refusal"]
        self.assertEqual(member["status"], "available")
        self.assertIs(policy.validate_source_refusal(member), member)
        self.assertEqual(record["chain"][0], {
            "type": "SourceRefusalFixtureError", "message": policy.SOURCE_REFUSAL_MESSAGES[0],
        })
        self.assertNotIn(b"MUST_NOT_EXPORT_SOURCE", policy.encoded(record))
        self.assertEqual(set(member["metadata"]), set(error.source_attribution) - {
            "expression", "read_form", "unresolved",
        })
        return record

    def test_source_projection_keeps_closed_metadata_and_all_variants(self):
        error = self.source_error()
        original = copy.deepcopy(error.source_attribution)
        expected = copy.deepcopy(original)
        for field in ("expression", "read_form", "unresolved"):
            del expected[field]
        for fact in expected["snapshot_facts"].values():
            del fact["read_forms"]
        for cause in expected["unsafe_unknown_causes"]:
            cause["site"] = dict(zip(("path", "logical", "start", "end"), cause["site"]))
        member = self.assert_source_available(error)["source_refusal"]
        self.assertEqual(member["metadata"], expected)
        self.assertEqual(error.source_attribution, original)
        self.assertEqual(member["omitted_by_contract"], [
            "expression", "read_form", "unresolved", "snapshot_facts.*.read_forms", "__notes__",
        ])
        for condition in ("unresolved-selector", "direct-wildcard", "namespace-dependency"):
            error.source_attribution["condition"] = condition
            with self.subTest(condition=condition):
                self.assertEqual(self.project_source(error)["metadata"]["condition"], condition)
        error.source_attribution.update(
            condition="export-namespace-dependency",
            source={"status": "unavailable", "reason": "export aggregate has no unique source occurrence"},
            expression=None, read_form=None, use_associations=[], association_status="unavailable-not-inferred",
            use_kind="export-read aggregate; no unique source occurrence",
        )
        self.assertEqual(self.project_source(error)["metadata"]["source"], error.source_attribution["source"])
        for field, values in (
            ("binding_status", ("unavailable", "version-mismatch", "stored-original-binding")),
            ("snapshot_decision", (
                "disabled-by-unknown-writer", "not-examined", "unsafe-binding", "ambiguous-binding",
                "not-simple", "exact-original-snapshot", "exact-original-value-unavailable",
            )),
            ("source_fact_kind", (None, "exact", "header-bound")),
            ("binding_version", (None, 0, policy.POLICY_SENTINEL)),
            ("original_input_flags", (None, 0, (1 << 31) - 1)),
            ("bindings", (None, [])),
        ):
            for value in values:
                fresh = self.source_error()
                fresh.source_attribution["snapshot_facts"]["SNAPSHOT"][field] = value
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.project_source(fresh)["metadata"]["snapshot_facts"]["SNAPSHOT"][field], value)
        for origin in ("default", "environment", "file", "command line", "override", "unknown", "undefined"):
            for flavor in ("simple", "recursive", "unknown", "undefined"):
                fresh = self.source_error()
                bindings = [{"origin": origin, "flavor": flavor}]
                fresh.source_attribution["snapshot_facts"]["SNAPSHOT"]["bindings"] = bindings
                self.assertEqual(self.project_source(fresh)["metadata"]["snapshot_facts"]["SNAPSHOT"]["bindings"], bindings)
        for kind in ("unproved-local-binder-analysis", "local-binders", "unproved-eval-writer", "eval-writer"):
            fresh = self.source_error()
            writer = {"kind": kind, "path": "Makefile", "stream_position": 2, "site": None}
            if kind == "local-binders":
                writer["names"] = ["LOCAL"]
            elif kind == "eval-writer":
                writer["name"] = "LOCAL"
            fresh.source_attribution["unsafe_unknown_causes"] = [writer]
            self.assertEqual(self.project_source(fresh)["metadata"]["unsafe_unknown_causes"], [writer])

    def test_source_unavailable_facts_and_associations_are_not_invented(self):
        for associations in (
            [], [{"kind": "unproved"}],
            [{"kind": "obligation", "target": "all", "ordinal": 0, "job": None}],
            [{"kind": "recipe", "target": "all", "ordinal": 0, "job": 9}],
            source_refusal_fixture()["use_associations"],
        ):
            error = self.source_error()
            error.source_attribution["use_associations"] = associations
            status = "recorded" if associations else "unavailable-not-inferred"
            error.source_attribution["association_status"] = status
            error.source_attribution["source"].update(
                visit=None, visit_status="unavailable-or-repeated", site=None, active=None,
                recipe_ordinal=None, rule_number=None,
            )
            result = self.project_source(error)["metadata"]
            self.assertEqual(result["use_associations"], associations)
            self.assertEqual(result["association_status"], status)
            self.assertEqual(result["source"], error.source_attribution["source"])
        for stage in ("source-data", "bounded-serialization", "retention-accounting"):
            for kind in ("MakeProbeError", "unavailable-type-name-exceeds-existing-bound"):
                error = SourceRefusalFixtureError(policy.SOURCE_REFUSAL_MESSAGES[0])
                error.source_attribution_unavailable = stage
                error.source_attribution_failure_type = kind
                error.__notes__ = ["MUST_NOT_EXPORT_SOURCE"]
                result = self.project_source(error)
                self.assertEqual(result["unavailable"], {
                    "stage": stage, "reason": "upstream-unavailable", "failure_type": kind,
                })
                self.assertIsNone(result["metadata"])
                self.assertNotIn(b"MUST_NOT_EXPORT_SOURCE", policy.encoded(result))
        error = SourceRefusalFixtureError(policy.SOURCE_REFUSAL_MESSAGES[1])
        result = self.project_source(error)
        self.assertEqual(result["unavailable"]["reason"], "not-attached")
        error.source_attribution_unavailable = "source-data"
        self.assertEqual(self.project_source(error)["status"], "unavailable")
        error.source_attribution_failure_type = "X" * 513
        self.assertEqual(self.project_source(error)["unavailable"]["reason"], "failure-type-over-bound")
        error.source_attribution = source_refusal_fixture()
        self.assertEqual(self.project_source(error)["unavailable"]["reason"], "invalid-source-shape")

    def test_source_omitted_subtrees_are_never_visited_or_formatted(self):
        called = []
        class Opaque:
            def forbidden(self, *args):
                called.append(True)
                raise AssertionError("raw omitted value was inspected")
            __str__ = __repr__ = __iter__ = __eq__ = forbidden
        opaque = Opaque()
        error = self.source_error()
        error.source_attribution["unresolved"] = [opaque]
        error.source_attribution["snapshot_facts"]["SNAPSHOT"]["read_forms"] = (opaque,)
        error.__notes__ = [opaque]
        self.assert_source_available(error)
        for name in ("expression", "read_form", "unresolved"):
            fresh = self.source_error()
            fresh.source_attribution[name] = opaque
            self.assertEqual(self.project_source(fresh)["status"], "unavailable")
        fresh = self.source_error()
        fresh.source_attribution["snapshot_facts"]["SNAPSHOT"]["read_forms"] = opaque
        self.assertEqual(self.project_source(fresh)["status"], "unavailable")
        wire = self.project_source(error)
        wire["source_revision"] = opaque
        self.assertEqual(policy.retain_source_refusal(wire)["status"], "unavailable")
        self.assertEqual(called, [])

    def test_source_projection_rejects_malformed_types_names_and_cycles(self):
        for path, value in (
            (("pass",), True), (("exec",), 0), (("pass",), 1.0), (("pass",), policy.POLICY_SENTINEL + 1),
            (("kind",), "foreign"), (("scope",), "/foreign"), (("scope",), "parent/../foreign"),
            (("source", "path"), "a//b"), (("source", "path"), "bad$(name)"),
            (("source", "site", "end"), 1), (("source", "active"), 1),
            (("reader_names",), ["NOT-A-NAME"]), (("reader_names",), ["A" * 129]),
            (("reader_names",), ("A",)), (("source", "path"), "A" * 4097),
            (("use_associations",), [{"kind": "obligation", "target": "all", "ordinal": 1, "job": 1}]),
            (("use_associations",), [{"kind": "recipe", "target": "all", "ordinal": 1, "job": None}]),
            (("use_associations",), [{"kind": "recipe", "target": "../all", "ordinal": 1, "job": 1}]),
            (("use_associations",), [{"kind": "unproved", "job": 1}]),
            (("snapshot_facts", "SNAPSHOT", "original_input_flags"), 1 << 31),
            (("snapshot_facts", "SNAPSHOT", "mode_version"), -1),
            (("snapshot_facts", "SNAPSHOT", "source_fact_kind"), "value-from-header"),
            (("snapshot_facts", "SNAPSHOT", "namespace_carrier"), 1),
            (("snapshot_facts", "SNAPSHOT", "target_scopes"), ["a%%b"]),
            (("unsafe_unknown_causes",), [{"kind": "eval-writer", "path": "Makefile", "stream_position": 1,
                                         "site": ("Makefile", 1), "name": "A"}]),
        ):
            error = self.source_error()
            owner = error.source_attribution
            for name in path[:-1]:
                owner = owner[name]
            owner[path[-1]] = value
            with self.subTest(path=path, type=type(value).__name__):
                result = self.project_source(error)
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["metadata"])
                policy.validate_source_refusal(result)
        for location in ("top", "fact"):
            error = self.source_error()
            owner = error.source_attribution if location == "top" else error.source_attribution["snapshot_facts"]["SNAPSHOT"]
            owner["raw_definition"] = "MUST_NOT_EXPORT_SOURCE"
            self.assertEqual(self.project_source(error)["status"], "unavailable")
        for constructor in (lambda data: type("ForeignDict", (dict,), {})(data), lambda data: None):
            error = self.source_error()
            error.source_attribution = constructor(error.source_attribution)
            self.assertEqual(self.project_source(error)["status"], "unavailable")
        error = self.source_error()
        error.source_attribution["reader_names"].append(error.source_attribution["reader_names"])
        self.assertEqual(self.project_source(error)["status"], "unavailable")
        error = self.source_error()
        error.source_attribution["snapshot_facts"]["SNAPSHOT"]["bindings"].append(error.source_attribution)
        self.assertEqual(self.project_source(error)["status"], "unavailable")

    def test_source_projection_checks_existing_bounds_before_full_copy(self):
        original = policy._SourceProjection.__init__
        constructed = []
        def observe(instance, **keywords):
            constructed.append(keywords.get("build", False))
            original(instance, **keywords)
        for field, values in (
            ("reader_names", ["A"] * policy.ORIGINAL_LIMITS["entries"]),
            ("target_scopes", ["A" * 4096] * 17),
        ):
            error = self.source_error()
            owner = error.source_attribution if field == "reader_names" else error.source_attribution["snapshot_facts"]["SNAPSHOT"]
            owner[field] = values
            constructed.clear()
            with mock.patch.object(policy._SourceProjection, "__init__", observe):
                result = self.project_source(error)
            self.assertEqual(result["unavailable"]["reason"], "metadata-bound")
            self.assertNotIn(True, constructed)
        error = self.source_error()
        scopes = error.source_attribution["snapshot_facts"]["SNAPSHOT"]["target_scopes"] = []
        base = len(policy.encoded(self.project_source(error)))
        scopes.extend(["A" * 4096] * ((policy.ERROR_BYTES - base) // 4099))
        current = self.project_source(error)
        self.assertEqual(current["status"], "available")
        remaining = policy.ERROR_BYTES - len(policy.encoded(current))
        scope = error.source_attribution["scope"]
        if remaining > 4096 - len(scope):
            scopes.append("B" * (remaining - 3))
        else:
            error.source_attribution["scope"] += "B" * remaining
        current = self.project_source(error)
        self.assertEqual(current["status"], "available")
        self.assertEqual(len(policy.encoded(current)), policy.ERROR_BYTES)
        measured = policy._SourceProjection()
        measured.value(current, "member")
        self.assertEqual(measured.size, len(policy.encoded(current)))
        self.assertLessEqual(measured.cells, policy.ORIGINAL_LIMITS["entries"])
        self.assertLessEqual(measured.storage, policy.ORIGINAL_LIMITS["file_bytes"])
        error.source_attribution["source"]["site"]["path"] += "A"
        self.assertEqual(self.project_source(error)["unavailable"]["reason"], "metadata-bound")
        self.assertEqual(self.project_source(self.source_error(), deadline=time.monotonic() - 1)["unavailable"]["reason"], "deadline")
        for deadline in (True, float("nan"), float("inf"), 0):
            self.assertEqual(self.project_source(self.source_error(), deadline=deadline)["unavailable"]["reason"], "deadline")

    def test_source_binding_uses_exact_type_message_revision_and_no_budget_reopening(self):
        error = self.source_error()
        touched = []
        class ClosedBudget:
            closed = True
            def forbidden(self, *args):
                touched.append(True)
                raise AssertionError("closed probe budget was reopened")
            remaining = charge = close = forbidden
        sampler = SimpleNamespace(budget=ClosedBudget(), snapshot=lambda: {"budget": {"closed": True}})
        deadline = time.monotonic() + 30
        binding = SourceRefusalFixtureError, policy.GRAPH, deadline
        record = worker.graph_error_record(error, sampler, None, source_binding=binding)
        self.assertEqual(record["source_refusal"]["status"], "available")
        self.assertEqual(touched, [])
        self.assertNotIn("source_refusal", worker.graph_error_record(error, sampler, None))
        for candidate in (
            type("SourceRefusalFixtureError", (RuntimeError,), {})(error.args[0]),
            type("Subclass", (SourceRefusalFixtureError,), {})(error.args[0]),
            SourceRefusalFixtureError("unrelated"), SourceRefusalFixtureError(error.args[0], "extra"),
        ):
            candidate.source_attribution = source_refusal_fixture()
            self.assertNotIn("source_refusal", worker.graph_error_record(candidate, sampler, None, source_binding=binding))
        for message in policy.SOURCE_REFUSAL_MESSAGES:
            candidate = SourceRefusalFixtureError(message)
            candidate.source_attribution = source_refusal_fixture()
            self.assertIn("source_refusal", worker.graph_error_record(candidate, sampler, None, source_binding=binding))
        self.assert_foreign_source_unavailable()

    def assert_foreign_source_unavailable(self):
        member = self.route_source(self.source_error(), revision="b" * 40)["source_refusal"]
        self.assertEqual(member["status"], "unavailable")
        self.assertEqual(member["unavailable"]["reason"], "unsupported-source-revision")
        self.assertEqual(member["source_revision"], "61856581bc9859f59fdd938edc219fabc07cd6ef")

    def assert_selected_formatter_primary(self):
        error = self.source_error()
        with mock.patch.object(policy, "error_record", side_effect=RuntimeError("MUST_NOT_EXPORT_FORMATTER")):
            try:
                record = self.route_source(error)
            except BaseException as failure:
                self.fail("formatter replaced selected primary with " + type(failure).__name__)
        self.assertEqual(record["chain"][0], {
            "type": "SourceRefusalFixtureError", "message": error.args[0],
        })
        self.assertEqual(record["frames"], [])
        self.assertEqual(record["source_refusal"]["publication_errors"]["formatter"], {
            "reason": "primary-formatter-failed", "failure_type": "RuntimeError",
        })
        self.assertNotIn(b"MUST_NOT_EXPORT_FORMATTER", policy.encoded(record))

    def test_source_publication_failures_preserve_primary_and_release_owned_frames(self):
        self.assert_selected_formatter_primary()
        for location in ("formatter", "projection"):
            references = []
            class Owner:
                def __str__(self):
                    raise AssertionError("secondary owner must not be formatted")
            def fail(*args, **keywords):
                owner = Owner()
                references.append(weakref.ref(owner))
                raise RuntimeError(owner)
            error = self.source_error()
            try:
                raise error
            except SourceRefusalFixtureError:
                trace, cause, context = error.__traceback__, error.__cause__, error.__context__
                target = "error_record" if location == "formatter" else "_project_source_refusal"
                with mock.patch.object(policy, target, side_effect=fail):
                    record = self.route_source(error)
                self.assertIs(error.__traceback__, trace)
                self.assertIs(error.__cause__, cause)
                self.assertIs(error.__context__, context)
            gc.collect()
            self.assertTrue(references)
            self.assertTrue(all(reference() is None for reference in references))
            self.assertEqual(record["chain"][0]["message"], error.args[0])
            self.assertEqual(record["source_refusal"]["publication_errors"][location]["failure_type"], "RuntimeError")
            if location == "projection":
                self.assertEqual(record["source_refusal"]["status"], "unavailable")
        class Unprintable(RuntimeError):
            def __str__(self):
                raise ValueError("unrelated formatter failure")
        with self.assertRaisesRegex(ValueError, "unrelated formatter"):
            worker.graph_error_record(Unprintable("unrelated"), SimpleNamespace(snapshot=lambda: {}), None)

    def test_source_error_pipe_round_trip_is_bounded_and_never_completion(self):
        record = self.assert_source_available()
        ready = {"scope": "source-unit", "kind": "ready", "data": {}}
        rejected = {"scope": "source-unit", "kind": "error", "data": record}
        expected = policy.encoded(ready) + b"\n" + policy.encoded(rejected) + b"\n"
        reader, writer = os.pipe()
        try:
            self.assertLess(len(expected), fcntl.fcntl(writer, fcntl.F_GETPIPE_SZ))
            with os.fdopen(writer, "wb") as outgoing:
                writer = None
                with mock.patch.object(kernel.sys, "stdout", SimpleNamespace(buffer=outgoing)):
                    kernel.emit("source-unit", "ready", {})
                    kernel.emit("source-unit", "error", record)
            with os.fdopen(reader, "rb") as incoming:
                reader = None
                raw = incoming.read(policy.ERROR_BYTES + 1)
        finally:
            for descriptor in (reader, writer):
                if descriptor is not None:
                    os.close(descriptor)
        self.assertEqual(raw, expected)
        parser = supervisor.Protocol("source-unit", policy.OUTPUT_BYTES)
        self.assertEqual(parser.feed(raw), [ready, rejected])
        self.assertEqual(parser.total, len(raw))
        self.assertFalse(parser.finished)
        for validate in (policy.validate_root_result, policy.validate_report, supervisor.validate_root_phase):
            with self.assertRaises(policy.GuardError):
                validate(record)
        result = {"status": "failed", "first_error": {"type": "worker-error", "error": record},
                  "phase": {"first_cause": {"type": "worker-error", "error": record}}}
        artifacts = supervisor.Artifacts(self.root / "source-artifacts")
        artifacts.write("result.json", result)
        self.assertEqual((artifacts.root / "result.json").stat().st_size, len(policy.encoded(result)) + 1)
        self.assertEqual(json.loads((artifacts.root / "result.json").read_text()), result)
        with self.assertRaises(policy.GuardError):
            artifacts.write("report.json", result)

    def test_source_wire_validation_discards_only_bad_optional_metadata(self):
        record = self.assert_source_available()
        for path, value in (
            (("format",), "future"), (("source_revision",), "b" * 40),
            (("metadata", "pass"), True), (("metadata", "expression"), "MUST_NOT_EXPORT_SOURCE"),
            (("metadata", "snapshot_facts", "SNAPSHOT", "read_forms"), ["MUST_NOT_EXPORT_SOURCE"]),
            (("publication_errors", "formatter"), {"reason": "foreign", "failure_type": None}),
        ):
            changed = copy.deepcopy(record)
            owner = changed["source_refusal"]
            for field in path[:-1]:
                owner = owner[field]
            owner[path[-1]] = value
            parser = supervisor.Protocol("source-unit", policy.OUTPUT_BYTES)
            output, = parser.feed(policy.encoded({"scope": "source-unit", "kind": "error", "data": changed}) + b"\n")
            self.assertEqual(output["data"]["chain"], record["chain"])
            member = output["data"]["source_refusal"]
            self.assertEqual(member["status"], "unavailable")
            self.assertEqual(member["unavailable"]["reason"], "invalid-wire-metadata")
            self.assertIsNotNone(member["publication_errors"]["validation"])
            self.assertNotIn(b"MUST_NOT_EXPORT_SOURCE", policy.encoded(output))
            self.assertFalse(parser.finished)
        for kind in ("ready", "progress", "root-start", "graph-start", "result", "probe-result", "cleanup-error", "escaped"):
            parser = supervisor.Protocol("source-unit", policy.OUTPUT_BYTES)
            if kind != "ready":
                parser.feed(policy.encoded({"scope": "source-unit", "kind": "ready", "data": {}}) + b"\n")
            with self.subTest(kind=kind), self.assertRaises(policy.GuardError):
                parser.feed(policy.encoded({"scope": "source-unit", "kind": kind, "data": record}) + b"\n")
        for change in ({"scope": "foreign"}, {"extra": True}, {"kind": "foreign"}, {"data": []}):
            parser = supervisor.Protocol("source-unit", policy.OUTPUT_BYTES)
            with self.assertRaises(policy.GuardError):
                parser.feed(policy.encoded({**{"scope": "source-unit", "kind": "error", "data": record}, **change}) + b"\n")

    def test_source_wire_validation_failure_releases_owners_and_keeps_primary(self):
        record = self.assert_source_available()
        references = []
        class Owner:
            def __repr__(self):
                raise AssertionError("wire failure owner must not be formatted")
        def fail(value):
            owner = Owner()
            references.append(weakref.ref(owner))
            raise RuntimeError(owner)
        parser = supervisor.Protocol("source-unit", policy.OUTPUT_BYTES)
        with mock.patch.object(policy, "validate_source_refusal", side_effect=fail):
            output, = parser.feed(policy.encoded({
                "scope": "source-unit", "kind": "error", "data": record,
            }) + b"\n")
        gc.collect()
        self.assertTrue(references)
        self.assertTrue(all(reference() is None for reference in references))
        self.assertEqual(output["data"]["chain"], record["chain"])
        member = output["data"]["source_refusal"]
        self.assertEqual(member["status"], "unavailable")
        self.assertEqual(member["publication_errors"]["validation"], {
            "reason": "invalid-wire-metadata", "failure_type": "RuntimeError",
        })
        self.assertFalse(parser.finished)
        policy.validate_source_refusal(member)

    def test_source_routing_restorations_break_focused_controls(self):
        source = subprocess.check_output(
            ["/usr/bin/git", "-C", str(ROOT), "show",
             supervisor.REVIEWED_HARNESS_SHA + ":scripts/ci_calibration/worker.py"], text=True, timeout=10,
        )
        node, = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "graph_error_record"]
        namespace = dict(vars(worker))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<reviewed-omission>", "exec"), namespace)
        function = namespace["graph_error_record"]
        old = FunctionType(function.__code__, vars(worker), function.__name__, function.__defaults__)
        def omit(error, sampler, observer, *, source_binding=None):
            return old(error, sampler, observer)
        with mock.patch.object(worker, "graph_error_record", side_effect=omit):
            with self.assertRaises(AssertionError):
                self.assert_source_available()
        def forward(error, **keywords):
            return policy._source_envelope(error.source_attribution)
        with mock.patch.object(policy, "project_source_refusal", side_effect=forward):
            with self.assertRaises(policy.GuardError):
                self.assert_source_available()
        original = policy.project_source_refusal
        def unbound(error, **keywords):
            return original(error, **{**keywords, "revision": policy.SOURCE_REFUSAL_REVISION})
        with mock.patch.object(policy, "project_source_refusal", side_effect=unbound):
            with self.assertRaises(AssertionError):
                self.assert_foreign_source_unavailable()
        original_route = worker.graph_error_record
        def replace_primary(error, sampler, observer, *, source_binding=None):
            policy.error_record(error)
            return original_route(error, sampler, observer, source_binding=source_binding)
        with mock.patch.object(worker, "graph_error_record", side_effect=replace_primary):
            with self.assertRaises(AssertionError):
                self.assert_selected_formatter_primary()


if __name__ == "__main__":
    unittest.main()
