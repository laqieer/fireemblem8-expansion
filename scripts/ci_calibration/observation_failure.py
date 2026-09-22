"""Bounded numeric failures and registered source-code locations."""

from __future__ import annotations

from importlib.machinery import ModuleSpec, SourceFileLoader
from pathlib import Path
import re
import sys
import types
import weakref

if __package__:
    from . import policy
else:
    import policy


NATIVE_REJECTION = "aggregate filesystem-observation budget exhausted"
SOURCE_ROOT = Path("/repo")
SOURCE_PACKAGE = "scripts.validation_ownership"
LOCATION_REASONS = frozenset({
    "binding-not-ready", "source-binding-invalid", "source-file-unowned", "source-module-unowned",
    "source-code-unbound", "public-call-unobserved", "no-source-trace", "cyclic-exception-chain",
    "exception-chain-bound", "trace-frame-bound", "registration-bound", "invalid-code-location",
    "location-size-bound", "locator-failed", "cyclic-wrapper-chain", "wrapper-chain-bound",
    "function-metadata-unavailable", "code-metadata-unavailable", "source-metadata-unavailable",
})


def location_unavailable(reason, *, references_closed=None):
    if reason not in LOCATION_REASONS:
        raise policy.GuardError("unknown location unavailability reason")
    return {
        "version": 1, "source_revision": policy.GRAPH, "root": "/repo", "api": policy.REPORT_API,
        "authority": False, "status": "unavailable", "reason": reason, "locations": [],
        "references_closed": references_closed,
    }


def validate_locations(value, binding):
    policy._component_fields(value, (
        "version source_revision root api authority status reason locations references_closed"
    ))
    if (
        type(value["version"]) is not int or value["version"] != 1
        or value["source_revision"] != binding["source_revision"] or value["root"] != "/repo"
        or value["api"] != binding["api"] or value["authority"] is not False
        or type(value["status"]) is not str or value["status"] not in {"observed", "unavailable"}
        or value["references_closed"] is not None and type(value["references_closed"]) is not bool
        or type(value["locations"]) is not list
    ):
        raise policy.GuardError("source location evidence is foreign or not a closed diagnostic record")
    if value["status"] == "unavailable":
        if type(value["reason"]) is not str or value["reason"] not in LOCATION_REASONS or value["locations"]:
            raise policy.GuardError("unavailable source location invented an observation")
    else:
        if value["reason"] is not None or value["references_closed"] is not True or not 1 <= len(value["locations"]) <= 32:
            raise policy.GuardError("source locations are incomplete or exceed the error-chain bound")
        for index, row in enumerate(value["locations"]):
            policy._component_fields(row, "exception relation file code first_line line offset")
            if (
                type(row["exception"]) is not int or row["exception"] != index
                or type(row["relation"]) is not str
                or row["relation"] not in ({"primary"} if index == 0 else {"cause", "context"})
                or type(row["file"]) is not str or not row["file"].startswith("scripts/validation_ownership/")
                or not row["file"].endswith(".py") or policy._root_path(row["file"]) != row["file"]
                or type(row["code"]) is not str
                or re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]{0,127}|<(?:lambda|listcomp|dictcomp|setcomp|genexpr)>)", row["code"]) is None
                or any(not policy._component_integer(row[name], policy.ORIGINAL_LIMITS["file_bytes"], 1)
                       for name in ("first_line", "line"))
                or not policy._component_integer(row["offset"], policy.ORIGINAL_LIMITS["file_bytes"])
            ):
                raise policy.GuardError("source location contains an unbound identity or non-scalar position")
    if len(policy.encoded(value)) > policy.ERROR_BYTES:
        raise policy.GuardError("source location evidence exceeds the existing error record bound")
    return value


class _LocationUnavailable(policy.GuardError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _location_fields(fields):
    # Mapping proxies here come only from ordinary type.__dict__ descriptors.
    if type(fields) is not dict and type(fields) is not types.MappingProxyType:
        raise _LocationUnavailable("source-metadata-unavailable")
    if len(fields) > policy.ORIGINAL_LIMITS["entries"]:
        raise _LocationUnavailable("registration-bound")
    if any(type(key) is not str for key in fields):
        raise _LocationUnavailable("source-metadata-unavailable")
    return fields


def _location_code(code):
    if type(code) is not types.CodeType or (
        type(code.co_filename) is not str or type(code.co_name) is not str
        or type(code.co_consts) is not tuple or type(code.co_code) is not bytes
        or type(code.co_linetable) is not bytes
    ):
        raise _LocationUnavailable("code-metadata-unavailable")
    return code


def _instance_fields(value, expected):
    if type(expected) is not type or type(value) is not expected:
        raise _LocationUnavailable("source-binding-invalid")
    ancestry = type.__getattribute__(expected, "__mro__")
    if len(ancestry) > 32:
        raise _LocationUnavailable("source-binding-invalid")
    for owner in ancestry:
        descriptor = _location_fields(type.__getattribute__(owner, "__dict__")).get("__dict__")
        if descriptor is not None:
            if type(descriptor) is not types.GetSetDescriptorType:
                raise _LocationUnavailable("source-binding-invalid")
            fields = descriptor.__get__(value, expected)
            if type(fields) is not dict:
                raise _LocationUnavailable("source-binding-invalid")
            return _location_fields(fields)
    raise _LocationUnavailable("source-binding-invalid")


class _SourceLocations:
    def __init__(self):
        self.codes, self.modules, self.seen, self.registered = {}, {}, set(), {}
        self.entries = self.entry_type = self.call = self.call_globals = None
        self.work = 0

    def account(self, amount=1):
        self.work += amount
        if self.work > policy.ORIGINAL_LIMITS["entries"]:
            raise _LocationUnavailable("registration-bound")

    def public_call(self, api, session_type):
        if (
            type(api) is not types.SimpleNamespace or api.session is not session_type
            or type(api.module) is not types.ModuleType or type(api.check) is not types.FunctionType
        ):
            raise _LocationUnavailable("source-binding-invalid")
        namespace = _location_fields(types.ModuleType.__getattribute__(api.module, "__dict__"))
        if (
            type(namespace.get("__name__")) is not str
            or namespace["__name__"] != SOURCE_PACKAGE + ".graph_report"
            or namespace.get("check") is not api.check or api.check.__globals__ is not namespace
        ):
            raise _LocationUnavailable("source-binding-invalid")
        self.call, self.call_globals = api.check.__code__, namespace

    def freeze(self, api, session_type):
        self.public_call(api, session_type)
        if type(sys.modules) is not dict:
            raise _LocationUnavailable("source-module-unowned")
        _location_fields(sys.modules)
        self.account(len(sys.modules))
        self.module(self.call_globals)
        for imported_name, module in list(sys.modules.items()):
            if not imported_name.startswith(SOURCE_PACKAGE + ".") or type(module) is not types.ModuleType:
                continue
            namespace = _location_fields(types.ModuleType.__getattribute__(module, "__dict__"))
            name, filename = namespace.get("__name__"), namespace.get("__file__")
            if (
                type(name) is str and name.startswith(SOURCE_PACKAGE + ".")
                and type(filename) is str and filename.startswith("/repo/")
            ):
                self.module(namespace)
        return {
            identity: (weakref.ref(code), weakref.ref(sys.modules[namespace["__name__"]]), relative)
            for identity, (code, namespace, relative) in self.codes.items()
        }

    def require_registered(self, code, namespace, relative):
        registered = self.registered.get(id(code))
        module = None if registered is None else registered[1]()
        if (
            registered is None or registered[0]() is not code or type(module) is not types.ModuleType
            or types.ModuleType.__getattribute__(module, "__dict__") is not namespace
            or registered[2] != relative
        ):
            raise _LocationUnavailable("source-code-unbound")

    def bind(self, observer, measurement):
        if (
            measurement is None or measurement.api is None or measurement.session_valid is not True
            or type(measurement.root) is not type(SOURCE_ROOT) or measurement.root != SOURCE_ROOT
            or measurement.budget is not observer.budget
            or type(measurement.session) is not observer.session_type
            or type(sys.modules) is not dict
        ):
            raise _LocationUnavailable("binding-not-ready")
        _location_fields(sys.modules)
        try:
            policy.validate_report_binding(measurement.binding)
        except policy.GuardError as error:
            raise _LocationUnavailable("source-binding-invalid") from error
        api = measurement.api
        self.public_call(api, observer.session_type)
        session = _instance_fields(measurement.session, observer.session_type)
        loader = _instance_fields(session.get("loader"), api.loader)
        entries = loader.get("entries")
        captured = _instance_fields(entries, api.entries)
        capture = captured.get("capture")
        if (
            session.get("budget") is not observer.budget or loader.get("budget") is not observer.budget
            or captured.get("budget") is not observer.budget
            or type(loader.get("root")) is not type(SOURCE_ROOT) or loader["root"] != SOURCE_ROOT
            or type(loader.get("revision")) is not str or loader["revision"] != policy.GRAPH
            or type(capture) is not tuple or len(capture) != 2
            or type(capture[0]) is not type(SOURCE_ROOT) or capture[0] != SOURCE_ROOT
            or type(capture[1]) is not str or capture[1] != policy.GRAPH
            or not isinstance(entries, dict) or dict.__len__(entries) > policy.ORIGINAL_LIMITS["entries"]
        ):
            raise _LocationUnavailable("source-binding-invalid")
        if any(type(key) is not str for key in dict.keys(entries)):
            raise _LocationUnavailable("source-metadata-unavailable")
        authority = sys.modules.get(SOURCE_PACKAGE + ".authority")
        if type(authority) is not types.ModuleType:
            raise _LocationUnavailable("source-binding-invalid")
        namespace = _location_fields(types.ModuleType.__getattribute__(authority, "__dict__"))
        if namespace.get("AuthorityLoader") is not api.loader or namespace.get("GitTreeEntries") is not api.entries:
            raise _LocationUnavailable("source-binding-invalid")
        self.entry_type, self.entries = namespace.get("GitTreeEntry"), entries
        if type(self.entry_type) is not type:
            raise _LocationUnavailable("source-binding-invalid")
        relative = self.module(self.call_globals)
        self.module(namespace)
        if id(self.call) not in self.codes:
            raise _LocationUnavailable("source-code-unbound")
        self.require_registered(self.call, self.call_globals, relative)

    def file(self, path):
        if type(path) is not str or not path.startswith("/repo/"):
            raise _LocationUnavailable("source-file-unowned")
        relative = path[len("/repo/"):]
        try:
            policy._root_path(relative)
        except policy.GuardError as error:
            raise _LocationUnavailable("source-file-unowned") from error
        if not relative.startswith("scripts/validation_ownership/") or not relative.endswith(".py"):
            raise _LocationUnavailable("source-file-unowned")
        if self.entries is None:
            return relative
        entry = dict.get(self.entries, relative)
        fields = _instance_fields(entry, self.entry_type)
        if (
            type(fields.get("path")) is not str or fields["path"] != relative
            or type(fields.get("mode")) is not str or fields["mode"] not in {"100644", "100755"}
            or type(fields.get("object_type")) is not str or fields["object_type"] != "blob"
            or "git_dir" not in fields or fields["git_dir"] is not None
            or type(fields.get("object_id")) is not str
            or re.fullmatch(r"[0-9a-f]{40}", fields["object_id"]) is None
        ):
            raise _LocationUnavailable("source-file-unowned")
        return relative

    def module(self, namespace):
        if type(namespace) is not dict:
            raise _LocationUnavailable("source-module-unowned")
        _location_fields(namespace)
        name = namespace.get("__name__")
        if type(name) is not str or not name.startswith(SOURCE_PACKAGE + ".") or (
            re.fullmatch(r"scripts\.validation_ownership(?:\.[A-Za-z_][A-Za-z0-9_]*)+", name) is None
        ):
            raise _LocationUnavailable("source-module-unowned")
        previous = self.modules.get(name)
        if previous is not None:
            if previous[0] is not namespace:
                raise _LocationUnavailable("source-module-unowned")
            return previous[1]
        module = sys.modules.get(name)
        if type(module) is not types.ModuleType or types.ModuleType.__getattribute__(module, "__dict__") is not namespace:
            raise _LocationUnavailable("source-module-unowned")
        filename = namespace.get("__file__")
        relative = self.file(filename)
        expected = name.replace(".", "/")
        if relative not in {expected + ".py", expected + "/__init__.py"}:
            raise _LocationUnavailable("source-module-unowned")
        spec = _instance_fields(namespace.get("__spec__"), ModuleSpec)
        loader = namespace.get("__loader__")
        loading = _instance_fields(loader, SourceFileLoader)
        if (
            type(spec.get("name")) is not str or spec["name"] != name
            or type(spec.get("origin")) is not str or spec["origin"] != filename or spec.get("loader") is not loader
            or type(loading.get("name")) is not str or loading["name"] != name
            or type(loading.get("path")) is not str or loading["path"] != filename
            or type(namespace.get("__package__")) is not str
            or namespace["__package__"] != (name if relative.endswith("/__init__.py") else name.rsplit(".", 1)[0])
        ):
            raise _LocationUnavailable("source-module-unowned")
        self.modules[name] = namespace, relative
        self.account(len(namespace))
        pending = list(namespace.values())
        while pending:
            member = pending.pop()
            identity = id(member)
            if identity in self.seen:
                continue
            if type(member) is types.FunctionType:
                self.register_function(member, namespace, filename, relative)
            elif type(member) is type:
                fields = _location_fields(type.__getattribute__(member, "__dict__"))
                if type(fields.get("__module__")) is not str or fields["__module__"] != name:
                    continue
                self.seen.add(identity)
                self.account(len(fields))
                pending.extend(fields.values())
            elif type(member) is staticmethod or type(member) is classmethod:
                self.seen.add(identity)
                self.account()
                pending.append(member.__func__)
            elif type(member) is property:
                self.seen.add(identity)
                values = [value for value in (member.fget, member.fset, member.fdel) if value is not None]
                self.account(len(values))
                pending.extend(values)
        return relative

    def register_function(self, member, namespace, filename, relative):
        if type(filename) is not str:
            raise _LocationUnavailable("code-metadata-unavailable")
        chain = set()
        while member is not None:
            if type(member) is not types.FunctionType:
                raise _LocationUnavailable("source-code-unbound")
            identity = id(member)
            if identity in chain:
                raise _LocationUnavailable("cyclic-wrapper-chain")
            if len(chain) >= 32:
                raise _LocationUnavailable("wrapper-chain-bound")
            chain.add(identity)
            metadata = member.__dict__
            if type(metadata) is not dict:
                raise _LocationUnavailable("function-metadata-unavailable")
            self.account(1 + len(metadata))
            if any(type(key) is not str for key in metadata):
                raise _LocationUnavailable("function-metadata-unavailable")
            if (
                member.__globals__ is namespace and type(member.__module__) is str
                and member.__module__ == namespace["__name__"]
            ):
                self.seen.add(identity)
                code = _location_code(member.__code__)
                if code.co_filename == filename:
                    self.register_code(code, namespace, filename, relative)
            # Standard wrappers can have foreign globals; only each original
            # function's own identity can admit its code.
            member = dict.get(metadata, "__wrapped__")

    def register_code(self, code, namespace, filename, relative):
        if type(filename) is not str:
            raise _LocationUnavailable("code-metadata-unavailable")
        pending = [code]
        while pending:
            current = _location_code(pending.pop())
            if current.co_filename != filename:
                raise _LocationUnavailable("source-code-unbound")
            previous = self.codes.get(id(current))
            if previous is not None:
                if previous[0] is not current or previous[1] is not namespace or previous[2] != relative:
                    raise _LocationUnavailable("source-code-unbound")
                continue
            self.account(1 + len(current.co_consts))
            self.codes[id(current)] = current, namespace, relative
            pending.extend(value for value in current.co_consts if type(value) is types.CodeType)

    def project(self, error):
        current, relation = error, "primary"
        seen, locations = set(), []
        frames, scope_seen = 0, False
        while current is not None:
            if id(current) in seen:
                raise _LocationUnavailable("cyclic-exception-chain")
            if len(seen) >= 32:
                raise _LocationUnavailable("exception-chain-bound")
            seen.add(id(current))
            trace = BaseException.__dict__["__traceback__"].__get__(current, BaseException)
            leaf = None
            while trace is not None:
                if type(trace) is not types.TracebackType or frames >= 256:
                    raise _LocationUnavailable("trace-frame-bound")
                frames += 1
                frame = trace.tb_frame
                code, namespace = frame.f_code, frame.f_globals
                if code is self.call and namespace is self.call_globals:
                    scope_seen = True
                leaf = code, namespace, trace.tb_lineno, trace.tb_lasti
                trace = trace.tb_next
                frame = None
            if leaf is None:
                raise _LocationUnavailable("no-source-trace")
            code, namespace, line, offset = leaf
            _location_code(code)
            relative = self.module(namespace)
            owned = self.codes.get(id(code))
            if owned is None or owned[0] is not code or owned[1] is not namespace or owned[2] != relative:
                raise _LocationUnavailable("source-code-unbound")
            self.require_registered(code, namespace, relative)
            if (
                not policy._component_integer(line, policy.ORIGINAL_LIMITS["file_bytes"], 1)
                or not policy._component_integer(code.co_firstlineno, policy.ORIGINAL_LIMITS["file_bytes"], 1)
                or not policy._component_integer(offset, policy.ORIGINAL_LIMITS["file_bytes"])
                or not offset < len(code.co_code) <= policy.ORIGINAL_LIMITS["file_bytes"]
                or not any(start <= offset < end and actual == line for start, end, actual in code.co_lines())
            ):
                raise _LocationUnavailable("invalid-code-location")
            locations.append({
                "exception": len(locations), "relation": relation, "file": relative, "code": code.co_name,
                "first_line": code.co_firstlineno, "line": line, "offset": offset,
            })
            cause = BaseException.__dict__["__cause__"].__get__(current, BaseException)
            current = cause if cause is not None else BaseException.__dict__["__context__"].__get__(current, BaseException)
            relation = "cause" if cause is not None else "context"
        if not scope_seen:
            raise _LocationUnavailable("public-call-unobserved")
        result = {
            **location_unavailable("binding-not-ready"), "status": "observed", "reason": None,
            "locations": locations, "references_closed": True,
        }
        if len(policy.encoded(result)) > policy.ERROR_BYTES:
            raise _LocationUnavailable("location-size-bound")
        return result

    def close(self):
        first = None
        for name in ("codes", "modules", "seen", "registered"):
            try:
                getattr(self, name).clear()
            except BaseException as error:
                if first is None:
                    first = error
        for name in ("entries", "entry_type", "call", "call_globals"):
            try:
                setattr(self, name, None)
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first


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

    def register_locations(self, api):
        if "location_codes" in vars(self):
            raise policy.GuardError("source location registration is single-use")
        locations = _SourceLocations()
        self.location_codes, self.location_reason = {}, "binding-not-ready"
        self.location_references_closed = None
        secondary = []
        try:
            self.location_codes = locations.freeze(api, self.session_type)
            self.location_reason = None
        except _LocationUnavailable as error:
            self.location_reason = error.reason
        except BaseException as error:
            self.location_reason = "locator-failed"
            secondary.append({"stage": "location-publication", "error": policy.component_secondary_error(error)})
        finally:
            try:
                locations.close()
                self.location_references_closed = True
            except BaseException as error:
                self.location_codes.clear()
                self.location_reason = "locator-failed"
                secondary.append({"stage": "location-publication", "error": policy.component_secondary_error(error)})
        return secondary

    def source_locations(self, error, measurement):
        locations = _SourceLocations()
        locations.registered = getattr(self, "location_codes", {})
        try:
            try:
                reason = getattr(self, "location_reason", "binding-not-ready")
                if reason is not None:
                    raise _LocationUnavailable(reason)
                locations.bind(self, measurement)
                value = locations.project(error)
            except _LocationUnavailable as unavailable_error:
                value = location_unavailable(unavailable_error.reason)
        finally:
            locations.close()
        value["references_closed"] = getattr(self, "location_references_closed", None)
        return validate_locations(value, {"source_revision": policy.GRAPH, "api": policy.REPORT_API})

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
