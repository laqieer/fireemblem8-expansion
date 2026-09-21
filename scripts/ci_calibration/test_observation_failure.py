"""Actual bounded observers with synthetic effects and real budget admissions."""

import errno
import copy
import io
from contextlib import contextmanager
from functools import wraps
from importlib.machinery import ModuleSpec, SourceFileLoader
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

from scripts.ci_calibration import kernel, observation_failure, policy, root_stage, supervisor, worker
from scripts.ci_calibration.test_ci_calibration import Inert, budgeting


PREIMAGE_SOURCE_CLEANUP_COUNT = None
PREIMAGE_REPORT_ERROR_RECORD = None
PREIMAGE_REGISTRATION_MODULE = None


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
                     "report_released": True, "serialization_released": True},
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


if __name__ == "__main__":
    unittest.main()
