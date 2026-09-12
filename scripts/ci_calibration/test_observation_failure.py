"""Real narrow policy failures, explicit report carriers; never a graph run."""

import os
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.ci_calibration import kernel, observation_failure, policy, supervisor, worker
from scripts.validation_ownership.make_probe import MakeProbeError, ProbeSession
from scripts.validation_ownership.syscall_guard import Policy, Violation


class FrameFixture:
    """Only this fixture's code is bound in unit tests, never as production code."""

    def __init__(self, budget):
        self.budget = budget
        self.cleaned = 0

    def _sandbox_run(
        self, guard, initial, *, mode="command", producer=False, missing=None,
        changes=None, config_changes=None, unsettled=False, malformed_report=False,
    ):
        config = {
            **initial, "mode": mode,
            "environment": {"TOKEN": "must-not-export-environment"},
            "report": "/must-not-export-candidate-path",
        }
        raw_payload = b"must-not-export-buffer"
        producer_handler = object() if producer else None
        channel = object() if producer else None
        if producer:
            config["producer_endpoint"] = "/must-not-export-endpoint"
        try:
            guard.reserve_observation("accessed", "b")
        except Violation as cause:
            observed = policy.parse_json(policy.encoded({
                "ok": False, "error": str(cause), **guard.counters(),
            }))
            if changes:
                observed.update(changes)
            if config_changes:
                config.update(config_changes)
            settled = {
                name: observed[name] for name in ("observations", "observation_bytes")
            }
            if unsettled:
                settled["observations"] = -1
            if malformed_report:
                observed = []
            if missing == "observed":
                del observed
            elif missing == "config":
                del config
            elif missing == "channel":
                del channel
            elif missing == "producer_handler":
                del producer_handler
            try:
                raise MakeProbeError("fixture confined probe rejected") from cause
            finally:
                self.cleaned += 1
        else:
            raise AssertionError("fixture expected an actual observation rejection")


class ObservationFailureControls(unittest.TestCase):
    def guard(self, count=1, size=258):
        guard = Policy.__new__(Policy)
        guard.config = {
            "descendant_limit": 1, "syscall_limit": 10, "write_limit": 10, "creation_limit": 1,
            "observation_count": count, "observation_limit": size,
            "process_limit": 1, "memory_limit": 4096,
        }
        guard.observation_attempts = {name: set() for name in ("accessed", "consumed", "code_consumed")}
        guard.observation_bytes = 0
        guard.total_processes = guard.calls = guard.written = guard.created = 0
        guard.live_process_peak = guard.memory_peak = 0
        guard.processes, guard.newborn_stops, guard.producer_requests = {}, set(), []
        return guard

    def failure(self, *, count=1, size=258, grant=None, **options):
        budget = object()
        carrier = FrameFixture(budget)
        observer = observation_failure.Observer(FrameFixture, budget)
        guard = self.guard(count, size)
        initial = guard.config.copy()
        guard.reserve_observation("accessed", "a")
        if grant is not None:
            guard.apply_producer_limits({**initial, **grant}, initial)
        try:
            carrier._sandbox_run(guard, initial, **options)
        except MakeProbeError as error:
            return error, observer, carrier, guard
        self.fail("missing original failure")

    def test_actual_count_bytes_and_both_are_distinct_with_exact_operators(self):
        for count, size, expected, predicates in (
            (1, 258, "count", (True, False)),
            (2, 257, "bytes", (False, True)),
            (1, 257, "both", (True, True)),
        ):
            with self.subTest(expected=expected):
                error, observer, carrier, guard = self.failure(count=count, size=size)
                result = observer.capture(error)
                self.assertEqual(result["status"], "attributed")
                self.assertEqual(result["exhaustion"], expected)
                self.assertEqual((result["count_predicate"], result["byte_predicate"]), predicates)
                self.assertEqual((result["observations"], result["observation_bytes"]), (1, 258))
                self.assertEqual(result["effective_observation_count"], count)
                self.assertEqual(result["effective_observation_limit"], size)
                self.assertEqual(guard.observation_attempts["accessed"], {"a"})
                self.assertIs(type(error.__cause__), Violation)
                self.assertEqual(str(error.__cause__), observation_failure.NATIVE_REJECTION)
                self.assertEqual(carrier.cleaned, 1)
                self.assertLess(len(policy.encoded(result)), 1024)

    def test_exact_admission_and_duplicate_do_not_invent_an_overflow(self):
        guard = self.guard(count=1, size=129)
        guard.reserve_observation("accessed", "a")
        guard.reserve_observation("accessed", "a")
        self.assertEqual(guard.counters()["observations"], 1)
        self.assertEqual(guard.counters()["observation_bytes"], 129)
        error, observer, _, _ = self.failure(mode="compile")
        self.assertEqual(observer.capture(error)["exhaustion"], "count")

    def test_actual_replacement_grants_are_not_inferred_from_parent_config(self):
        for grant in (
            {"observation_count": 1, "observation_limit": 258},
            {"observation_count": 4, "observation_limit": 257},
        ):
            with self.subTest(grant=grant):
                error, observer, _, guard = self.failure(
                    count=4, size=4096, grant=grant, mode="make", producer=True,
                )
                self.assertEqual({name: guard.config[name] for name in grant}, grant)
                result = observer.capture(error)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["initial_observation_count"], 4)
                self.assertEqual(result["initial_observation_limit"], 4096)
                self.assertEqual((result["observations"], result["observation_bytes"]), (1, 258))
                for name in (
                    "effective_observation_count", "effective_observation_limit",
                    "count_predicate", "byte_predicate", "exhaustion",
                ):
                    self.assertIsNone(result[name])
        error, observer, _, _ = self.failure(mode="make")
        self.assertEqual(observer.capture(error)["status"], "unknown")

    def test_missing_unrelated_and_malformed_frames_never_fabricate_attribution(self):
        for options, status in (
            ({"missing": "observed"}, "unavailable"),
            ({"changes": {"error": "a different original rejection"}}, "unavailable"),
            ({"changes": {"ok": True}}, "unavailable"),
            ({"missing": "config"}, "invalid"),
            ({"missing": "channel"}, "invalid"),
            ({"missing": "producer_handler"}, "invalid"),
            ({"malformed_report": True}, "invalid"),
            ({"changes": {"ok": 0}}, "invalid"),
            ({"changes": {"error": None}}, "invalid"),
            ({"changes": {"observations": True}}, "invalid"),
            ({"changes": {"observation_bytes": -1}}, "invalid"),
            ({"changes": {"observation_bytes": 1.5}}, "invalid"),
            ({"changes": {"observation_bytes": 1 << 63}}, "invalid"),
            ({"changes": {"observations": 2}}, "invalid"),
            ({"changes": {"observation_bytes": 127}}, "invalid"),
            ({"config_changes": {"observation_count": 0}}, "invalid"),
            ({"config_changes": {"observation_limit": None}}, "invalid"),
            ({"config_changes": {"observation_count": 2, "observation_limit": 258}}, "invalid"),
            ({"config_changes": {"mode": "compile"}}, "invalid"),
            ({"mode": "other"}, "invalid"),
            ({"unsettled": True}, "invalid"),
            ({"producer": True}, "invalid"),
        ):
            with self.subTest(options=options):
                error, observer, carrier, _ = self.failure(**options)
                original = policy.error_record(error)
                result = observer.capture(error)
                self.assertEqual(result["status"], status)
                self.assertEqual(set(result), {"status", "reason"})
                self.assertEqual(policy.error_record(error), original)
                self.assertEqual(carrier.cleaned, 1)

    def test_code_session_and_budget_identity_are_required(self):
        error, observer, carrier, _ = self.failure()
        real_binding = observation_failure.Observer(ProbeSession, carrier.budget)
        self.assertEqual(real_binding.capture(error)["status"], "unavailable")
        wrong_budget = observation_failure.Observer(FrameFixture, object())
        self.assertEqual(wrong_budget.capture(error)["status"], "invalid")

        class DifferentSession(FrameFixture):
            pass

        wrong_type = observation_failure.Observer(DifferentSession, carrier.budget)
        self.assertIs(wrong_type.code, observer.code)
        self.assertEqual(wrong_type.capture(error)["status"], "invalid")
        self.assertEqual(observer.capture(RuntimeError(observation_failure.NATIVE_REJECTION))["status"], "unavailable")

    def test_duplicate_frame_is_not_a_second_observation_but_distinct_frames_are_ambiguous(self):
        error, observer, carrier, _ = self.failure()
        try:
            raise error
        except MakeProbeError as reraised:
            self.assertEqual(observer.capture(reraised)["status"], "attributed")
        other, _, other_carrier, _ = self.failure()
        other_carrier.budget = carrier.budget
        other.__cause__.__cause__ = error
        self.assertEqual(observer.capture(other), observation_failure.invalid("multiple-native-rejection-frames"))

    def test_chain_and_trace_bounds_do_not_truncate_into_a_positive_attribution(self):
        error, observer, _, _ = self.failure()
        error.__cause__.__cause__ = error
        self.assertEqual(observer.capture(error), observation_failure.unavailable("cyclic-exception-chain"))
        error.__cause__.__cause__ = None
        for _ in range(33):
            outer = RuntimeError("bounded chain fixture")
            outer.__cause__ = error
            error = outer
        self.assertEqual(observer.capture(error), observation_failure.unavailable("exception-chain-bound"))

        def recurse(depth):
            if depth:
                return recurse(depth - 1)
            raise RuntimeError("bounded traceback fixture")

        try:
            recurse(260)
        except RuntimeError as deep:
            self.assertEqual(observer.capture(deep), observation_failure.unavailable("trace-frame-bound"))

    def test_scalar_output_is_bounded_even_at_the_existing_integer_ceiling(self):
        error, observer, _, _ = self.failure(
            changes={"observation_bytes": policy.POLICY_SENTINEL},
            config_changes={"observation_limit": policy.POLICY_SENTINEL - 1},
        )
        result = observer.capture(error)
        self.assertEqual(result["exhaustion"], "both")
        self.assertLess(len(policy.encoded(result)), 1024)
        self.assertTrue(all(type(value) in {str, int, bool, type(None)} for value in result.values()))

    def test_real_pipe_keeps_original_failure_and_exports_no_arbitrary_locals(self):
        error, observer, carrier, _ = self.failure()
        original, cause, trace = policy.error_record(error), error.__cause__, error.__traceback__
        sampler = worker.Sampler("fixture")
        sampler.budget = SimpleNamespace(
            bytes={"control": 9999}, runs=0, states=0, failed=True, closed=False,
        )
        record = worker.graph_error_record(error, sampler, observer)
        self.assertEqual({key: record[key] for key in original}, original)
        self.assertIs(error.__cause__, cause)
        self.assertIs(error.__traceback__, trace)
        self.assertEqual(record["observation_failure"]["observation_bytes"], 258)
        self.assertEqual(record["counters"]["budget"]["bytes"]["control"], 9999)
        self.assertEqual(
            worker.graph_error_record(error, sampler, None)["observation_failure"],
            observation_failure.unavailable("binding-not-ready"),
        )
        reader, writer = os.pipe()
        with os.fdopen(reader, "rb") as incoming:
            with os.fdopen(writer, "wb") as outgoing, mock.patch.object(
                kernel.sys, "stdout", SimpleNamespace(buffer=outgoing),
            ):
                kernel.emit("fixture", "ready", {})
                kernel.emit("fixture", "error", record)
            raw = incoming.read(policy.ERROR_BYTES + 1)
        self.assertLess(len(raw), policy.ERROR_BYTES)
        self.assertNotIn(b"must-not-export", raw)
        parser = supervisor.Protocol("fixture", policy.OUTPUT_BYTES)
        ready, rejected = parser.feed(raw)
        self.assertEqual(ready["kind"], "ready")
        self.assertEqual(rejected, {"scope": "fixture", "kind": "error", "data": record})
        self.assertFalse(parser.finished)
        with self.assertRaises(policy.GuardError):
            policy.validate_report(rejected["data"])
        self.assertEqual(carrier.cleaned, 1)


if __name__ == "__main__":
    unittest.main()
