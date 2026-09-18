"""Benign data/API controls only: never invoke CURRENT's native/root planner."""

import ast
import builtins
import copy
import dataclasses
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import typing
import unittest
from unittest import mock

from scripts.ci_calibration import policy, root_stage, supervisor, worker


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


class RootStageControls(unittest.TestCase):
    def setUp(self):
        parent = ROOT / "build/test-artifacts/root17-benign"
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
                         ("8d03b518c714ff4915220af4568f0274f3c8d292", "ec1dc8553419c8833a687fd8d4a6521a4e29ff7a"))
        self.assertEqual(scope["workload_kind"], "original-root-acceptance")
        self.assertTrue(scope["source_phases"])
        self.assertFalse(scope["production_acceptance"])
        for key, value in (("attempt", "2"), ("run_number", "2"), ("environment", "self-hosted")):
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                policy.validate_event(event, **{**arguments, key: value})
        for branch in ("calibration/issue-180-ci-baseline-16", "master"):
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
                if primary is None:
                    with self.assertRaises(RuntimeError) as caught:
                        worker.finish_root("unit/root", sampler, budget, primary)
                    self.assertIs(caught.exception, first)
                else:
                    worker.finish_root("unit/root", sampler, budget, primary)
                sampler.close.assert_called_once_with()
                budget.close.assert_called_once_with()
                self.assertEqual(emitted.call_count, 2)
                self.assertTrue(all(call.args[:2] == ("unit/root", "cleanup-error") for call in emitted.call_args_list))

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


if __name__ == "__main__":
    unittest.main()
