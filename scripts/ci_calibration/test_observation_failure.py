"""Actual bounded observers with synthetic effects and real budget admissions."""

import errno
import copy
from types import SimpleNamespace
import unittest

from scripts.ci_calibration import observation_failure, policy, supervisor
from scripts.ci_calibration.test_ci_calibration import Inert, budgeting


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
            diagnostic=True, before=policy.CONTROL_CEILING - 1, request=1, other=1,
        )
        value = observer.budget_admission(error)
        observation_failure.validate_fact(value, admission=True)
        self.assertEqual(value["issued_cap"], policy.CONTROL_CEILING)
        self.assertFalse(value["category_exhausted"])
        self.assertEqual(value["category_shortfall"], 0)
        self.assertIsNone(value["total_at_admission"])
        self.assertEqual(value["collected"]["total_charged"], policy.CONTROL_CEILING)
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
            "cleanup": None, "counters": None, "secondary": [], "source_cleanup_failures": 0,
            "summary": None, "serialized_bytes": None,
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


if __name__ == "__main__":
    unittest.main()
