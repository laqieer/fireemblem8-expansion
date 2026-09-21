"""Actual harness composition with inert report/session effects, never native source imports."""

import builtins
import copy
import dataclasses
import errno
import itertools
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import unittest
from unittest import mock
from contextlib import ExitStack

from scripts.ci_calibration import kernel, observation_failure, policy, root_stage, supervisor, worker
from scripts.ci_calibration.test_ci_calibration import Inert, budgeting


CANDIDATE_API = root_stage.candidate_api


class RootStageControls(Inert):
    def composition(self, *faults, use_worker=False):
        faults = set(faults)
        budget = self.budget()
        sampler = SimpleNamespace(phase="candidate-import", session=None)
        events, sessions, measurements, frames, calls, serialized = [], [], [], [], [], []
        errors = {
            name: OSError(number, "private " + name) for name, number in (
                ("constructor", errno.EIO), ("enter", errno.EACCES), ("teardown", errno.ENOTEMPTY),
                ("cleanup-observation", errno.EIO), ("counter-observation", errno.EOVERFLOW),
                ("reference", errno.EBADF), ("serialization", errno.ENOSPC),
                ("sampler-close", errno.EIO), ("budget-close", errno.EIO),
                ("publication", errno.EPIPE),
            )
        }
        class SourceError(RuntimeError):
            def __setattr__(self, name, value):
                if name == "report_publication_error" and "publication-attachment" in faults:
                    raise RuntimeError("private attachment failure")
                super().__setattr__(name, value)
        errors["source"] = SourceError("private source first failure")
        controls = self
        runtime = ("/fixed/original/runtime", "/fixed/original/metadata")
        config = {"mode": "report", "scope": "12345/report", "deadline": 3700.0, "report_binding": self.binding()}

        class Entries(dict):
            def __init__(self, budget):
                super().__init__()
                self.budget, self.capture = budget, (Path("/repo"), policy.GRAPH)

        class Loader:
            def __init__(self, budget):
                self.budget, self.entries = budget, Entries(budget)
                self.root, self.revision = Path("/repo"), policy.GRAPH

        class Session:
            def __init__(self, loader, *, scratch_root, budget, runtime_files):
                events.append("constructor")
                if "constructor-before" in faults:
                    raise errors["constructor"]
                vars(self).update(vars(controls.session(budget)))
                self.loader, self.runtime_paths, self.scratch_root = loader, runtime_files, scratch_root
                sessions.append(self)
                if "foreign-session-budget" in faults:
                    self.budget = object()
                if "constructor-after" in faults:
                    raise errors["constructor"]

            def __enter__(self):
                events.append("enter")
                self.budget.session_started = True
                try:
                    if "enter-before" in faults:
                        raise errors["enter"]
                    self.base = Path("/owned/session")
                    self.budget.plan(2)
                    self.budget.charge("control", 104697218)
                    self.budget.runs = 28
                    if "enter-after" in faults:
                        raise errors["enter"]
                    return self
                except BaseException as error:
                    self.__exit__(type(error), error, error.__traceback__)
                    raise

            def __exit__(self, kind, value, trace):
                events.append("exit")
                def retire():
                    events.append("session-retire")
                    if "teardown-before" in faults:
                        self._file_owners["retained"] = SimpleNamespace(retained=True)
                        raise errors["teardown"]
                    self._views.clear()
                    if "teardown-after" in faults:
                        raise errors["teardown"]
                def remove():
                    events.append("session-remove")
                    if not self._file_owners:
                        self.base = None
                try:
                    budgeting.finish_cleanup([self.budget.close, retire, remove], primary=value)
                except BaseException:
                    self.budget.failed = True
                    raise

            def _sandbox_run(self):
                raise AssertionError("no native model is callable")

        class ReportModule(SimpleNamespace):
            def __setattr__(self, name, value):
                if name == "ProbeSession" and getattr(self, "armed", False):
                    action = "restore" if value is Session else "install"
                    events.append(action)
                    if action + "-before" in faults:
                        raise errors["reference"]
                    super().__setattr__(name, value)
                    if action + "-after" in faults:
                        raise errors["reference"]
                    return
                super().__setattr__(name, value)

        module = ReportModule(ProbeSession=Session, armed=True)
        def check(root, *, budget, revision="HEAD", base_revision=None, changed_paths=(),
                  runtime_files=runtime, lifecycle=True):
            calls.append((root, budget, revision, base_revision, changed_paths, runtime_files, lifecycle))
            if "source-before-constructor" in faults:
                raise errors["source"]
            loader = Loader(budget)
            if "foreign-loader" in faults:
                loader.revision = policy.BASE
            if "foreign-loader-budget" in faults:
                loader.budget = object()
            if "no-session" in faults:
                return {}
            with module.ProbeSession(
                loader, scratch_root=root / "build/test-artifacts/validation-ownership", budget=budget,
                runtime_files=runtime_files + ("wrong",) if "runtime" in faults else runtime_files,
            ) as session:
                events.extend(("CURRENT", "BASE", "source-phases", "lifecycle"))
                if "second-session" in faults:
                    module.ProbeSession(loader, scratch_root=session.scratch_root, budget=budget, runtime_files=runtime)
                if "source" in faults:
                    raise errors["source"]
                result = controls.raw_report(budget, session)
                if "partial" in faults:
                    result["resolutions"].pop()
                return result
        module.check = check
        def serialize(value):
            events.append("serialize")
            serialized.append(value)
            controls.assertTrue(budget.closed)
            if "serialization" in faults:
                raise errors["serialization"]
            if "serialization-type" in faults:
                return "not bytes"
            return policy.encoded(value) + b"\n"
        api = SimpleNamespace(
            module=module, check=check, serializer=serialize, session=Session, loader=Loader, entries=Entries,
            budget_type=budgeting.ProbeBudget, runtime_files=runtime,
        )
        methods = {name: getattr(Session, name) for name in ("__init__", "__enter__", "__exit__", "_sandbox_run")}
        if "already-replaced" in faults:
            module.armed = False
            module.ProbeSession = object()
            module.armed = True
        original_type = root_stage.ReportMeasurement
        class Measurement(original_type):
            def __init__(self, *args):
                super().__init__(*args)
                measurements.append(self)
                self.armed = True

            def __setattr__(self, name, value):
                if name in {"raw_report", "raw_serialization"} and value is None and getattr(self, "armed", False):
                    action = "report" if name == "raw_report" else "serialization-reference"
                    events.append("withdraw-" + action)
                    if action + "-before" in faults:
                        raise errors["reference"]
                    super().__setattr__(name, value)
                    if action + "-after" in faults:
                        raise errors["reference"]
                    return
                super().__setattr__(name, value)

        actual_import = builtins.__import__
        def git(root, issued, *args):
            self.assertIs(issued, budget)
            if args == ("rev-parse", "HEAD"):
                return policy.GRAPH.encode()
            if args == ("rev-parse", policy.BASE + "^{commit}"):
                return policy.BASE.encode()
            self.assertEqual(args, (
                "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                "--ignore-submodules=none", "--name-status", "-z", policy.BASE, policy.GRAPH, "--",
            ))
            return b"".join(kind.encode() + b"\0" + path.encode() + b"\0" for path, kind in self.changes().items())
        def imports(name, *args, **kwargs):
            if name == "scripts.validation_ownership.authority":
                return SimpleNamespace(git=git)
            if name == "scripts.validation_ownership.budget":
                return budgeting
            if name == "scripts.validation_ownership.make_probe":
                return SimpleNamespace(ProbeSession=Session)
            return actual_import(name, *args, **kwargs)
        def sampler_close(value):
            events.append("sampler-close")
            if "sampler-close" in faults:
                raise errors["sampler-close"]
        original_close = budget.close
        def budget_close():
            events.append("budget-close")
            final = "sampler-close" in events
            if final and "budget-close-before" in faults:
                raise errors["budget-close"]
            original_close()
            if final and "budget-close-after" in faults:
                raise errors["budget-close"]
        def emit(scope, kind, data):
            frames.append((kind, data))
            if kind == "error" and "publication" in faults:
                raise errors["publication"]
        result = failure = None
        with ExitStack() as effects:
            effects.enter_context(mock.patch.object(worker, "require_contained", return_value={}))
            effects.enter_context(mock.patch.object(root_stage, "candidate_api", return_value=api))
            effects.enter_context(mock.patch.object(root_stage, "ReportMeasurement", Measurement))
            effects.enter_context(mock.patch.object(budget, "close", side_effect=budget_close))
            if "cleanup-observation" in faults:
                effects.enter_context(mock.patch.object(root_stage, "cleanup_state", side_effect=errors["cleanup-observation"]))
            if "counter-observation" in faults:
                effects.enter_context(mock.patch.object(policy, "counter_snapshot", side_effect=errors["counter-observation"]))
            if "formatter" in faults:
                effects.enter_context(mock.patch.object(policy, "component_error_record", side_effect=ValueError("private format")))
            if use_worker:
                effects.enter_context(mock.patch.object(builtins, "__import__", side_effect=imports))
                effects.enter_context(mock.patch.object(sys, "path", list(sys.path)))
                effects.enter_context(mock.patch.object(threading.Thread, "start", side_effect=lambda: events.append("sampler-start")))
                effects.enter_context(mock.patch.object(worker.Sampler, "close", sampler_close))
                effects.enter_context(mock.patch.object(worker, "calibration_budget", return_value=(
                    budget, budget.limits, dataclasses.asdict(budgeting.Limits()),
                    policy.profile_manifest(policy.ORIGINAL_LIMITS, observation_count=32768),
                )))
                effects.enter_context(mock.patch.object(kernel, "emit", side_effect=emit))
            try:
                result = worker.report(config) if use_worker else Measurement(
                    Path("/repo"), budget, config, sampler, self.changes(),
                ).run()
            except BaseException as error:
                failure = error
        return SimpleNamespace(
            result=result, failure=failure, errors=errors, measurements=measurements, sessions=sessions,
            events=events, frames=frames, budget=budget, sampler=sampler, api=api, calls=calls, serialized=serialized,
            methods=methods,
        )

    def test_actual_candidate_factory_only_binds_original_report_serializer_and_authority_types(self):
        original_check, original_serializer = object(), object()
        original_session, original_loader, original_entries = object(), object(), object()
        runtime = ("original-runtime",)
        graph = SimpleNamespace(check=original_check, ROOT_RUNTIME_FILES=runtime)
        reporter = SimpleNamespace(normalized_json=original_serializer)
        seen = []
        def selected(name, *args, **kwargs):
            seen.append(name)
            if name == "scripts.validation_ownership":
                return SimpleNamespace(graph_report=graph, reporter=reporter)
            if name == "scripts.validation_ownership.authority":
                return SimpleNamespace(AuthorityLoader=original_loader, GitTreeEntries=original_entries)
            if name == "scripts.validation_ownership.budget":
                return budgeting
            if name == "scripts.validation_ownership.make_probe":
                return SimpleNamespace(ProbeSession=original_session)
            raise AssertionError("unallocated import: " + name)
        with mock.patch.object(builtins, "__import__", side_effect=selected):
            api = CANDIDATE_API()
        self.assertEqual(len(seen), 4)
        for observed, expected in (
            (api.module, graph), (api.check, original_check), (api.serializer, original_serializer),
            (api.session, original_session), (api.loader, original_loader), (api.entries, original_entries),
            (api.runtime_files, runtime), (api.budget_type, budgeting.ProbeBudget),
        ):
            self.assertIs(observed, expected)

    def test_exact_public_call_forwards_complete_current_base_deletions_lifecycle_and_defaults(self):
        value = self.composition()
        self.assertIsNone(value.failure)
        self.assertEqual(len(value.calls), 1)
        root, budget, revision, base, paths, runtime, lifecycle = value.calls[0]
        self.assertEqual((root, revision, base, paths, lifecycle),
                         (Path("/repo"), policy.GRAPH, policy.BASE, tuple(self.changes()), True))
        self.assertIs(budget, value.budget)
        self.assertIs(runtime, value.api.runtime_files)
        self.assertEqual(len(value.sessions), 1)
        session = value.sessions[0]
        self.assertIs(type(session), value.api.session)
        self.assertIs(session.budget, value.budget)
        self.assertIs(session.loader.budget, value.budget)
        self.assertIs(session.loader.entries.budget, value.budget)
        self.assertIs(value.sampler.session, session)
        self.assertIs(value.api.module.ProbeSession, value.api.session)
        self.assertIs(value.api.module.check, value.api.check)
        for name in ("__enter__", "__exit__", "_sandbox_run"):
            self.assertNotIn(name, vars(session))
        for name, method in value.methods.items():
            self.assertIs(getattr(value.api.session, name), method)
        self.assertEqual([event for event in value.events if event in {"CURRENT", "BASE", "source-phases", "lifecycle"}],
                         ["CURRENT", "BASE", "source-phases", "lifecycle"])
        self.assertEqual(value.result["serialized_bytes"], len(policy.encoded(value.serialized[0])) + 1)
        self.assertEqual(value.result["summary"]["base_paths"], 1)
        self.assertEqual(value.result["counters"]["budget"]["categories"]["control"]["charged"], 104697218)
        self.assertEqual((budget.started, budget.deadline), (100.0, 3700.0))
        self.assertLess(value.events.index("session-remove"), value.events.index("serialize"))
        self.assertIsNone(value.measurements[0].raw_report)
        self.assertIsNone(value.measurements[0].raw_serialization)
        self.assertNotIn(b"private", policy.encoded(value.result))
        with mock.patch.object(worker, "require_contained", return_value={}), \
             mock.patch.object(root_stage, "candidate_api") as imported:
            with self.assertRaises(policy.GuardError):
                value.measurements[0].run()
            imported.assert_not_called()

    def test_uncontained_foreign_clock_root_mode_and_paths_never_import_candidate(self):
        budget = self.budget()
        config = {"mode": "report", "deadline": 3700.0, "report_binding": self.binding()}
        with mock.patch.object(root_stage, "candidate_api") as imported:
            with mock.patch.object(worker, "require_contained", side_effect=policy.GuardError("not contained")):
                with self.assertRaises(policy.GuardError):
                    root_stage.ReportMeasurement(Path("/repo"), budget, config, object(), self.changes()).run()
            with mock.patch.object(worker, "require_contained", return_value={}):
                for root, mode, deadline, paths in (
                    (Path("/foreign"), "report", 3700.0, self.changes()),
                    (Path("/repo"), "component", 3700.0, self.changes()),
                    (Path("/repo"), "report", 3701.0, self.changes()),
                    (Path("/repo"), "report", 3700.0, {"src/current.c": "M"}),
                ):
                    with self.assertRaises(policy.GuardError):
                        root_stage.ReportMeasurement(
                            root, budget, {**config, "mode": mode, "deadline": deadline}, object(), paths,
                        ).run()
            imported.assert_not_called()

    def test_constructor_enter_foreign_repeated_partial_and_serialization_failures_never_complete(self):
        for fault in (
            "constructor-before", "constructor-after", "enter-before", "enter-after",
            "foreign-session-budget", "foreign-loader", "foreign-loader-budget", "runtime",
            "no-session", "second-session", "already-replaced", "partial", "serialization", "serialization-type",
        ):
            with self.subTest(fault=fault):
                value = self.composition(fault)
                self.assertIsNone(value.result)
                self.assertIsNotNone(value.failure)
                self.assertLessEqual(value.events.count("constructor"), 1)
                self.assertLessEqual(len(value.calls), 1)
                if fault != "already-replaced":
                    self.assertIs(value.api.module.ProbeSession, value.api.session)
                if fault.startswith("constructor-"):
                    self.assertEqual(value.measurements[0].states["session_constructed"], 0)
                    self.assertIsNone(value.measurements[0].cleanup["session_base_removed"])
                if fault.startswith("enter-"):
                    self.assertIn("session-retire", value.events)
                    self.assertIn("session-remove", value.events)

    def test_first_source_teardown_collectors_and_formatter_combinations_preserve_all_attempts(self):
        for source, teardown, cleanup, counters, formatter in itertools.product((False, True), repeat=5):
            faults = [name for name, enabled in (
                ("source", source), ("teardown-before", teardown), ("cleanup-observation", cleanup),
                ("counter-observation", counters), ("formatter", formatter),
            ) if enabled]
            with self.subTest(faults=faults):
                value = self.composition(*faults)
                for event in ("budget-close", "session-retire", "session-remove", "restore",
                              "withdraw-report", "withdraw-serialization-reference"):
                    self.assertIn(event, value.events)
                self.assertIs(value.api.module.ProbeSession, value.api.session)
                measurement = value.measurements[0]
                self.assertIsNone(measurement.raw_report)
                self.assertIsNone(measurement.raw_serialization)
                expected = (
                    "source" if source else "teardown" if teardown else
                    "cleanup-observation" if cleanup else "counter-observation" if counters else None
                )
                if expected is None:
                    self.assertIsNone(value.failure)
                    self.assertTrue(value.result["states"]["completed"])
                else:
                    self.assertIs(value.failure, value.errors[expected])
                    self.assertIsNone(value.result)
                    self.assertFalse(measurement.states["completed"])
                    if cleanup:
                        self.assertIsNone(measurement.cleanup)
                    if source and teardown:
                        self.assertEqual(policy.source_cleanup_count(value.failure), 1)
                    if formatter and measurement.secondary:
                        self.assertTrue(all(row["error"]["reason"] == "secondary-format-failed"
                                            for row in measurement.secondary))

    def test_independent_reference_faults_before_and_after_still_withdraw_every_other_reference(self):
        for operation, when, source, collector in itertools.product(
            ("restore", "report", "serialization-reference"), ("before", "after"), (False, True), (False, True),
        ):
            faults = [operation + "-" + when]
            if source:
                faults.append("source")
            if collector:
                faults.append("cleanup-observation")
            with self.subTest(faults=faults):
                value = self.composition(*faults)
                expected = "source" if source else "cleanup-observation" if collector else "reference"
                self.assertIs(value.failure, value.errors[expected])
                self.assertIsNone(value.result)
                for event in ("restore", "withdraw-report", "withdraw-serialization-reference"):
                    self.assertEqual(value.events.count(event), 1)
                measurement = value.measurements[0]
                if not collector:
                    field = {"restore": "constructor_restored", "report": "report_released",
                             "serialization-reference": "serialization_released"}[operation]
                    self.assertIsNone(measurement.cleanup[field])
                if operation != "report" or when == "after":
                    self.assertIsNone(measurement.raw_report)
                if operation != "serialization-reference" or when == "after":
                    self.assertIsNone(measurement.raw_serialization)
                if operation != "restore" or when == "after":
                    self.assertIs(value.api.module.ProbeSession, value.api.session)
        for fault in ("install-before", "install-after"):
            value = self.composition(fault)
            self.assertIs(value.failure, value.errors["reference"])
            self.assertFalse(value.calls)
            self.assertIs(value.api.module.ProbeSession, value.api.session)
            self.assertIn("withdraw-report", value.events)
            self.assertIn("withdraw-serialization-reference", value.events)
        value = self.composition("restore-before", "report-before", "serialization-reference-before", "source")
        self.assertIs(value.failure, value.errors["source"])
        self.assertEqual(len(value.measurements[0].secondary), 3)

    def test_worker_runs_the_actual_adapter_and_closes_both_owners_before_publishing_result(self):
        value = self.composition(use_worker=True)
        self.assertIsNone(value.failure)
        self.assertIsNotNone(value.result)
        policy.validate_report_worker(value.result, self.binding(), 3700.0)
        self.assertEqual(value.events[-2:], ["sampler-close", "budget-close"])
        self.assertEqual([kind for kind, data in value.frames], ["report-start"])
        self.assertEqual(value.frames[0][1]["check_attempts"], 0)
        self.assertEqual(value.result["report"]["states"]["check_attempts"], 1)
        self.assertEqual(value.result["counters"]["counters"], value.result["report"]["counters"])

    def test_constructor_failures_with_collectors_teardown_and_reference_faults_keep_uncertainty(self):
        for constructor, collector, reference, teardown in itertools.product(
            ("constructor-before", "constructor-after"),
            ((), ("cleanup-observation",), ("counter-observation",),
             ("cleanup-observation", "counter-observation")),
            ((), ("restore-before",), ("restore-after",), ("report-before",), ("report-after",),
             ("serialization-reference-before",), ("serialization-reference-after",)),
            ((), ("teardown-before",), ("teardown-after",)),
        ):
            faults = (constructor, *collector, *reference, *teardown)
            with self.subTest(faults=faults):
                value = self.composition(*faults, use_worker=True)
                self.assertIsNone(value.result)
                self.assertIs(value.measurements[0].first, value.errors["constructor"])
                self.assertNotIn("enter", value.events)
                self.assertNotIn("exit", value.events)
                for event in ("restore", "withdraw-report", "withdraw-serialization-reference",
                              "sampler-close", "budget-close"):
                    self.assertIn(event, value.events)
                record, = [data for kind, data in value.frames if kind == "error"]
                self.assertEqual(record["error"]["chain"][0]["errno"], errno.EIO)
                self.assertEqual(record["states"]["check_attempts"], 1)
                self.assertEqual(record["states"]["session_attempts"], 1)
                self.assertEqual(record["states"]["session_constructed"], 0)
                self.assertEqual(record["states"]["check_returned"], 0)
                self.assertIsNone(record["summary"])
                self.assertIsNone(record["serialized_bytes"])
                if record["cleanup"] is not None:
                    self.assertIsNone(record["cleanup"]["session_base_removed"])
                    self.assertIsNone(record["cleanup"]["retained_owners"])

    def test_failed_or_unknown_inner_close_is_retained_even_if_outer_death_was_observed(self):
        for faults in (
            ("teardown-after",), ("source", "teardown-after"), ("budget-close-after",), ("sampler-close",),
            ("cleanup-observation",), ("counter-observation",), ("restore-after",), ("report-after",),
            ("serialization-reference-after",), ("constructor-before",),
        ):
            with self.subTest(faults=faults):
                value = self.composition(*faults, use_worker=True)
                record, = [data for kind, data in value.frames if kind == "error"]
                phase = {**self.report_phase(), "worker": None, "first_cause": {"type": "worker-error", "error": record}}
                self.assertTrue(supervisor.report_retention(phase))
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_report_phase(phase, self.binding())
        value = self.composition("source", use_worker=True)
        record, = [data for kind, data in value.frames if kind == "error"]
        phase = {**self.report_phase(), "worker": None, "first_cause": {"type": "worker-error", "error": record}}
        self.assertFalse(supervisor.report_retention(phase))
        with self.assertRaises(policy.GuardError):
            supervisor.validate_report_phase(phase, self.binding())

    def test_worker_all_closes_counter_errors_publication_errors_preserve_the_source_primary(self):
        for source, sampler, close, publication in itertools.product((False, True), repeat=4):
            faults = [name for name, enabled in (
                ("source", source), ("sampler-close", sampler), ("budget-close-before", close),
                ("publication", publication),
            ) if enabled]
            with self.subTest(faults=faults):
                value = self.composition(*faults, use_worker=True)
                self.assertIn("sampler-close", value.events)
                self.assertEqual(value.events[-1], "budget-close")
                failed = source or sampler or close
                if not failed:
                    self.assertIsNone(value.failure)
                    self.assertIsNotNone(value.result)
                    continue
                self.assertIsNone(value.result)
                expected = "source" if source else "sampler-close" if sampler else "budget-close"
                records = [data for kind, data in value.frames if kind == "error"]
                self.assertEqual(len(records), 1)
                record = records[0]
                policy.validate_report_error(record, self.binding())
                self.assertFalse(record["states"]["completed"])
                self.assertNotIn(b"private", policy.encoded(record))
                if publication:
                    self.assertIs(value.failure, value.errors[expected])
                    self.assertEqual(value.failure.report_publication_error["chain"][0]["errno"], errno.EPIPE)
                else:
                    self.assertIsNone(value.failure)
        for faults in (
            ("source", "cleanup-observation", "counter-observation", "formatter"),
            ("constructor-after",), ("source-before-constructor",), ("budget-close-after",),
            ("source", "teardown-after", "sampler-close", "budget-close-after"),
        ):
            value = self.composition(*faults, use_worker=True)
            self.assertIsNone(value.result)
            record, = [data for kind, data in value.frames if kind == "error"]
            policy.validate_report_error(record, self.binding())
            if "counter-observation" in faults:
                self.assertIsNone(record["counters"])
            if "cleanup-observation" in faults:
                self.assertIsNone(record["cleanup"])
            if "teardown-after" in faults:
                self.assertEqual(record["source_cleanup_failures"], 1)
            self.assertNotIn(b"private", policy.encoded(record))

    def test_collector_facts_are_closed_and_first_error_survives_later_attribution_faults(self):
        sampler = worker.Sampler("12345/report")
        sampler.budget = self.budget()
        first = RuntimeError("private primary")
        observer = SimpleNamespace(
            capture=mock.Mock(side_effect=OSError("private native collector")),
            budget_admission=mock.Mock(return_value={"status": "observed", "raw": "private"}),
        )
        record = worker.report_error_record(first, None, sampler, observer, self.binding(), [])
        policy.validate_report_error(record, self.binding())
        self.assertEqual(record["error"]["chain"][0]["type"], "RuntimeError")
        self.assertEqual(record["observation_failure"], observation_failure.unavailable("collector-failed"))
        self.assertEqual(record["budget_admission"], observation_failure.unavailable("collector-failed"))
        self.assertEqual(len(record["secondary"]), 2)
        self.assertNotIn(b"private", policy.encoded(record))

    def test_close_reference_and_publication_attachment_failures_do_not_replace_first_or_skip_close(self):
        for prior, sampler_get, budget_get in itertools.product((False, True), repeat=3):
            events, secondary = [], []
            first = RuntimeError("private first")
            errors = {"sampler": OSError(errno.EIO, "private sampler reference"),
                      "budget": OSError(errno.EBADF, "private budget reference")}
            class Owner:
                def __init__(self, name, fail):
                    self.name, self.fail = name, fail
                @property
                def close(self):
                    events.append("get-" + self.name)
                    if self.fail:
                        raise errors[self.name]
                    return lambda: events.append("close-" + self.name)
            result, stage = worker.finish_report(
                Owner("sampler", sampler_get), Owner("budget", budget_get), first if prior else None, secondary,
            )
            self.assertIn("get-sampler", events)
            self.assertIn("get-budget", events)
            if not budget_get:
                self.assertIn("close-budget", events)
            expected = first if prior else errors["sampler"] if sampler_get else errors["budget"] if budget_get else None
            self.assertIs(result, expected)
        value = self.composition("source", "publication", "publication-attachment", use_worker=True)
        self.assertIs(value.failure, value.errors["source"])
        self.assertIn("sampler-close", value.events)
        self.assertEqual(value.events[-1], "budget-close")

    def test_sampler_failure_is_not_a_successful_session_close(self):
        sampler = worker.Sampler("12345/report")
        sampler.budget = self.budget()
        sampler.session = self.session(sampler.budget)
        first = OSError("private sampler")
        with mock.patch.object(sampler.stop, "wait", return_value=False), \
             mock.patch.object(kernel, "emit", side_effect=first):
            sampler.run()
        self.assertIs(sampler.failure, first)
        with mock.patch.object(sampler.thread, "join"), mock.patch.object(sampler.thread, "is_alive", return_value=False):
            with self.assertRaises(OSError) as caught:
                sampler.close()
        self.assertIs(caught.exception, first)
        sampler.budget.close()
        self.assertTrue(sampler.snapshot()["counters"]["budget"]["closed"])

    def supervisor_case(self, fault=None):
        arguments = SimpleNamespace(
            operation="run", harness="/owned/harness", candidate="/owned/candidate",
            output="/owned/" + policy.OUTPUT_PREFIX + "12345", event="/owned/event",
            sha="a" * 40, event_name="push", run_id="12345", attempt="1", run_number="1",
            runner_environment="github-hosted", runner_os="Linux",
        )
        event = {
            "ref": "refs/heads/" + policy.BRANCH, "before": "0" * 40, "after": "a" * 40,
            "created": True, "deleted": False, "repository": {"full_name": policy.REPOSITORY, "private": False},
            "sender": {"login": "laqieer"},
        }
        scope = policy.validate_event(
            event, sha=arguments.sha, run_id="12345", attempt="1", run_number="1",
            environment="github-hosted", operating_system="Linux", event_name="push",
        )
        scope.update(
            report_launch_requested=False, report_attempted=False, report_returned=False, report_completed=False,
            planned_at_monotonic=100.0,
        )
        stored, modes, cleanup, volumes = {}, [], [], []
        facts = {
            "memory_total": 16 * policy.GIB, "memory_available": 12 * policy.GIB,
            "disk_available": 24 * policy.GIB, "cpus": 4, "threads_max": 100000, "threads_current": 500,
            "cgroup_ancestors": [{"memory_max": None, "memory_current": 0, "pids_max": None, "pids_current": 0}],
        }
        output = SimpleNamespace(root=Path(arguments.output), write=lambda name, value: stored.__setitem__(name, copy.deepcopy(value)))
        binding = self.binding()
        class Owner:
            def __init__(self, args, actual_scope, artifacts):
                self.scope = actual_scope
            def prepare(self):
                if fault == "prepare":
                    raise OSError(errno.EIO, "private owned prepare")
                self.scope.update(changed_paths=binding["changed_paths"], tracked_paths=binding["tracked_paths"])
            def volume(self, name, size):
                volumes.append((name, size))
                if name == "graph-volume" and fault == "volume":
                    raise OSError(errno.EIO, "private owned volume")
                return SimpleNamespace(close=lambda: cleanup.append("probe-volume-close"))
            def cleanup(self):
                cleanup.append("owner-cleanup")
                if fault == "cleanup":
                    raise OSError(errno.EIO, "private owned cleanup")
            def source_status(self, label):
                cleanup.append("source-" + label)
                if fault == "source-after":
                    raise OSError(errno.EIO, "private changed source")

        def proc_proof():
            return {
                "pid": 1, "proc_pid": 1, "oom_score_adj_after": "0", "proc_mount_flags": 14,
                "denied_writes": {
                    path: {"opened": True, "errno": errno.EPERM, "before": "0", "after": "0"}
                    for path in ("/proc/self/oom_score_adj", "/proc/self/oom_adj", "/proc/sys/kernel/kptr_restrict",
                                 "/proc/sys/vm/overcommit_memory", "/proc/sys/kernel/overflowuid", "/proc/sys/kernel/overflowgid")
                },
                "sysrq_write_open": {"exposed": False, "errno": errno.ENOENT},
            }
        def phase(owner, mode, volume, *, memory, pids, seconds):
            modes.append((mode, memory, pids, seconds))
            if mode == "report":
                if fault == "prereturn":
                    raise OSError(errno.EIO, "private report prereturn")
                value = self.report_phase()
                if fault == "pre-kill":
                    value["empty_before_outer_cleanup"] = False
                if fault in ("report", "retained"):
                    value["first_cause"] = {"type": "worker-error", "error": {
                        "cleanup": copy.deepcopy(value["worker"]["report"]["cleanup"]),
                        "source_cleanup_failures": 0, "stage": "check", "secondary": [],
                        "counters": copy.deepcopy(value["worker"]["report"]["counters"]),
                    }}
                    if fault == "retained":
                        value["worker"]["report"]["cleanup"]["retained_owners"] = 1
                owner.scope["report_attempted"] = owner.scope["report_returned"] = True
                return value
            value = {
                "mode": mode, "identity": {"uid": 999, "gid": 998, "cgroup": "/owned", "namespaces": {"user": 1}},
                "empty": True, "empty_before_outer_cleanup": True, "watchdog_reaped": True,
                "returncode": 0, "first_cause": None, "probe": {},
            }
            if mode == "identity":
                proof = proc_proof()
                value["probe"] = {
                    "proc_protection": proof,
                    "nested": {"uid": 0, "uid_map": ["0", "999", "1"], "gid_map": ["0", "998", "1"],
                               "no_new_privs": "1", "cgroup": "/owned", "namespaces": {"user": 2},
                               "cgroup_control_probe": {"namespace_denied": errno.EPERM}, "proc_protection": proof},
                }
            elif mode == "memory":
                value["kernel"] = {"memory_events": {"oom_kill": 1}}
            elif mode == "output":
                value.update(first_cause={"type": "output-bound"}, output_exceeded=True, output_bytes=65537)
            elif mode in ("deadline", "lifetime"):
                value.update(
                    first_cause={"type": "deadline" if mode == "deadline" else "lifetime-eof"},
                    escaped={"pid": 9}, held_descendants_terminal=2, caller_lifetime_control_exercised=True,
                )
            if fault == "preflight-" + mode:
                value["supervisor_error"] = {"type": "inert-failure"}
            return value

        local_sys = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), dont_write_bytecode=True)
        with mock.patch.object(supervisor, "arguments", return_value=arguments), \
             mock.patch.object(supervisor, "sys", local_sys), \
             mock.patch.object(kernel, "read", side_effect=lambda path, *a: policy.encoded(
                 event if str(path) == arguments.event else scope)), \
             mock.patch.object(supervisor, "Artifacts", return_value=output), \
             mock.patch.object(supervisor, "Owner", Owner), \
             mock.patch.object(supervisor, "capacity_facts", return_value=facts), \
             mock.patch.object(supervisor, "phase", side_effect=phase), \
             mock.patch.object(Path, "exists", return_value=False), \
             mock.patch.object(Path, "is_symlink", return_value=False), \
             mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(os, "chown"):
            code = supervisor.main()
        return SimpleNamespace(code=code, stored=stored, modes=modes, cleanup=cleanup, volumes=volumes)

    def test_supervisor_composes_all_seven_fresh_controls_before_the_only_report(self):
        value = self.supervisor_case()
        self.assertEqual(value.code, 0)
        self.assertEqual([row[0] for row in value.modes],
                         ["identity", "memory", "pids", "disk", "output", "deadline", "lifetime", "report"])
        self.assertEqual(value.modes[1][1], 64 * policy.MIB)
        self.assertEqual(value.modes[2][2], 8)
        self.assertEqual(value.modes[5][3], 2)
        self.assertEqual(value.modes[-1][3], 3600)
        self.assertEqual(value.cleanup, ["probe-volume-close", "owner-cleanup", "source-after"])
        self.assertEqual(value.stored["result.json"]["status"], "completed-report-diagnostic-only")
        self.assertFalse(value.stored["result.json"]["production_acceptance"])
        self.assertTrue(value.stored["scope.json"]["report_completed"])
        for mode in ("identity", "memory", "pids", "disk", "output", "deadline", "lifetime"):
            value = self.supervisor_case("preflight-" + mode)
            self.assertEqual(value.code, 1)
            self.assertNotIn("report", [row[0] for row in value.modes])
            self.assertEqual(value.modes[-1][0], mode)

    def test_supervisor_first_failures_unknown_inner_ownership_and_pre_kill_emptiness_never_complete(self):
        for fault in ("prepare", "volume", "prereturn", "report", "retained", "cleanup", "pre-kill", "source-after"):
            with self.subTest(fault=fault):
                value = self.supervisor_case(fault)
                self.assertEqual(value.code, 1)
                self.assertEqual(value.stored["result.json"]["status"], "failed")
                self.assertNotIn(b"private", policy.encoded(value.stored))
                if fault in ("prereturn", "retained", "pre-kill"):
                    self.assertNotIn("owner-cleanup", value.cleanup)
                self.assertEqual(value.cleanup[-1], "source-after")
                if fault == "prereturn":
                    self.assertIsNone(value.stored["scope.json"]["report_attempted"])
                    self.assertIsNone(value.stored["scope.json"]["report_returned"])


if __name__ == "__main__":
    unittest.main()
