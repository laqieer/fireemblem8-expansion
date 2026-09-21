"""Scalar-only attribution of the pinned native observation rejection."""

from __future__ import annotations

if __package__:
    from . import policy
else:
    import policy


NATIVE_REJECTION = "aggregate filesystem-observation budget exhausted"


def unavailable(reason):
    return {"status": "unavailable", "reason": reason}


def invalid(reason):
    return {"status": "invalid", "reason": reason}


def scalar(value, *, positive=False):
    return type(value) is int and int(positive) <= value <= policy.POLICY_SENTINEL


def validate_fact(value, *, admission):
    if type(value) is not dict or type(value.get("status")) is not str:
        raise policy.GuardError("failure attribution is not a typed observation")
    if value["status"] in {"invalid", "unavailable"}:
        policy._component_fields(value, "status reason")
        if type(value["reason"]) is not str or value["reason"] not in {
            "binding-not-ready", "collector-failed", "foreign-admission-budget",
            "unretained-admission-category", "malformed-admission-scalars", "admission-cap-changed",
            "admission-collection-disagreement", "admission-binding-not-ready", "cyclic-exception-chain",
            "exception-chain-bound", "trace-frame-bound", "multiple-admission-frames", "no-matching-admission",
            "foreign-session-or-budget", "malformed-native-report", "missing-or-invalid-native-context",
            "malformed-observation-scalars", "native-report-not-settled", "producer-channel-outside-native-make",
            "native-rejection-without-either-predicate", "multiple-native-rejection-frames",
            "no-matching-native-rejection",
        }:
            raise policy.GuardError("failure attribution has an unsupported unavailable reason")
        return value
    if admission:
        policy._component_fields(value, (
            "status category requested charged_before issued_cap prospective_category remaining_category "
            "category_exhausted category_shortfall total_at_admission total_predicate total_cap "
            "original_category_cap diagnostic_override collected semantics"
        ))
        if value["status"] != "observed" or type(value["category"]) is not str or (
            value["category"] not in policy.BYTE_CATEGORIES
        ):
            raise policy.GuardError("admission attribution has no original category")
        if any(not scalar(value[name]) for name in (
            "requested", "charged_before", "issued_cap", "prospective_category", "remaining_category",
            "category_shortfall", "total_cap", "original_category_cap",
        )) or (
            not 1 <= value["original_category_cap"] <= policy.ORIGINAL_LIMITS[value["category"] + "_bytes"]
            or value["issued_cap"] < 1
            or value["prospective_category"] != value["charged_before"] + value["requested"]
            or value["remaining_category"] != value["issued_cap"] - value["charged_before"]
            or type(value["category_exhausted"]) is not bool
            or value["category_exhausted"] != (value["prospective_category"] > value["issued_cap"])
            or value["category_shortfall"] != max(0, value["prospective_category"] - value["issued_cap"])
            or type(value["diagnostic_override"]) is not bool
            or value["diagnostic_override"] != (value["issued_cap"] != value["original_category_cap"])
            or value["total_at_admission"] is not None or value["total_predicate"] is not None
            or value["total_cap"] < 1
            or value["semantics"] != (
                "Refusal locals and later collection are distinct; aggregate expression was not retained."
            )
        ):
            raise policy.GuardError("admission attribution replaced or contradicted an observed fact")
        collected = value["collected"]
        policy._component_fields(collected, "category_charged total_charged runs states closed owned_children")
        if any(not scalar(collected[name]) for name in (
            "category_charged", "total_charged", "runs", "states", "owned_children",
        )) or (
            type(collected["closed"]) is not bool
            or collected["category_charged"] != value["charged_before"]
            or not collected["category_charged"] <= collected["total_charged"] <= value["total_cap"]
        ):
            raise policy.GuardError("admission collection facts are malformed")
        return value
    policy._component_fields(value, (
        "status reason mode observations observation_bytes initial_observation_count initial_observation_limit "
        "effective_observation_count effective_observation_limit count_predicate byte_predicate exhaustion"
    ))
    if type(value["mode"]) is not str or value["mode"] not in {"command", "compile", "make"} or any(
        not scalar(value[name], positive=name.startswith("initial_")) for name in (
            "observations", "observation_bytes", "initial_observation_count", "initial_observation_limit",
        )
    ) or value["observations"] > value["initial_observation_count"] or (
        value["observation_bytes"] < 128 * value["observations"]
    ):
        raise policy.GuardError("native failure attribution is malformed")
    if value["mode"] == "make":
        if value["status"] != "unknown" or value["reason"] != "make-effective-grant-unavailable" or any(
            value[name] is not None for name in (
                "effective_observation_count", "effective_observation_limit",
                "count_predicate", "byte_predicate", "exhaustion",
            )
        ):
            raise policy.GuardError("native Make effective grants must remain explicitly unknown")
    else:
        count = value["observations"] >= value["initial_observation_count"]
        size = value["observation_bytes"] > value["initial_observation_limit"]
        if (
            value["status"] != "attributed" or value["reason"] != "fixed-non-producer-grant"
            or not count and not size
            or type(value["effective_observation_count"]) is not int
            or type(value["effective_observation_limit"]) is not int
            or value["effective_observation_count"] != value["initial_observation_count"]
            or value["effective_observation_limit"] != value["initial_observation_limit"]
            or value["count_predicate"] is not count or value["byte_predicate"] is not size
            or value["exhaustion"] != ("both" if count and size else "count" if count else "bytes")
        ):
            raise policy.GuardError("native failure attribution disagrees with the fixed grant")
    return value


class Observer:
    def __init__(self, session_type, budget):
        self.session_type = session_type
        self.code = session_type._sandbox_run.__code__
        self.budget = budget
        charge = getattr(type(budget), "charge", None)
        self.charge_code = getattr(charge, "__code__", None)
        self.budget_error = getattr(charge, "__globals__", {}).get("MakeProbeError")

    def _admission_frame(self, error, values):
        if values.get("self") is not self.budget:
            return invalid("foreign-admission-budget")
        category = values.get("category")
        if type(category) is not str or category not in policy.BYTE_CATEGORIES:
            return unavailable("unretained-admission-category")
        arguments = BaseException.__getattribute__(error, "args")
        if (
            type(error) is not self.budget_error or type(arguments) is not tuple or len(arguments) != 1
            or type(arguments[0]) is not str or arguments[0] != f"aggregate {category} byte budget exhausted"
        ):
            return None
        requested, cap, used = (values.get(name) for name in ("size", "cap", "used"))
        if not scalar(requested) or not scalar(cap, positive=True) or not scalar(used) or used < requested:
            return invalid("malformed-admission-scalars")
        charged = used - requested
        if cap != self.budget.cumulative_limit(category + "_bytes"):
            return invalid("admission-cap-changed")
        amounts = self.budget.bytes.copy()
        total_cap = self.budget.cumulative_limit("total_bytes")
        original_cap = getattr(self.budget.limits, category + "_bytes")
        if (
            not amounts.keys() <= set(policy.BYTE_CATEGORIES)
            or any(not scalar(number) for number in amounts.values())
            or amounts.get(category, 0) != charged or charged > cap
            or not scalar(total_cap, positive=True)
            or not scalar(original_cap, positive=True)
            or sum(amounts.values()) > total_cap
            or type(self.budget.closed) is not bool
            or type(self.budget.failed) is not bool or not self.budget.failed
            or not scalar(self.budget.runs) or not scalar(self.budget.states)
        ):
            return invalid("admission-collection-disagreement")
        return {
            "status": "observed", "category": category,
            "requested": requested, "charged_before": charged, "issued_cap": cap,
            "prospective_category": used, "remaining_category": cap - charged,
            "category_exhausted": used > cap, "category_shortfall": max(0, used - cap),
            "total_at_admission": None, "total_predicate": None,
            "total_cap": total_cap,
            "original_category_cap": original_cap,
            "diagnostic_override": cap != original_cap,
            "collected": {
                "category_charged": amounts.get(category, 0), "total_charged": sum(amounts.values()),
                "runs": self.budget.runs, "states": self.budget.states, "closed": self.budget.closed,
                "owned_children": len(self.budget.children),
            },
            "semantics": "Refusal locals and later collection are distinct; aggregate expression was not retained.",
        }

    def budget_admission(self, error):
        if self.charge_code is None or self.budget_error is None:
            return unavailable("admission-binding-not-ready")
        current, result = error, None
        exceptions, bound_frames = set(), set()
        frames = 0
        while current is not None:
            if id(current) in exceptions:
                return unavailable("cyclic-exception-chain")
            if len(exceptions) >= 32:
                return unavailable("exception-chain-bound")
            exceptions.add(id(current))
            trace = current.__traceback__
            while trace is not None:
                if frames >= 256:
                    return unavailable("trace-frame-bound")
                frames += 1
                frame = trace.tb_frame
                if frame.f_code is self.charge_code and id(frame) not in bound_frames:
                    bound_frames.add(id(frame))
                    candidate = self._admission_frame(current, frame.f_locals)
                    if candidate is not None:
                        if result is not None:
                            return invalid("multiple-admission-frames")
                        result = candidate
                trace = trace.tb_next
            current = current.__cause__ if current.__cause__ is not None else current.__context__
        return result if result is not None else unavailable("no-matching-admission")

    def _frame(self, values):
        owner = values.get("self")
        if type(owner) is not self.session_type or vars(owner).get("budget") is not self.budget:
            return invalid("foreign-session-or-budget")
        if "observed" not in values:
            return None
        observed = values["observed"]
        if type(observed) is not dict or type(observed.get("ok")) is not bool:
            return invalid("malformed-native-report")
        if observed["ok"] is False and type(observed.get("error")) is not str:
            return invalid("malformed-native-report")
        if observed["ok"] is not False or observed.get("error") != NATIVE_REJECTION:
            return None
        config, mode, settled = values.get("config"), values.get("mode"), values.get("settled")
        if (
            type(config) is not dict or type(mode) is not str
            or mode not in {"command", "compile", "make"}
            or type(config.get("mode")) is not str or config["mode"] != mode
            or "channel" not in values or "producer_handler" not in values
        ):
            return invalid("missing-or-invalid-native-context")
        count, size = observed.get("observations"), observed.get("observation_bytes")
        initial_count = config.get("observation_count")
        initial_size = config.get("observation_limit")
        if (
            not scalar(count) or not scalar(size)
            or not scalar(initial_count, positive=True) or not scalar(initial_size, positive=True)
            or count > initial_count or size < 128 * count
        ):
            return invalid("malformed-observation-scalars")
        if type(settled) is not dict or any(
            type(settled.get(name)) is not int or settled[name] != observed[name]
            for name in ("observations", "observation_bytes")
        ):
            return invalid("native-report-not-settled")
        producer = (
            values["channel"] is not None or values["producer_handler"] is not None
            or "producer_endpoint" in config
        )
        if mode != "make" and producer:
            return invalid("producer-channel-outside-native-make")
        result = {
            "status": "unknown", "reason": "make-effective-grant-unavailable", "mode": mode,
            "observations": count, "observation_bytes": size,
            "initial_observation_count": initial_count, "initial_observation_limit": initial_size,
            "effective_observation_count": None, "effective_observation_limit": None,
            "count_predicate": None, "byte_predicate": None, "exhaustion": None,
        }
        # Native Make can replace its grants in another process. Parent config
        # and aggregate ProbeBudget counters cannot establish those actual grants.
        if mode == "make":
            return result
        count_exhausted, bytes_exhausted = count >= initial_count, size > initial_size
        if not count_exhausted and not bytes_exhausted:
            return invalid("native-rejection-without-either-predicate")
        result.update({
            "status": "attributed", "reason": "fixed-non-producer-grant",
            "effective_observation_count": initial_count,
            "effective_observation_limit": initial_size,
            "count_predicate": count_exhausted, "byte_predicate": bytes_exhausted,
            "exhaustion": "both" if count_exhausted and bytes_exhausted else (
                "count" if count_exhausted else "bytes"
            ),
        })
        return result

    def capture(self, error):
        current, result = error, None
        exceptions, bound_frames = set(), set()
        frames = 0
        while current is not None:
            if id(current) in exceptions:
                return unavailable("cyclic-exception-chain")
            if len(exceptions) >= 32:
                return unavailable("exception-chain-bound")
            exceptions.add(id(current))
            trace = current.__traceback__
            while trace is not None:
                if frames >= 256:
                    return unavailable("trace-frame-bound")
                frames += 1
                frame = trace.tb_frame
                if frame.f_code is self.code and id(frame) not in bound_frames:
                    bound_frames.add(id(frame))
                    candidate = self._frame(frame.f_locals)
                    if candidate is not None:
                        if result is not None:
                            return invalid("multiple-native-rejection-frames")
                        result = candidate
                trace = trace.tb_next
            current = current.__cause__ if current.__cause__ is not None else current.__context__
        return result if result is not None else unavailable("no-matching-native-rejection")
