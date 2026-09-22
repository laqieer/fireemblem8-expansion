"""Actual bounded observers with synthetic effects and real budget admissions."""

import errno
import builtins
import copy
import dataclasses
import hashlib
import importlib._bootstrap as import_bootstrap
import importlib._bootstrap_external as import_external
import io
from contextlib import AbstractContextManager, ExitStack, contextmanager
from functools import wraps
from importlib.machinery import ModuleSpec, SourceFileLoader
from pathlib import Path
import sys
import threading
from types import CodeType, ModuleType, SimpleNamespace
import unittest
import weakref
from unittest import mock

from scripts.ci_calibration import kernel, observation_failure, policy, root_stage, supervisor, worker
from scripts.ci_calibration.test_ci_calibration import Inert, budgeting


PREIMAGE_SOURCE_CLEANUP_COUNT = None
PREIMAGE_REPORT_ERROR_RECORD = None
PREIMAGE_REGISTRATION_MODULE = None
PREIMAGE_CODE_METHODS = None
PREIMAGE_ANCHOR_PROJECT = None
PREIMAGE_TRACELESS_PROJECT = None
PREIMAGE_LOCATION_OBSERVER = None
IMPORT_COMPILE_AUDIT = None
MAKE_CONTEXT_SOURCE = None
MAKE_CONTEXT_PARENT_PROJECT = None


class FrameFixture:
    def __init__(self, budget):
        self.budget = budget

    def _sandbox_run(self, *, mode="command", count=1, size=258, initial_count=1, initial_size=258,
                     producer=False, unsettled=False, missing=False, changed=None):
        observed = {
            "ok": False, "error": observation_failure.NATIVE_REJECTION,
            "observations": count, "observation_bytes": size,
        }
        config = {"mode": mode, "observation_count": initial_count, "observation_limit": initial_size,
                  "environment": {"SECRET": "private-environment"}, "report": "/private/scratch"}
        settled = {"observations": count, "observation_bytes": -1 if unsettled else size}
        channel = object() if producer else None
        producer_handler = object() if producer else None
        if changed:
            observed.update(changed)
        if missing:
            del observed
        raise budgeting.MakeProbeError("private native result; never export this message")


class ObservationFailureControls(Inert):
    def cleaned_error(self, failures=1, levels=1):
        class OwnershipError(RuntimeError):
            pass
        budget = self.budget()
        budget.plan(1)
        budget.charge("control", 8)
        budget.session_started = True
        inner = budgeting.MakeProbeError("private source refusal")
        closed = []
        def failing_close():
            closed.append("failed-close")
            raise OSError(errno.EIO, "private cleanup")
        try:
            try:
                raise inner
            except budgeting.MakeProbeError as error:
                budgeting.finish_cleanup(
                    [*([failing_close] * failures), budget.close, lambda: closed.append("later-close")],
                    primary=error,
                )
                if levels:
                    raise OwnershipError(str(error)) from error
                raise
        except BaseException as caught:
            outer = caught
        for _ in range(max(0, levels - 1)):
            try:
                raise OwnershipError(str(outer)) from outer
            except OwnershipError as caught:
                outer = caught
        self.assertTrue(budget.closed)
        self.assertEqual(closed, ["failed-close"] * failures + ["later-close"])
        return SimpleNamespace(inner=inner, outer=outer, budget=budget, closed=closed)

    def cleanup_wire(self, value):
        session = self.session(value.budget)
        measurement = SimpleNamespace(
            states={**dict.fromkeys(policy.REPORT_STATES, 0), "check_attempts": 1,
                    "session_attempts": 1, "session_constructed": 1, "completed": False},
            cleanup={**root_stage.cleanup_state(session, value.budget), "constructor_restored": True,
                     "report_released": True, "serialization_released": True,
                     "source_imports_restored": True, "source_imports_released": True},
            summary=None, serialized_bytes=None, secondary=[],
        )
        sampler = SimpleNamespace(snapshot=lambda: {"counters": policy.counter_snapshot(value.budget, session), "accounting": None})
        record = worker.report_error_record(value.outer, measurement, sampler, None, self.binding(), [])
        wire = io.BytesIO()
        with mock.patch.object(kernel, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=wire))):
            kernel.emit("12345/report", "error", record)
        parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES, report_binding=self.binding(), deadline=3700.0)
        row, = parser.feed(wire.getvalue())
        phase = {
            "mode": "report", "empty_before_outer_cleanup": True, "empty": True, "watchdog_reaped": True,
            "lifetime_writer_closed": True, "first_cause": {"type": "worker-error", "error": row["data"]},
        }
        self.assertTrue(parser.failed)
        self.assertFalse(parser.finished)
        self.assertNotIn(b"private", wire.getvalue())
        return row["data"], supervisor.report_retention(phase)

    def test_real_cleanup_survives_direct_wrapped_and_multilevel_wire_projection(self):
        for levels in (0, 1, 4):
            for failures in (0, 1, 2):
                with self.subTest(levels=levels, failures=failures):
                    value = self.cleaned_error(failures, levels)
                    self.assertEqual(len(getattr(value.inner, "cleanup_errors", ())), failures)
                    self.assertEqual(policy.source_cleanup_count(value.outer), failures)
                    record, retained = self.cleanup_wire(value)
                    self.assertEqual(record["source_cleanup_failures"], failures)
                    self.assertEqual(retained, failures != 0)
                    if levels:
                        self.assertEqual(record["error"]["chain"][0]["type"], "OwnershipError")
                        self.assertEqual(record["error"]["chain"][-1]["type"], "MakeProbeError")
                    self.assertNotIn("OSError", [row["type"] for row in record["error"]["chain"]])
                    self.assertFalse(record["states"]["completed"])

    def test_chain_and_metadata_aliases_never_double_count_or_guess_distinct_failures(self):
        value = self.cleaned_error(1)
        self.assertIs(value.outer.__cause__, value.inner)
        self.assertIs(value.outer.__context__, value.inner)
        value.outer.cleanup_errors = value.inner.cleanup_errors
        self.assertEqual(self.cleanup_wire(value)[0]["source_cleanup_failures"], 1)
        del value.outer.cleanup_errors
        other = self.cleaned_error(1).inner
        value.outer.__context__ = other
        self.assertEqual(self.cleanup_wire(value)[0]["source_cleanup_failures"], 2)
        # A shared descendant is an alias, not an ancestor cycle.
        other.__cause__ = value.inner
        self.assertEqual(self.cleanup_wire(value)[0]["source_cleanup_failures"], 2)
        value.outer.cleanup_errors = tuple(list(value.inner.cleanup_errors))
        self.assertIsNot(value.outer.cleanup_errors, value.inner.cleanup_errors)
        record, retained = self.cleanup_wire(value)
        self.assertIsNone(record["source_cleanup_failures"])
        self.assertTrue(retained)
        value = self.cleaned_error(1)
        message, = value.inner.cleanup_errors
        value.inner.cleanup_errors = (message, message)
        self.assertIsNone(self.cleanup_wire(value)[0]["source_cleanup_failures"])

    def test_cycles_truncation_and_malformed_metadata_remain_unavailable_on_the_wire(self):
        for fault in ("self-cycle", "ancestor-cycle", "too-deep", "list", "nonstring", "empty-string", "over-bound"):
            with self.subTest(fault=fault):
                value = self.cleaned_error(1)
                if fault == "self-cycle":
                    value.outer.__cause__ = value.outer
                elif fault == "ancestor-cycle":
                    value.inner.__context__ = value.outer
                elif fault == "too-deep":
                    for _ in range(32):
                        following = RuntimeError()
                        following.__cause__ = value.outer
                        value.outer = following
                elif fault == "list":
                    value.inner.cleanup_errors = ["private"]
                elif fault == "nonstring":
                    value.inner.cleanup_errors = (object(),)
                elif fault == "empty-string":
                    value.inner.cleanup_errors = ("",)
                else:
                    value.inner.cleanup_errors = ("private",) * (policy.ORIGINAL_LIMITS["entries"] + 1)
                record, retained = self.cleanup_wire(value)
                self.assertIsNone(record["source_cleanup_failures"])
                self.assertTrue(retained)
        for kind in (AttributeError, OSError):
            class Unreadable(RuntimeError):
                @property
                def cleanup_errors(self):
                    raise kind("private unavailable metadata")
            value = self.cleaned_error(0)
            value.outer.__context__ = Unreadable()
            record, retained = self.cleanup_wire(value)
            self.assertIsNone(record["source_cleanup_failures"])
            self.assertTrue(retained)
        class ForeignCause(RuntimeError):
            @property
            def __cause__(self):
                return object()
        value.outer = ForeignCause()
        record, retained = self.cleanup_wire(value)
        self.assertIsNone(record["source_cleanup_failures"])
        self.assertTrue(retained)

    def test_complete_chain_and_metadata_boundaries_distinguish_true_zero_from_unknown(self):
        value = self.cleaned_error(0, levels=0)
        for _ in range(31):
            following = RuntimeError()
            following.__cause__ = value.outer
            value.outer = following
        record, retained = self.cleanup_wire(value)
        self.assertEqual(record["source_cleanup_failures"], 0)
        self.assertFalse(retained)
        following = RuntimeError()
        following.__cause__ = value.outer
        value.outer = following
        record, retained = self.cleanup_wire(value)
        self.assertIsNone(record["source_cleanup_failures"])
        self.assertTrue(retained)
        for count in (policy.ORIGINAL_LIMITS["entries"], policy.ORIGINAL_LIMITS["entries"] + 1):
            value = self.cleaned_error(0)
            value.inner.cleanup_errors = tuple(f"private retained {index}" for index in range(count))
            record, retained = self.cleanup_wire(value)
            self.assertEqual(record["source_cleanup_failures"],
                             count if count == policy.ORIGINAL_LIMITS["entries"] else None)
            self.assertTrue(retained)

    def test_old_outer_only_helper_restores_wrapped_zero_and_neutral_wrapping_preserves_count(self):
        self.assertIsNotNone(PREIMAGE_SOURCE_CLEANUP_COUNT, "requires the inspected correction runner")
        value = self.cleaned_error(1)
        self.assertEqual(self.cleanup_wire(value)[0]["source_cleanup_failures"], 1)
        with mock.patch.object(policy, "source_cleanup_count", PREIMAGE_SOURCE_CLEANUP_COUNT):
            self.assertEqual(policy.source_cleanup_count(value.inner), 1)
            record, retained = self.cleanup_wire(value)
            self.assertEqual(record["source_cleanup_failures"], 0)
            self.assertFalse(retained)
            with self.assertRaises(AssertionError):
                self.assertEqual(record["source_cleanup_failures"], len(value.inner.cleanup_errors))
        value.outer.__context__ = None
        value.outer.add_note("private irrelevant note")
        record, retained = self.cleanup_wire(value)
        self.assertEqual(record["source_cleanup_failures"], 1)
        self.assertTrue(retained)

    def native(self, **changes):
        budget = self.budget()
        carrier = FrameFixture(budget)
        observer = observation_failure.Observer(FrameFixture, budget)
        try:
            carrier._sandbox_run(**changes)
        except budgeting.MakeProbeError as error:
            return error, observer, carrier
        raise AssertionError("missing modeled native rejection")

    def admission(self, *, diagnostic=False, category="control", before=31668852, request=2039626, other=0):
        budget = self.budget() if diagnostic else budgeting.ProbeBudget()
        budget.started = 100.0
        if other:
            budget.charge("snapshot", other)
        budget.charge(category, before)
        budget.runs, budget.states = 28, 1
        observer = observation_failure.Observer(FrameFixture, budget)
        try:
            budget.charge(category, request)
        except budgeting.MakeProbeError as error:
            budget.close()
            return error, observer, budget
        raise AssertionError("expected a real policy rejection")

    def test_actual_normal_control_refusal_retains_only_original_numeric_locals(self):
        error, observer, budget = self.admission()
        value = observer.budget_admission(error)
        self.assertIs(observation_failure.validate_fact(value, admission=True), value)
        self.assertEqual(value["status"], "observed")
        self.assertEqual((value["requested"], value["charged_before"], value["issued_cap"]),
                         (2039626, 31668852, 33554432))
        self.assertEqual((value["prospective_category"], value["remaining_category"], value["category_shortfall"]),
                         (33708478, 1885580, 154046))
        self.assertTrue(value["category_exhausted"])
        self.assertFalse(value["diagnostic_override"])
        self.assertIsNone(value["total_at_admission"])
        self.assertIsNone(value["total_predicate"])
        self.assertEqual(value["collected"], {
            "category_charged": 31668852, "total_charged": 31668852,
            "runs": 28, "states": 1, "closed": True, "owned_children": 0,
        })
        self.assertEqual(budget.bytes, {"control": 31668852})
        self.assertLess(len(policy.encoded(value)), 2048)

    def test_diagnostic_aggregate_refusal_is_not_mislabeled_as_control_overflow(self):
        error, observer, _ = self.admission(
            diagnostic=True, before=policy.POLICY_SENTINEL - 1, request=1, other=1,
        )
        value = observer.budget_admission(error)
        observation_failure.validate_fact(value, admission=True)
        self.assertEqual(value["issued_cap"], policy.POLICY_SENTINEL)
        self.assertFalse(value["category_exhausted"])
        self.assertEqual(value["category_shortfall"], 0)
        self.assertIsNone(value["total_at_admission"])
        self.assertEqual(value["collected"]["total_charged"], policy.POLICY_SENTINEL)
        self.assertTrue(value["diagnostic_override"])

    def test_admission_rejects_foreign_unsettled_malformed_and_unavailable_bindings(self):
        error, observer, budget = self.admission()
        wrong = observation_failure.Observer(FrameFixture, self.budget())
        self.assertEqual(wrong.budget_admission(error)["status"], "invalid")
        self.assertEqual(observer.budget_admission(RuntimeError("same words"))["status"], "unavailable")
        value = {"self": budget, "category": "control", "size": 2039626, "cap": 33554432, "used": 33708478}
        for name, changed in (
            ("self", object()), ("size", True), ("size", -1), ("size", "2039626"),
            ("cap", 0), ("cap", 33554431), ("used", 1), ("used", 1 << 63), ("category", []),
        ):
            with self.subTest(name=name):
                result = observer._admission_frame(error, {**value, name: changed})
                self.assertIn(result["status"], ("invalid", "unavailable"))
        budget.bytes["control"] -= 1
        self.assertEqual(observer.budget_admission(error)["status"], "invalid")
        unknown = observation_failure.Observer(FrameFixture, object())
        self.assertEqual(unknown.budget_admission(error), observation_failure.unavailable("admission-binding-not-ready"))

    def test_admission_chain_bound_and_duplicate_frame_preserve_unavailability(self):
        error, observer, budget = self.admission()
        try:
            raise error
        except budgeting.MakeProbeError as same:
            self.assertEqual(observer.budget_admission(same)["status"], "observed")
        error.__cause__ = error
        self.assertEqual(observer.budget_admission(error)["status"], "unavailable")
        error.__cause__ = None
        current = error
        for _ in range(33):
            following = RuntimeError()
            following.__cause__ = current
            current = following
        self.assertEqual(observer.budget_admission(current), observation_failure.unavailable("exception-chain-bound"))
        other, _, _ = self.admission()
        other.__cause__ = error
        self.assertEqual(observer.budget_admission(other)["status"], "invalid")

    def test_existing_native_count_bytes_and_make_unknown_are_preserved(self):
        for count, size, expected in ((1, 258, "count"), (2, 257, "bytes"), (1, 257, "both")):
            error, observer, _ = self.native(initial_count=count, initial_size=size)
            value = observer.capture(error)
            observation_failure.validate_fact(value, admission=False)
            self.assertEqual(value["status"], "attributed")
            self.assertEqual(value["exhaustion"], expected)
            self.assertEqual((value["observations"], value["observation_bytes"]), (1, 258))
        error, observer, _ = self.native(mode="make", initial_count=4, initial_size=4096, producer=True)
        result = observer.capture(error)
        observation_failure.validate_fact(result, admission=False)
        self.assertEqual(result["status"], "unknown")
        for name in ("effective_observation_count", "effective_observation_limit", "count_predicate",
                     "byte_predicate", "exhaustion"):
            self.assertIsNone(result[name])
        self.assertEqual(observer.budget_admission(error)["status"], "unavailable")

    def test_native_observer_never_accepts_foreign_or_missing_original_reports(self):
        for changes, expected in (
            ({"missing": True}, "unavailable"), ({"unsettled": True}, "invalid"),
            ({"changed": {"ok": False, "error": "different"}}, "unavailable"),
            ({"changed": {"observations": True}}, "invalid"), ({"changed": {"observation_bytes": 1}}, "invalid"),
            ({"producer": True}, "invalid"), ({"mode": "unknown"}, "invalid"),
        ):
            with self.subTest(changes=changes):
                error, observer, _ = self.native(**changes)
                self.assertEqual(observer.capture(error)["status"], expected)
        error, observer, carrier = self.native()
        class Other(FrameFixture):
            pass
        self.assertEqual(observation_failure.Observer(Other, carrier.budget).capture(error)["status"], "invalid")
        self.assertEqual(observation_failure.Observer(FrameFixture, object()).capture(error)["status"], "invalid")

    def test_error_protocol_retains_numeric_facts_without_private_data_or_completion(self):
        error, observer, _ = self.admission()
        primary = {
            "binding": self.binding(), "stage": "check", "error": policy.component_error_record(error),
            "states": {**dict.fromkeys(policy.REPORT_STATES, 0), "check_attempts": 1, "completed": False},
            "cleanup": None, "counters": None, "accounting": None, "secondary": [], "source_cleanup_failures": 0,
            "summary": None, "serialized_bytes": None,
            "source_locations": observation_failure.location_unavailable("binding-not-ready"),
            "budget_admission": observer.budget_admission(error),
            "observation_failure": observer.capture(error),
        }
        parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES, report_binding=self.binding(), deadline=3700.0)
        parser.feed(policy.encoded({"scope": "12345/report", "kind": "ready", "data": {}}) + b"\n")
        record, = parser.feed(policy.encoded({"scope": "12345/report", "kind": "error", "data": primary}) + b"\n")
        self.assertEqual(record["data"]["budget_admission"]["requested"], 2039626)
        self.assertFalse(parser.finished)
        for forbidden in (b"private", b"frames", b"environment", b"assembly"):
            self.assertNotIn(forbidden, policy.encoded(record))
        self.assertNotIn("locals", record["data"])
        self.assertNotIn("locals", record["data"]["budget_admission"])
        with self.assertRaises(policy.GuardError):
            policy.validate_report_result(record["data"], self.binding())
        self.assertEqual(error.args, ("aggregate control byte budget exhausted",))

    def test_closed_failure_facts_reject_raw_data_inferred_totals_and_effective_make_grants(self):
        error, observer, _ = self.admission()
        admitted = observer.budget_admission(error)
        for key, value in (
            ("total_at_admission", admitted["collected"]["total_charged"]),
            ("total_predicate", True), ("requested", True), ("charged_before", -1),
            ("category_exhausted", 1), ("category_shortfall", 0), ("category", "private"),
            ("semantics", "private environment"), ("extra", "private source"),
        ):
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                observation_failure.validate_fact({**admitted, key: value}, admission=True)
        changed = copy.deepcopy(admitted)
        changed["collected"]["category_charged"] += 1
        with self.assertRaises(policy.GuardError):
            observation_failure.validate_fact(changed, admission=True)
        error, observer, _ = self.native(mode="make", initial_count=4, initial_size=4096, producer=True)
        native = observer.capture(error)
        for key, value in (("effective_observation_count", 4), ("effective_observation_limit", 4096),
                           ("count_predicate", False), ("byte_predicate", False), ("exhaustion", "bytes"),
                           ("observations", True), ("status", "attributed"), ("extra", "private")):
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                observation_failure.validate_fact({**native, key: value}, admission=False)
        for unavailable in (
            {"status": "unavailable", "reason": "private"},
            {"status": "unknown", "reason": "no-matching-admission"},
            {"status": "invalid", "reason": [], "raw": "private"},
        ):
            with self.assertRaises(policy.GuardError):
                observation_failure.validate_fact(unavailable, admission=True)

    def test_original_source_cleanup_strings_are_counted_but_never_projected(self):
        first = RuntimeError("private source failure")
        self.assertEqual(policy.source_cleanup_count(first), 0)
        first.cleanup_errors = ("private source path", "private SDK output")
        self.assertEqual(policy.source_cleanup_count(first), 2)
        self.assertNotIn(b"private", policy.encoded(policy.component_error_record(first)))
        first.cleanup_errors = {"raw": "private"}
        self.assertIsNone(policy.source_cleanup_count(first))


class LocationControls(Inert):
    @contextmanager
    def fixture(self, kind="direct", *, callback=None, depth=0):
        package = "scripts.validation_ownership"
        def module(name, code):
            value = ModuleType(package + "." + name)
            path = "/repo/scripts/validation_ownership/" + name + ".py"
            value.__file__, value.__package__ = path, package
            value.__loader__ = SourceFileLoader(value.__name__, path)
            value.__spec__ = ModuleSpec(value.__name__, value.__loader__, origin=path)
            exec(compile(code, path, "exec"), value.__dict__)
            return value
        authority = module("authority", """
class GitTreeEntry:
    pass
class GitTreeEntries(dict):
    pass
class AuthorityLoader:
    pass
""")
        probing = module("make_probe", """
class ProbeSession:
    def _sandbox_run(self):
        raise RuntimeError("native method is not a fixture operation")
""")
        graph = module("graph_report", """
class ModelError(RuntimeError):
    pass
class WrappedError(RuntimeError):
    pass
touched = []
class Descriptor:
    def __get__(self, instance, owner):
        touched.append("descriptor")
        raise RuntimeError("private descriptor execution")
class Work:
    descriptor = Descriptor()
    @property
    def property(self):
        touched.append("property")
        raise RuntimeError("private property execution")
    def method(self):
        raise ModelError("private method source SDK argv environment")
    @staticmethod
    def static():
        raise ModelError("private static source")
    @classmethod
    def class_method(cls):
        raise ModelError("private class source")
def direct():
    raise ModelError("private source SDK argv environment key")
def outer():
    def nested():
        raise ModelError("private nested source")
    return nested()
def decorate(function):
    def guarded():
        return function()
    guarded.__wrapped__ = function
    return guarded
@decorate
def decorated():
    raise ModelError("private decorated source")
def recursive(depth):
    if depth:
        return recursive(depth - 1)
    direct()
def check(kind="direct", callback=None, depth=0):
    if kind == "direct":
        direct()
    elif kind == "nested":
        outer()
    elif kind == "method":
        Work().method()
    elif kind == "static":
        Work.static()
    elif kind == "class":
        Work.class_method()
    elif kind == "decorated":
        decorated()
    elif kind == "wrapped":
        try:
            direct()
        except ModelError as error:
            raise WrappedError("private wrapper") from error
    elif kind == "dynamic":
        exec("raise ModelError('private dynamic source')")
    elif kind == "dynamic-bound":
        exec(compile("def generated():\\n    raise ModelError('private dynamic source')", __file__, "exec"), globals())
        generated()
    elif kind == "foreign":
        callback()
    elif kind == "recursive":
        recursive(depth)
""")
        budget = self.budget()
        entries = authority.GitTreeEntries()
        entries.budget, entries.capture = budget, (Path("/repo"), policy.GRAPH)
        for value in (authority, probing, graph):
            relative = value.__file__.removeprefix("/repo/")
            entry = authority.GitTreeEntry()
            entry.path, entry.mode, entry.object_type = relative, "100644", "blob"
            entry.object_id, entry.git_dir = "a" * 40, None
            entries[relative] = entry
        loader = authority.AuthorityLoader()
        loader.root, loader.revision, loader.entries, loader.budget = Path("/repo"), policy.GRAPH, entries, budget
        session = probing.ProbeSession()
        session.budget, session.loader = budget, loader
        api = SimpleNamespace(
            module=graph, check=graph.check, session=probing.ProbeSession,
            loader=authority.AuthorityLoader, entries=authority.GitTreeEntries,
        )
        measurement = SimpleNamespace(
            api=api, root=Path("/repo"), budget=budget, session=session, session_valid=True, binding=self.binding(),
            states={**dict.fromkeys(policy.REPORT_STATES, 0), "check_attempts": 1,
                    "session_attempts": 1, "session_constructed": 1, "completed": False},
            cleanup=None, summary=None, serialized_bytes=None, secondary=[],
        )
        observer = observation_failure.Observer(probing.ProbeSession, budget)
        first = None
        with mock.patch.dict(sys.modules, {value.__name__: value for value in (authority, probing, graph)}):
            self.assertEqual(observer.register_locations(api), [])
            try:
                graph.check(kind, callback, depth)
            except BaseException as error:
                first = error
            self.assertIsNotNone(first)
            yield SimpleNamespace(
                graph=graph, authority=authority, probing=probing, entries=entries, loader=loader,
                session=session, budget=budget, measurement=measurement, observer=observer, error=first,
            )

    def wire(self, value):
        sampler = SimpleNamespace(snapshot=lambda: {
            "counters": policy.counter_snapshot(value.budget), "accounting": None,
        })
        record = worker.report_error_record(
            value.error, value.measurement, sampler, value.observer, self.binding(), getattr(value, "secondary", []),
        )
        data = io.BytesIO()
        with mock.patch.object(kernel, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=data))):
            kernel.emit("12345/report", "error", record)
        parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES, report_binding=self.binding(), deadline=3700.0)
        row, = parser.feed(data.getvalue())
        self.assertTrue(parser.failed)
        self.assertFalse(parser.finished)
        self.assertNotIn(b"private", data.getvalue())
        self.assertNotIn(b'"message"', data.getvalue())
        self.assertNotIn(b'"frames"', data.getvalue())
        return row["data"]

    def test_actual_direct_nested_methods_wrapped_and_decorated_locations_reach_protocol(self):
        for kind in ("direct", "nested", "method", "static", "class", "decorated", "wrapped"):
            with self.subTest(kind=kind), self.fixture(kind) as value:
                projected = self.wire(value)["source_locations"]
                self.assertEqual(projected["status"], "observed")
                self.assertTrue(projected["references_closed"])
                self.assertFalse(projected["authority"])
                current = value.error
                for index, row in enumerate(projected["locations"]):
                    trace = current.__traceback__
                    while trace.tb_next is not None:
                        trace = trace.tb_next
                    self.assertEqual(row, {
                        "exception": index, "relation": "primary" if index == 0 else "cause",
                        "file": trace.tb_frame.f_code.co_filename.removeprefix("/repo/"),
                        "code": trace.tb_frame.f_code.co_name,
                        "first_line": trace.tb_frame.f_code.co_firstlineno,
                        "line": trace.tb_lineno, "offset": trace.tb_lasti,
                    })
                    current = current.__cause__
                self.assertIsNone(current)
                self.assertEqual(value.graph.touched, [])
                self.assertEqual(value.observer.location_codes, {})
                self.assertLess(len(policy.encoded(projected)), policy.ERROR_BYTES)

    def test_foreign_dynamic_unraised_and_outside_public_call_stay_unavailable(self):
        def foreign():
            raise RuntimeError("private foreign source")
        for kind in ("foreign", "dynamic", "dynamic-bound"):
            with self.subTest(kind=kind), self.fixture(kind, callback=foreign) as value:
                projected = self.wire(value)["source_locations"]
                self.assertEqual(projected["status"], "unavailable")
                self.assertEqual(projected["locations"], [])
                self.assertTrue(projected["references_closed"])
        with self.fixture() as value:
            value.error = RuntimeError("private unraised error")
            self.assertEqual(self.wire(value)["source_locations"]["reason"], "no-source-trace")
        with self.fixture() as value:
            try:
                value.graph.direct()
            except BaseException as error:
                value.error = error
            self.assertEqual(self.wire(value)["source_locations"]["reason"], "public-call-unobserved")

    def test_late_code_registration_restoration_breaks_dynamic_rejection_and_original_aliases_are_neutral(self):
        with self.fixture("dynamic-bound") as value:
            with self.assertRaises(policy.GuardError):
                value.observer.register_locations(value.measurement.api)
            self.assertEqual(self.wire(value)["source_locations"]["reason"], "source-code-unbound")
        with self.fixture("dynamic-bound") as value, mock.patch.object(
            observation_failure._SourceLocations, "require_registered", return_value=None,
        ):
            self.assertEqual(self.wire(value)["source_locations"]["status"], "observed")
        with self.fixture() as value:
            value.graph.harmless_alias = value.graph.direct
            self.assertEqual(self.wire(value)["source_locations"]["status"], "observed")

    def test_exception_slot_inspection_does_not_invoke_candidate_descriptors(self):
        with self.fixture() as value:
            touched = []
            def descriptor(instance):
                touched.append(True)
                raise AssertionError("candidate descriptor executed")
            for name in ("__traceback__", "__cause__", "__context__"):
                setattr(value.graph.ModelError, name, property(descriptor))
            projected = value.observer.source_locations(value.error, value.measurement)
            self.assertEqual(projected["status"], "observed")
            self.assertEqual(touched, [])
            self.assertEqual(value.observer.location_codes, {})

    def test_wrong_root_revision_inventory_module_and_code_identity_do_not_borrow_a_location(self):
        for fault in ("root", "revision", "capture", "budget", "entry-budget", "missing-file", "symlink",
                      "gitlink", "missing-owner", "origin", "loader", "module-alias", "call", "code"):
            with self.subTest(fault=fault), self.fixture() as value:
                if fault == "root":
                    value.measurement.root = Path("/foreign")
                elif fault == "revision":
                    value.loader.revision = policy.BASE
                elif fault == "capture":
                    value.entries.capture = (Path("/repo"), policy.BASE)
                elif fault == "budget":
                    value.session.budget = object()
                elif fault == "entry-budget":
                    value.entries.budget = object()
                elif fault == "missing-file":
                    del value.entries["scripts/validation_ownership/graph_report.py"]
                elif fault == "symlink":
                    value.entries["scripts/validation_ownership/graph_report.py"].mode = "120000"
                elif fault == "gitlink":
                    value.entries["scripts/validation_ownership/graph_report.py"].git_dir = Path("/private/sdk")
                elif fault == "missing-owner":
                    del value.entries["scripts/validation_ownership/graph_report.py"].git_dir
                elif fault == "origin":
                    value.graph.__spec__.origin = "/foreign/source.py"
                elif fault == "loader":
                    class NoDescriptor:
                        def __getattribute__(self, name):
                            raise AssertionError("candidate descriptor executed")
                    value.graph.__loader__ = NoDescriptor()
                elif fault == "module-alias":
                    sys.modules[value.graph.__name__] = ModuleType(value.graph.__name__)
                elif fault == "call":
                    value.graph.check = value.graph.direct
                else:
                    original = value.graph.direct
                    value.graph.direct = type(original)(
                        original.__code__.replace(co_name="replacement"), original.__globals__, "replacement",
                    )
                projected = self.wire(value)["source_locations"]
                self.assertEqual(projected["status"], "unavailable")
                self.assertEqual(projected["locations"], [])

    def test_cycles_and_bounded_trace_chain_registration_or_output_fail_without_partial_success(self):
        for fault in ("cycle", "chain", "trace", "registry", "output"):
            with self.subTest(fault=fault), self.fixture("recursive" if fault == "trace" else "direct",
                                                       depth=270 if fault == "trace" else 0) as value:
                if fault == "cycle":
                    value.error.__cause__ = value.error
                elif fault in ("chain", "output"):
                    current = value.error
                    for _ in range(33 if fault == "chain" else 6):
                        try:
                            value.graph.check()
                        except BaseException as following:
                            current.__cause__ = following
                            current = following
                elif fault == "registry":
                    value.graph.__dict__.update({f"extra_{index}": None for index in range(policy.ORIGINAL_LIMITS["entries"])})
                with mock.patch.object(policy, "ERROR_BYTES", 512 if fault == "output" else policy.ERROR_BYTES):
                    projected = value.observer.source_locations(value.error, value.measurement)
                self.assertEqual(projected["status"], "unavailable")
                self.assertEqual(projected["locations"], [])
                self.assertTrue(projected["references_closed"])
                self.assertIn(projected["reason"], {
                    "cyclic-exception-chain", "exception-chain-bound", "trace-frame-bound",
                    "registration-bound", "location-size-bound",
                })

    def test_exact_exception_and_trace_limits_remain_observed_and_one_over_is_unavailable(self):
        for count in (31, 32, 33):
            with self.subTest(exceptions=count), self.fixture() as value:
                current = value.error
                for _ in range(count - 1):
                    try:
                        value.graph.check()
                    except BaseException as following:
                        current.__cause__ = following
                        current = following
                projected = self.wire(value)["source_locations"]
                self.assertEqual(projected["status"], "observed" if count <= 32 else "unavailable")
                self.assertEqual(len(projected["locations"]), count if count <= 32 else 0)
        for count in (255, 256, 257):
            with self.subTest(frames=count), self.fixture("recursive", depth=count - 4) as value:
                trace, actual = value.error.__traceback__, 0
                while trace is not None:
                    actual += 1
                    trace = trace.tb_next
                self.assertEqual(actual, count)
                projected = self.wire(value)["source_locations"]
                self.assertEqual(projected["status"], "observed" if count <= 256 else "unavailable")
                if count > 256:
                    self.assertEqual(projected["reason"], "trace-frame-bound")

    def test_exact_registration_capacity_and_one_below_cannot_silently_drop_code(self):
        with self.fixture() as value:
            registry = observation_failure._SourceLocations()
            registry.freeze(value.measurement.api, value.probing.ProbeSession)
            needed = registry.work
            registry.close()
            self.assertGreater(needed, 0)
            self.assertLess(needed, policy.ORIGINAL_LIMITS["entries"])
            for cap in (needed, needed - 1):
                with self.subTest(cap=cap), mock.patch.dict(policy.ORIGINAL_LIMITS, {"entries": cap}):
                    observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                    self.assertEqual(observer.register_locations(value.measurement.api), [])
                    projected = observer.source_locations(value.error, value.measurement)
                    self.assertEqual(projected["status"], "observed" if cap == needed else "unavailable")
                    if cap < needed:
                        self.assertEqual(projected["reason"], "registration-bound")
                    self.assertEqual(observer.location_codes, {})

    def test_registration_faults_preserve_all_known_secondaries_and_do_not_claim_earlier_withdrawal(self):
        original = observation_failure._SourceLocations
        for freeze_failed, clear_failed in ((True, False), (False, True), (True, True)):
            instances, attempts = [], []
            class Closing(dict):
                def clear(self):
                    attempts.append("codes")
                    super().clear()
                    if clear_failed:
                        raise OSError(errno.EIO, "private earlier withdrawal")
            class Registry(original):
                def __init__(self):
                    super().__init__()
                    self.codes = Closing()
                    instances.append(self)
                def freeze(self, api, session_type):
                    result = super().freeze(api, session_type)
                    if freeze_failed:
                        raise ValueError("private earlier registration")
                    return result
            with self.subTest(freeze=freeze_failed, close=clear_failed), self.fixture() as value:
                value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                with mock.patch.object(observation_failure, "_SourceLocations", Registry):
                    value.secondary = value.observer.register_locations(value.measurement.api)
                record = self.wire(value)
                self.assertEqual(record["error"]["chain"][0]["type"], type(value.error).__name__)
                self.assertEqual(record["source_locations"]["reason"], "locator-failed")
                self.assertIs(record["source_locations"]["references_closed"], None if clear_failed else True)
                self.assertEqual([row["error"]["chain"][0]["type"] for row in record["secondary"]],
                                 (["ValueError"] if freeze_failed else []) + (["OSError"] if clear_failed else []))
                self.assertEqual(attempts, ["codes"])
                instance, = instances
                self.assertFalse(instance.codes)
                self.assertFalse(instance.modules)
                self.assertFalse(instance.seen)
                self.assertFalse(instance.registered)
                for name in ("entries", "entry_type", "call", "call_globals"):
                    self.assertIsNone(getattr(instance, name))

    def test_locator_failure_does_not_erase_primary_accounting_or_independent_cleanup_metadata(self):
        with self.fixture() as value:
            first = value.error
            value.measurement.secondary = [{
                "stage": "budget-close", "error": policy.component_secondary_error(OSError(errno.EIO, "private close")),
            }]
            with mock.patch.object(value.observer, "source_locations", side_effect=ValueError("private locator")):
                record = self.wire(value)
            self.assertIs(value.error, first)
            self.assertEqual(record["error"]["chain"][0]["type"], type(first).__name__)
            self.assertEqual([row["stage"] for row in record["secondary"]], ["budget-close", "location-publication"])
            self.assertIsNotNone(record["counters"])
            self.assertEqual(record["source_locations"]["reason"], "locator-failed")
            self.assertIsNone(record["source_locations"]["references_closed"])

    def test_every_owned_location_reference_is_withdrawn_even_when_one_clear_fails(self):
        original = observation_failure._SourceLocations
        for failed in (None, "codes", "modules", "seen", "registered"):
            for when in ("before", "after"):
                events, instances = [], []
                class Clearing(dict):
                    def __init__(self, name, values=()):
                        super().__init__(values)
                        self.name = name
                    def clear(self):
                        events.append(self.name)
                        if self.name == failed and when == "before":
                            raise OSError(errno.EIO, "private withdrawal")
                        super().clear()
                        if self.name == failed and when == "after":
                            raise OSError(errno.EIO, "private withdrawal")
                class ClearingSet(set):
                    def clear(self):
                        events.append("seen")
                        if failed == "seen" and when == "before":
                            raise OSError(errno.EIO, "private withdrawal")
                        super().clear()
                        if failed == "seen" and when == "after":
                            raise OSError(errno.EIO, "private withdrawal")
                class Registry(original):
                    def __init__(self):
                        super().__init__()
                        self.codes, self.modules = Clearing("codes"), Clearing("modules")
                        self.seen = ClearingSet()
                        instances.append(self)
                with self.subTest(failed=failed, when=when), self.fixture() as value, \
                     mock.patch.object(observation_failure, "_SourceLocations", Registry):
                    value.observer.location_codes = Clearing("registered", value.observer.location_codes)
                    record = self.wire(value)
                    self.assertEqual(events, ["codes", "modules", "seen", "registered"])
                    instance, = instances
                    for name in ("entries", "entry_type", "call", "call_globals"):
                        self.assertIsNone(getattr(instance, name))
                    if failed != "seen" or when != "before":
                        self.assertFalse(instance.seen)
                    if failed is None:
                        self.assertEqual(record["source_locations"]["status"], "observed")
                    else:
                        self.assertEqual(record["source_locations"]["reason"], "locator-failed")
                        self.assertEqual(record["error"]["chain"][0]["type"], type(value.error).__name__)

    def test_original_error_collector_restoration_reproduces_missing_location_transport(self):
        self.assertIsNotNone(PREIMAGE_REPORT_ERROR_RECORD, "requires the inspected localization runner")
        with self.fixture() as value:
            self.assertEqual(self.wire(value)["source_locations"]["status"], "observed")
        with self.fixture() as value, mock.patch.object(
            worker, "report_error_record", PREIMAGE_REPORT_ERROR_RECORD,
        ), self.assertRaises(policy.GuardError):
            self.wire(value)

    def test_missing_location_or_wrong_binding_restoration_breaks_the_wire_oracle_and_neutral_order_passes(self):
        with self.fixture("wrapped") as value:
            record = self.wire(value)
            policy.validate_report_error(record, self.binding())
            for change in ("missing", "revision", "root", "authority", "position", "extra", "partial", "uncalled"):
                mutated = copy.deepcopy(record)
                if change == "missing":
                    del mutated["source_locations"]
                elif change == "revision":
                    mutated["source_locations"]["source_revision"] = policy.BASE
                elif change == "root":
                    mutated["source_locations"]["root"] = "/foreign"
                elif change == "authority":
                    mutated["source_locations"]["authority"] = True
                elif change == "position":
                    mutated["source_locations"]["locations"][0]["offset"] = True
                elif change == "partial":
                    mutated["source_locations"]["locations"].pop()
                elif change == "uncalled":
                    mutated["states"]["check_attempts"] = 0
                    mutated["states"]["session_attempts"] = 0
                    mutated["states"]["session_constructed"] = 0
                else:
                    mutated["source_locations"]["locations"][0]["message"] = "private"
                with self.subTest(change=change), self.assertRaises(policy.GuardError):
                    policy.validate_report_error(mutated, self.binding())
            neutral = dict(reversed(list(record.items())))
            neutral["source_locations"] = dict(reversed(list(record["source_locations"].items())))
            self.assertEqual(policy.validate_report_error(neutral, self.binding()), record)

    def test_observed_locations_survive_the_real_publication_fallback_and_cannot_be_changed_or_replayed(self):
        with self.fixture("wrapped") as value:
            record = self.wire(value)
            failure = worker.ReportFailure()
            failure.capture(value.error, "check")
            failure.record = record
            failure.secondary.append({
                "stage": "error-publication", "error": policy.component_secondary_error(OSError(errno.EIO, "private write")),
            })
            following = failure.fallback(
                {"scope": "12345/report", "report_binding": self.binding()}, value.error,
            )
            parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES,
                                         report_binding=self.binding(), deadline=3700.0)
            def send(data):
                return parser.feed(policy.encoded({"scope": "12345/report", "kind": "error", "data": data}) + b"\n")
            first, = send(record)
            updated, = send(following)
            self.assertIs(first["data"], updated["data"])
            self.assertEqual(updated["data"]["source_locations"], record["source_locations"])
            self.assertEqual(updated["data"]["error"], record["error"])
            self.assertTrue(parser.failed)
            self.assertFalse(parser.finished)
            with self.assertRaises(policy.GuardError):
                send(following)
            for field in ("line", "offset", "code"):
                changed = copy.deepcopy(following)
                row = changed["source_locations"]["locations"][0]
                row[field] = "replacement" if field == "code" else row[field] + 1
                with self.subTest(field=field), self.assertRaises(policy.GuardError):
                    policy.merge_report_failure(record, changed, self.binding())


class RegistrationControls(Inert):
    wire = LocationControls.wire

    @contextmanager
    def wrapper_fixture(self):
        with LocationControls.fixture(self) as value:
            value.graph.contextmanager = contextmanager
            exec(compile("""
@contextmanager
def source_scope(kind):
    if kind == "nested":
        def nested_boundary():
            raise ModelError("private nested generator")
        nested_boundary()
    elif kind == "wrapped":
        try:
            raise ModelError("private generator cause")
        except ModelError as error:
            raise WrappedError("private generator wrapper") from error
    else:
        raise ModelError("private original generator")
    yield
class ScopeWork:
    @contextmanager
    def scope(self):
        raise ModelError("private method generator")
        yield
def check(kind="direct"):
    if kind == "method":
        with ScopeWork().scope():
            pass
    else:
        with source_scope(kind):
            pass
""", value.graph.__file__, "exec"), value.graph.__dict__)
            value.measurement.api.check = value.graph.check
            value.original = value.graph.source_scope.__wrapped__
            value.method_original = value.graph.ScopeWork.__dict__["scope"].__wrapped__
            self.assertIs(value.original.__globals__, value.graph.__dict__)
            self.assertIsNot(value.graph.source_scope.__globals__, value.graph.__dict__)
            self.assertFalse(any(member is value.original for member in value.graph.__dict__.values()))
            value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
            yield value

    def register(self, value):
        value.secondary = value.observer.register_locations(value.measurement.api)
        self.assertEqual(value.secondary, [])

    def raise_check(self, value, kind="direct"):
        try:
            value.graph.check(kind)
        except BaseException as error:
            value.error = error
        else:
            self.fail("inert source boundary did not raise")

    def assert_observed(self, value):
        first = value.error
        record = self.wire(value)
        locations = record["source_locations"]
        self.assertEqual(locations["status"], "observed")
        self.assertIs(value.error, first)
        self.assertTrue(locations["references_closed"])
        self.assertFalse(locations["authority"])
        current = first
        for row in locations["locations"]:
            trace = current.__traceback__
            while trace.tb_next is not None:
                trace = trace.tb_next
            self.assertEqual(
                (row["file"], row["code"], row["line"], row["offset"]),
                (trace.tb_frame.f_code.co_filename.removeprefix("/repo/"),
                 trace.tb_frame.f_code.co_name, trace.tb_lineno, trace.tb_lasti),
            )
            current = current.__cause__
        self.assertIsNone(current)
        self.assertEqual(value.observer.location_codes, {})
        return record

    @staticmethod
    def forwarding(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            return function(*args, **kwargs)
        return wrapper

    def test_standard_contextmanager_originals_and_nested_methods_reach_protocol_without_alias(self):
        for kind in ("direct", "nested", "method", "wrapped"):
            for decorated in (False, True):
                with self.subTest(kind=kind, decorated=decorated), self.wrapper_fixture() as value:
                    if decorated:
                        value.graph.source_scope = self.forwarding(value.graph.source_scope)
                    self.register(value)
                    self.assertIsNone(value.observer.location_reason)
                    self.assertIn(id(value.original.__code__), value.observer.location_codes)
                    self.assertIn(id(value.method_original.__code__), value.observer.location_codes)
                    self.assertNotIn(id(value.graph.source_scope.__code__), value.observer.location_codes)
                    self.raise_check(value, kind)
                    self.assert_observed(value)

    def test_foreign_wrapper_code_cannot_borrow_its_originals_source_identity(self):
        for false_filename in (False, True):
            with self.subTest(false_filename=false_filename), self.wrapper_fixture() as value:
                original_wrapper = value.graph.source_scope
                @wraps(original_wrapper)
                def foreign(kind):
                    raise RuntimeError("private foreign wrapper")
                if false_filename:
                    foreign.__code__ = foreign.__code__.replace(co_filename=value.graph.__file__)
                value.graph.source_scope = foreign
                self.register(value)
                self.assertIn(id(value.original.__code__), value.observer.location_codes)
                self.assertNotIn(id(foreign.__code__), value.observer.location_codes)
                self.raise_check(value)
                record = self.wire(value)
                self.assertEqual(record["error"]["chain"][0]["type"], "RuntimeError")
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertEqual(record["source_locations"]["locations"], [])
                self.assertTrue(record["source_locations"]["references_closed"])

    def test_wrapper_cycles_depth_and_nonfunction_targets_are_bounded_on_both_passes(self):
        for stage in ("registration", "projection"):
            for fault in ("self-cycle", "cycle", "target", 31, 32, 33):
                with self.subTest(stage=stage, fault=fault), self.wrapper_fixture() as value:
                    callbacks = []
                    if stage == "projection":
                        self.register(value)
                    if fault == "self-cycle":
                        value.graph.source_scope.__wrapped__ = value.graph.source_scope
                    elif fault == "cycle":
                        value.original.__wrapped__ = value.graph.source_scope
                    elif fault == "target":
                        class Unsupported:
                            def __getattribute__(self, name):
                                callbacks.append(name)
                                return object.__getattribute__(self, name)
                            def __call__(self, *args):
                                callbacks.append("call")
                        value.graph.source_scope.__wrapped__ = Unsupported()
                    else:
                        for _ in range(fault - 2):
                            value.graph.source_scope = self.forwarding(value.graph.source_scope)
                    if stage == "registration":
                        self.register(value)
                    self.raise_check(value)
                    record = self.wire(value)
                    observed = type(fault) is int and fault <= 32
                    self.assertEqual(record["source_locations"]["status"], "observed" if observed else "unavailable")
                    self.assertEqual(callbacks, [])
                    self.assertTrue(record["source_locations"]["references_closed"])
                    self.assertEqual(value.observer.location_codes, {})
                    if not observed:
                        self.assertEqual(record["source_locations"]["reason"], {
                            "self-cycle": "cyclic-wrapper-chain", "cycle": "cyclic-wrapper-chain",
                            "target": "source-code-unbound", 33: "wrapper-chain-bound",
                        }[fault])

    def test_wrapped_original_module_file_and_frozen_code_cannot_be_replaced(self):
        for fault in ("module", "filename", "globals", "late-code", "late-wrapped"):
            with self.subTest(fault=fault), self.wrapper_fixture() as value:
                if fault.startswith("late-"):
                    self.register(value)
                original = value.original
                if fault == "module":
                    original.__module__ = "foreign"
                elif fault == "filename":
                    original.__code__ = original.__code__.replace(co_filename="/foreign/source.py")
                elif fault == "globals":
                    value.graph.source_scope.__wrapped__ = type(original)(
                        original.__code__, {"__name__": value.graph.__name__}, original.__name__,
                    )
                elif fault == "late-code":
                    original.__code__ = original.__code__.replace(co_name="late_generator")
                else:
                    value.graph.source_scope.__wrapped__ = type(original)(
                        original.__code__.replace(co_name="late_generator"), original.__globals__, original.__name__,
                    )
                if not fault.startswith("late-"):
                    self.register(value)
                self.raise_check(value)
                record = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertEqual(record["source_locations"]["locations"], [])
                self.assertEqual(record["error"]["chain"][0]["type"], "ModelError")
                self.assertTrue(record["source_locations"]["references_closed"])

    @staticmethod
    def callback_metadata(function, callbacks):
        class Metadata(dict):
            def get(self, *args):
                callbacks.append("get")
                return dict.get(self, *args)
            def __len__(self):
                callbacks.append("len")
                return dict.__len__(self)
            def __iter__(self):
                callbacks.append("iter")
                return dict.__iter__(self)
            def __getitem__(self, key):
                callbacks.append("getitem")
                return dict.__getitem__(self, key)
        function.__dict__ = Metadata(function.__dict__)

    def test_metadata_subclass_callbacks_never_run_at_registration_or_projection(self):
        for stage in ("registration", "projection"):
            for target in ("wrapper", "original", "direct"):
                with self.subTest(stage=stage, target=target), self.wrapper_fixture() as value:
                    if stage == "projection":
                        self.register(value)
                    callbacks = []
                    function = {
                        "wrapper": value.graph.source_scope, "original": value.original, "direct": value.graph.direct,
                    }[target]
                    self.callback_metadata(function, callbacks)
                    if stage == "registration":
                        self.register(value)
                    self.assertEqual(callbacks, [])
                    self.raise_check(value)
                    first = value.error
                    record = self.wire(value)
                    self.assertEqual(callbacks, [])
                    self.assertIs(value.error, first)
                    self.assertEqual(record["error"]["chain"][0]["type"], "ModelError")
                    self.assertEqual(record["source_locations"]["reason"], "function-metadata-unavailable")
                    self.assertTrue(record["source_locations"]["references_closed"])
                    self.assertIsNotNone(record["counters"])
                    self.assertEqual(value.observer.location_codes, {})

    def test_malformed_metadata_keys_and_oversized_attributes_fail_before_callbacks(self):
        for stage in ("registration", "projection"):
            for fault in ("colliding-key", "string-subclass", "capacity"):
                with self.subTest(stage=stage, fault=fault), self.wrapper_fixture() as value:
                    if stage == "projection":
                        self.register(value)
                    callbacks = []
                    class Key:
                        def __hash__(self):
                            callbacks.append("hash")
                            return hash("__wrapped__")
                        def __eq__(self, other):
                            callbacks.append("equal")
                            return False
                    class StringKey(str):
                        def __eq__(self, other):
                            callbacks.append("equal")
                            return str.__eq__(self, other)
                        __hash__ = str.__hash__
                    value.original.__dict__ = (
                        {f"entry{index}": None for index in range(policy.ORIGINAL_LIMITS["entries"])}
                        if fault == "capacity" else {Key() if fault == "colliding-key" else StringKey("__wrapped__"): None}
                    )
                    callbacks.clear()
                    if stage == "registration":
                        self.register(value)
                    self.assertEqual(callbacks, [])
                    self.raise_check(value)
                    record = self.wire(value)
                    self.assertEqual(callbacks, [])
                    self.assertEqual(record["source_locations"]["reason"],
                                     "registration-bound" if fault == "capacity" else "function-metadata-unavailable")
                    self.assertTrue(record["source_locations"]["references_closed"])

    def test_old_scanner_restoration_reproduces_both_review_witnesses(self):
        self.assertIsNotNone(PREIMAGE_REGISTRATION_MODULE, "requires the inspected registration correction runner")
        with self.wrapper_fixture() as value:
            self.register(value)
            self.raise_check(value)
            self.assert_observed(value)
        with mock.patch.object(observation_failure._SourceLocations, "module", PREIMAGE_REGISTRATION_MODULE):
            with self.wrapper_fixture() as value:
                self.register(value)
                self.assertNotIn(id(value.original.__code__), value.observer.location_codes)
                self.raise_check(value)
                self.assertEqual(self.wire(value)["source_locations"]["reason"], "source-code-unbound")
            with LocationControls.fixture(self) as value:
                callbacks = []
                self.callback_metadata(value.graph.direct, callbacks)
                value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                self.register(value)
                self.assertEqual(callbacks, ["get"])
                self.assertEqual(self.wire(value)["source_locations"]["status"], "observed")
                self.assertEqual(callbacks, ["get", "get"])

    def test_neutral_names_and_metadata_order_preserve_original_wrapper_locations(self):
        for change in ("name", "metadata-order", "module-order"):
            with self.subTest(change=change), self.wrapper_fixture() as value:
                if change == "name":
                    value.original.__name__ = "equivalent_generator_name"
                elif change == "metadata-order":
                    metadata = {**value.graph.source_scope.__dict__, "note": None}
                    value.graph.source_scope.__dict__ = dict(reversed(tuple(metadata.items())))
                else:
                    namespace = value.graph.__dict__
                    ordered = dict(reversed(tuple(namespace.items())))
                    namespace.clear()
                    namespace.update(ordered)
                self.register(value)
                self.raise_check(value, "nested")
                self.assert_observed(value)


class CodeMetadataControls(Inert):
    wire = LocationControls.wire
    register = RegistrationControls.register
    raise_check = RegistrationControls.raise_check

    @staticmethod
    def metadata_types(callbacks):
        class Text(str):
            def __eq__(self, other):
                callbacks.append("equal")
                return True
            def __ne__(self, other):
                callbacks.append("not-equal")
                return False
            def __hash__(self):
                callbacks.append("hash")
                return str.__hash__(self)
            def __str__(self):
                callbacks.append("coerce")
                return str.__str__(self)
        class Constants(tuple):
            def __len__(self):
                callbacks.append("length")
                return tuple.__len__(self)
            def __iter__(self):
                callbacks.append("iterate")
                return tuple.__iter__(self)
            def __getitem__(self, key):
                callbacks.append("getitem")
                return tuple.__getitem__(self, key)
        class Lines(bytes):
            def __len__(self):
                callbacks.append("length")
                return bytes.__len__(self)
            def __iter__(self):
                callbacks.append("iterate")
                return bytes.__iter__(self)
        return Text, Constants, Lines

    def alter_code(self, function, field, callbacks, *, nested=False):
        text, constants, lines = self.metadata_types(callbacks)
        outer = function.__code__
        code = next(value for value in outer.co_consts if type(value) is CodeType) if nested else outer
        changed = code.replace(**{field: {
            "co_filename": lambda: text("/foreign/private-code.py"),
            "co_name": lambda: text("private_code_name"),
            "co_consts": lambda: constants(code.co_consts),
            "co_linetable": lambda: lines(code.co_linetable),
        }[field]()})
        self.assertIs(type(getattr(changed, field)), {
            "co_filename": text, "co_name": text, "co_consts": constants, "co_linetable": lines,
        }[field])
        function.__code__ = outer.replace(
            co_consts=tuple(changed if value is code else value for value in outer.co_consts),
        ) if nested else changed
        self.assertEqual(callbacks, [])

    def assert_unavailable(self, value, callbacks, reason="code-metadata-unavailable"):
        first = value.error
        record = self.wire(value)
        self.assertEqual(callbacks, [])
        self.assertIs(value.error, first)
        self.assertEqual(record["error"]["chain"][0]["type"], type(first).__name__)
        self.assertEqual(record["source_locations"]["status"], "unavailable")
        self.assertEqual(record["source_locations"]["reason"], reason)
        self.assertEqual(record["source_locations"]["locations"], [])
        self.assertTrue(record["source_locations"]["references_closed"])
        self.assertIsNotNone(record["counters"])
        self.assertEqual(value.observer.location_codes, {})
        return record

    def test_exact_code_shapes_precede_root_and_nested_comparisons_on_both_passes(self):
        for stage in ("registration", "projection"):
            for nested in (False, True):
                for field in ("co_filename", "co_name", "co_consts", "co_linetable"):
                    with self.subTest(stage=stage, nested=nested, field=field), LocationControls.fixture(self) as value:
                        callbacks = []
                        function = value.graph.outer if nested else value.graph.direct
                        if stage == "registration":
                            value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                        self.alter_code(function, field, callbacks, nested=nested)
                        if stage == "registration":
                            self.register(value)
                        self.assertEqual(callbacks, [])
                        self.raise_check(value, "nested" if nested else "direct")
                        self.assert_unavailable(value, callbacks)

    def test_trace_leaf_metadata_is_checked_even_after_original_module_reference_restores(self):
        for field in ("co_filename", "co_name", "co_consts", "co_linetable"):
            with self.subTest(field=field), LocationControls.fixture(self) as value:
                original = value.graph.direct.__code__
                callbacks = []
                self.alter_code(value.graph.direct, field, callbacks)
                self.raise_check(value)
                value.graph.direct.__code__ = original
                self.assert_unavailable(value, callbacks)

    def test_existing_string_and_capture_guards_reject_subclasses_before_comparison_or_hash(self):
        for target in (
            "module-name", "module-file", "package", "spec-name", "spec-origin", "loader-path",
            "function-module", "entry-path", "entry-mode", "entry-kind", "entry-id", "revision", "capture",
        ):
            with self.subTest(target=target), LocationControls.fixture(self) as value:
                callbacks = []
                text, constants, _ = self.metadata_types(callbacks)
                entry = value.entries["scripts/validation_ownership/graph_report.py"]
                if target == "module-name":
                    value.graph.__name__ = text(value.graph.__name__)
                elif target == "module-file":
                    value.graph.__file__ = text(value.graph.__file__)
                elif target == "package":
                    value.graph.__package__ = text(value.graph.__package__)
                elif target in ("spec-name", "spec-origin"):
                    field = "name" if target == "spec-name" else "origin"
                    setattr(value.graph.__spec__, field, text(getattr(value.graph.__spec__, field)))
                elif target == "loader-path":
                    value.graph.__loader__.path = text(value.graph.__loader__.path)
                elif target == "function-module":
                    value.graph.direct.__module__ = text(value.graph.direct.__module__)
                elif target.startswith("entry-"):
                    field = {"entry-path": "path", "entry-mode": "mode", "entry-kind": "object_type", "entry-id": "object_id"}[target]
                    setattr(entry, field, text(getattr(entry, field)))
                elif target == "revision":
                    value.loader.revision = text(value.loader.revision)
                else:
                    value.entries.capture = constants(value.entries.capture)
                record = self.wire(value)
                self.assertEqual(callbacks, [])
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertEqual(record["source_locations"]["locations"], [])
                self.assertTrue(record["source_locations"]["references_closed"])

    def test_adjacent_metadata_keys_cannot_run_equality_during_original_ownership_lookup(self):
        for stage in ("registration", "projection"):
            for target in ("module", "spec", "loader", "instance", "entry", "entries", "class"):
                with self.subTest(stage=stage, target=target), LocationControls.fixture(self) as value:
                    callbacks = []
                    entry_path = "scripts/validation_ownership/graph_report.py"
                    mapping, key = {
                        "module": (value.graph.__dict__, "__name__"),
                        "spec": (value.graph.__spec__.__dict__, "origin"),
                        "loader": (value.graph.__loader__.__dict__, "path"),
                        "instance": (value.loader.__dict__, "revision"),
                        "entry": (value.entries[entry_path].__dict__, "path"),
                        "entries": (value.entries, entry_path),
                        "class": (dict(value.graph.Work.__dict__), "__module__"),
                    }[target]
                    class Key:
                        def __hash__(self):
                            callbacks.append("hash")
                            return hash(key)
                        def __eq__(self, other):
                            callbacks.append("equal")
                            return False
                    original = dict.pop(mapping, key)
                    mapping[Key()] = None
                    mapping[key] = original
                    if target == "class":
                        value.graph.Work = type("Work", (), mapping)
                    callbacks.clear()
                    if stage == "registration":
                        value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                        self.register(value)
                    self.assertEqual(callbacks, [])
                    self.raise_check(value)
                    self.assert_unavailable(value, callbacks, "source-metadata-unavailable")

    def test_metadata_refusal_keeps_primary_cleanup_and_publication_recovery_facts(self):
        for field in ("co_filename", "co_consts", "co_linetable"):
            with self.subTest(field=field), LocationControls.fixture(self) as value:
                callbacks = []
                self.alter_code(value.graph.direct, field, callbacks)
                self.raise_check(value)
                first = value.error
                first.cleanup_errors = ("private independent cleanup failure",)
                value.measurement.secondary = [
                    {"stage": stage, "error": policy.component_secondary_error(OSError(errno.EIO, "private close"))}
                    for stage in ("sampler-close", "budget-close")
                ]
                record = self.assert_unavailable(value, callbacks)
                self.assertEqual(record["source_cleanup_failures"], 1)
                self.assertEqual([row["stage"] for row in record["secondary"]], ["sampler-close", "budget-close"])
                failure = worker.ReportFailure()
                failure.capture(first, "check")
                failure.record = record
                failure.secondary.append({
                    "stage": "error-publication", "error": policy.component_secondary_error(OSError(errno.EIO, "private write")),
                })
                following = failure.fallback({"scope": "12345/report", "report_binding": self.binding()}, first)
                parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES,
                                             report_binding=self.binding(), deadline=3700.0)
                for data in (record, following):
                    parser.feed(policy.encoded({"scope": "12345/report", "kind": "error", "data": data}) + b"\n")
                self.assertTrue(parser.failed)
                self.assertFalse(parser.finished)
                self.assertEqual(parser.report_error["error"], record["error"])
                self.assertEqual(parser.report_error["source_cleanup_failures"], 1)
                self.assertEqual(parser.report_error["source_locations"], record["source_locations"])
                self.assertEqual(callbacks, [])
                self.assertNotIn(b"private", policy.encoded(parser.report_error))

    def test_unsupported_member_types_never_invoke_metaclass_comparison(self):
        for stage in ("registration", "projection"):
            with self.subTest(stage=stage), LocationControls.fixture(self) as value:
                callbacks = []
                class Meta(type):
                    def __eq__(self, other):
                        callbacks.append("type-equal")
                        return False
                    def __ne__(self, other):
                        callbacks.append("type-not-equal")
                        return True
                class Unsupported(metaclass=Meta):
                    pass
                value.graph.unrelated_member = Unsupported()
                if stage == "registration":
                    value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                    self.register(value)
                self.assertEqual(self.wire(value)["source_locations"]["status"], "observed")
                self.assertEqual(callbacks, [])
                with self.assertRaises(observation_failure._LocationUnavailable):
                    observation_failure._location_fields(value.graph.unrelated_member)
                self.assertEqual(callbacks, [])

    def test_restoring_exact_1e6_code_helpers_reproduces_callbacks_and_false_file_attribution(self):
        self.assertIsNotNone(PREIMAGE_CODE_METHODS, "requires the inspected code-metadata runner")
        with ExitStack() as restored:
            for name, method in PREIMAGE_CODE_METHODS.items():
                restored.enter_context(mock.patch.object(observation_failure._SourceLocations, name, method))
            for stage in ("registration", "projection"):
                with self.subTest(stage=stage), LocationControls.fixture(self) as value:
                    callbacks = []
                    self.alter_code(value.graph.direct, "co_filename", callbacks)
                    if stage == "registration":
                        value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                        self.register(value)
                    self.assertEqual(callbacks, ["equal", "not-equal"] if stage == "registration" else [])
                    self.raise_check(value)
                    record = self.wire(value)
                    self.assertEqual(callbacks, ["equal", "not-equal"] * (2 if stage == "registration" else 1))
                    if stage == "registration":
                        self.assertEqual(record["source_locations"]["status"], "observed")
                        self.assertEqual(record["source_locations"]["locations"][0]["file"],
                                         "scripts/validation_ownership/graph_report.py")
                    else:
                        self.assertEqual(record["source_locations"]["reason"], "source-code-unbound")
                    self.assertTrue(record["source_locations"]["references_closed"])
            with LocationControls.fixture(self) as value:
                callbacks = []
                self.alter_code(value.graph.direct, "co_consts", callbacks)
                value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                self.register(value)
                self.assertEqual(callbacks, ["length", "iterate"])
                self.raise_check(value)
                self.assertEqual(self.wire(value)["source_locations"]["status"], "observed")
                self.assertEqual(callbacks, ["length", "iterate"] * 2)

    def test_ordinary_strings_constant_tuples_and_neutral_code_names_remain_registered(self):
        for field in ("co_filename", "co_name", "co_consts", "co_linetable", "co_code"):
            with self.subTest(field=field), LocationControls.fixture(self) as value:
                code = value.graph.direct.__code__
                replacement = "equivalent_boundary" if field == "co_name" else getattr(code, field)
                value.graph.direct.__code__ = code.replace(**{field: replacement})
                value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
                self.register(value)
                self.raise_check(value)
                record = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "observed")
                self.assertEqual(record["source_locations"]["locations"][0]["code"],
                                 "equivalent_boundary" if field == "co_name" else "direct")
                self.assertTrue(record["source_locations"]["references_closed"])


class PartialAnchorControls(Inert):
    wire = LocationControls.wire

    @contextmanager
    def fixture(self, kind="late-original", *, eager=False, negative=None, execute=True, depth=0):
        with LocationControls.fixture(self) as value:
            events = []
            def module(name, body, **values):
                result = ModuleType("scripts.validation_ownership." + name)
                result.__file__ = "/repo/scripts/validation_ownership/" + name + ".py"
                result.__package__ = "scripts.validation_ownership"
                result.__loader__ = SourceFileLoader(result.__name__, result.__file__)
                result.__spec__ = ModuleSpec(result.__name__, result.__loader__, origin=result.__file__)
                result.__dict__.update(values)
                exec(compile(body, result.__file__, "exec"), result.__dict__)
                return result
            phase = module("phase_census", """
def analyze(depth=0):
    if depth:
        return analyze(depth - 1)
    raise ModelError("private synthetic late boundary")
""", ModelError=value.graph.ModelError)
            def model_make():
                events.append("inert-make-return")
            def model_load():
                events.append("inert-module-publication")
                sys.modules[phase.__name__] = phase
                if negative == "replacement":
                    phase.analyze.__code__ = phase.analyze.__code__.replace(co_name="replacement")
                return phase
            probe = module("graph_probe", """
def run_probe():
    model_make()
    phase = model_load()
    return phase.analyze(depth)
""", model_make=model_make, model_load=model_load, depth=depth)
            if kind == "wrapped-late":
                probe.__dict__.update(ModelError=value.graph.ModelError, WrappedError=value.graph.WrappedError)
                exec(compile("""
def run_probe():
    model_make()
    phase = model_load()
    try:
        return phase.analyze()
    except ModelError as error:
        raise WrappedError("private intermediate") from error
""", probe.__file__, "exec"), probe.__dict__)
            if kind == "late-forwards":
                probe.ModelError = value.graph.ModelError
                exec(compile("""
def source_boundary():
    raise ModelError("private registered boundary")
""", probe.__file__, "exec"), probe.__dict__)
                phase.probe = probe
                exec(compile("def analyze(depth=0):\n    return probe.source_boundary()\n",
                             phase.__file__, "exec"), phase.__dict__)
            if kind in {"abc-context", "ordinary-context", "abc-forwards"}:
                value.probing.__dict__.update(
                    Base=object if kind == "ordinary-context" else AbstractContextManager,
                    ModelError=value.graph.ModelError, fail_in_class=kind != "abc-forwards",
                )
                exec(compile("""
class _OwnedViewContext(Base):
    def _require_owner(self):
        if fail_in_class:
            raise ModelError("private context boundary")
    def __enter__(self):
        self._require_owner()
        return source_boundary()
    def __exit__(self, kind, value, trace):
        return False
def make_view():
    return _OwnedViewContext()
def source_boundary():
    raise ModelError("private registered context boundary")
""", value.probing.__file__, "exec"), value.probing.__dict__)
                value.graph.context_module = value.probing
                body = "def check():\n    with context_module.make_view():\n        pass\n"
            else:
                value.graph.probe = probe
                body = ("def check():\n    try:\n        probe.run_probe()\n    except WrappedError as error:\n"
                        "        raise ModelError('private outer') from error\n"
                        if kind == "wrapped-late" else "def check():\n    probe.run_probe()\n")
            exec(compile(body, value.graph.__file__, "exec"), value.graph.__dict__)
            value.measurement.api.check = value.graph.check
            for added in (probe, phase):
                entry = value.authority.GitTreeEntry()
                entry.path = added.__file__.removeprefix("/repo/")
                entry.mode, entry.object_type, entry.object_id, entry.git_dir = "100644", "blob", "a" * 40, None
                value.entries[entry.path] = entry
            value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
            value.events, value.phase, value.probe = events, phase, probe
            def invoke():
                try:
                    (phase.analyze if negative == "outside-scope" else value.graph.check)()
                except BaseException as error:
                    value.error = error
                else:
                    self.fail("inert source boundary did not raise")
                if negative == "foreign-root":
                    value.measurement.root = Path("/foreign")
                elif negative == "foreign-entry":
                    value.entries["scripts/validation_ownership/graph_report.py"].mode = "120000"
                elif negative == "caller-module":
                    sys.modules[probe.__name__] = ModuleType(probe.__name__)
                elif negative == "cycle":
                    value.error.__cause__ = value.error
                return value.error
            value.invoke = invoke
            with mock.patch.dict(sys.modules, {probe.__name__: probe}):
                self.assertNotIn(phase.__name__, sys.modules)
                if eager:
                    sys.modules[phase.__name__] = phase
                if execute:
                    self.assertEqual(value.observer.register_locations(value.measurement.api), [])
                    self.assertIsNone(value.observer.location_reason)
                    events.append("original-freeze")
                    invoke()
                yield value

    def check_anchors(self, value, record):
        current, index, actual = value.error, 0, {}
        while current is not None:
            trace = current.__traceback__
            while trace is not None:
                code = trace.tb_frame.f_code
                actual[(index, code.co_filename.removeprefix("/repo/"), code.co_name, trace.tb_lineno, trace.tb_lasti)] = (
                    "registered-raising-frame" if trace.tb_next is None else "registered-caller"
                )
                trace = trace.tb_next
            current = current.__cause__ if current.__cause__ is not None else current.__context__
            index += 1
        locations = record["source_locations"]
        self.assertEqual(locations["version"], 3)
        self.assertFalse(locations["authority"])
        self.assertTrue(locations["references_closed"])
        for row in locations["anchors"]:
            self.assertEqual(row["role"], actual[(row["exception"], row["file"], row["code"], row["line"], row["offset"])])
        self.assertEqual(value.observer.location_codes, {})

    def test_actual_wire_preserves_all_fourteen_triage_shapes(self):
        cases = [
            ("late-original", False, None, 0, False),
            ("late-original", True, None, 0, True),
            ("wrapped-late", False, None, 0, False),
            ("abc-context", False, None, 0, False),
            ("ordinary-context", False, None, 0, True),
            ("late-forwards", False, None, 0, True),
            ("abc-forwards", False, None, 0, True),
            ("late-original", False, "replacement", 0, False),
            ("late-original", False, "foreign-root", 0, False),
            ("late-original", False, "foreign-entry", 0, False),
            ("late-original", False, "caller-module", 0, False),
            ("late-original", False, "cycle", 0, False),
            ("late-original", False, None, 260, False),
            ("late-original", True, "outside-scope", 0, False),
        ]
        for kind, eager, negative, depth, observed in cases:
            with self.subTest(kind=kind, eager=eager, negative=negative, depth=depth), \
                 self.fixture(kind, eager=eager, negative=negative, depth=depth) as value:
                first = value.error
                record = self.wire(value)
                self.assertIs(value.error, first)
                locations = record["source_locations"]
                self.assertEqual(locations["status"], "observed" if observed else "unavailable")
                if observed:
                    self.assertEqual(locations["anchors"], [])
                    self.assertTrue(locations["locations"])
                elif negative in {None, "replacement"} and not depth:
                    self.assertEqual(locations["reason"], "source-code-unbound")
                    self.assertEqual(locations["locations"], [])
                    self.assertTrue(locations["anchors"])
                    self.check_anchors(value, record)
                    self.assertTrue(any(row["role"] == "registered-caller" for row in locations["anchors"]))
                    if kind == "wrapped-late":
                        self.assertEqual([row["role"] for row in locations["anchors"]],
                                         ["registered-raising-frame", "registered-raising-frame", "registered-caller"])
                else:
                    self.assertEqual(locations["anchors"], [])
                self.assertTrue(locations["references_closed"])
                self.assertEqual(value.observer.location_codes, {})

    def test_partial_schema_rejects_roles_indices_bindings_unclosed_and_unavailable_overclaims(self):
        with self.fixture("wrapped-late") as value:
            record = self.wire(value)
            mutations = (
                lambda row: row["source_locations"].pop("anchors"),
                lambda row: row["source_locations"].update(version=1),
                lambda row: row["source_locations"].update(authority=True),
                lambda row: row["source_locations"].update(status="observed", reason=None),
                lambda row: row["source_locations"].update(reason="source-file-unowned"),
                lambda row: row["source_locations"].update(source_revision=policy.BASE),
                lambda row: row["source_locations"].update(root="/foreign"),
                lambda row: row["source_locations"].update(references_closed=None),
                lambda row: row["source_locations"]["anchors"][0].update(role="raising"),
                lambda row: row["source_locations"]["anchors"][2].update(role="registered-raising-frame"),
                lambda row: row["source_locations"]["anchors"][0].update(exception=True),
                lambda row: row["source_locations"]["anchors"][0].update(relation="cause"),
                lambda row: row["source_locations"]["anchors"][1].update(relation="primary"),
                lambda row: row["source_locations"]["anchors"][2].update(exception=3),
                lambda row: row["source_locations"]["anchors"][1].update(exception=0),
                lambda row: row["source_locations"]["anchors"].reverse(),
                lambda row: row["source_locations"]["anchors"][0].update(file="../foreign"),
                lambda row: row["source_locations"]["anchors"][0].update(offset=True),
                lambda row: row["source_locations"]["anchors"][0].update(message="private"),
                lambda row: row["source_locations"].update(anchors=row["source_locations"]["anchors"] * 12),
                lambda row: row.update(states=None),
                lambda row: row["states"].update(check_attempts=0, session_attempts=0, session_constructed=0),
            )
            for index, mutate in enumerate(mutations):
                changed = copy.deepcopy(record)
                mutate(changed)
                with self.subTest(index=index), self.assertRaises(policy.GuardError):
                    parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES,
                                                 report_binding=self.binding(), deadline=3700.0)
                    parser.feed(policy.encoded({"scope": "12345/report", "kind": "error", "data": changed}) + b"\n")
            neutral = json_order(record)
            self.assertEqual(policy.validate_report_error(neutral, self.binding()), record)

    def test_context_relations_and_sparse_original_indices_remain_honest(self):
        with self.fixture("wrapped-late") as value:
            value.error.__cause__ = None
            record = self.wire(value)
            self.assertEqual([row["relation"] for row in record["source_locations"]["anchors"]],
                             ["primary", "context", "cause"])
            sparse = copy.deepcopy(record)
            sparse["source_locations"]["anchors"].pop(1)
            policy.validate_report_error(sparse, self.binding())
            self.assertEqual([row["exception"] for row in sparse["source_locations"]["anchors"]], [0, 2])

    def test_original_code_with_foreign_globals_cannot_supply_partial_anchors(self):
        with self.fixture(execute=False) as value:
            self.assertEqual(value.observer.register_locations(value.measurement.api), [])
            original = value.probe.run_probe
            foreign = type(original)(original.__code__, dict(original.__globals__), original.__name__)
            self.assertIs(foreign.__code__, original.__code__)
            self.assertIsNot(foreign.__globals__, original.__globals__)
            value.graph.probe = SimpleNamespace(run_probe=foreign)
            value.invoke()
            record = self.wire(value)
            self.assertEqual(record["source_locations"]["reason"], "source-code-unbound")
            self.assertEqual(record["source_locations"]["anchors"], [])

    def test_foreign_or_malformed_leaf_never_receives_caller_anchors(self):
        for fault in ("foreign-file", "malformed-name", "position", "metadata", "unraised"):
            with self.subTest(fault=fault), self.fixture(execute=False) as value:
                self.assertEqual(value.observer.register_locations(value.measurement.api), [])
                original = value.phase.analyze.__code__
                callbacks = []
                if fault == "foreign-file":
                    value.phase.analyze.__code__ = original.replace(co_filename="/foreign/source.py")
                elif fault == "malformed-name":
                    value.phase.analyze.__code__ = original.replace(co_name="private invalid name")
                elif fault == "position":
                    value.phase.analyze.__code__ = original.replace(co_firstlineno=policy.ORIGINAL_LIMITS["file_bytes"] + 1)
                elif fault == "metadata":
                    class Text(str):
                        def __eq__(self, other):
                            callbacks.append("equal")
                            return True
                    value.phase.analyze.__code__ = original.replace(co_filename=Text(value.phase.__file__))
                value.invoke()
                if fault == "unraised":
                    value.error = RuntimeError("private unraised")
                record = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertEqual(record["source_locations"]["anchors"], [])
                self.assertEqual(callbacks, [])

    def test_no_eligible_frame_is_not_invented_and_neutral_aliases_preserve_anchors(self):
        with self.fixture("wrapped-late") as value:
            previous = value.error
            try:
                value.phase.analyze()
            except BaseException as error:
                error.__cause__ = previous
                value.error = error
            value.probe.harmless_alias = value.probe.run_probe
            record = self.wire(value)
            self.assertEqual([row["exception"] for row in record["source_locations"]["anchors"]], [1, 2, 3])
            self.assertEqual([row["relation"] for row in record["source_locations"]["anchors"]], ["cause"] * 3)
            self.check_anchors(value, record)

    def test_total_frame_chain_entry_and_envelope_bounds_do_not_reset_for_anchors(self):
        for count in (32, 33):
            with self.subTest(exceptions=count), self.fixture() as value:
                current = value.error
                for _ in range(count - 1):
                    try:
                        value.graph.check()
                    except BaseException as error:
                        current.__cause__ = error
                        current = error
                record = self.wire(value)
                self.assertEqual(len(record["source_locations"]["anchors"]), count if count == 32 else 0)
                if count == 33:
                    self.assertEqual(record["source_locations"]["reason"], "exception-chain-bound")
        for frames in (255, 256, 257):
            with self.subTest(frames=frames), self.fixture(depth=frames - 4) as value:
                record = self.wire(value)
                self.assertEqual(bool(record["source_locations"]["anchors"]), frames <= 256)
                if frames > 256:
                    self.assertEqual(record["source_locations"]["reason"], "trace-frame-bound")
        with self.fixture(depth=126) as value:
            try:
                value.graph.check()
            except BaseException as following:
                value.error.__cause__ = following
            record = self.wire(value)
            self.assertEqual(record["source_locations"]["reason"], "trace-frame-bound")
            self.assertEqual(record["source_locations"]["anchors"], [])
        with self.fixture("wrapped-late") as value:
            locations = self.wire(value)["source_locations"]
            size = len(policy.encoded(locations))
        for bound in (size, size - 1):
            with self.subTest(bytes=bound), self.fixture("wrapped-late") as value, \
                 mock.patch.object(policy, "ERROR_BYTES", bound):
                projected = value.observer.source_locations(value.error, value.measurement)
                self.assertEqual(bool(projected["anchors"]), bound == size)
                if bound < size:
                    self.assertEqual(projected["reason"], "location-size-bound")
        with self.fixture() as value:
            value.phase.__dict__.update({f"bound_{index}": None for index in range(policy.ORIGINAL_LIMITS["entries"])})
            record = self.wire(value)
            self.assertEqual(record["source_locations"]["reason"], "registration-bound")
            self.assertEqual(record["source_locations"]["anchors"], [])

    def test_old_project_restoration_loses_partial_anchors_but_complete_observations_stay_green(self):
        self.assertIsNotNone(PREIMAGE_ANCHOR_PROJECT, "requires the inspected partial-anchor runner")
        with self.fixture() as value:
            self.assertTrue(self.wire(value)["source_locations"]["anchors"])
        with mock.patch.object(observation_failure._SourceLocations, "project", PREIMAGE_ANCHOR_PROJECT):
            with self.fixture() as value:
                old = self.wire(value)["source_locations"]
                self.assertEqual(old["reason"], "source-code-unbound")
                self.assertEqual(old["anchors"], [])
            with self.fixture(eager=True) as value:
                complete = self.wire(value)["source_locations"]
                self.assertEqual(complete["status"], "observed")
                self.assertEqual(complete["anchors"], [])

    def test_partial_projection_and_every_withdrawal_fault_preserve_primary_and_other_facts(self):
        original = observation_failure._SourceLocations
        for field in ("codes", "modules", "seen", "registered"):
            for when in ("before", "after"):
                events, instances = [], []
                class Clearing(dict):
                    def __init__(self, name, values=()):
                        super().__init__(values)
                        self.name = name
                    def clear(self):
                        events.append(self.name)
                        if self.name == field and when == "before":
                            raise OSError(errno.EIO, "private withdrawal")
                        super().clear()
                        if self.name == field and when == "after":
                            raise OSError(errno.EIO, "private withdrawal")
                class ClearingSet(set):
                    def clear(self):
                        events.append("seen")
                        if field == "seen" and when == "before":
                            raise OSError(errno.EIO, "private withdrawal")
                        super().clear()
                        if field == "seen" and when == "after":
                            raise OSError(errno.EIO, "private withdrawal")
                class Registry(original):
                    def __init__(self):
                        super().__init__()
                        self.codes, self.modules, self.seen = Clearing("codes"), Clearing("modules"), ClearingSet()
                        instances.append(self)
                with self.subTest(field=field, when=when), self.fixture("wrapped-late") as value, \
                     mock.patch.object(observation_failure, "_SourceLocations", Registry):
                    value.observer.location_codes = Clearing("registered", value.observer.location_codes)
                    first = value.error
                    record = self.wire(value)
                    self.assertIs(value.error, first)
                    self.assertEqual(events, ["codes", "modules", "seen", "registered"])
                    instance, = instances
                    for name in ("entries", "entry_type", "call", "call_globals"):
                        self.assertIsNone(getattr(instance, name))
                    self.assertEqual(record["source_locations"]["anchors"], [])
                    self.assertIsNone(record["source_locations"]["references_closed"])
                    self.assertEqual(record["secondary"][-1]["stage"], "location-publication")
                    self.assertIsNotNone(record["counters"])

    def entrypoint_case(self, *faults):
        with self.fixture("wrapped-late", execute=False) as value:
            faults, events = set(faults), value.events
            class Measurement(SimpleNamespace):
                pass
            value.measurement = Measurement(**vars(value.measurement))
            value.measurement.first = None
            value.measurement.first_stage = "check"
            value.measurement.raw_report = value.measurement.raw_serialization = None
            value.graph.ProbeSession = value.probing.ProbeSession
            value.session.__dict__.update(vars(self.session(value.budget)))
            original_close = value.budget.close
            def run():
                events.append("model-check")
                value.budget.session_started = True
                observer = value.measurement.observer
                observer.start_imports(value.measurement)
                try:
                    observer.bind_imports(value.measurement)
                    error = value.invoke()
                finally:
                    observer.restore_imports()
                value.measurement.first = error
                original_close()
                value.measurement.cleanup = {
                    **root_stage.cleanup_state(value.session, value.budget),
                    "constructor_restored": value.graph.ProbeSession is value.probing.ProbeSession,
                    "report_released": value.measurement.raw_report is None,
                    "serialization_released": value.measurement.raw_serialization is None,
                    **observer.import_cleanup(),
                }
                raise error
            value.measurement.run = run
            def measurement(root, issued, config, sampler, changes):
                self.assertIs(issued, value.budget)
                self.assertEqual(root, Path("/repo"))
                self.assertEqual(changes, self.changes())
                sampler.session = value.session
                return value.measurement
            def git(root, issued, *args):
                self.assertIs(issued, value.budget)
                if args == ("rev-parse", "HEAD"):
                    return policy.GRAPH.encode()
                if args == ("rev-parse", policy.BASE + "^{commit}"):
                    return policy.BASE.encode()
                return b"".join(kind.encode() + b"\0" + path.encode() + b"\0" for path, kind in self.changes().items())
            value.authority.git = git
            actual_import = builtins.__import__
            def imports(name, *args, **kwargs):
                if name == "scripts.validation_ownership.authority":
                    return value.authority
                if name == "scripts.validation_ownership.budget":
                    return budgeting
                if name == "scripts.validation_ownership.make_probe":
                    return value.probing
                return actual_import(name, *args, **kwargs)
            def sampler_close(sampler):
                events.append("sampler-close")
                if "sampler-close" in faults:
                    raise OSError(errno.EIO, "private sampler")
            def budget_close():
                events.append("budget-close")
                if "budget-close-before" in faults:
                    raise OSError(errno.EIO, "private budget")
                original_close()
                if "budget-close-after" in faults:
                    raise OSError(errno.EIO, "private budget")
            raw, stderr, error_emits = io.BytesIO(), io.StringIO(), []
            original_emit = kernel.emit
            def emit(scope, kind, record):
                if kind == "error":
                    error_emits.append(record)
                    if "publication-always" in faults or len(error_emits) == 1 and "publication-before" in faults:
                        raise OSError(errno.EIO, "private publication")
                original_emit(scope, kind, record)
                if kind == "error" and len(error_emits) == 1 and "publication-after" in faults:
                    raise OSError(errno.EIO, "private publication")
            config = {"mode": "report", "scope": "12345/report", "deadline": 3700.0, "report_binding": self.binding()}
            value.measurement.config = config
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(worker, "require_contained", return_value={}))
                stack.enter_context(mock.patch.object(root_stage, "candidate_api", return_value=value.measurement.api))
                stack.enter_context(mock.patch.object(root_stage, "ReportMeasurement", side_effect=measurement))
                stack.enter_context(mock.patch.object(builtins, "__import__", side_effect=imports))
                stack.enter_context(mock.patch.object(sys, "path", list(sys.path)))
                stack.enter_context(mock.patch.object(threading.Thread, "start", return_value=None))
                stack.enter_context(mock.patch.object(worker.Sampler, "close", sampler_close))
                stack.enter_context(mock.patch.object(value.budget, "close", side_effect=budget_close))
                stack.enter_context(mock.patch.object(worker, "calibration_budget", return_value=(
                    value.budget, value.budget.limits, dataclasses.asdict(budgeting.Limits()),
                    policy.profile_manifest(policy.ORIGINAL_LIMITS, observation_count=32768),
                )))
                stack.enter_context(mock.patch.object(kernel, "owned_config", return_value=config))
                stack.enter_context(mock.patch.object(sys, "argv", ["worker.py", "/guard/config.json"]))
                stack.enter_context(mock.patch.object(sys, "stderr", stderr))
                stack.enter_context(mock.patch.object(kernel, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=raw))))
                stack.enter_context(mock.patch.object(kernel, "emit", side_effect=emit))
                if "locator" in faults:
                    stack.enter_context(mock.patch.object(observation_failure._SourceLocations, "project",
                                                         side_effect=ValueError("private locator")))
                if "formatter" in faults:
                    stack.enter_context(mock.patch.object(policy, "component_error_record",
                                                         side_effect=ValueError("private formatter")))
                if "record" in faults:
                    stack.enter_context(mock.patch.object(worker, "report_error_record",
                                                         side_effect=ValueError("private record")))
                code = worker.entrypoint()
            parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES,
                                         report_binding=self.binding(), deadline=3700.0)
            rows = []
            for line in raw.getvalue().splitlines(keepends=True):
                rows.extend(parser.feed(line))
            self.assertNotIn(b"private", raw.getvalue())
            self.assertNotIn("private", stderr.getvalue())
            return SimpleNamespace(code=code, parser=parser, rows=rows, raw=raw.getvalue(), stderr=stderr.getvalue(),
                                   events=list(events), first=value.error, errors=error_emits)

    def test_actual_entrypoint_publication_close_and_locator_fault_combinations(self):
        for publication in (None, "publication-before", "publication-after", "publication-always"):
            for observer_fault in (None, "locator", "formatter", "record"):
                faults = ["sampler-close", "budget-close-before"]
                faults.extend(value for value in (publication, observer_fault) if value is not None)
                with self.subTest(publication=publication, observer=observer_fault):
                    value = self.entrypoint_case(*faults)
                    self.assertEqual(value.code, 1)
                    self.assertFalse(value.parser.finished)
                    self.assertEqual(value.events[-2:], ["sampler-close", "budget-close"])
                    errors = [row["data"] for row in value.rows if row["kind"] == "error"]
                    if publication == "publication-always" or (
                        publication == "publication-before" and observer_fault == "record"
                    ):
                        self.assertEqual(errors, [])
                        self.assertFalse(value.parser.failed)
                        self.assertTrue(value.stderr)
                        continue
                    self.assertTrue(value.parser.failed)
                    error = errors[-1]
                    stages = [row["stage"] for row in error["secondary"]]
                    self.assertIn("sampler-close", stages)
                    self.assertIn("budget-close", stages)
                    if observer_fault != "formatter":
                        self.assertEqual(error["error"]["chain"][0]["type"], type(value.first).__name__)
                    if observer_fault == "record":
                        self.assertEqual(error["source_locations"]["anchors"], [])
                        self.assertIsNone(error["counters"])
                    else:
                        self.assertIsNotNone(error["counters"])
                        self.assertIsNotNone(error["cleanup"])
                        self.assertEqual(bool(error["source_locations"]["anchors"]), observer_fault != "locator")
                    if observer_fault == "locator":
                        self.assertIn("location-publication", stages)
                        self.assertIsNone(error["source_locations"]["references_closed"])
                    if publication is not None:
                        self.assertIn("error-publication", stages)

    def test_anchors_survive_after_write_recovery_without_replay_or_completion(self):
        value = self.entrypoint_case("publication-after")
        errors = [row["data"] for row in value.rows if row["kind"] == "error"]
        self.assertEqual(len(errors), 2)
        self.assertIs(errors[0], errors[1])
        self.assertEqual(len(errors[1]["source_locations"]["anchors"]), 3)
        self.assertEqual(value.parser.report_error_records, 2)
        with self.assertRaises(policy.GuardError):
            value.parser.feed(value.raw.splitlines(keepends=True)[-1])
        for field, replacement in (("role", "registered-caller"), ("line", 1), ("exception", 1)):
            before = copy.deepcopy(value.errors[0])
            after = copy.deepcopy(value.errors[-1])
            after["source_locations"]["anchors"][0][field] = replacement
            with self.subTest(field=field), self.assertRaises(policy.GuardError):
                policy.merge_report_failure(before, after, self.binding())
        after_close = self.entrypoint_case("sampler-close", "budget-close-after", "publication-after")
        error = [row["data"] for row in after_close.rows if row["kind"] == "error"][-1]
        self.assertTrue(error["source_locations"]["anchors"])
        self.assertEqual(error["cleanup"]["budget_closed"], True)
        self.assertCountEqual([row["stage"] for row in error["secondary"]],
                              ["sampler-close", "budget-close", "error-publication"])


class TracelessAnchorControls(Inert):
    wire = LocationControls.wire
    entrypoint_case = PartialAnchorControls.entrypoint_case

    @staticmethod
    def nodes(error):
        result = []
        while error is not None:
            result.append(error)
            error = error.__cause__ if error.__cause__ is not None else error.__context__
        return result

    @contextmanager
    def fixture(self, kind="wrapped-late", *, missing=(1,), execute=True, **kwargs):
        with PartialAnchorControls.fixture(self, kind, execute=False, **kwargs) as value:
            original = value.invoke
            def invoke():
                error = original()
                nodes = self.nodes(error)
                for index in missing:
                    nodes[index].__traceback__ = None
                return error
            value.invoke = invoke
            if execute:
                self.assertEqual(value.observer.register_locations(value.measurement.api), [])
                invoke()
            yield value

    def assert_partial(self, value, indices, relations=None):
        first = value.error
        record = self.wire(value)
        location = record["source_locations"]
        self.assertIs(value.error, first)
        self.assertEqual(location["status"], "unavailable")
        self.assertEqual(location["reason"], "no-source-trace")
        self.assertEqual(location["locations"], [])
        self.assertFalse(location["authority"])
        self.assertTrue(location["references_closed"])
        self.assertEqual([row["exception"] for row in location["anchors"]], indices)
        if relations is not None:
            self.assertEqual([row["relation"] for row in location["anchors"]], relations)
        nodes = self.nodes(value.error)
        for row in location["anchors"]:
            trace = nodes[row["exception"]].__traceback__
            self.assertIsNotNone(trace)
            matches = []
            while trace is not None:
                code = trace.tb_frame.f_code
                if (code.co_filename.removeprefix("/repo/"), code.co_name, trace.tb_lineno, trace.tb_lasti) == (
                    row["file"], row["code"], row["line"], row["offset"],
                ):
                    matches.append("registered-raising-frame" if trace.tb_next is None else "registered-caller")
                trace = trace.tb_next
            self.assertEqual(matches, [row["role"]])
        self.assertEqual(value.observer.location_codes, {})
        return record

    def test_raised_source_with_unraised_or_cleared_cause_retains_only_real_other_anchor(self):
        for kind in ("unraised", "cleared"):
            with self.subTest(kind=kind), LocationControls.fixture(self, "wrapped" if kind == "cleared" else "direct") as value:
                if kind == "unraised":
                    value.error.__cause__ = RuntimeError("private explanatory cause")
                else:
                    value.error.__cause__.__traceback__ = None
                record = self.assert_partial(value, [0], ["primary"])
                self.assertEqual(record["source_locations"]["anchors"][0]["role"], "registered-raising-frame")

    def test_cleared_first_middle_last_and_all_missing_keep_indices_and_scope_honest(self):
        for missing, expected in (
            ((0,), [1, 2]), ((1,), [0, 2]), ((2,), [0, 1]),
            ((0, 2), [1]), ((1, 2), [0]), ((0, 1, 2), []),
        ):
            with self.subTest(missing=missing), self.fixture(missing=missing) as value:
                self.assert_partial(value, expected)
        with self.fixture(missing=(0, 1)) as value:
            record = self.wire(value)
            self.assertEqual(record["source_locations"]["reason"], "public-call-unobserved")
            self.assertEqual(record["source_locations"]["anchors"], [])

    def test_unraised_first_or_middle_and_context_links_do_not_borrow_missing_member_lines(self):
        with LocationControls.fixture(self) as value:
            first = RuntimeError("private unraised primary")
            first.__cause__ = value.error
            value.error = first
            self.assert_partial(value, [1], ["cause"])
        with LocationControls.fixture(self, "wrapped") as value:
            inner = value.error.__cause__
            middle = RuntimeError("private unraised middle")
            middle.__cause__ = inner
            value.error.__cause__ = None
            value.error.__context__ = middle
            self.assert_partial(value, [0, 2], ["primary", "cause"])

    def test_missing_trace_cannot_hide_foreign_cycle_scope_or_replacement_rejection(self):
        for fault in ("cycle", "foreign-root", "foreign-file", "foreign-leaf", "replaced-caller", "no-scope", "malformed"):
            with self.subTest(fault=fault), self.fixture() as value:
                if fault == "cycle":
                    self.nodes(value.error)[-1].__cause__ = value.error
                elif fault == "foreign-root":
                    value.measurement.root = Path("/foreign")
                elif fault == "foreign-file":
                    value.entries["scripts/validation_ownership/graph_report.py"].mode = "120000"
                elif fault == "foreign-leaf":
                    def foreign():
                        raise RuntimeError("private foreign")
                    try:
                        foreign()
                    except BaseException as error:
                        self.nodes(value.error)[-1].__cause__ = error
                elif fault == "replaced-caller":
                    value.probe.run_probe = type(value.probe.run_probe)(
                        value.probe.run_probe.__code__.replace(co_name="replacement"), value.probe.__dict__,
                    )
                elif fault == "no-scope":
                    try:
                        value.phase.analyze()
                    except BaseException as error:
                        error.__cause__ = RuntimeError("private unraised")
                        value.error = error
                else:
                    class Bad(str):
                        def __eq__(self, other):
                            raise AssertionError("metadata callback")
                    value.phase.analyze.__code__ = value.phase.analyze.__code__.replace(
                        co_filename=Bad(value.phase.__file__),
                    )
                record = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertEqual(record["source_locations"]["anchors"], [])
                self.assertTrue(record["source_locations"]["references_closed"])

    def test_missing_nodes_count_toward_total_exception_frame_and_envelope_bounds(self):
        for count in (32, 33):
            with self.subTest(count=count), LocationControls.fixture(self) as value:
                current = value.error
                for _ in range(count - 1):
                    following = RuntimeError("private explanatory")
                    current.__cause__ = following
                    current = following
                record = self.wire(value)
                self.assertEqual(record["source_locations"]["reason"],
                                 "no-source-trace" if count == 32 else "exception-chain-bound")
                self.assertEqual(len(record["source_locations"]["anchors"]), 1 if count == 32 else 0)
        for frames in (256, 257):
            with self.subTest(frames=frames), PartialAnchorControls.fixture(self, depth=frames - 4) as value:
                value.error.__cause__ = RuntimeError("private unraised")
                record = self.wire(value)
                self.assertEqual(record["source_locations"]["reason"],
                                 "no-source-trace" if frames == 256 else "trace-frame-bound")
                self.assertEqual(bool(record["source_locations"]["anchors"]), frames == 256)
        with self.fixture() as value:
            size = len(policy.encoded(self.wire(value)["source_locations"]))
        for bound in (size, size - 1):
            with self.subTest(bound=bound), self.fixture() as value, mock.patch.object(policy, "ERROR_BYTES", bound):
                location = value.observer.source_locations(value.error, value.measurement)
                self.assertEqual(bool(location["anchors"]), bound == size)
                self.assertEqual(location["reason"], "no-source-trace" if bound == size else "location-size-bound")
        with self.fixture() as value:
            value.phase.__dict__.update({f"extra_{index}": None for index in range(policy.ORIGINAL_LIMITS["entries"])})
            record = self.wire(value)
            self.assertEqual(record["source_locations"]["reason"], "registration-bound")
            self.assertEqual(record["source_locations"]["anchors"], [])

    def test_traceless_wire_rejects_complete_coverage_claims_mutations_and_accepts_neutral_order(self):
        with self.fixture(missing=(2,)) as value:
            value.graph.harmless_alias = value.graph.check
            record = self.assert_partial(value, [0, 1])
            policy.validate_report_error(json_order(record), self.binding())
            for fault in ("covers-missing", "out-of-range", "duplicate", "relation", "observed", "unclosed", "foreign"):
                changed = copy.deepcopy(record)
                location = changed["source_locations"]
                if fault == "covers-missing":
                    location["anchors"].append({**location["anchors"][0], "exception": 2,
                                                "relation": "cause", "role": "registered-caller"})
                elif fault == "out-of-range":
                    location["anchors"][-1]["exception"] = 3
                elif fault == "duplicate":
                    location["anchors"][-1]["exception"] = 0
                elif fault == "relation":
                    location["anchors"][0]["relation"] = "context"
                elif fault == "observed":
                    location.update(status="observed", reason=None)
                elif fault == "unclosed":
                    location["references_closed"] = None
                else:
                    location["source_revision"] = policy.BASE
                with self.subTest(fault=fault), self.assertRaises(policy.GuardError):
                    parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES,
                                                 report_binding=self.binding(), deadline=3700.0)
                    parser.feed(policy.encoded({"scope": "12345/report", "kind": "error", "data": changed}) + b"\n")

    def test_old_abort_restoration_loses_bound_anchors_and_complete_locations_are_unchanged(self):
        self.assertIsNotNone(PREIMAGE_TRACELESS_PROJECT, "requires inspected traceless runner")
        with self.fixture() as value:
            self.assertTrue(self.assert_partial(value, [0, 2])["source_locations"]["anchors"])
        with mock.patch.object(observation_failure._SourceLocations, "project", PREIMAGE_TRACELESS_PROJECT):
            with self.fixture() as value:
                old = self.wire(value)["source_locations"]
                self.assertEqual(old["reason"], "no-source-trace")
                self.assertEqual(old["anchors"], [])
            with LocationControls.fixture(self) as value:
                complete = self.wire(value)["source_locations"]
                self.assertEqual(complete["status"], "observed")
                self.assertEqual(complete["anchors"], [])
        with PartialAnchorControls.fixture(self) as value:
            unbound = self.wire(value)["source_locations"]
            self.assertEqual(unbound["reason"], "source-code-unbound")
            self.assertTrue(unbound["anchors"])

    def test_actual_traceless_entrypoint_publication_faults_and_retention_keep_first_source(self):
        for faults in (
            (), ("publication-before",), ("publication-after",),
            ("sampler-close", "budget-close-before", "publication-after"),
            ("sampler-close", "budget-close-after", "publication-after"),
            ("locator", "publication-after"), ("formatter", "publication-before"),
            ("record",), ("record", "publication-before"), ("publication-always",),
        ):
            with self.subTest(faults=faults):
                value = self.entrypoint_case(*faults)
                self.assertEqual(value.code, 1)
                self.assertFalse(value.parser.finished)
                errors = [row["data"] for row in value.rows if row["kind"] == "error"]
                if "publication-always" in faults or {"record", "publication-before"} <= set(faults):
                    self.assertEqual(errors, [])
                    self.assertFalse(value.parser.failed)
                    self.assertTrue(value.stderr)
                    continue
                error = errors[-1]
                self.assertTrue(value.parser.failed)
                if "formatter" not in faults:
                    self.assertEqual(error["error"]["chain"][0]["type"], type(value.first).__name__)
                if "record" not in faults and "locator" not in faults:
                    self.assertEqual(error["source_locations"]["reason"], "no-source-trace")
                    self.assertEqual([row["exception"] for row in error["source_locations"]["anchors"]], [0, 2])
                    self.assertIsNotNone(error["counters"])
                    self.assertIsNotNone(error["cleanup"])
                else:
                    self.assertEqual(error["source_locations"]["anchors"], [])
                phase = {"mode": "report", "worker": None, "first_cause": {"type": "worker-error", "error": error},
                         "empty": True, "empty_before_outer_cleanup": True, "watchdog_reaped": True,
                         "lifetime_writer_closed": True}
                if not faults:
                    self.assertFalse(supervisor.report_retention(phase))
                    error["counters"]["budget"]["failed"] = True
                    self.assertTrue(supervisor.report_retention(phase))

    def test_traceless_withdrawal_failures_never_publish_qualified_anchors(self):
        original = observation_failure._SourceLocations
        for when in ("before", "after"):
            attempts, instances = [], []
            class Clearing(dict):
                def clear(self):
                    attempts.append("codes")
                    if when == "before":
                        raise OSError(errno.EIO, "private withdrawal")
                    super().clear()
                    raise OSError(errno.EIO, "private withdrawal")
            class Registry(original):
                def __init__(self):
                    super().__init__()
                    self.codes = Clearing()
                    instances.append(self)
            with self.subTest(when=when), self.fixture() as value, \
                 mock.patch.object(observation_failure, "_SourceLocations", Registry):
                first = value.error
                record = self.wire(value)
                self.assertIs(value.error, first)
                self.assertEqual(record["source_locations"]["anchors"], [])
                self.assertIsNone(record["source_locations"]["references_closed"])
                self.assertEqual(record["secondary"][-1]["stage"], "location-publication")
                self.assertIsNotNone(record["counters"])
                self.assertEqual(attempts, ["codes"])
                instance, = instances
                self.assertFalse(instance.modules)
                self.assertFalse(instance.seen)
                self.assertFalse(instance.registered)
                for name in ("entries", "entry_type", "call", "call_globals"):
                    self.assertIsNone(getattr(instance, name))


class OriginalImportControls(Inert):
    BODY = (
        b'MARKER = "original"\n'
        b'def outer():\n'
        b'    def nested():\n'
        b'        raise RuntimeError("private original nested")\n'
        b'    return nested()\n'
    )
    OTHER = BODY.replace(b'"original"', b'"replaced"').replace(b'original nested', b'replaced nested')

    @staticmethod
    def blob(data):
        digest = hashlib.sha1()
        digest.update(b"blob " + str(len(data)).encode("ascii") + b"\0")
        digest.update(data)
        return digest.hexdigest()

    @contextmanager
    def imported(self, kind="source", *, raise_leaf=True, body=None, mutate=None, restoring=None,
                 owned=(), operation="natural", graph_probe=False):
        self.assertIsNotNone(IMPORT_COMPILE_AUDIT, "requires the effect-trapped import runner")
        with LocationControls.fixture(self) as value, ExitStack() as patches:
            module_name = "graph_probe" if graph_probe else "inert_original"
            fullname = observation_failure.SOURCE_PACKAGE + "." + module_name
            filename = "/repo/scripts/validation_ownership/" + module_name + ".py"
            if kind == "foreign":
                fullname = "unselected.inert_original"
            body = self.BODY if body is None else body
            cache = None
            if kind in {"timestamp", "timestamp-substitution", "hash-substitution"}:
                cached_code = compile(self.BODY if kind == "timestamp" else self.OTHER,
                                      filename, "exec", dont_inherit=True)
                cache = bytes(import_external._code_to_timestamp_pyc(cached_code, 7, len(body)))
                if kind == "hash-substitution":
                    source_hash = import_external._imp.source_hash(import_external._RAW_MAGIC_NUMBER, body)
                    cache = bytes(import_external._code_to_hash_pyc(cached_code, source_hash, checked=True))
            elif kind == "broken-cache":
                cache = b"invalid synthetic pyc"
            elif kind == "cache-read":
                cache = OSError("private cache read")
            source_loader = SourceFileLoader(fullname, filename)
            spec = import_external.spec_from_file_location(fullname, filename, loader=source_loader)
            item = value.authority.GitTreeEntry()
            item.path, item.mode, item.object_type = filename.removeprefix("/repo/"), "100644", "blob"
            item.object_id, item.git_dir = self.blob(bytes(body)), None
            value.entries[item.path] = item
            source_error = None
            if kind == "source-read":
                source_error = OSError("private original source read")
            elif kind == "cancel":
                class Cancelled(BaseException):
                    pass
                source_error = Cancelled("private original cancellation")
            value.__dict__.update(
                source_loader=source_loader, spec=spec, item=item, source_error=source_error,
                reads=[], registrations=[], tokens=[], callbacks=[], restore_attempts=[],
                compile_returns=[], body=body, cache=cache, filename=filename, fullname=fullname,
                before_read=None, before_record=None, result=None, original_failure=None,
                foreign_sources={},
            )
            controls, runtime = self, ("/inert/original/runtime",)

            def constructor(session, loader, *, scratch_root, budget, runtime_files):
                session.__dict__.update(vars(controls.session(budget)))
                session.loader, session.scratch_root = loader, scratch_root
                session.runtime_paths, session.owner_thread = runtime_files, threading.get_ident()
                value.constructed = session

            def enter(session):
                session.budget.session_started = True
                session.budget.plan(1)
                session.budget.charge("control", 8)
                session.budget.runs = 1
                return session

            def leave(session, kind, error, trace):
                value.original_failure = error
                value.budget.close()

            value.probing.ProbeSession.__init__ = constructor
            value.probing.ProbeSession.__enter__ = enter
            value.probing.ProbeSession.__exit__ = leave
            value.graph.__dict__.update(
                ProbeSession=value.probing.ProbeSession, LOADER=value.loader, RUNTIME=runtime,
                SPEC=spec, LOAD=import_bootstrap._load_unlocked, BODY=body, ENTRY="outer",
                OPERATION=operation, RAISE_LEAF=raise_leaf, BEFORE_IMPORT=None, AFTER_LOAD=None, WRAP=False,
                REPORT=lambda: controls.raw_report(value.budget, value.constructed),
            )
            exec(compile("""
def check(root, *, budget, revision, base_revision, changed_paths, lifecycle):
    global loaded
    with ProbeSession(LOADER, scratch_root=root / "build/test-artifacts/validation-ownership",
                      budget=budget, runtime_files=RUNTIME):
        try:
            if BEFORE_IMPORT is not None:
                BEFORE_IMPORT()
            if OPERATION == "get-code":
                loaded = SPEC.loader.get_code(SPEC.name)
            elif OPERATION == "source-to-code":
                loaded = SPEC.loader.source_to_code(BODY, SPEC.origin)
            else:
                loaded = LOAD(SPEC)
                if AFTER_LOAD is not None:
                    AFTER_LOAD(loaded)
                if RAISE_LEAF:
                    getattr(loaded, ENTRY)()
        except RuntimeError as error:
            if WRAP:
                raise type(error)("private reporter wrapper") from error
            raise
        return REPORT()
""", value.graph.__file__, "exec"), value.graph.__dict__)
            api = value.measurement.api
            api.check, api.runtime_files = value.graph.check, runtime
            api.budget_type = budgeting.ProbeBudget
            api.serializer = lambda report: policy.encoded(report) + b"\n"
            config = {"mode": "report", "scope": "12345/report", "deadline": 3700.0,
                      "report_binding": self.binding()}
            sampler = SimpleNamespace(phase="candidate-import", session=None)
            value.measurement = root_stage.ReportMeasurement(Path("/repo"), value.budget, config, sampler, self.changes())
            value.measurement.api = api
            value.observer = observation_failure.Observer(value.probing.ProbeSession, value.budget)
            value.measurement.observer = value.observer
            self.assertEqual(value.observer.register_locations(api), [])
            original_slots = {
                name: (name in SourceFileLoader.__dict__, SourceFileLoader.__dict__.get(name))
                for name in ("get_code", "source_to_code")
            }
            for name in owned:
                patches.enter_context(mock.patch.object(SourceFileLoader, name, getattr(SourceFileLoader, name)))
            expected_slots = {
                name: (name in SourceFileLoader.__dict__, SourceFileLoader.__dict__.get(name))
                for name in original_slots
            }

            def get_data(loader, path):
                if type(loader.name) is str and loader.name in value.foreign_sources:
                    foreign_path, data = value.foreign_sources[loader.name]
                    if path == foreign_path:
                        return data
                    raise FileNotFoundError("private absent foreign cache")
                value.reads.append("source" if path == filename else "cache")
                if value.before_read is not None:
                    action, value.before_read = value.before_read, None
                    action(value)
                if path == filename:
                    if value.source_error is not None:
                        raise value.source_error
                    return value.body
                if value.cache is None:
                    raise FileNotFoundError("private missing synthetic cache")
                if isinstance(value.cache, BaseException):
                    raise value.cache
                return value.cache

            def path_stats(loader, path):
                return {"mtime": 7, "size": len(body)}

            def no_write(*args, **kwargs):
                self.fail("the original no-bytecode-write import attempted a write")

            methods = dict(observation_failure._IMPORT_METHODS)
            functions = dict(observation_failure._IMPORT_FUNCTIONS)
            for name, function in (
                ("get_data", get_data), ("path_stats", path_stats),
                ("set_data", no_write), ("_cache_bytecode", no_write),
            ):
                patches.enter_context(mock.patch.object(SourceFileLoader, name, function))
                methods[name] = function
                functions[function] = function.__code__, function.__globals__
            patches.enter_context(mock.patch.object(observation_failure, "_IMPORT_METHODS", methods))
            patches.enter_context(mock.patch.object(observation_failure, "_IMPORT_FUNCTIONS", functions))
            record = observation_failure._OriginalImports.record
            restore = observation_failure._OriginalImports.restore_slot

            def registered(imports, token, code):
                value.tokens.append(token)
                if value.before_record is not None:
                    value.before_record(imports, token, code)
                record(imports, token, code)
                module = token["binding"][0]
                pending, codes = [code], []
                while pending:
                    current = pending.pop()
                    codes.append(current)
                    pending.extend(child for child in current.co_consts if type(child) is CodeType)
                self.assertIs(code, token["compiled"])
                self.assertTrue(all(imports.observer.location_codes[id(item)][0]() is item for item in codes))
                self.assertNotIn("MARKER", module.__dict__)
                value.compile_returns.append(token["compiled"])
                value.registrations.append((code, tuple(codes), module))

            def restored(imports, name):
                value.restore_attempts.append(name)
                if restoring in {("before", name), ("both-before", name)} or restoring == ("both-before", "*"):
                    raise OSError(errno.EIO, "private before restoration")
                restore(imports, name)
                if restoring == ("after", name):
                    raise OSError(errno.EIO, "private after restoration")

            patches.enter_context(mock.patch.object(observation_failure._OriginalImports, "record", registered))
            patches.enter_context(mock.patch.object(observation_failure._OriginalImports, "restore_slot", restored))
            patches.enter_context(mock.patch.object(root_stage, "candidate_api", return_value=api))
            patches.enter_context(mock.patch.object(worker, "require_contained", return_value={}))
            IMPORT_COMPILE_AUDIT.update(filename=filename, count=0)
            value.before_read = mutate

            def invoke():
                try:
                    value.result = value.measurement.run()
                except BaseException as error:
                    value.error = error
                else:
                    value.error = None
                value.compiles = IMPORT_COMPILE_AUDIT["count"]
                return value.error

            value.invoke = invoke
            try:
                yield value
            finally:
                IMPORT_COMPILE_AUDIT.clear()
                if hasattr(value.observer, "imports"):
                    imports = value.observer.imports
                    imports.restore()
                    try:
                        value.observer.close_imports(value.measurement)
                    except observation_failure._LocationUnavailable:
                        self.assertIsNot(imports.released, True)
                    self.assertIsNone(imports.active)
                    self.assertIsNone(imports.bound)
                    self.assertIsNone(imports.observer)
                    self.assertIsNone(imports.measurement)
                    self.assertTrue(all(token == {} for token in value.tokens))
                    if restoring is None:
                        self.assertEqual(expected_slots, {
                            name: (name in SourceFileLoader.__dict__, SourceFileLoader.__dict__.get(name))
                            for name in expected_slots
                        })
                # Test containment only, never credited as observer restoration.
                for name, (present, original) in expected_slots.items():
                    if present:
                        setattr(SourceFileLoader, name, original)
                    elif name in SourceFileLoader.__dict__:
                        delattr(SourceFileLoader, name)
                sys.modules.pop(fullname, None)

    def wire(self, value, *, observer=None):
        sampler = SimpleNamespace(snapshot=lambda: {
            "counters": policy.counter_snapshot(value.budget, value.measurement.session), "accounting": None,
        })
        record = worker.report_error_record(
            value.error, value.measurement, sampler, value.observer if observer is None else observer,
            self.binding(), [],
        )
        raw = io.BytesIO()
        with mock.patch.object(kernel, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=raw))):
            kernel.emit("12345/report", "error", record)
        parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES,
                                     report_binding=self.binding(), deadline=3700.0)
        row, = parser.feed(raw.getvalue())
        self.assertTrue(parser.failed)
        self.assertFalse(parser.finished)
        self.assertNotIn(b"private", raw.getvalue())
        self.assertNotIn(b'"message"', raw.getvalue())
        self.assertNotIn(b'"frames"', raw.getvalue())
        phase = {
            "mode": "report", "empty_before_outer_cleanup": True, "empty": True,
            "watchdog_reaped": True, "lifetime_writer_closed": True,
            "first_cause": {"type": "worker-error", "error": row["data"]},
        }
        return row["data"], supervisor.report_retention(phase)

    def test_genuine_source_and_nested_code_bind_before_body_and_reach_actual_wire(self):
        for kind in ("source", "broken-cache", "cache-read"):
            with self.subTest(kind=kind), self.imported(kind) as value:
                value.invoke()
                self.assertIs(value.error, value.original_failure)
                self.assertEqual(value.compiles, 1)
                self.assertEqual(value.reads, ["cache", "source"])
                root, codes, module = value.registrations[0]
                self.assertIs(root, value.compile_returns[0])
                self.assertEqual(len(codes), 3)
                self.assertEqual(module.MARKER, "original")
                record, retained = self.wire(value)
                location, = record["source_locations"]["locations"]
                self.assertEqual(record["source_locations"]["status"], "observed")
                self.assertEqual(location["code"], "nested")
                self.assertFalse(record["source_locations"]["authority"])
                self.assertFalse(retained)
                self.assertEqual(value.restore_attempts, ["get_code", "source_to_code"])
                self.assertTrue(record["cleanup"]["source_imports_restored"])
                self.assertTrue(record["cleanup"]["source_imports_released"])

    def test_valid_timestamp_and_checked_hash_substitution_never_attest_cached_code(self):
        for kind in ("timestamp", "timestamp-substitution", "hash-substitution"):
            with self.subTest(kind=kind), self.imported(kind) as value:
                value.invoke()
                self.assertIs(value.error, value.original_failure)
                self.assertEqual(value.compiles, 0)
                self.assertEqual(value.registrations, [])
                self.assertEqual(value.reads, ["cache", "source"] if kind == "hash-substitution" else ["cache"])
                record, _ = self.wire(value)
                self.assertEqual(record["source_locations"]["reason"], "source-code-unbound")
                self.assertEqual(record["source_locations"]["locations"], [])
                self.assertEqual(record["source_locations"]["anchors"][0]["role"], "registered-caller")
                self.assertEqual(value.graph.loaded.MARKER, "original" if kind == "timestamp" else "replaced")

    def test_direct_and_foreign_calls_preserve_original_returns_without_code_authority(self):
        for kind, operation in (("source", "get-code"), ("source", "source-to-code"), ("foreign", "natural")):
            with self.subTest(kind=kind, operation=operation), self.imported(
                kind, operation=operation, raise_leaf=False,
            ) as value:
                value.invoke()
                self.assertIsNone(value.error)
                self.assertEqual(value.compiles, 1)
                self.assertEqual(value.registrations, [])
                self.assertTrue(value.result["states"]["completed"])
                if operation != "natural":
                    self.assertIs(type(value.graph.loaded), CodeType)
                self.assertEqual(value.restore_attempts, ["get_code", "source_to_code"])

    def test_nested_foreign_import_is_not_the_active_selected_get_code_invocation(self):
        with self.imported() as value:
            name, path = "unselected.nested_inert", "/inert/nested_inert.py"
            value.foreign_sources[name] = (path, b"VALUE = 7\n")
            def nested(current):
                spec = import_external.spec_from_file_location(name, path, loader=SourceFileLoader(name, path))
                module = import_bootstrap._load_unlocked(spec)
                self.assertEqual(module.VALUE, 7)
                self.assertFalse(current.observer.imports.failed)
                self.assertIsNone(current.observer.imports.active["compiled"])
                self.assertFalse(current.observer.imports.active["compile_seen"])
            value.before_read = nested
            value.invoke()
            record, _ = self.wire(value)
            self.assertEqual(record["source_locations"]["status"], "observed")
            self.assertEqual(value.compiles, 1)
            self.assertEqual(value.reads, ["cache", "source"])
            self.assertEqual(len(value.registrations), 1)
            self.assertFalse(value.observer.imports.failed)

    def test_source_read_compile_body_and_cancellation_preserve_the_actual_first_exception(self):
        cases = (
            ("source-read", self.BODY, 0), ("cancel", self.BODY, 0),
            ("source", b"\0", 0),
            ("source", b'ERROR = RuntimeError("private body")\nraise ERROR\n', 1),
        )
        for kind, body, count in cases:
            with self.subTest(kind=kind, body_length=len(body)), self.imported(kind, body=body) as value:
                value.invoke()
                self.assertIs(value.error, value.original_failure)
                if value.source_error is not None:
                    self.assertIs(value.error, value.source_error)
                elif body == b"\0":
                    self.assertIs(type(value.error), SyntaxError)
                    trace, calls = value.error.__traceback__, []
                    while trace is not None:
                        calls.append(trace.tb_frame.f_code)
                        trace = trace.tb_next
                    self.assertEqual(calls.count(observation_failure._GET_CODE.__code__), 1)
                    self.assertEqual(calls.count(observation_failure._SOURCE_TO_CODE.__code__), 1)
                else:
                    trace = value.error.__traceback__
                    while trace.tb_next is not None:
                        trace = trace.tb_next
                    self.assertIs(value.error, trace.tb_frame.f_globals["ERROR"])
                self.assertEqual(value.compiles, count)
                record, _ = self.wire(value)
                self.assertEqual(record["error"]["chain"][0]["type"], type(value.error).__name__)
                self.assertTrue(record["cleanup"]["source_imports_restored"])
                self.assertTrue(record["cleanup"]["source_imports_released"])

    def test_wrong_source_bytes_are_diagnostic_failure_not_a_substituted_source_result(self):
        for raising in (False, True):
            def wrong(value):
                value.body = self.OTHER
            with self.subTest(raising=raising), self.imported(raise_leaf=raising, mutate=wrong) as value:
                value.invoke()
                self.assertEqual(value.graph.loaded.MARKER, "replaced")
                self.assertEqual(value.compiles, 1)
                self.assertEqual(value.registrations, [])
                if raising:
                    self.assertIs(value.error, value.original_failure)
                else:
                    self.assertIsNone(value.original_failure)
                    self.assertEqual(value.measurement.states["serialization_returned"], 1)
                    self.assertIs(type(value.error), policy.GuardError)
                record, _ = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertIn("import-observation", [row["stage"] for row in record["secondary"]])

    def test_before_and_after_identity_changes_cannot_register_or_borrow_original_code(self):
        faults = ("loader", "spec", "module", "entry", "entry-bytes", "budget", "session", "entries",
                  "thread", "clock", "root", "method", "compiler")
        for fault in faults:
            with self.subTest(fault=fault), self.imported(raise_leaf=False) as value, ExitStack() as changes:
                def mutate(value):
                    imports = value.observer.imports
                    if fault == "loader":
                        value.source_loader.path = "/foreign/inert_original.py"
                    elif fault == "spec":
                        value.spec.origin = "/foreign/inert_original.py"
                    elif fault == "module":
                        sys.modules[value.fullname] = ModuleType(value.fullname)
                    elif fault == "entry":
                        value.entries[value.item.path] = copy.copy(value.item)
                    elif fault == "entry-bytes":
                        value.item.object_id = "b" * 40
                    elif fault == "budget":
                        value.loader.budget = object()
                    elif fault == "session":
                        value.measurement.session = value.probing.ProbeSession.__new__(value.probing.ProbeSession)
                    elif fault == "entries":
                        value.loader.entries = copy.copy(value.entries)
                    elif fault == "thread":
                        imports.thread = -1
                    elif fault == "clock":
                        imports.deadline -= 1
                    elif fault == "root":
                        value.loader.root = Path("/foreign")
                    elif fault == "method":
                        changes.enter_context(mock.patch.object(SourceFileLoader, "path_stats", lambda *args: {}))
                    else:
                        original_compile = builtins.compile
                        def other(*args, **kwargs):
                            return original_compile(*args, **kwargs)
                        changes.enter_context(mock.patch.object(builtins, "compile", other))
                value.before_read = mutate
                value.invoke()
                self.assertIsNone(value.original_failure)
                self.assertEqual(value.compiles, 1)
                self.assertEqual(value.registrations, [])
                self.assertIsNotNone(value.error)
                record, _ = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertFalse(record["states"]["completed"])

    def test_metadata_subclasses_do_not_receive_observer_callbacks(self):
        class Mapping(dict):
            def get(self, *args):
                touched.append("get")
                raise AssertionError("metadata callback")
            def __iter__(self):
                touched.append("iter")
                raise AssertionError("metadata callback")
        class Text(str):
            def __eq__(self, other):
                touched.append("eq")
                raise AssertionError("metadata callback")
            __hash__ = str.__hash__
        for fault in ("loader-dict", "spec-dict", "origin", "entry-id"):
            touched = []
            with self.subTest(fault=fault), self.imported(raise_leaf=False) as value:
                def mutate(value):
                    if fault == "loader-dict":
                        value.source_loader.__dict__ = Mapping(value.source_loader.__dict__)
                    elif fault == "spec-dict":
                        value.spec.__dict__ = Mapping(value.spec.__dict__)
                    elif fault == "origin":
                        value.spec.origin = Text(value.spec.origin)
                    else:
                        value.item.object_id = Text(value.item.object_id)
                value.before_read = mutate
                value.invoke()
                self.assertEqual(touched, [])
                self.assertIsNone(value.original_failure)
                self.assertEqual(value.compiles, 1)
                self.assertEqual(value.registrations, [])
                self.assertIsNotNone(value.error)
                self.wire(value)

    def test_original_slots_restore_independently_for_all_owned_and_inherited_combinations(self):
        for owned in ((), ("get_code",), ("source_to_code",), ("get_code", "source_to_code")):
            for raising in (False, True):
                with self.subTest(owned=owned, raising=raising), self.imported(owned=owned, raise_leaf=raising) as value:
                    value.invoke()
                    if raising:
                        self.wire(value)
                    else:
                        self.assertIsNone(value.error)
                    self.assertEqual(value.restore_attempts, ["get_code", "source_to_code"])
                    self.assertEqual(value.observer.imports.restored, {"get_code": True, "source_to_code": True})
                    self.assertTrue(value.observer.imports.released)

    def test_restoration_faults_attempt_both_slots_preserve_primary_and_never_complete(self):
        for when in ("before", "after"):
            for name in ("get_code", "source_to_code"):
                for source in ("source-read", "cancel", "source"):
                    with self.subTest(when=when, name=name, source=source), self.imported(
                        source, restoring=(when, name),
                    ) as value:
                        value.invoke()
                        self.assertIs(value.error, value.original_failure)
                        self.assertEqual(value.restore_attempts, ["get_code", "source_to_code"])
                        record, retained = self.wire(value)
                        self.assertTrue(retained)
                        self.assertFalse(record["states"]["completed"])
                        self.assertEqual(record["error"]["chain"][0]["type"], type(value.error).__name__)
                        self.assertIn("import-" + name.replace("_", "-") + "-restore",
                                      [row["stage"] for row in record["secondary"]])
                        self.assertIs(record["cleanup"]["source_imports_restored"], when == "after")
        with self.imported("source-read", restoring=("both-before", "*")) as value:
            value.invoke()
            record, retained = self.wire(value)
            self.assertIs(value.error, value.source_error)
            self.assertEqual(value.restore_attempts, ["get_code", "source_to_code"])
            self.assertTrue(retained)
            self.assertEqual([row["stage"] for row in record["secondary"]],
                             ["import-get-code-restore", "import-source-to-code-restore"])

    def test_record_and_capacity_failures_do_not_prevent_original_body_or_serialization(self):
        for fault in ("record", "work", "source-bytes", "code-identity"):
            for raising in (False, True):
                with self.subTest(fault=fault, raising=raising), self.imported(raise_leaf=raising) as value:
                    def mutate(value):
                        if fault == "work":
                            value.observer.location_work = policy.ORIGINAL_LIMITS["entries"]
                        elif fault == "source-bytes":
                            value.observer.imports.source_bytes = policy.ORIGINAL_LIMITS["file_bytes"]
                    def record(imports, token, code):
                        if fault == "record":
                            raise OSError(errno.EIO, "private collector")
                        if fault == "code-identity":
                            token["compiled"] = code.replace(co_name="different")
                    value.before_read, value.before_record = mutate, record
                    value.invoke()
                    self.assertEqual(value.graph.loaded.MARKER, "original")
                    self.assertEqual(value.compiles, 1)
                    self.assertEqual(value.registrations, [])
                    if raising:
                        self.assertIs(value.error, value.original_failure)
                    else:
                        self.assertIsNone(value.original_failure)
                        self.assertEqual(value.measurement.states["serialization_returned"], 1)
                    record, _ = self.wire(value)
                    self.assertFalse(record["states"]["completed"])
                    self.assertEqual(record["source_locations"]["status"], "unavailable")

    def test_late_module_spec_entry_and_function_replacements_remain_unavailable_at_projection(self):
        for fault in ("module", "spec", "entry", "method", "function"):
            with self.subTest(fault=fault), self.imported() as value, ExitStack() as changes:
                if fault == "function":
                    def replacement(module):
                        code = module.outer.__code__
                        module.outer.__code__ = code.replace(co_consts=tuple(
                            child.replace(co_name="replacement") if type(child) is CodeType else child
                            for child in code.co_consts
                        ))
                    value.graph.AFTER_LOAD = replacement
                value.invoke()
                if fault == "module":
                    sys.modules[value.fullname] = ModuleType(value.fullname)
                elif fault == "spec":
                    value.graph.loaded.__spec__ = copy.copy(value.spec)
                elif fault == "entry":
                    value.entries[value.item.path] = copy.copy(value.item)
                elif fault == "method":
                    changes.enter_context(mock.patch.object(SourceFileLoader, "path_stats", lambda *args: {}))
                record, _ = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertEqual(record["source_locations"]["locations"], [])

    def test_parent_observer_reproduces_missing_lazy_source_without_a_second_import(self):
        self.assertIsNotNone(PREIMAGE_LOCATION_OBSERVER)
        with self.imported() as value:
            previous = PREIMAGE_LOCATION_OBSERVER(value.probing.ProbeSession, value.budget)
            self.assertEqual(previous.register_locations(value.measurement.api), [])
            value.invoke()
            old, _ = self.wire(value, observer=previous)
            self.assertEqual(old["source_locations"]["reason"], "source-code-unbound")
            self.assertEqual(old["source_locations"]["locations"], [])
            self.assertEqual(old["source_locations"]["anchors"][0]["role"], "registered-caller")
            current, _ = self.wire(value)
            self.assertEqual(current["source_locations"]["status"], "observed")
            self.assertEqual(value.compiles, 1)
            self.assertEqual(value.reads, ["cache", "source"])

    def test_neutral_source_names_order_and_wire_order_keep_actual_position_evidence(self):
        body = self.BODY.replace(b"outer", b"entry").replace(b"nested", b"branch")
        for data, name in ((self.BODY, "outer"), (body, "entry")):
            with self.subTest(name=name), self.imported(body=data) as value:
                value.graph.ENTRY = name
                value.invoke()
                record, retained = self.wire(value)
                self.assertFalse(retained)
                self.assertEqual(record["source_locations"]["status"], "observed")
                trace = value.error.__traceback__
                while trace.tb_next is not None:
                    trace = trace.tb_next
                row, = record["source_locations"]["locations"]
                self.assertEqual((row["code"], row["line"], row["offset"]),
                                 (trace.tb_frame.f_code.co_name, trace.tb_lineno, trace.tb_lasti))
                self.assertEqual(policy.validate_report_error(json_order(record), self.binding()), record)

    def test_initial_metadata_and_mid_import_loader_replacements_gain_no_authority(self):
        for fault in ("loader-subclass", "spec-subclass", "instance-shadow", "spec-origin", "name",
                      "mode", "gitlink", "module-loader", "module-spec", "session-thread"):
            with self.subTest(fault=fault), self.imported(raise_leaf=False) as value:
                def mutate():
                    if fault == "loader-subclass":
                        class ForeignLoader(SourceFileLoader):
                            pass
                        value.source_loader.__class__ = ForeignLoader
                    elif fault == "spec-subclass":
                        class ForeignSpec(ModuleSpec):
                            pass
                        value.spec.__class__ = ForeignSpec
                    elif fault == "instance-shadow":
                        value.source_loader.extra = object()
                    elif fault == "spec-origin":
                        value.spec.origin = "/foreign/inert_original.py"
                    elif fault == "name":
                        value.source_loader.name = "foreign.inert_original"
                    elif fault == "mode":
                        value.item.mode = "120000"
                    elif fault == "gitlink":
                        value.item.git_dir = Path("/foreign/git")
                    elif fault == "session-thread":
                        value.constructed.owner_thread = -1
                    elif fault == "module-loader":
                        sys.modules[value.fullname].__loader__ = SourceFileLoader(value.fullname, value.filename)
                    else:
                        sys.modules[value.fullname].__spec__ = copy.copy(value.spec)
                if fault in {"module-loader", "module-spec"}:
                    value.before_read = lambda unused: mutate()
                else:
                    value.graph.BEFORE_IMPORT = mutate
                value.invoke()
                self.assertEqual(value.registrations, [])
                if value.original_failure is not None:
                    self.assertIs(value.error, value.original_failure)
                self.assertIsNotNone(value.error)
                record, _ = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "unavailable")

    def test_original_method_code_and_compiler_global_identities_are_not_metadata_equivalence(self):
        for fault in ("get-code", "source-to-code", "compiler-global", "compiler-builtin", "exec-module"):
            with self.subTest(fault=fault), self.imported() as value, ExitStack() as changes:
                foreign = compile(self.OTHER, value.filename, "exec", dont_inherit=True)
                IMPORT_COMPILE_AUDIT["count"] = 0
                def mutate():
                    if fault in {"get-code", "source-to-code", "exec-module"}:
                        original = {
                            "get-code": observation_failure._GET_CODE,
                            "source-to-code": observation_failure._SOURCE_TO_CODE,
                            "exec-module": observation_failure._EXEC_MODULE,
                        }[fault]
                        original_code = original.__code__
                        changes.callback(setattr, original, "__code__", original_code)
                        original.__code__ = original_code.replace(co_name="renamed")
                    elif fault == "compiler-global":
                        changes.enter_context(mock.patch.dict(observation_failure._SOURCE_TO_CODE.__globals__,
                                                             {"compile": lambda *args, **kwargs: foreign}))
                    else:
                        changes.enter_context(mock.patch.object(builtins, "compile", lambda *args, **kwargs: foreign))
                value.graph.BEFORE_IMPORT = mutate
                value.invoke()
                self.assertIs(value.error, value.original_failure)
                self.assertEqual(value.registrations, [])
                record, _ = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "unavailable")
                self.assertEqual(value.reads, ["cache", "source"])

    def test_exact_registration_and_cumulative_source_byte_capacities(self):
        with self.imported() as first:
            first.invoke()
            _, codes, _ = first.registrations[0]
            needed = sum(1 + len(code.co_consts) for code in codes)
            self.wire(first)
        for boundary in ("entries", "source-bytes"):
            for over in (0, 1):
                with self.subTest(boundary=boundary, over=over), self.imported() as value:
                    def mutate(value):
                        if boundary == "entries":
                            value.observer.location_work = policy.ORIGINAL_LIMITS["entries"] - needed + over
                        else:
                            value.observer.imports.source_bytes = policy.ORIGINAL_LIMITS["file_bytes"] - len(value.body) + over
                    value.before_read = mutate
                    value.invoke()
                    self.assertIs(value.error, value.original_failure)
                    self.assertEqual(value.compiles, 1)
                    record, _ = self.wire(value)
                    self.assertEqual(record["source_locations"]["status"], "unavailable" if over else "observed")
                    self.assertEqual(len(value.registrations), 0 if over else 1)

    def test_nonbuiltin_compiler_input_does_not_invoke_metadata_callbacks_or_skip_compile(self):
        touched = []
        class Bytes(bytes):
            def __len__(self):
                touched.append("len")
                raise AssertionError("private bytes callback")
        for shape in (bytearray, Bytes):
            with self.subTest(shape=shape.__name__), self.imported(raise_leaf=False) as value:
                value.before_read = lambda current: setattr(current, "body", shape(self.BODY))
                value.invoke()
                self.assertIsNone(value.original_failure)
                self.assertIsNotNone(value.error)
                self.assertEqual(value.compiles, 1)
                self.assertEqual(value.graph.loaded.MARKER, "original")
                self.assertEqual(value.registrations, [])
                self.assertEqual(touched, [])
                self.wire(value)

    def test_stale_hooks_and_private_record_tokens_cannot_reopen_a_finished_lifetime(self):
        with self.imported() as value:
            hooks = []
            def save(current):
                hooks.extend((current.observer.imports.get_hook, current.observer.imports.compile_hook))
            value.before_read = save
            value.invoke()
            code = value.compile_returns[0]
            self.wire(value)
            imports = value.observer.imports
            with self.assertRaises(observation_failure._LocationUnavailable):
                imports.record({}, code)
            self.assertTrue(imports.released)
            self.assertEqual(value.observer.location_codes, {})
            returned = hooks[1](value.source_loader, self.BODY, value.filename)
            self.assertIs(type(returned), CodeType)
            self.assertEqual(value.observer.location_codes, {})
            self.assertIsNone(imports.active)

    def test_weak_module_identity_does_not_keep_a_removed_source_module_alive(self):
        with self.imported() as value:
            value.invoke()
            reference = weakref.ref(value.graph.loaded)
            value.registrations.clear()
            del value.graph.loaded
            del sys.modules[value.fullname]
            self.assertIsNone(reference())
            record, _ = self.wire(value)
            self.assertEqual(record["source_locations"]["status"], "unavailable")
            self.assertEqual(record["source_locations"]["locations"], [])

    def test_successful_source_and_serializer_still_fail_after_restoration_faults(self):
        for when in ("before", "after"):
            for name in ("get_code", "source_to_code"):
                with self.subTest(when=when, name=name), self.imported(
                    raise_leaf=False, restoring=(when, name),
                ) as value:
                    value.invoke()
                    self.assertIsNone(value.original_failure)
                    self.assertIs(type(value.error), policy.GuardError)
                    self.assertEqual(value.measurement.states["check_returned"], 1)
                    self.assertEqual(value.measurement.states["serialization_returned"], 1)
                    self.assertEqual(value.restore_attempts, ["get_code", "source_to_code"])
                    record, retained = self.wire(value)
                    self.assertTrue(retained)
                    self.assertFalse(record["states"]["completed"])

    def test_original_compile_fault_stays_first_even_when_the_observer_also_fails(self):
        with self.imported(body=b"\0", mutate=lambda value: setattr(value.item, "object_id", "b" * 40)) as value:
            value.invoke()
            self.assertIs(type(value.error), SyntaxError)
            self.assertIs(value.error, value.original_failure)
            record, _ = self.wire(value)
            self.assertEqual(record["error"]["chain"][0]["type"], "SyntaxError")
            self.assertEqual(record["source_locations"]["status"], "unavailable")
            self.assertIn("import-observation", [row["stage"] for row in record["secondary"]])

    def test_reference_release_faults_are_incomplete_and_do_not_hide_the_source_or_other_attempts(self):
        for failed in ("codes", "slots"):
            for when in ("before", "after"):
                attempts = []
                class Clearing(dict):
                    def __init__(self, name, values=()):
                        super().__init__(values)
                        self.name = name
                    def clear(self):
                        attempts.append(self.name)
                        if self.name == failed and when == "before":
                            raise OSError(errno.EIO, "private reference release")
                        super().clear()
                        if self.name == failed and when == "after":
                            raise OSError(errno.EIO, "private reference release")
                with self.subTest(failed=failed, when=when), self.imported() as value:
                    value.invoke()
                    first = value.error
                    value.observer.location_codes = Clearing("codes", value.observer.location_codes)
                    value.observer.imports.old_slots = Clearing("slots")
                    record, retained = self.wire(value)
                    self.assertIs(value.error, first)
                    self.assertTrue(retained)
                    self.assertIsNone(record["cleanup"]["source_imports_released"])
                    self.assertTrue(record["cleanup"]["source_imports_restored"])
                    self.assertEqual(record["source_locations"]["reason"], "locator-failed")
                    self.assertIsNone(record["source_locations"]["references_closed"])
                    self.assertIn("location-publication", [row["stage"] for row in record["secondary"]])
                    self.assertIn("codes", attempts)
                    self.assertIn("slots", attempts)
                    self.assertEqual(value.restore_attempts, ["get_code", "source_to_code"])
                    imports = value.observer.imports
                    for name in ("active", "bound", "observer", "measurement", "get_hook", "compile_hook", "clock"):
                        self.assertIsNone(getattr(imports, name))

    def test_traceless_explanatory_note_keeps_real_lazy_raise_but_no_note_location(self):
        self.assertIsNotNone(PREIMAGE_LOCATION_OBSERVER)
        body = b"""
class MakeProbeError(RuntimeError):
    pass
diagnostics = []
def diagnostic():
    raise ValueError("private diagnostic")
def outer():
    failure = MakeProbeError("private source failure")
    try:
        diagnostic()
    except BaseException as error:
        error.__traceback__ = None
        error.__cause__ = error.__context__ = None
        diagnostics.append(error)
    note = "private explanatory note"
    failure.add_note(note)
    raise failure from MakeProbeError(note)
"""
        with self.imported(body=body) as value:
            value.graph.WRAP = True
            previous = PREIMAGE_LOCATION_OBSERVER(value.probing.ProbeSession, value.budget)
            self.assertEqual(previous.register_locations(value.measurement.api), [])
            value.invoke()
            self.assertIs(value.error, value.original_failure)
            self.assertIsNone(value.error.__cause__.__cause__.__traceback__)
            diagnostic, = value.graph.loaded.diagnostics
            self.assertIsNone(diagnostic.__traceback__)
            self.assertIsNone(diagnostic.__cause__)
            self.assertIsNone(diagnostic.__context__)
            old, _ = self.wire(value, observer=previous)
            self.assertEqual([row["role"] for row in old["source_locations"]["anchors"]],
                             ["registered-raising-frame", "registered-caller"])
            record, _ = self.wire(value)
            location = record["source_locations"]
            self.assertEqual(location["reason"], "no-source-trace")
            self.assertEqual(location["locations"], [])
            self.assertEqual([row["exception"] for row in location["anchors"]], [0, 1])
            self.assertEqual([row["role"] for row in location["anchors"]],
                             ["registered-raising-frame", "registered-raising-frame"])
            self.assertEqual([row["type"] for row in record["error"]["chain"]], ["MakeProbeError"] * 3)
            self.assertFalse(location["authority"])
            self.assertNotIn(b"private", policy.encoded(record))
            self.assertEqual(value.compiles, 1)

    def test_get_code_only_and_compiler_identity_bypass_mutations_break_cache_refusal_oracle(self):
        def unsafe_get(imports, loader, fullname):
            binding = imports.metadata(loader, fullname, initializing=True)
            token = {"loader": loader, "name": fullname, "binding": binding,
                     "compile_seen": False, "compiled": None}
            imports.active = token
            try:
                code = observation_failure._GET_CODE(loader, fullname)
                token["compiled"] = code
                imports.record(token, code)
                return code
            finally:
                imports.active = None
                token.clear()
        for bypass in ("get-code-only", "compiler-identity"):
            kind = "timestamp-substitution" if bypass == "get-code-only" else "source"
            with self.subTest(bypass=bypass), self.imported(kind) as value, ExitStack() as mutation:
                if bypass == "get-code-only":
                    mutation.enter_context(mock.patch.object(observation_failure._OriginalImports, "get_code", unsafe_get))
                else:
                    foreign = compile(self.OTHER, value.filename, "exec", dont_inherit=True)
                    IMPORT_COMPILE_AUDIT["count"] = 0
                    mutation.enter_context(mock.patch.object(observation_failure._OriginalImports, "unchanged", lambda *args, **kwargs: None))
                    mutation.enter_context(mock.patch.object(builtins, "compile", lambda *args, **kwargs: foreign))
                value.invoke()
                self.assertEqual(value.graph.loaded.MARKER, "replaced")
                self.assertEqual(value.compiles, 0)
                record, _ = self.wire(value)
                self.assertEqual(record["source_locations"]["status"], "observed")
                with self.assertRaises(AssertionError):
                    self.assertEqual(record["source_locations"]["status"], "unavailable")


class MakeContextControls(Inert):
    wire = OriginalImportControls.wire
    imported = OriginalImportControls.imported
    blob = staticmethod(OriginalImportControls.blob)
    BODY, OTHER = OriginalImportControls.BODY, OriginalImportControls.OTHER

    @contextmanager
    def fixture(self, *, failure=("Makefile", 4, 7, 9), uncertainty=("generated_data.mk", 2, 3, 3),
                unknown=False, wrapped=True):
        self.assertIsNotNone(MAKE_CONTEXT_SOURCE, "requires the inspected selected pure AST")
        with self.imported(body=MAKE_CONTEXT_SOURCE, graph_probe=True) as value:
            for path in ("Makefile", "generated_data.mk"):
                entry = value.authority.GitTreeEntry()
                entry.path, entry.mode, entry.object_type, entry.object_id, entry.git_dir = path, "100644", "blob", "c" * 40, None
                value.entries[path] = entry
            def configure(module):
                module.MODE.budget = value.budget
                module.MODE.site = None if failure is None else module._SourceSite(*failure)
                module.MODE.first_uncertainty = (
                    (None, "private reason", "PRIVATE_INPUT") if unknown else
                    None if uncertainty is None else (module._SourceSite(*uncertainty), "private reason", "PRIVATE_INPUT")
                )
            value.graph.AFTER_LOAD = configure
            value.graph.WRAP = wrapped
            yield value

    def assert_context(self, result, *, count=2):
        record, retained = self.wire(result)
        self.assertIs(result.error, result.original_failure)
        self.assertFalse(retained)
        location = record["source_locations"]
        self.assertEqual(location["version"], 3)
        self.assertEqual(location["reason"], "no-source-trace")
        self.assertEqual(location["locations"], [])
        self.assertTrue(location["references_closed"])
        self.assertFalse(location["authority"])
        context = location["context"]
        self.assertFalse(context["authority"])
        self.assertEqual(context["exception"], 1 if result.graph.WRAP else 0)
        self.assertEqual(sum(row["status"] == "reported-selected-tree" for row in context["spans"]), count)
        self.assertEqual(context["status"], "reported" if count == 2 else "partial" if count else "unavailable")
        self.assertEqual(len(context["spans"]), 2)
        self.assertNotIn(b"private", policy.encoded(record))
        self.assertNotIn(b"PRIVATE_INPUT", policy.encoded(record))
        self.assertNotIn(b"PREFIX", policy.encoded(record))
        return record

    def test_actual_selected_collapse_reports_both_spans_without_native_or_namespace_authority(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped), self.fixture(wrapped=wrapped) as value:
                value.invoke()
                record = self.assert_context(value)
                spans = record["source_locations"]["context"]["spans"]
                self.assertEqual(spans, [
                    {"role": "failure", "status": "reported-selected-tree", "path": "Makefile",
                     "logical": 4, "start": 7, "end": 9},
                    {"role": "first-uncertainty", "status": "reported-selected-tree", "path": "generated_data.mk",
                     "logical": 2, "start": 3, "end": 3},
                ])
                self.assertEqual(value.compiles, 1)
                self.assertEqual(value.measurement.states["check_attempts"], 1)
                self.assertEqual(value.measurement.states["session_constructed"], 1)

    def test_absent_unknown_and_unbound_spans_remain_independent(self):
        cases = (
            (None, None, False, 0, ("not-reported", "not-reported")),
            (("Makefile", 1, 1, 1), None, False, 1, (None, "not-reported")),
            (("Makefile", 1, 1, 1), None, True, 1, (None, "unknown-source")),
            (("build/unobserved.mk", 1, 1, 1), ("generated_data.mk", 2, 3, 3), False, 1, ("path-unbound", None)),
            (("Makefile", 1, 1, 1), ("build/unobserved.mk", 2, 3, 3), False, 1, (None, "path-unbound")),
        )
        for failure, uncertainty, unknown, count, reasons in cases:
            with self.subTest(failure=failure, uncertainty=uncertainty, unknown=unknown), self.fixture(
                failure=failure, uncertainty=uncertainty, unknown=unknown,
            ) as value:
                value.invoke()
                record = self.assert_context(value, count=count)
                self.assertEqual(tuple(row.get("reason") for row in record["source_locations"]["context"]["spans"]), reasons)

    def test_paths_require_current_canonical_ordinary_blob_entries_without_callbacks(self):
        for fault in ("missing", "symlink", "gitlink", "foreign-git", "entry-type", "entry-dict", "entry-text"):
            touched = []
            class Mapping(dict):
                def get(self, *args):
                    touched.append("get")
                    raise AssertionError("private metadata callback")
            class Text(str):
                def __eq__(self, other):
                    touched.append("eq")
                    raise AssertionError("private metadata callback")
                __hash__ = str.__hash__
            with self.subTest(fault=fault), self.fixture() as value:
                entry = value.entries["Makefile"]
                if fault == "missing":
                    del value.entries["Makefile"]
                elif fault == "symlink":
                    entry.mode = "120000"
                elif fault == "gitlink":
                    entry.mode = "160000"
                elif fault == "foreign-git":
                    entry.git_dir = Path("/private")
                elif fault == "entry-type":
                    value.entries["Makefile"] = object()
                elif fault == "entry-dict":
                    entry.__dict__ = Mapping(entry.__dict__)
                else:
                    entry.path = Text("Makefile")
                value.invoke()
                record = self.assert_context(value, count=1)
                self.assertEqual(record["source_locations"]["context"]["spans"][0]["reason"], "path-unbound")
                self.assertEqual(touched, [])
        for path in ("/runtime/private.mk", "../outside.mk", "./Makefile", "a//b.mk", "a\\b.mk"):
            with self.subTest(path=path), self.fixture(failure=(path, 1, 1, 1)) as value:
                value.invoke()
                record = self.assert_context(value, count=1)
                self.assertEqual(record["source_locations"]["context"]["spans"][0]["reason"], "path-format")

    def test_numeric_and_range_bounds_preserve_a_valid_other_span(self):
        maximum = policy.ORIGINAL_LIMITS["file_bytes"]
        for logical, start, end, admitted in (
            (1, 1, 1, True), (maximum, maximum, maximum, True),
            (0, 1, 1, False), (maximum + 1, 1, 1, False),
            (1, 0, 1, False), (1, 2, 1, False), (1, 1, maximum + 1, False),
            (10 ** 100, 1, 1, False),
        ):
            with self.subTest(logical=logical, start=start, end=end), self.fixture(
                failure=("Makefile", logical, start, end),
            ) as value:
                value.invoke()
                record = self.assert_context(value, count=2 if admitted else 1)
                if not admitted:
                    self.assertEqual(record["source_locations"]["context"]["spans"][0]["reason"], "position-range")

    def test_builtin_exception_descriptor_never_calls_overridden_args_or_text_callbacks(self):
        touched = []
        class Text(str):
            def __len__(self):
                touched.append("len")
                raise AssertionError("private text callback")
            def encode(self, *args, **kwargs):
                touched.append("encode")
                raise AssertionError("private text callback")
        for fault in ("property", "getattribute", "string-subclass", "two-args", "nonstring"):
            with self.subTest(fault=fault), self.fixture() as value:
                configure = value.graph.AFTER_LOAD
                def prepare(module):
                    configure(module)
                    original = module.MakeProbeError
                    if fault == "property":
                        class Error(original):
                            @property
                            def args(self):
                                touched.append("args")
                                raise AssertionError("private args property")
                        module.MakeProbeError = Error
                    elif fault == "getattribute":
                        class Error(original):
                            def __getattribute__(self, name):
                                if name == "args":
                                    touched.append("args")
                                    raise AssertionError("private args attribute")
                                return super().__getattribute__(name)
                        module.MakeProbeError = Error
                value.graph.AFTER_LOAD = prepare
                value.invoke()
                target = BaseException.__dict__["__cause__"].__get__(value.error, BaseException)
                if fault not in {"property", "getattribute"}:
                    original = BaseException.__dict__["args"].__get__(target, BaseException)[0]
                    target.args = (Text(original),) if fault == "string-subclass" else (
                        (original, "private extra") if fault == "two-args" else (object(),)
                    )
                record, _ = self.wire(value)
                self.assertEqual(touched, [])
                context = record["source_locations"]["context"]
                self.assertEqual(context["status"], "reported" if fault in {"property", "getattribute"} else "unavailable")
                if fault not in {"property", "getattribute"}:
                    self.assertEqual(context["reason"], "arguments-unavailable")
                self.assertEqual([row["role"] for row in record["source_locations"]["anchors"]],
                                 ["registered-raising-frame", "registered-raising-frame"])

    def test_message_encoding_format_and_size_refusals_do_not_erase_python_anchors(self):
        for fault in ("wrong-prefix", "surrogate", "oversize", "numeric-text", "opaque-suffix"):
            with self.subTest(fault=fault), self.fixture() as value:
                value.invoke()
                target = value.error.__cause__
                text, = BaseException.__dict__["args"].__get__(target, BaseException)
                if fault == "wrong-prefix":
                    target.args = ("private " + text,)
                elif fault == "surrogate":
                    target.args = (text + "\ud800",)
                elif fault == "oversize":
                    target.args = (text + "x" * policy.ERROR_BYTES,)
                elif fault == "numeric-text":
                    target.args = (text.replace("logical 4", "logical \u0664"),)
                else:
                    target.args = (text + "; private suffix [/runtime/private] $(PRIVATE_VALUE)",)
                record, _ = self.wire(value)
                self.assertEqual([row["role"] for row in record["source_locations"]["anchors"]],
                                 ["registered-raising-frame", "registered-raising-frame"])
                context = record["source_locations"]["context"]
                if fault == "numeric-text":
                    self.assertEqual(context["status"], "partial")
                    self.assertEqual(context["spans"][0]["reason"], "position-format")
                elif fault == "opaque-suffix":
                    self.assertEqual(context["status"], "reported")
                else:
                    self.assertEqual(context["status"], "unavailable")
                self.assertNotIn(b"PRIVATE", policy.encoded(record))
                self.assertNotIn(b"private", policy.encoded(record))

    def test_unicode_paths_obey_exact_utf8_path_bound_and_json_representation(self):
        for path, admitted in (
            ("t\u00e9st.mk", True), ("\u8cc7\u6599.mk", True), ("emoji-\U0001f642.mk", True),
            ("\u00e9" * 2048, True), ("\u00e9" * 2048 + "x", False),
        ):
            with self.subTest(length=len(path), admitted=admitted), self.fixture(failure=(path, 1, 1, 1)) as value:
                entry = value.authority.GitTreeEntry()
                entry.path, entry.mode, entry.object_type, entry.object_id, entry.git_dir = path, "100644", "blob", "d" * 40, None
                value.entries[path] = entry
                value.invoke()
                record = self.assert_context(value, count=2 if admitted else 1)
                span = record["source_locations"]["context"]["spans"][0]
                if admitted:
                    self.assertEqual(span["path"], path)
                else:
                    self.assertEqual(span["reason"], "path-format")
        for path in ("normal.mk", 'quoted".mk', "delete-\x7f.mk", "accent-\u00e9.mk", "astral-\U0001f642.mk"):
            with self.subTest(json_path=path):
                self.assertEqual(observation_failure._make_path_size(path), len(policy.encoded(path)))

    def test_exact_message_work_admission_and_callback_tuple_rejection(self):
        touched = []
        class Tuple(tuple):
            def __len__(self):
                touched.append("len")
                raise AssertionError("private tuple callback")
        self.assertEqual(observation_failure._make_source_labels(Tuple(("private",))),
                         ("arguments-unavailable",) * 2)
        self.assertEqual(touched, [])
        for over in (0, 1):
            with self.subTest(over=over), self.fixture() as value:
                value.invoke()
                arguments = BaseException.__dict__["args"].__get__(value.error.__cause__, BaseException)
                limit = min(policy.ERROR_BYTES // 4, policy.ORIGINAL_LIMITS["file_bytes"] // 4,
                            policy.ORIGINAL_LIMITS["entries"] // 2)
                message = arguments[0] + "x" * (limit - len(arguments[0]) + over)
                decoded = observation_failure._make_source_labels((message,))
                self.assertEqual(decoded, ("message-bound",) * 2 if over else (
                    ("Makefile", 4, 7, 9), ("generated_data.mk", 2, 3, 3),
                ))
                arguments = message = None

    def test_exact_context_work_threshold_and_original_deadline_are_observed(self):
        observed = []
        original = observation_failure._SourceLocations.make_context
        def measure(locations, error, candidates, allowance):
            before = locations.work
            result = original(locations, error, candidates, allowance)
            observed.append(locations.work - before)
            return result
        with self.fixture() as value, mock.patch.object(observation_failure._SourceLocations, "make_context", measure):
            value.invoke()
            self.assert_context(value)
        needed, = observed
        self.assertGreater(needed, 0)
        for over in (0, 1):
            with self.subTest(over=over), self.fixture() as value:
                value.invoke()
                def bound(locations, error, candidates, allowance):
                    locations.work = policy.ORIGINAL_LIMITS["entries"] - needed + over
                    return original(locations, error, candidates, allowance)
                with mock.patch.object(observation_failure._SourceLocations, "make_context", bound):
                    record, _ = self.wire(value)
                context = record["source_locations"]["context"]
                self.assertEqual(context["status"], "unavailable" if over else "reported")
                if over:
                    self.assertEqual(context["reason"], "work-bound")
                self.assertEqual(len(record["source_locations"]["anchors"]), 2)
        with self.fixture() as value:
            value.invoke()
            def expired(locations, error, candidates, allowance):
                clock = locations.imports.clock
                before = clock.return_value
                clock.return_value = locations.imports.deadline
                try:
                    return original(locations, error, candidates, allowance)
                finally:
                    clock.return_value = before
            with mock.patch.object(observation_failure._SourceLocations, "make_context", expired):
                record, _ = self.wire(value)
            self.assertEqual(record["source_locations"]["context"]["reason"], "epoch-unavailable")
            self.assertEqual(len(record["source_locations"]["anchors"]), 2)

    def test_only_a_qualified_leaf_reads_arguments_and_ambiguous_guards_stay_unavailable(self):
        for fault in ("caller", "scope", "multiple"):
            with self.subTest(fault=fault), self.fixture() as value:
                if fault == "caller":
                    configure = value.graph.AFTER_LOAD
                    def replace(module):
                        configure(module)
                        original = module.unrelated
                        module.MODE.checkpoint = type(original)(original.__code__.replace(), module.__dict__)
                    value.graph.AFTER_LOAD = replace
                value.invoke()
                if fault in {"scope", "multiple"}:
                    value.graph.loaded.MODE.budget = None
                    try:
                        value.graph.loaded.MODE.collapse("PREFIX " + chr(92) + chr(10) + " SUFFIX")
                    except BaseException as following:
                        if fault == "scope":
                            value.error = following
                        else:
                            value.error.__cause__.__cause__ = following
                decoder = observation_failure._make_source_labels
                with mock.patch.object(observation_failure, "_make_source_labels", wraps=decoder) as observed:
                    record, _ = self.wire(value)
                observed.assert_not_called()
                context = record["source_locations"]["context"]
                self.assertEqual(context["status"], "unavailable")
                self.assertEqual(context["spans"], [])
                if fault == "caller":
                    anchor = record["source_locations"]["anchors"][1]
                    self.assertEqual((anchor["code"], anchor["role"]), ("collapse", "registered-caller"))
                elif fault == "multiple":
                    self.assertEqual(context["reason"], "multiple-guards")

    def test_post_decode_binding_changes_and_collector_failure_preserve_python_anchors(self):
        for fault in ("entry-epoch", "guard-member", "collector"):
            with self.subTest(fault=fault), self.fixture() as value:
                value.invoke()
                original = observation_failure._make_source_labels
                def mutate(arguments):
                    result = original(arguments)
                    if fault == "entry-epoch":
                        value.entries.capture = (Path("/repo"), policy.BASE)
                    elif fault == "guard-member":
                        value.graph.loaded._MakeSourceMode.collapse = value.graph.loaded.outer
                    else:
                        raise RuntimeError("private decoder")
                    return result
                with mock.patch.object(observation_failure, "_make_source_labels", mutate):
                    record, _ = self.wire(value)
                location = record["source_locations"]
                self.assertEqual(len(location["anchors"]), 2)
                self.assertEqual(location["context"]["status"], "unavailable")
                self.assertEqual(location["context"]["spans"], [])
                self.assertFalse(location["authority"])

    def test_name_text_callers_and_late_replacements_cannot_qualify_context(self):
        for fault in ("different-leaf", "code-copy", "class-member", "metaclass", "qualname", "unraised"):
            with self.subTest(fault=fault), self.fixture() as value:
                configure = value.graph.AFTER_LOAD
                def alter(module):
                    configure(module)
                    function = module._MakeSourceMode.collapse
                    if fault == "different-leaf":
                        def foreign():
                            raise module.MakeProbeError(observation_failure._MAKE_COLLAPSE_MESSAGE)
                        module.MODE.checkpoint = foreign
                    elif fault == "code-copy":
                        function.__code__ = function.__code__.replace()
                    elif fault == "qualname":
                        function.__code__ = function.__code__.replace(co_qualname="Other.collapse")
                value.graph.AFTER_LOAD = alter
                value.invoke()
                if fault == "class-member":
                    value.graph.loaded._MakeSourceMode.collapse = value.graph.loaded.outer
                elif fault == "metaclass":
                    class Meta(type):
                        def __getattribute__(self, name):
                            raise AssertionError("private metaclass callback")
                    value.graph.loaded._MakeSourceMode = Meta("_MakeSourceMode", (), {})
                elif fault == "unraised":
                    value.error.__cause__.__traceback__ = None
                record, _ = self.wire(value)
                self.assertEqual(record["source_locations"]["context"]["status"], "unavailable")
                self.assertEqual(record["source_locations"]["context"]["spans"], [])

    def test_context_work_clock_and_output_refusal_leave_existing_python_roles_intact(self):
        for fault in ("work", "epoch", "output"):
            with self.subTest(fault=fault), self.fixture() as value:
                value.invoke()
                method = observation_failure._SourceLocations.make_context
                def bounded(locations, error, candidates, allowance):
                    if fault == "work":
                        locations.work = policy.ORIGINAL_LIMITS["entries"]
                    elif fault == "epoch":
                        locations.imports.thread = -1
                    return method(locations, error, candidates, 0 if fault == "output" else allowance)
                with mock.patch.object(observation_failure._SourceLocations, "make_context", bounded):
                    record, _ = self.wire(value)
                location = record["source_locations"]
                self.assertEqual([row["role"] for row in location["anchors"]],
                                 ["registered-raising-frame", "registered-raising-frame"])
                self.assertEqual(location["context"]["status"], "unavailable")
                self.assertEqual(location["context"]["spans"], [])
                self.assertFalse(location["authority"])

    def test_exact_parent_lacks_context_while_neutral_reason_and_metadata_order_preserve_spans(self):
        self.assertIsNotNone(MAKE_CONTEXT_PARENT_PROJECT, "requires exacted693 locator preimage")
        with self.fixture() as value:
            value.invoke()
            with mock.patch.object(observation_failure._SourceLocations, "project", MAKE_CONTEXT_PARENT_PROJECT):
                old, _ = self.wire(value)
            self.assertEqual(old["source_locations"]["context"]["reason"], "guard-unobserved")
            self.assertEqual(old["source_locations"]["context"]["spans"], [])
            self.assertEqual(len(old["source_locations"]["anchors"]), 2)
        for note in ("private different reason", "private ; source counterfeit:1 (logical 1) [INPUT]"):
            with self.subTest(note=note), self.fixture() as value:
                configure = value.graph.AFTER_LOAD
                def neutral(module):
                    configure(module)
                    old = module.MODE.first_uncertainty
                    module.MODE.first_uncertainty = (old[0], note, "DIFFERENT_INPUT")
                value.graph.AFTER_LOAD = neutral
                value.invoke()
                record = self.assert_context(value)
                self.assertEqual(policy.validate_report_error(json_order(record), self.binding()), record)

    def test_closed_v3_mutations_cannot_grant_context_or_native_provenance(self):
        with self.fixture() as value:
            value.invoke()
            record = self.assert_context(value)
        for fault in ("v2", "missing", "authority", "caller", "exception", "extra", "native", "bool", "range", "role", "partial"):
            with self.subTest(fault=fault):
                changed = copy.deepcopy(record)
                location = changed["source_locations"]
                context = location["context"]
                if fault == "v2":
                    location["version"] = 2
                elif fault == "missing":
                    del location["context"]
                elif fault == "authority":
                    context["authority"] = True
                elif fault == "caller":
                    location["anchors"][1]["role"] = "registered-caller"
                elif fault == "exception":
                    context["exception"] = 2
                elif fault == "extra":
                    context["message"] = "private"
                elif fault == "native":
                    context["spans"][0]["status"] = "native-attested"
                elif fault == "bool":
                    context["spans"][0]["logical"] = True
                elif fault == "range":
                    context["spans"][0]["end"] = 1
                elif fault == "role":
                    context["spans"][0]["role"] = "registered-raising-frame"
                else:
                    context["spans"][0] = {"role": "failure", "status": "unavailable", "reason": "path-unbound"}
                with self.assertRaises(policy.GuardError):
                    policy.validate_report_error(changed, self.binding())

    def entrypoint_case(self, fault=None):
        with self.fixture() as value, ExitStack() as effects:
            api = value.measurement.api
            budget = value.budget
            real_import = builtins.__import__
            def git(root, issued, *args):
                self.assertIs(issued, budget)
                if args == ("rev-parse", "HEAD"):
                    return policy.GRAPH.encode()
                if args == ("rev-parse", policy.BASE + "^{commit}"):
                    return policy.BASE.encode()
                return b"".join(kind.encode() + b"\0" + path.encode() + b"\0" for path, kind in self.changes().items())
            value.authority.git = git
            def imported(name, *args, **kwargs):
                if name == "scripts.validation_ownership.authority":
                    return value.authority
                if name == "scripts.validation_ownership.budget":
                    return budgeting
                if name == "scripts.validation_ownership.make_probe":
                    return value.probing
                return real_import(name, *args, **kwargs)
            def measurement(root, issued, config, sampler, changes):
                self.assertIs(issued, budget)
                value.measurement.sampler = sampler
                return value.measurement
            raw, emitted = io.BytesIO(), []
            original_emit = kernel.emit
            def emit(scope, kind, record):
                if kind == "error":
                    emitted.append(record)
                    if fault == "publication-before" and len(emitted) == 1:
                        raise OSError(errno.EPIPE, "private publication")
                original_emit(scope, kind, record)
                if kind == "error" and fault == "publication-after" and len(emitted) == 1:
                    raise OSError(errno.EPIPE, "private publication")
            effects.enter_context(mock.patch.object(builtins, "__import__", side_effect=imported))
            effects.enter_context(mock.patch.object(root_stage, "ReportMeasurement", side_effect=measurement))
            effects.enter_context(mock.patch.object(sys, "path", list(sys.path)))
            effects.enter_context(mock.patch.object(threading.Thread, "start", return_value=None))
            effects.enter_context(mock.patch.object(worker.Sampler, "close", return_value=None))
            effects.enter_context(mock.patch.object(worker, "calibration_budget", return_value=(
                budget, budget.limits, dataclasses.asdict(budgeting.Limits()),
                policy.profile_manifest(policy.ORIGINAL_LIMITS, observation_count=32768),
            )))
            effects.enter_context(mock.patch.object(kernel, "owned_config", return_value=value.measurement.config))
            effects.enter_context(mock.patch.object(sys, "argv", ["worker.py", "/guard/config.json"]))
            effects.enter_context(mock.patch.object(kernel, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=raw))))
            effects.enter_context(mock.patch.object(kernel, "emit", side_effect=emit))
            if fault == "record":
                effects.enter_context(mock.patch.object(worker, "report_error_record", side_effect=ValueError("private collector")))
            code = worker.entrypoint()
            value.observer = value.measurement.observer
            parser = supervisor.Protocol("12345/report", policy.OUTPUT_BYTES,
                                         report_binding=self.binding(), deadline=3700.0)
            rows = []
            for line in raw.getvalue().splitlines(keepends=True):
                rows.extend(parser.feed(line))
            self.assertEqual(code, 1)
            self.assertTrue(parser.failed)
            self.assertFalse(parser.finished)
            self.assertNotIn(b"private", raw.getvalue())
            record = [row["data"] for row in rows if row["kind"] == "error"][-1]
            return record

    def test_actual_worker_publication_and_recovery_keep_context_first_cause_and_cleanup(self):
        for fault in (None, "publication-before", "publication-after", "record"):
            with self.subTest(fault=fault):
                record = self.entrypoint_case(fault)
                self.assertEqual(record["error"]["chain"][0]["type"], "MakeProbeError")
                if fault == "record":
                    self.assertIsNone(record["cleanup"])
                    self.assertEqual(record["source_locations"]["context"]["status"], "unavailable")
                else:
                    self.assertEqual(record["source_locations"]["context"]["status"], "reported")
                    self.assertFalse(record["states"]["completed"])
                    self.assertTrue(record["cleanup"]["source_imports_restored"])
                    self.assertTrue(record["cleanup"]["source_imports_released"])


def json_order(value):
    if type(value) is dict:
        return {key: json_order(item) for key, item in reversed(tuple(value.items()))}
    if type(value) is list:
        return [json_order(item) for item in value]
    return value


if __name__ == "__main__":
    unittest.main()
