"""Closed header launch and actual kernel-input receipts, not standalone grants."""

from __future__ import annotations

import errno
import hashlib
import re
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


def launch_binding(config):
    return hashlib.sha256(encoded({name: config[name] for name in LAUNCH_FIELDS})).hexdigest()


def validate_launch(config):
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
    if (
        len(kinds) != 1 or config["mode"] != "compile" or config.get("private_install") is not None
        or not isinstance(value, dict) or set(value) != {"version", "scope", "binding"}
        or type(value["version"]) is not int or value["version"] != 1
        or value["scope"] != launch_scope(config["root"])
        or value["binding"] != launch_binding(config)
        or config["executables"] != dependency["executables"]
        or not config["argv"] or config["argv"][0] != config["executables"][0]
    ):
        raise ChannelError("header runtime launch is unbound or malformed")
    if "filter_kernel" in kinds:
        validate_filter(dependency["filter_kernel"], dependency["executables"])
    elif len(config["executables"]) != 2 or PurePosixPath(config["executables"][0]).name not in {
        "arm-none-eabi-gcc", "arm-none-eabi-gcc.exe",
    }:
        raise ChannelError("header runtime launch has a foreign compiler")


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
        if (
            not isinstance(row, dict)
            or set(row) != {"sequence", "operation", "path", "result", "data", "bytes", "sha256", "eof"}
            or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= count_limit
            or row["sequence"] in result or type(row["result"]) is not int
            or not isinstance(row["operation"], str) or not isinstance(row["path"], str)
        ):
            raise ChannelError("malformed or repeated header kernel input")
        payload = row["data"], row["bytes"], row["sha256"], row["eof"]
        if row["operation"] == "statfs":
            present = dict(profile["statfs"]).get(row["path"])
            if present is None or row["result"] != (0 if present else -errno.ENOENT):
                raise ChannelError("header statfs result contradicts its exact path")
            if present:
                if (
                    not isinstance(row["data"], str) or not re.fullmatch("[0-9a-f]{240}", row["data"])
                    or payload[1:] != (None, None, None)
                    or not int.from_bytes(bytes.fromhex(row["data"])[80:88], "little") & 1
                ):
                    raise ChannelError("header statfs omitted its real readonly result")
            elif payload != (None, None, None, None):
                raise ChannelError("absent header statfs claims returned data")
        elif row["operation"] == "stream":
            if (
                row["path"] not in READ_PATHS or row["result"] != 0 or row["data"] is not None
                or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= file_limit
                or not isinstance(row["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", row["sha256"])
                or type(row["eof"]) is not bool
            ):
                raise ChannelError("invalid actual header kernel stream")
        elif row["operation"] == "absent":
            if row["path"] not in ABSENT_PATHS or row["result"] != -errno.ENOENT or payload != (None, None, None, None):
                raise ChannelError("invalid negative header runtime input")
        else:
            raise ChannelError("unknown header kernel operation")
        result[row["sequence"]] = row
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
