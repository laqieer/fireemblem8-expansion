"""Closed header launch and actual kernel-input receipts, not standalone grants."""

from __future__ import annotations

import errno
import hashlib
import hmac
import re
import struct
from dataclasses import dataclass, field
from pathlib import PurePosixPath

if __package__:
    from .authority import encoded, parse_json
    from .budget import MakeProbeError
    from .private_install import launch_scope
    from .producer_channel import ChannelError
else:
    from authority import encoded, parse_json
    from budget import MakeProbeError
    from private_install import launch_scope
    from producer_channel import ChannelError


KERNEL_PREFIX = "header-kernel:"
KERNEL_END = "header-kernel-end:"
STATFS_PATHS = ("/sys/fs/selinux", "/selinux")
READ_PATHS = ("/proc/filesystems", "/proc/mounts")
ABSENT_PATHS = ("/etc/selinux/config",)
LAUNCH_FIELDS = (
    "root", "mode", "argv", "environment", "code", "sources", "enumerations",
    "executables", "mounts", "dependency",
)
COMPLETION_DOMAIN = "header-kernel-completion-v2"
FILTER_PURPOSE = "header-filter-completion-v2"
ROW_FIELDS = ("sequence", "operation", "path", "result", "data", "bytes", "sha256", "eof")
COMPLETION_FIELDS = ("version", "scope", "binding", "status", "count", "manifest_sha256", "tag")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_POINTER_BYTES = struct.calcsize("P")
_ROW_CONTAINER_BYTES = 512 + len(ROW_FIELDS) * 3 * _POINTER_BYTES
_TERMINAL_CONTAINER_BYTES = 512 + len(COMPLETION_FIELDS) * 3 * _POINTER_BYTES
_HASH_STATE_BYTES = 512


@dataclass(frozen=True, slots=True)
class _FilterLaunch:
    scope: str
    binding: str
    receipt_key: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class _HeaderRow:
    sequence: int
    operation: str
    path: str
    result: int
    data: str | None
    bytes: int | None
    sha256: str | None
    eof: bool | None

    def mapping(self):
        return {
            "sequence": self.sequence, "operation": self.operation, "path": self.path,
            "result": self.result, "data": self.data, "bytes": self.bytes,
            "sha256": self.sha256, "eof": self.eof,
        }


@dataclass(frozen=True, slots=True)
class _AcceptedHeaderTranscript:
    rows: tuple[_HeaderRow, ...]
    row_limit: int
    semantic_size: int


class _ImmutableHeaderView(dict):
    __slots__ = ()

    @classmethod
    def from_row(cls, row):
        value = dict.__new__(cls)
        dict.__init__(value)
        dict.update(value, row.mapping())
        return value

    def __init__(self, *args, **kwargs):
        raise TypeError("accepted header rows cannot be reinitialized")

    def _immutable(self, *args, **kwargs):
        raise TypeError("accepted header rows are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self


def launch_binding(config):
    return hashlib.sha256(encoded({name: config[name] for name in LAUNCH_FIELDS})).hexdigest()


def validate_launch(config, *, consume_key=True):
    dependency = config.get("dependency") or {}
    kinds = {"header_search", "filter_kernel"} & set(dependency)
    value = config.get("header_runtime")
    if "toolchain_probe" in dependency:
        if value is not None:
            raise ChannelError("toolchain launch cannot borrow a header runtime grant")
        return
    if not kinds:
        if value is not None:
            raise ChannelError("unrelated command carries a header runtime grant")
        return
    filter_launch = "filter_kernel" in kinds
    fields = {"version", "scope", "binding", "receipt_key"} if filter_launch else {
        "version", "scope", "binding",
    }
    version = 2 if filter_launch else 1
    if (
        len(kinds) != 1 or config["mode"] != "compile" or config.get("private_install") is not None
        or not isinstance(value, dict) or set(value) != fields
        or type(value["version"]) is not int or value["version"] != version
        or value["scope"] != launch_scope(config["root"])
        or not isinstance(value["binding"], str) or not _HEX64.fullmatch(value["binding"])
        or value["binding"] != launch_binding(config)
        or config["executables"] != dependency["executables"]
        or not config["argv"] or config["argv"][0] != config["executables"][0]
    ):
        raise ChannelError("header runtime launch is unbound or malformed")
    if filter_launch:
        if not isinstance(value["receipt_key"], str) or not _HEX64.fullmatch(value["receipt_key"]):
            raise ChannelError("header runtime launch has a malformed private receipt key")
        validate_filter(dependency["filter_kernel"], dependency["executables"])
        if not consume_key:
            return None
        receipt_key = bytes.fromhex(value.pop("receipt_key"))
        return _FilterLaunch(value["scope"], value["binding"], receipt_key)
    elif len(config["executables"]) != 2 or PurePosixPath(config["executables"][0]).name not in {
        "arm-none-eabi-gcc", "arm-none-eabi-gcc.exe",
    }:
        raise ChannelError("header runtime launch has a foreign compiler")
    return None


def validate_filter(value, executables):
    if (
        executables != ["/usr/bin/sed"] and executables != ("/usr/bin/sed",)
        or not isinstance(value, dict) or set(value) != {"version", "statfs", "reads", "absent"}
        or type(value["version"]) is not int or value["version"] != 1
        or not isinstance(value["statfs"], list) or len(value["statfs"]) != len(STATFS_PATHS)
        or value["reads"] != list(READ_PATHS) or value["absent"] != list(ABSENT_PATHS)
    ):
        raise ChannelError("malformed or unbound sed kernel-input profile")
    for row, expected in zip(value["statfs"], STATFS_PATHS):
        if (
            not isinstance(row, list) or len(row) != 2 or row[0] != expected
            or type(row[1]) is not bool
        ):
            raise ChannelError("sed statfs declaration differs from its actual runtime paths")
    return value


def _row_mapping(sequence, operation, path, result, data, count, digest, eof):
    return {
        "sequence": sequence, "operation": operation, "path": path, "result": result,
        "data": data, "bytes": count, "sha256": digest, "eof": eof,
    }


def row_wire_limit(profile, *, count_limit, file_limit):
    validate_filter(profile, ["/usr/bin/sed"])
    if (
        type(count_limit) is not int or count_limit < 2
        or type(file_limit) is not int or file_limit < 0
    ):
        raise ChannelError("header receipt limits cannot reserve a completion")
    sequence = count_limit - 1
    rows = []
    for path, present in profile["statfs"]:
        rows.append(_row_mapping(
            sequence, "statfs", path, 0 if present else -errno.ENOENT,
            "0" * 240 if present else None, None, None, None,
        ))
    rows.extend(
        _row_mapping(sequence, "stream", path, 0, None, file_limit, "0" * 64, False)
        for path in READ_PATHS
    )
    rows.extend(
        _row_mapping(sequence, "absent", path, -errno.ENOENT, None, None, None, None)
        for path in ABSENT_PATHS
    )
    return len(KERNEL_PREFIX) + max(len(encoded(row)) for row in rows)


def terminal_wire_limit(scope, binding, *, count_limit):
    if (
        type(scope) is not str or type(binding) is not str or not _HEX64.fullmatch(binding)
        or type(count_limit) is not int or count_limit < 2
    ):
        raise ChannelError("header completion reservation is malformed")
    body = {
        "version": 2, "scope": scope, "binding": binding, "status": 0,
        "count": count_limit - 1, "manifest_sha256": "0" * 64, "tag": "0" * 64,
    }
    return len(KERNEL_END) + len(encoded(body))


def row_private_reservation(row_limit):
    return row_limit + _ROW_CONTAINER_BYTES + _HASH_STATE_BYTES


def terminal_private_reservation(terminal_limit):
    return terminal_limit + _TERMINAL_CONTAINER_BYTES + 2 * _HASH_STATE_BYTES


def _reserve_decode(reserve, length, *, terminal=False):
    # ASCII copy, decoded text, parser containers and one canonical re-encoding.
    reserve(6 * length + (_TERMINAL_CONTAINER_BYTES if terminal else _ROW_CONTAINER_BYTES))


def validate_row_values(
    profile, sequence, operation, path, result, data, count, digest, eof,
    *, count_limit, file_limit,
):
    if (
        type(sequence) is not int or not 1 <= sequence <= count_limit
        or type(result) is not int
        or not isinstance(operation, str) or not isinstance(path, str)
    ):
        raise ChannelError("malformed header kernel input")
    payload = data, count, digest, eof
    if operation == "statfs":
        present = next((present for candidate, present in profile["statfs"] if candidate == path), None)
        if present is None or result != (0 if present else -errno.ENOENT):
            raise ChannelError("header statfs result contradicts its exact path")
        if present:
            if (
                not isinstance(data, str) or not re.fullmatch("[0-9a-f]{240}", data)
                or payload[1:] != (None, None, None)
                or not int.from_bytes(bytes.fromhex(data)[80:88], "little") & 1
            ):
                raise ChannelError("header statfs omitted its real readonly result")
        elif payload != (None, None, None, None):
            raise ChannelError("absent header statfs claims returned data")
    elif operation == "stream":
        if (
            path not in READ_PATHS or result != 0 or data is not None
            or type(count) is not int or not 0 <= count <= file_limit
            or not isinstance(digest, str) or not _HEX64.fullmatch(digest)
            or type(eof) is not bool
        ):
            raise ChannelError("invalid actual header kernel stream")
    elif operation == "absent":
        if path not in ABSENT_PATHS or result != -errno.ENOENT or payload != (None, None, None, None):
            raise ChannelError("invalid negative header runtime input")
    else:
        raise ChannelError("unknown header kernel operation")


def _parse_row(row, profile, *, count_limit, file_limit):
    if not isinstance(row, dict) or set(row) != set(ROW_FIELDS):
        raise ChannelError("malformed header kernel input")
    validate_row_values(
        profile, row["sequence"], row["operation"], row["path"], row["result"],
        row["data"], row["bytes"], row["sha256"], row["eof"],
        count_limit=count_limit, file_limit=file_limit,
    )
    return _HeaderRow(
        row["sequence"], row["operation"], row["path"], row["result"],
        row["data"], row["bytes"], row["sha256"], row["eof"],
    )


def completion(scope, binding, receipt_key, count, manifest_sha256):
    body = {
        "version": 2, "scope": scope, "binding": binding, "status": 0,
        "count": count, "manifest_sha256": manifest_sha256,
    }
    return {
        **body,
        "tag": hmac.new(receipt_key, encoded([COMPLETION_DOMAIN, body]), hashlib.sha256).hexdigest(),
    }


def authenticate_records(
    values, profile, verifier, *, count_limit, file_limit, reserve=lambda size: None,
):
    validate_filter(profile, ["/usr/bin/sed"])
    if (
        type(verifier) is not _FilterLaunch or type(verifier.scope) is not str
        or type(verifier.binding) is not str or not _HEX64.fullmatch(verifier.binding)
        or type(verifier.receipt_key) is not bytes or len(verifier.receipt_key) != 32
    ):
        raise ChannelError("header completion lacks its private launch verifier")
    row_limit = row_wire_limit(profile, count_limit=count_limit, file_limit=file_limit)
    terminal_limit = terminal_wire_limit(verifier.scope, verifier.binding, count_limit=count_limit)
    reserve(256)
    rows = {}
    completed = None
    for value in values:
        if isinstance(value, str) and value.startswith(KERNEL_END):
            if completed is not None or len(value) > terminal_limit:
                raise ChannelError("unbound or repeated header kernel completion")
            payload = value[len(KERNEL_END):]
            _reserve_decode(reserve, len(payload), terminal=True)
            try:
                completed = parse_json(payload.encode("ascii"), "header kernel completion")
            except (MakeProbeError, UnicodeError) as error:
                raise ChannelError(str(error)) from error
            if (
                not isinstance(completed, dict) or set(completed) != set(COMPLETION_FIELDS)
                or type(completed["version"]) is not int or completed["version"] != 2
                or completed["scope"] != verifier.scope or completed["binding"] != verifier.binding
                or type(completed["status"]) is not int or completed["status"] != 0
                or type(completed["count"]) is not int or not 1 <= completed["count"] < count_limit
                or not isinstance(completed["manifest_sha256"], str)
                or not _HEX64.fullmatch(completed["manifest_sha256"])
                or not isinstance(completed["tag"], str) or not _HEX64.fullmatch(completed["tag"])
            ):
                raise ChannelError("malformed or foreign header kernel completion")
            continue
        if not isinstance(value, str) or not value.startswith(KERNEL_PREFIX):
            continue
        if len(value) > row_limit:
            raise ChannelError("header kernel input exceeds its row bound")
        payload = value[len(KERNEL_PREFIX):]
        _reserve_decode(reserve, len(payload))
        try:
            parsed = parse_json(payload.encode("ascii"), "header kernel input")
        except (MakeProbeError, UnicodeError) as error:
            raise ChannelError(str(error)) from error
        reserve(_ROW_CONTAINER_BYTES)
        row = _parse_row(parsed, profile, count_limit=count_limit - 1, file_limit=file_limit)
        if row.sequence in rows:
            raise ChannelError("malformed or repeated header kernel input")
        reserve(2 * _POINTER_BYTES)
        rows[row.sequence] = row
    if (
        completed is None or len(rows) != completed["count"]
        or any(sequence not in rows for sequence in range(1, completed["count"] + 1))
    ):
        raise ChannelError("incomplete header kernel input sequence")
    manifest = hashlib.sha256()
    reserve(_HASH_STATE_BYTES)
    manifest.update(b"[")
    semantic_size = 2 + max(0, completed["count"] - 1)
    for sequence in range(1, completed["count"] + 1):
        row = rows[sequence]
        reserve(row_limit + _HASH_STATE_BYTES)
        value = encoded(row.mapping())
        if len(KERNEL_PREFIX) + len(value) > row_limit:
            raise ChannelError("header kernel input exceeded its admitted row shape")
        if sequence != 1:
            manifest.update(b",")
        manifest.update(value)
        semantic_size += len(value)
    manifest.update(b"]")
    reserve(256)
    digest = manifest.hexdigest()
    reserve(_TERMINAL_CONTAINER_BYTES)
    body = {name: completed[name] for name in COMPLETION_FIELDS if name != "tag"}
    reserve(terminal_limit + _HASH_STATE_BYTES)
    expected = hmac.new(verifier.receipt_key, encoded([COMPLETION_DOMAIN, body]), hashlib.sha256).hexdigest()
    if digest != completed["manifest_sha256"] or not hmac.compare_digest(expected, completed["tag"]):
        raise ChannelError("header kernel completion authentication failed")
    if not any(
        row.operation == "statfs" and row.path == STATFS_PATHS[0] for row in rows.values()
    ):
        raise ChannelError("header kernel transcript omitted its first statfs evidence")
    reserve(128 + completed["count"] * _POINTER_BYTES)
    return _AcceptedHeaderTranscript(
        tuple(rows[index] for index in range(1, completed["count"] + 1)),
        row_limit, semantic_size,
    )


def immutable_views(transcript, *, reserve=lambda size: None):
    if type(transcript) is not _AcceptedHeaderTranscript:
        raise ChannelError("header runtime transcript is not accepted authority")
    result = []
    reserve(640 + transcript.semantic_size + len(transcript.rows) * _POINTER_BYTES)
    for row in transcript.rows:
        reserve(_ROW_CONTAINER_BYTES + transcript.row_limit)
        result.append(_ImmutableHeaderView.from_row(row))
    return tuple(result)


def records(values, profile, *, count_limit, file_limit):
    result = {}
    completed = None
    if profile is not None:
        validate_filter(profile, ["/usr/bin/sed"])
    for value in values:
        if isinstance(value, str) and value.startswith(KERNEL_END):
            count = value[len(KERNEL_END):]
            if (
                profile is None or completed is not None or len(count) > len(str(count_limit))
                or not re.fullmatch(r"[0-9]+", count)
            ):
                raise ChannelError("unbound or repeated header kernel completion")
            completed = int(count)
            if not 1 <= completed <= count_limit:
                raise ChannelError("header kernel completion exceeds its count bound")
            continue
        if not isinstance(value, str) or not value.startswith(KERNEL_PREFIX):
            continue
        if profile is None:
            raise ChannelError("ungranted command reports sed kernel inputs")
        try:
            row = parse_json(value[len(KERNEL_PREFIX):].encode("ascii"), "header kernel input")
        except (MakeProbeError, UnicodeError) as error:
            raise ChannelError(str(error)) from error
        accepted = _parse_row(row, profile, count_limit=count_limit, file_limit=file_limit)
        if accepted.sequence in result:
            raise ChannelError("malformed or repeated header kernel input")
        result[accepted.sequence] = row
    if (
        set(result) != set(range(1, len(result) + 1))
        or profile is not None and (
            completed != len(result) or not any(
                row["operation"] == "statfs" and row["path"] == STATFS_PATHS[0]
                for row in result.values()
            )
        )
    ):
        raise ChannelError("incomplete header kernel input sequence")
    return tuple(result[index] for index in sorted(result))
