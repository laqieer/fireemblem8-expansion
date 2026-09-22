"""Bounded numeric failures and registered source-code locations."""

from __future__ import annotations

import builtins
import copy
import hashlib
import importlib._bootstrap as _bootstrap
from importlib.machinery import ModuleSpec, SourceFileLoader
import math
from pathlib import Path
import re
import sys
import threading
import time
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
    "original-import-unavailable", "import-restoration-unavailable",
})

_IMPORT_MRO = SourceFileLoader.__mro__
_IMPORT_METHODS = {
    name: getattr(SourceFileLoader, name) for name in (
        "get_code", "source_to_code", "exec_module", "get_filename", "get_data",
        "path_stats", "_cache_bytecode", "set_data", "is_package",
    )
}
_GET_CODE = _IMPORT_METHODS["get_code"]
_SOURCE_TO_CODE = _IMPORT_METHODS["source_to_code"]
_EXEC_MODULE = _IMPORT_METHODS["exec_module"]
_LOAD_UNLOCKED = _bootstrap._load_unlocked
_CALL_REMOVED = _bootstrap._call_with_frames_removed
_IMPORT_FUNCTIONS = {
    function: (function.__code__, function.__globals__)
    for function in (*_IMPORT_METHODS.values(), _LOAD_UNLOCKED, _CALL_REMOVED)
}
_COMPILE, _EXEC = builtins.compile, builtins.exec
_IMPORT_BUILTINS = _SOURCE_TO_CODE.__globals__["__builtins__"]
_CLOCK, _THREAD, _FRAME = time.monotonic, threading.get_ident, sys._getframe
_BLOB_HASH = hashlib.sha1
_MAKE_COLLAPSE_MESSAGE = "unproven GNU Make parsing-mode context changes continuation data"
_MAKE_LABEL = re.compile(r"([^:;\x00-\x1f]+):([0-9]+)(?:-([0-9]+))? \(logical ([0-9]+)\)")
_MAKE_CONTEXT_REASONS = frozenset({
    "guard-unobserved", "multiple-guards", "arguments-unavailable", "message-bound",
    "message-encoding", "message-format", "work-bound", "epoch-unavailable",
    "output-bound", "context-unavailable", "spans-unavailable",
})
_MAKE_SPAN_REASONS = frozenset({
    "not-reported", "unknown-source", "position-format", "position-range",
    "path-unbound", "path-format", "output-bound", "context-unavailable",
})


def make_context_unavailable(reason, *, exception=None):
    if reason not in _MAKE_CONTEXT_REASONS:
        raise policy.GuardError("unknown Make context unavailability reason")
    return {
        "kind": "make-parsing-mode", "authority": False, "exception": exception,
        "status": "unavailable", "reason": reason, "spans": [],
    }


def _make_span_unavailable(role, reason):
    return {"role": role, "status": "unavailable", "reason": reason}


def _make_path_size(path):
    size = 2
    for character in path:
        number = ord(character)
        size += 12 if number > 0xFFFF else 6 if number > 126 or number < 32 else (
            2 if character in {'"', "\\"} else 1
        )
    return size


def validate_make_context(value, locations):
    _location_fields(value)
    policy._component_fields(value, "kind authority exception status reason spans")
    if (
        type(value["kind"]) is not str or value["kind"] != "make-parsing-mode"
        or value["authority"] is not False
        or value["exception"] is not None and not policy._component_integer(value["exception"], 31)
        or type(value["status"]) is not str or value["status"] not in {"reported", "partial", "unavailable"}
        or type(value["spans"]) is not list or len(value["spans"]) not in {0, 2}
    ):
        raise policy.GuardError("Make source context has an unclosed diagnostic shape")
    if value["exception"] is not None:
        raising = [row for row in (*locations["locations"], *locations["anchors"])
                   if row["exception"] == value["exception"]]
        if (
            locations["references_closed"] is not True or len(raising) != 1
            or raising[0]["file"] != "scripts/validation_ownership/graph_probe.py"
            or raising[0]["code"] != "collapse"
            or raising[0].get("role", "registered-raising-frame") != "registered-raising-frame"
        ):
            raise policy.GuardError("Make context lacks its verified Python raising anchor")
    if not value["spans"]:
        if (
            value["status"] != "unavailable" or type(value["reason"]) is not str
            or value["reason"] not in _MAKE_CONTEXT_REASONS - {"spans-unavailable"}
        ):
            raise policy.GuardError("absent Make context claims reported data")
        return value
    if value["exception"] is None:
        raise policy.GuardError("Make source spans lack their original exception index")
    reported = 0
    for role, span in zip(("failure", "first-uncertainty"), value["spans"]):
        _location_fields(span)
        if type(span) is not dict or type(span.get("status")) is not str or (
            type(span.get("role")) is not str or span["role"] != role
        ):
            raise policy.GuardError("Make source span has a foreign role")
        if span["status"] == "unavailable":
            policy._component_fields(span, "role status reason")
            if type(span["reason"]) is not str or span["reason"] not in _MAKE_SPAN_REASONS:
                raise policy.GuardError("unavailable Make span invented a position")
        elif span["status"] == "reported-selected-tree":
            policy._component_fields(span, "role status path logical start end")
            if type(span["path"]) is not str or len(span["path"]) > 4096 or any(
                not policy._component_integer(span[name], policy.ORIGINAL_LIMITS["file_bytes"], 1)
                for name in ("logical", "start", "end")
            ) or span["start"] > span["end"]:
                raise policy.GuardError("Make source span contains invalid bounded positions")
            try:
                policy._root_path(span["path"])
            except (policy.GuardError, UnicodeError) as error:
                raise policy.GuardError("Make source span lacks a canonical selected-tree path") from error
            reported += 1
        else:
            raise policy.GuardError("Make span has an unsupported provenance")
    if (
        value["status"] != ("reported" if reported == 2 else "partial" if reported else "unavailable")
        or reported == 2 and value["reason"] is not None
        or reported != 2 and (type(value["reason"]) is not str or value["reason"] != "spans-unavailable")
    ):
        raise policy.GuardError("Make context silently qualified a missing span")
    return value


def _make_label_position(match):
    if match is None:
        return "position-format"
    try:
        numbers = match.group(2), match.group(3), match.group(4)
        if any(value is not None and (len(value) > 8 or value.startswith("0")) for value in numbers):
            return "position-range"
        start, end, logical = (None if value is None else int(value) for value in numbers)
        end = start if end is None else end
        if not all(1 <= value <= policy.ORIGINAL_LIMITS["file_bytes"] for value in (start, end, logical)) or start > end:
            return "position-range"
        return match.group(1), logical, start, end
    finally:
        match = None


def _make_source_labels(arguments):
    """Decode reported labels only; this does not admit a path or establish provenance."""
    text = match = None
    try:
        if type(arguments) is not tuple or len(arguments) != 1 or type(arguments[0]) is not str:
            return "arguments-unavailable", "arguments-unavailable"
        text = arguments[0]
        limit = min(policy.ERROR_BYTES // 4, policy.ORIGINAL_LIMITS["file_bytes"] // 4,
                    policy.ORIGINAL_LIMITS["entries"] // 2)
        if len(text) > limit:
            return "message-bound", "message-bound"
        try:
            if len(text.encode("utf-8")) > policy.ERROR_BYTES:
                return "message-bound", "message-bound"
        except UnicodeError:
            return "message-encoding", "message-encoding"
        if not text.startswith(_MAKE_COLLAPSE_MESSAGE):
            return "message-format", "message-format"
        cursor = len(_MAKE_COLLAPSE_MESSAGE)
        failure = uncertainty = "not-reported"
        if text.startswith("; source ", cursor):
            cursor += len("; source ")
            end = text.find("; first uncertainty ", cursor)
            end = len(text) if end < 0 else end
            failure = _make_label_position(_MAKE_LABEL.fullmatch(text, cursor, end))
            cursor = end
        if cursor != len(text):
            if not text.startswith("; first uncertainty ", cursor):
                return "message-format", "message-format"
            cursor += len("; first uncertainty ")
            if text.startswith("<unknown source>: ", cursor):
                uncertainty = "unknown-source"
            else:
                match = _MAKE_LABEL.match(text, cursor)
                if match is None or not text.startswith(": ", match.end()):
                    uncertainty = "position-format"
                else:
                    uncertainty = _make_label_position(match)
        return failure, uncertainty
    finally:
        arguments = text = match = None


def location_unavailable(reason, *, references_closed=None):
    if reason not in LOCATION_REASONS:
        raise policy.GuardError("unknown location unavailability reason")
    return {
        "version": 3, "source_revision": policy.GRAPH, "root": "/repo", "api": policy.REPORT_API,
        "authority": False, "status": "unavailable", "reason": reason, "locations": [],
        "anchors": [], "references_closed": references_closed,
        "context": make_context_unavailable("guard-unobserved"),
    }


def validate_locations(value, binding):
    policy._component_fields(value, (
        "version source_revision root api authority status reason locations anchors references_closed context"
    ))
    if (
        type(value["version"]) is not int or value["version"] != 3
        or value["source_revision"] != binding["source_revision"] or value["root"] != "/repo"
        or value["api"] != binding["api"] or value["authority"] is not False
        or type(value["status"]) is not str or value["status"] not in {"observed", "unavailable"}
        or value["references_closed"] is not None and type(value["references_closed"]) is not bool
        or type(value["locations"]) is not list or type(value["anchors"]) is not list
        or len(value["anchors"]) > 32
    ):
        raise policy.GuardError("source location evidence is foreign or not a closed diagnostic record")
    if value["status"] == "unavailable":
        if type(value["reason"]) is not str or value["reason"] not in LOCATION_REASONS or value["locations"]:
            raise policy.GuardError("unavailable source location invented an observation")
        if value["anchors"] and (
            value["reason"] not in {"source-code-unbound", "no-source-trace"}
            or value["references_closed"] is not True
        ):
            raise policy.GuardError("partial anchors lack a closed original-code observation")
    else:
        if (
            value["reason"] is not None or value["references_closed"] is not True
            or not 1 <= len(value["locations"]) <= 32 or value["anchors"]
        ):
            raise policy.GuardError("source locations are incomplete or exceed the error-chain bound")
    for anchored, rows in ((False, value["locations"]), (True, value["anchors"])):
        previous = -1
        for index, row in enumerate(rows):
            policy._component_fields(row, "exception relation file code first_line line offset" + (" role" if anchored else ""))
            if (
                not policy._component_integer(row["exception"], 31)
                or (row["exception"] <= previous if anchored else row["exception"] != index)
                or type(row["relation"]) is not str
                or row["relation"] not in ({"primary"} if row["exception"] == 0 else {"cause", "context"})
                or type(row["file"]) is not str or not row["file"].startswith("scripts/validation_ownership/")
                or not row["file"].endswith(".py") or policy._root_path(row["file"]) != row["file"]
                or type(row["code"]) is not str
                or re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]{0,127}|<(?:lambda|listcomp|dictcomp|setcomp|genexpr)>)", row["code"]) is None
                or any(not policy._component_integer(row[name], policy.ORIGINAL_LIMITS["file_bytes"], 1)
                       for name in ("first_line", "line"))
                or not policy._component_integer(row["offset"], policy.ORIGINAL_LIMITS["file_bytes"])
            ):
                raise policy.GuardError("source location contains an unbound identity or non-scalar position")
            if anchored and (type(row["role"]) is not str or row["role"] not in {
                "registered-raising-frame", "registered-caller",
            }):
                raise policy.GuardError("partial source anchor has an unknown observation role")
            previous = row["exception"]
    validate_make_context(value["context"], value)
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
        self.imports = self.clock = self.deadline = None
        self.work = 0

    def account(self, amount=1):
        self.work += amount
        if self.work > policy.ORIGINAL_LIMITS["entries"]:
            raise _LocationUnavailable("registration-bound")
        if self.clock is not None and self.clock() >= self.deadline:
            raise _LocationUnavailable("original-import-unavailable")

    def public_call(self, api, session_type):
        if type(api) is types.SimpleNamespace:
            _location_fields(object.__getattribute__(api, "__dict__"))
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
        if len(registered) == 4:
            if self.imports is None:
                raise _LocationUnavailable("source-code-unbound")
            self.imports.require_registered(registered, code, namespace, relative)

    def bind_source(self, observer, measurement):
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

    def bind(self, observer, measurement):
        self.bind_source(observer, measurement)
        relative = self.module(self.call_globals)
        authority = sys.modules[SOURCE_PACKAGE + ".authority"]
        self.module(types.ModuleType.__getattribute__(authority, "__dict__"))
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

    def context_checkpoint(self, amount=1):
        self.account(amount)
        if self.imports is None:
            raise _LocationUnavailable("original-import-unavailable")
        self.imports.live(projection=True)

    def make_guard(self, registered):
        code, module = registered[0](), registered[1]()
        if code is None or type(module) is not types.ModuleType or (
            registered[2] != "scripts/validation_ownership/graph_probe.py"
        ):
            return False
        _location_code(code)
        if type(code.co_qualname) is not str or code.co_qualname != "_MakeSourceMode.collapse":
            return False
        namespace = _location_fields(types.ModuleType.__getattribute__(module, "__dict__"))
        owner = namespace.get("_MakeSourceMode")
        if type(owner) is not type:
            return False
        fields = type.__getattribute__(owner, "__dict__")
        self.context_checkpoint(len(fields))
        fields = _location_fields(fields)
        function = fields.get("collapse")
        qualified_name = type.__dict__["__qualname__"].__get__(owner, type)
        if (
            type(fields.get("__module__")) is not str or fields["__module__"] != SOURCE_PACKAGE + ".graph_probe"
            or type(qualified_name) is not str or qualified_name != "_MakeSourceMode"
            or type(function) is not types.FunctionType or function.__code__ is not code
            or function.__globals__ is not namespace or type(function.__module__) is not str
            or function.__module__ != SOURCE_PACKAGE + ".graph_probe"
        ):
            return False
        metadata = function.__dict__
        if type(metadata) is not dict:
            return False
        self.context_checkpoint(len(metadata))
        _location_fields(metadata)
        self.require_registered(code, namespace, registered[2])
        actual = self.imports.metadata(namespace.get("__loader__"), SOURCE_PACKAGE + ".graph_probe", initializing=False)
        if actual[0] is not module or actual[3] != registered[2]:
            return False
        return True

    def make_span(self, role, position):
        if type(position) is str:
            return _make_span_unavailable(role, position)
        path, logical, start, end = position
        self.context_checkpoint(2 * len(path) + 1)
        try:
            policy._root_path(path)
        except (policy.GuardError, UnicodeError):
            return _make_span_unavailable(role, "path-format")
        entry = dict.get(self.entries, path)
        try:
            fields = _instance_fields(entry, self.entry_type)
        except _LocationUnavailable:
            return _make_span_unavailable(role, "path-unbound")
        if (
            type(fields.get("path")) is not str or fields["path"] != path
            or type(fields.get("mode")) is not str or fields["mode"] not in {"100644", "100755"}
            or type(fields.get("object_type")) is not str or fields["object_type"] != "blob"
            or "git_dir" not in fields or fields["git_dir"] is not None
            or type(fields.get("object_id")) is not str
            or re.fullmatch(r"[0-9a-f]{40}", fields["object_id"]) is None
        ):
            return _make_span_unavailable(role, "path-unbound")
        return {
            "role": role, "status": "reported-selected-tree", "path": path,
            "logical": logical, "start": start, "end": end,
        }

    def make_context(self, error, candidates, allowance):
        current = trace = frame = arguments = positions = qualified = cause = None
        module = registered = position = None
        index = None
        try:
            qualified = [(number, registered) for number, registered in candidates if self.make_guard(registered)]
            if not qualified:
                return make_context_unavailable("guard-unobserved")
            if len(qualified) != 1:
                return make_context_unavailable("multiple-guards")
            index, registered = qualified[0]
            self.context_checkpoint(index + 1)
            current = error
            for unused in range(index):
                cause = BaseException.__dict__["__cause__"].__get__(current, BaseException)
                current = cause if cause is not None else BaseException.__dict__["__context__"].__get__(current, BaseException)
                if current is None:
                    return make_context_unavailable("context-unavailable", exception=index)
            trace = BaseException.__dict__["__traceback__"].__get__(current, BaseException)
            count = 0
            while trace is not None:
                if type(trace) is not types.TracebackType or count == 256:
                    return make_context_unavailable("context-unavailable", exception=index)
                self.context_checkpoint()
                count += 1
                frame = trace.tb_frame
                if trace.tb_next is None:
                    break
                trace = trace.tb_next
            module = registered[1]()
            if (
                frame is None or registered[0]() is not frame.f_code or type(module) is not types.ModuleType
                or types.ModuleType.__getattribute__(module, "__dict__") is not frame.f_globals
                or not self.make_guard(registered)
            ):
                return make_context_unavailable("context-unavailable", exception=index)
            frame = trace = None
            self.context_checkpoint()
            arguments = BaseException.__dict__["args"].__get__(current, BaseException)
            if type(arguments) is tuple and len(arguments) == 1 and type(arguments[0]) is str:
                limit = min(policy.ERROR_BYTES // 4, policy.ORIGINAL_LIMITS["file_bytes"] // 4,
                            policy.ORIGINAL_LIMITS["entries"] // 2)
                if len(arguments[0]) > limit:
                    return make_context_unavailable("message-bound", exception=index)
                self.context_checkpoint(2 * len(arguments[0]))
            positions = _make_source_labels(arguments)
            arguments = current = None
            if type(positions[0]) is str and positions[0] in _MAKE_CONTEXT_REASONS:
                return make_context_unavailable(positions[0], exception=index)
            result = {
                **make_context_unavailable("spans-unavailable", exception=index),
                "spans": [_make_span_unavailable(role, "output-bound")
                          for role in ("failure", "first-uncertainty")],
            }
            if len(policy.encoded(result)) > allowance:
                return make_context_unavailable("output-bound", exception=index)
            for ordinal, (role, position) in enumerate(zip(("failure", "first-uncertainty"), positions)):
                span = self.make_span(role, position)
                if span["status"] == "reported-selected-tree":
                    sample = {**span, "path": ""}
                    size = len(policy.encoded(sample)) - 2 + _make_path_size(span["path"])
                else:
                    size = len(policy.encoded(span))
                if len(policy.encoded(result)) - len(policy.encoded(result["spans"][ordinal])) + size <= allowance:
                    result["spans"][ordinal] = span
            reported = sum(span["status"] == "reported-selected-tree" for span in result["spans"])
            result["status"] = "reported" if reported == 2 else "partial" if reported else "unavailable"
            result["reason"] = None if reported == 2 else "spans-unavailable"
            self.context_checkpoint()
            if not self.make_guard(registered):
                return make_context_unavailable("epoch-unavailable", exception=index)
            return result
        except _LocationUnavailable as error:
            return make_context_unavailable("work-bound" if error.reason == "registration-bound" else "epoch-unavailable",
                                            exception=index)
        except BaseException:
            return make_context_unavailable("context-unavailable", exception=index)
        finally:
            error = current = trace = frame = arguments = positions = qualified = cause = None
            module = registered = position = None

    def project(self, error):
        def position(code, line, offset):
            _location_code(code)
            if (
                re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]{0,127}|<(?:lambda|listcomp|dictcomp|setcomp|genexpr)>)", code.co_name) is None
                or not policy._component_integer(line, policy.ORIGINAL_LIMITS["file_bytes"], 1)
                or not policy._component_integer(code.co_firstlineno, policy.ORIGINAL_LIMITS["file_bytes"], 1)
                or not policy._component_integer(offset, policy.ORIGINAL_LIMITS["file_bytes"])
                or not offset < len(code.co_code) <= policy.ORIGINAL_LIMITS["file_bytes"]
                or not any(start <= offset < end and actual == line for start, end, actual in code.co_lines())
            ):
                raise _LocationUnavailable("invalid-code-location")
            return {"code": code.co_name, "first_line": code.co_firstlineno, "line": line, "offset": offset}

        current, relation = error, "primary"
        seen, locations, candidates, contexts = set(), [], [], []
        frames, scope_seen, incomplete, foreign_candidate = 0, False, False, False
        missing_trace = False
        while current is not None:
            if id(current) in seen:
                raise _LocationUnavailable("cyclic-exception-chain")
            if len(seen) >= 32:
                raise _LocationUnavailable("exception-chain-bound")
            index = len(seen)
            seen.add(id(current))
            trace = BaseException.__dict__["__traceback__"].__get__(current, BaseException)
            leaf = candidate = None
            while trace is not None:
                if type(trace) is not types.TracebackType or frames >= 256:
                    raise _LocationUnavailable("trace-frame-bound")
                frames += 1
                frame = trace.tb_frame
                code, namespace = frame.f_code, frame.f_globals
                if code is self.call and namespace is self.call_globals:
                    scope_seen = True
                registered = self.registered.get(id(code))
                if registered is not None:
                    module = registered[1]()
                    if (
                        registered[0]() is not code or type(module) is not types.ModuleType
                        or types.ModuleType.__getattribute__(module, "__dict__") is not namespace
                    ):
                        foreign_candidate = True
                    else:
                        # Only weak identities and scalar positions survive this walk.
                        candidate = (index, relation, registered, trace.tb_lineno, trace.tb_lasti,
                                     "registered-raising-frame" if trace.tb_next is None else "registered-caller")
                leaf = code, namespace, trace.tb_lineno, trace.tb_lasti
                trace = trace.tb_next
                frame = None
            if leaf is None:
                incomplete = missing_trace = True
            else:
                code, namespace, line, offset = leaf
                _location_code(code)
                relative = self.module(namespace)
                filename = namespace.get("__file__")
                if type(filename) is not str or code.co_filename != filename:
                    raise _LocationUnavailable("source-code-unbound")
                fields = position(code, line, offset)
                owned = self.codes.get(id(code))
                try:
                    self.require_registered(code, namespace, relative)
                    registered_leaf = self.registered.get(id(code))
                    if (registered_leaf is None or len(registered_leaf) != 4) and (
                        owned is None or owned[0] is not code or owned[1] is not namespace or owned[2] != relative
                    ):
                        raise _LocationUnavailable("source-code-unbound")
                except _LocationUnavailable as unavailable:
                    if unavailable.reason != "source-code-unbound":
                        raise
                    incomplete = True
                else:
                    locations.append({"exception": index, "relation": relation, "file": relative, **fields})
                    if registered_leaf is not None:
                        contexts.append((index, registered_leaf))
            if candidate is not None:
                candidates.append(candidate)
            cause = BaseException.__dict__["__cause__"].__get__(current, BaseException)
            current = cause if cause is not None else BaseException.__dict__["__context__"].__get__(current, BaseException)
            relation = "cause" if cause is not None else "context"
        if not scope_seen:
            raise _LocationUnavailable("public-call-unobserved" if frames else "no-source-trace")
        if incomplete:
            if foreign_candidate:
                raise _LocationUnavailable("source-code-unbound")
            anchors = []
            for index, relation, registered, line, offset, role in candidates:
                code, module = registered[0](), registered[1]()
                if code is None or type(module) is not types.ModuleType:
                    raise _LocationUnavailable("source-code-unbound")
                namespace = types.ModuleType.__getattribute__(module, "__dict__")
                relative = self.module(namespace)
                owned = self.codes.get(id(code))
                self.require_registered(code, namespace, relative)
                if len(registered) != 4 and (
                    owned is None or owned[0] is not code or owned[1] is not namespace or owned[2] != relative
                ):
                    raise _LocationUnavailable("source-code-unbound")
                anchors.append({
                    "exception": index, "relation": relation, "role": role,
                    "file": relative, **position(code, line, offset),
                })
            reason = "no-source-trace" if missing_trace else "source-code-unbound"
            result = {**location_unavailable(reason, references_closed=True), "anchors": anchors}
        else:
            result = {
                **location_unavailable("binding-not-ready"), "status": "observed", "reason": None,
                "locations": locations, "references_closed": True,
            }
        size = len(policy.encoded(result))
        if size > policy.ERROR_BYTES:
            raise _LocationUnavailable("location-size-bound")
        try:
            allowance = policy.ERROR_BYTES - size + len(policy.encoded(result["context"]))
            context = self.make_context(error, contexts, allowance)
            result["context"] = context if len(policy.encoded(context)) <= allowance else make_context_unavailable("output-bound")
        finally:
            contexts.clear()
        return result

    def close(self):
        first = None
        for name in ("codes", "modules", "seen", "registered"):
            try:
                getattr(self, name).clear()
            except BaseException as error:
                if first is None:
                    first = error
        for name in ("entries", "entry_type", "call", "call_globals", "imports", "clock", "deadline"):
            try:
                setattr(self, name, None)
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first


def _import_slot(name):
    ancestry = type.__dict__["__mro__"].__get__(SourceFileLoader, type)
    if len(ancestry) != len(_IMPORT_MRO) or any(
        left is not right for left, right in zip(ancestry, _IMPORT_MRO)
    ):
        raise _LocationUnavailable("original-import-unavailable")
    for parent in ancestry:
        namespace = _location_fields(type.__dict__["__dict__"].__get__(parent, type))
        if name in namespace:
            return namespace[name]
    raise _LocationUnavailable("original-import-unavailable")


class _OriginalImports:
    """Two original return boundaries, scoped to one report and its original session."""

    def __init__(self, observer, measurement):
        self.observer, self.measurement = observer, weakref.ref(measurement)
        self.thread, self.clock = _THREAD(), _CLOCK
        self.deadline = measurement.config["deadline"]
        self.bound = self.active = None
        self.get_hook = self.compile_hook = None
        self.old_slots = {}
        self.restored = dict.fromkeys(("get_code", "source_to_code"))
        self.secondary = []
        self.failed = self.note_failed = self.stopped = self.release_attempted = False
        self.released = None
        self.source_bytes = 0

    def fault(self, error, stage="import-observation"):
        # Stop observation after the first fault; delegation and restoration
        # continue. No exception/traceback (and thus no compiler bytes) is kept.
        self.failed = True
        try:
            if not any(row["stage"] == stage for row in self.secondary):
                self.secondary.append({"stage": stage, "error": policy.component_secondary_error(error)})
        except BaseException:
            self.note_failed = True

    def unchanged(self, *, installed):
        for name, original in _IMPORT_METHODS.items():
            expected = self.get_hook if installed and name == "get_code" else (
                self.compile_hook if installed and name == "source_to_code" else original
            )
            if _import_slot(name) is not expected:
                raise _LocationUnavailable("original-import-unavailable")
        for function, (code, namespace) in _IMPORT_FUNCTIONS.items():
            if function.__code__ is not code or function.__globals__ is not namespace:
                raise _LocationUnavailable("original-import-unavailable")
        namespace = _SOURCE_TO_CODE.__globals__
        _location_fields(namespace)
        modules = []
        for module in (_bootstrap, builtins, time, threading, sys, hashlib):
            if type(module) is not types.ModuleType:
                raise _LocationUnavailable("original-import-unavailable")
            modules.append(_location_fields(types.ModuleType.__getattribute__(module, "__dict__")))
        bootstrap_fields, builtin_fields, time_fields, thread_fields, sys_fields, hash_fields = modules
        if (
            bootstrap_fields.get("_load_unlocked") is not _LOAD_UNLOCKED
            or bootstrap_fields.get("_call_with_frames_removed") is not _CALL_REMOVED
            or namespace.get("_bootstrap") is not _bootstrap
            or namespace.get("__builtins__") is not _IMPORT_BUILTINS
            or namespace.get("compile", _COMPILE) is not _COMPILE
            or namespace.get("exec", _EXEC) is not _EXEC
            or builtin_fields.get("compile") is not _COMPILE or builtin_fields.get("exec") is not _EXEC
            or time_fields.get("monotonic") is not _CLOCK or thread_fields.get("get_ident") is not _THREAD
            or sys_fields.get("_getframe") is not _FRAME
            or hash_fields.get("sha1") is not _BLOB_HASH
        ):
            raise _LocationUnavailable("original-import-unavailable")

    def live(self, *, projection=False):
        if (
            self.release_attempted or self.failed or _THREAD() != self.thread
            or not projection and self.stopped or projection and not all(self.restored.values())
            or self.clock is not _CLOCK
            or type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)
        ):
            raise _LocationUnavailable("original-import-unavailable")
        self.unchanged(installed=not self.stopped)
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now >= self.deadline:
            raise _LocationUnavailable("original-import-unavailable")
        measurement = self.measurement()
        if measurement is None or self.bound is None:
            raise _LocationUnavailable("binding-not-ready")
        if type(measurement.config) is not dict:
            raise _LocationUnavailable("source-binding-invalid")
        _location_fields(measurement.config)
        session, loader, entries, limits, started = self.bound
        observer = self.observer
        budget = _instance_fields(observer.budget, type(observer.budget))
        limit_fields = _instance_fields(limits(), type(limits()))
        if (
            measurement.budget is not observer.budget or measurement.session is not session()
            or type(budget.get("started")) not in (int, float) or budget["started"] != started
            or budget.get("limits") is not limits()
            or type(limit_fields.get("seconds")) is not int
            or started + limit_fields["seconds"] != self.deadline
            or type(measurement.config.get("deadline")) not in (int, float)
            or measurement.config["deadline"] != self.deadline
        ):
            raise _LocationUnavailable("source-binding-invalid")
        fields = _instance_fields(session(), observer.session_type)
        api = measurement.api
        loading = _instance_fields(loader(), api.loader)
        if (
            fields.get("loader") is not loader() or loading.get("entries") is not entries()
            or type(fields.get("owner_thread")) is not int or fields["owner_thread"] != self.thread
        ):
            raise _LocationUnavailable("source-binding-invalid")
        return measurement

    def bind(self, measurement):
        try:
            if self.bound is not None or self.measurement() is not measurement or self.stopped:
                raise _LocationUnavailable("source-binding-invalid")
            observer, api = self.observer, measurement.api
            fields = _instance_fields(measurement.session, observer.session_type)
            loader = fields.get("loader")
            loading = _instance_fields(loader, api.loader)
            entries = loading.get("entries")
            _instance_fields(entries, api.entries)
            budget = _instance_fields(observer.budget, type(observer.budget))
            limits = budget.get("limits")
            _instance_fields(limits, type(limits))
            _location_fields(measurement.states)
            if (
                measurement.session_valid is not True or measurement.budget is not observer.budget
                or fields.get("budget") is not observer.budget
                or loading.get("budget") is not observer.budget
                or type(fields.get("owner_thread")) is not int or fields["owner_thread"] != self.thread
                or type(measurement.states) is not dict
                or any(type(measurement.states.get(name)) is not int or measurement.states[name] != 1
                       for name in ("check_attempts", "session_attempts", "session_constructed"))
                or type(measurement.states.get("check_returned")) is not int
                or measurement.states["check_returned"] != 0
            ):
                raise _LocationUnavailable("source-binding-invalid")
            self.bound = (
                weakref.ref(measurement.session), weakref.ref(loader), weakref.ref(entries),
                weakref.ref(limits), budget.get("started"),
            )
            self.live()
        except BaseException as error:
            self.fault(error)

    def metadata(self, loader, fullname, *, initializing):
        measurement = self.live(projection=not initializing)
        if (
            type(fullname) is not str
            or re.fullmatch(r"scripts\.validation_ownership(?:\.[A-Za-z_][A-Za-z0-9_]*)+", fullname) is None
            or type(sys.modules) is not dict
        ):
            raise _LocationUnavailable("source-module-unowned")
        _location_fields(sys.modules)
        loading = _instance_fields(loader, SourceFileLoader)
        if set(loading) != {"name", "path"} or any(type(loading[name]) is not str for name in loading):
            raise _LocationUnavailable("source-module-unowned")
        module = dict.get(sys.modules, fullname)
        if type(module) is not types.ModuleType:
            raise _LocationUnavailable("source-module-unowned")
        namespace = _location_fields(types.ModuleType.__getattribute__(module, "__dict__"))
        filename = loading["path"]
        registry = _SourceLocations()
        try:
            registry.bind_source(self.observer, measurement)
            if registry.entries is not self.bound[2]():
                raise _LocationUnavailable("source-binding-invalid")
            relative = registry.file(filename)
            entry = dict.get(registry.entries, relative)
            item = _instance_fields(entry, registry.entry_type)
            object_id = item["object_id"]
        finally:
            registry.close()
        expected = fullname.replace(".", "/")
        if relative not in {expected + ".py", expected + "/__init__.py"} or loading["name"] != fullname:
            raise _LocationUnavailable("source-module-unowned")
        package = relative.endswith("/__init__.py")
        spec = namespace.get("__spec__")
        declared = _instance_fields(spec, ModuleSpec)
        search = declared.get("submodule_search_locations")
        if (
            namespace.get("__loader__") is not loader
            or any(type(namespace.get(name)) is not str or namespace[name] != value for name, value in (
                ("__name__", fullname), ("__file__", filename),
                ("__package__", fullname if package else fullname.rsplit(".", 1)[0]),
            ))
            or any(type(declared.get(name)) is not str or declared[name] != value for name, value in (
                ("name", fullname), ("origin", filename),
            ))
            or declared.get("loader") is not loader or declared.get("_initializing") is not initializing
            or declared.get("loader_state") is not None or declared.get("_set_fileattr") is not True
            or ((type(search) is not list or len(search) != 1 or type(search[0]) is not str
                 or search[0] != filename.rsplit("/", 1)[0]) if package else search is not None)
        ):
            raise _LocationUnavailable("source-module-unowned")
        return module, spec, entry, relative, object_id

    def same(self, token):
        actual = self.metadata(token["loader"], token["name"], initializing=True)
        if any(actual[index] is not token["binding"][index] for index in range(3)) or (
            actual[3:] != token["binding"][3:]
        ):
            raise _LocationUnavailable("source-binding-invalid")

    def install(self):
        owner = self

        def get_code(self, fullname):
            try:
                return owner.get_code(self, fullname)
            finally:
                self = fullname = None

        def source_to_code(self, data, path, *, _optimize=-1):
            try:
                return owner.source_to_code(self, data, path, _optimize=_optimize)
            finally:
                self = data = path = None

        self.get_hook, self.compile_hook = get_code, source_to_code
        try:
            namespace = _location_fields(type.__dict__["__dict__"].__get__(SourceFileLoader, type))
            self.old_slots = {name: (name in namespace, namespace.get(name)) for name in self.restored}
            self.unchanged(installed=False)
            SourceFileLoader.source_to_code = source_to_code
            SourceFileLoader.get_code = get_code
            self.unchanged(installed=True)
        except BaseException as error:
            self.fault(error)

    def get_code(self, loader, fullname):
        token = caller = result = binding = None
        try:
            if not self.stopped and not self.failed and type(fullname) is str and fullname.startswith(
                SOURCE_PACKAGE + "."
            ):
                try:
                    caller = _FRAME(2)
                    natural = (
                        caller.f_code is _IMPORT_FUNCTIONS[_EXEC_MODULE][0]
                        and caller.f_globals is _EXEC_MODULE.__globals__ and caller.f_back is not None
                        and caller.f_back.f_code is _IMPORT_FUNCTIONS[_LOAD_UNLOCKED][0]
                        and caller.f_back.f_globals is _LOAD_UNLOCKED.__globals__
                    )
                    caller = None
                    if natural:
                        if self.active is not None:
                            raise _LocationUnavailable("original-import-unavailable")
                        binding = self.metadata(loader, fullname, initializing=True)
                        token = {"loader": loader, "name": fullname, "binding": binding,
                                 "compiled": None, "compile_seen": False, "caller": id(_FRAME())}
                        binding = None
                        self.active = token
                except BaseException as error:
                    self.fault(error)
            result = _GET_CODE(loader, fullname)
            if token is not None and not self.failed:
                try:
                    self.same(token)
                    # Cache-only returns never passed the source compiler.
                    if token["compiled"] is not None:
                        if result is not token["compiled"]:
                            raise _LocationUnavailable("source-code-unbound")
                        self.record(token, result)
                except BaseException as error:
                    self.fault(error)
            return result
        finally:
            caller = loader = fullname = result = binding = None
            if token is not None:
                self.active = None
                token.clear()

    def source_to_code(self, loader, data, path, *, _optimize=-1):
        token = self.active
        caller = result = None
        valid = False
        try:
            if token is not None and not self.failed:
                try:
                    caller = _FRAME(2)
                    original_call = (
                        caller.f_code is _IMPORT_FUNCTIONS[_GET_CODE][0] and caller.f_globals is _GET_CODE.__globals__
                        and caller.f_back is not None and id(caller.f_back) == token["caller"]
                    )
                    caller = None
                    if not original_call:
                        token = None
                except BaseException as error:
                    self.fault(error)
            if token is not None and not self.failed:
                try:
                    self.same(token)
                    if (
                        loader is not token["loader"] or token["compile_seen"]
                        or type(path) is not str or path != "/repo/" + token["binding"][3]
                        or type(data) is not bytes or len(data) > policy.ORIGINAL_LIMITS["file_bytes"]
                        or type(_optimize) is not int or _optimize != -1
                    ):
                        raise _LocationUnavailable("original-import-unavailable")
                    token["compile_seen"] = True
                    self.source_bytes += len(data)
                    if self.source_bytes > policy.ORIGINAL_LIMITS["file_bytes"]:
                        raise _LocationUnavailable("registration-bound")
                    digest = _BLOB_HASH()
                    digest.update(b"blob " + str(len(data)).encode("ascii") + b"\0")
                    digest.update(data)
                    if digest.hexdigest() != token["binding"][4]:
                        raise _LocationUnavailable("source-binding-invalid")
                    digest = None
                    valid = True
                except BaseException as error:
                    self.fault(error)
            result = _SOURCE_TO_CODE(loader, data, path, _optimize=_optimize)
            if valid and not self.failed:
                try:
                    self.same(token)
                    _location_code(result)
                    token["compiled"] = result
                except BaseException as error:
                    self.fault(error)
            return result
        finally:
            caller = loader = data = path = result = token = None

    def record(self, token, code):
        if self.active is not token or token["compiled"] is not code:
            raise _LocationUnavailable("source-code-unbound")
        self.same(token)
        observer = self.observer
        if getattr(observer, "location_reason", "binding-not-ready") is not None:
            raise _LocationUnavailable("binding-not-ready")
        module, spec, entry, relative, object_id = token["binding"]
        namespace = types.ModuleType.__getattribute__(module, "__dict__")
        registry = _SourceLocations()
        registry.work = observer.location_work
        registry.clock, registry.deadline = self.clock, self.deadline
        staged = {}
        try:
            registry.register_code(code, namespace, "/repo/" + relative, relative)
            binding = (weakref.ref(token["loader"]), weakref.ref(spec), weakref.ref(entry),
                       token["name"], object_id)
            staged = {
                identity: (weakref.ref(item[0]), weakref.ref(module), relative, binding)
                for identity, item in registry.codes.items()
            }
            if len(observer.location_codes) + len(staged) > policy.ORIGINAL_LIMITS["entries"]:
                raise _LocationUnavailable("registration-bound")
            observer.location_codes.update(staged)
            observer.location_work = registry.work
        finally:
            staged.clear()
            registry.close()

    def require_registered(self, registered, code, namespace, relative):
        loader, spec, entry, fullname, object_id = registered[3]
        actual = self.metadata(loader(), fullname, initializing=False)
        if (
            registered[0]() is not code or registered[1]() is not actual[0]
            or types.ModuleType.__getattribute__(actual[0], "__dict__") is not namespace
            or actual[1] is not spec() or actual[2] is not entry()
            or actual[3:] != (relative, object_id)
        ):
            raise _LocationUnavailable("source-code-unbound")

    def restore_slot(self, name):
        present, original = self.old_slots[name]
        if present:
            setattr(SourceFileLoader, name, original)
        else:
            namespace = _location_fields(type.__dict__["__dict__"].__get__(SourceFileLoader, type))
            if name in namespace:
                delattr(SourceFileLoader, name)

    def restore(self):
        if self.stopped:
            return
        try:
            self.unchanged(installed=True)
        except BaseException as error:
            self.fault(error)
        self.stopped = True
        for name in self.restored:
            try:
                self.restore_slot(name)
            except BaseException as error:
                self.fault(error, "import-" + name.replace("_", "-") + "-restore")
            try:
                present, original = self.old_slots[name]
                namespace = _location_fields(type.__dict__["__dict__"].__get__(SourceFileLoader, type))
                self.restored[name] = (name in namespace) is present and (
                    not present or namespace[name] is original
                ) and _import_slot(name) is _IMPORT_METHODS[name]
                if self.restored[name] is not True:
                    raise _LocationUnavailable("import-restoration-unavailable")
            except BaseException as error:
                self.restored[name] = None
                self.fault(error, "import-" + name.replace("_", "-") + "-restore")
        if self.active is not None:
            self.active.clear()
        self.active = self.get_hook = self.compile_hook = None
        self.old_slots.clear()

    def close(self):
        if self.release_attempted:
            if self.released is not True:
                raise _LocationUnavailable("import-restoration-unavailable")
            return
        self.release_attempted = True
        first = None
        for action in (
            lambda: getattr(self.observer, "location_codes", {}).clear(),
            lambda: self.old_slots.clear(),
            lambda: None if self.active is None else self.active.clear(),
        ):
            try:
                action()
            except BaseException as error:
                if first is None:
                    first = error
        for name in ("active", "bound", "observer", "measurement", "get_hook", "compile_hook", "clock"):
            try:
                setattr(self, name, None)
                if getattr(self, name) is not None:
                    raise _LocationUnavailable("import-restoration-unavailable")
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first
        self.released = True


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
        self.import_release_attempted = self.import_release_reported = False
        self.import_release_error = None

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
            self.location_work = locations.work
            try:
                locations.close()
                self.location_references_closed = True
            except BaseException as error:
                self.location_codes.clear()
                self.location_reason = "locator-failed"
                secondary.append({"stage": "location-publication", "error": policy.component_secondary_error(error)})
        return secondary

    def start_imports(self, measurement):
        if hasattr(self, "imports"):
            raise policy.GuardError("original source import observation is single-use")
        self.imports = _OriginalImports(self, measurement)
        self.imports.install()

    def bind_imports(self, measurement):
        self.imports.bind(measurement)

    def restore_imports(self):
        self.imports.restore()

    def import_cleanup(self):
        imports = getattr(self, "imports", None)
        return {
            "source_imports_restored": None if imports is None else all(
                value is True for value in imports.restored.values()
            ),
            "source_imports_released": None if imports is None else imports.released,
        }

    def close_imports(self, measurement):
        if self.import_release_attempted:
            if self.import_release_error is not None:
                raise _LocationUnavailable("import-restoration-unavailable")
            return
        self.import_release_attempted = True
        try:
            try:
                imports = getattr(self, "imports", None)
                if imports is not None:
                    imports.close()
            finally:
                if measurement is not None and measurement.cleanup is not None:
                    measurement.cleanup.update(self.import_cleanup())
        except BaseException as error:
            self.import_release_error = policy.component_secondary_error(error)
            raise

    def finish_imports(self, measurement):
        """Transfer one release failure, not permission to retry or cleanup credit."""
        if not self.import_release_attempted:
            try:
                self.close_imports(measurement)
            except BaseException:
                if self.import_release_error is None:
                    raise
        if self.import_release_error is not None and not self.import_release_reported:
            closing = {"stage": "import-reference", "error": copy.deepcopy(self.import_release_error)}
            self.import_release_reported = True
            return closing
        return None

    def source_locations(self, error, measurement):
        locations = _SourceLocations()
        locations.registered = getattr(self, "location_codes", {})
        locations.imports = imports = getattr(self, "imports", None)
        try:
            try:
                reason = getattr(self, "location_reason", "binding-not-ready")
                if reason is not None:
                    raise _LocationUnavailable(reason)
                if imports is not None:
                    if not all(value is True for value in imports.restored.values()):
                        raise _LocationUnavailable("import-restoration-unavailable")
                    if imports.failed:
                        raise _LocationUnavailable("original-import-unavailable")
                locations.bind(self, measurement)
                value = locations.project(error)
            except _LocationUnavailable as unavailable_error:
                value = location_unavailable(unavailable_error.reason)
        finally:
            try:
                locations.close()
            finally:
                if imports is not None:
                    self.close_imports(measurement)
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
