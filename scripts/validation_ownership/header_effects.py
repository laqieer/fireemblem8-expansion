"""Closed per-dispatch header filesystem effect data, never standalone authority."""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath

if __package__:
    from .authority import relative_path
    from .budget import MakeProbeError
    from .producer_channel import ChannelError, validate_publication_identity
else:
    from authority import relative_path
    from budget import MakeProbeError
    from producer_channel import ChannelError, validate_publication_identity


OPERATIONS = frozenset({"directory", "retire", "transfer"})


@dataclass(frozen=True)
class Effect:
    operation: str
    path: str
    source: str | None
    expected: tuple | None
    owner: str


def _path(value):
    try:
        return relative_path(value)
    except MakeProbeError as error:
        raise ChannelError(str(error)) from error


def header_target(value):
    value = _path(value)
    if not value.startswith("build/") or not value.endswith(".headers.d"):
        raise ChannelError("filesystem effect requires its exact header target")
    return value


def validate_record(record):
    if (
        not isinstance(record, (list, tuple)) or len(record) != 6
        or not isinstance(record[0], str)
        or not isinstance(record[1], str) or not re.fullmatch("[0-9a-f]{64}", record[1])
        or type(record[2]) is not int or not 0 <= record[2] <= 0o777
        or type(record[3]) is not int or record[3] < 0
        or not isinstance(record[4], str) or not re.fullmatch("[0-9a-f]{64}", record[4])
    ):
        raise ChannelError("malformed header temporary version")
    _path(record[0])
    identity = validate_publication_identity(record[5], record[2], record[3])
    if identity[6] != 1:
        raise ChannelError("header temporary is not a single-link regular publication")
    return (*record[:5], identity)


def validate_effect(effect, target):
    target = header_target(target)
    if (
        type(effect) is not Effect or type(effect.operation) is not str
        or effect.operation not in OPERATIONS
    ):
        raise ChannelError("untyped header filesystem effect")
    if not isinstance(effect.owner, str) or not re.fullmatch("[0-9a-f]{64}", effect.owner):
        raise ChannelError("header filesystem effect has no owner")
    path = _path(effect.path)
    parent = PurePosixPath(target).parent.as_posix()
    if effect.operation == "directory":
        if path != parent or effect.source is not None or effect.expected is not None:
            raise ChannelError("directory effect differs from its header parent")
    else:
        expected = validate_record(effect.expected)
        if effect.operation == "retire":
            if path != target + ".tmp" or effect.source is not None or expected[0] != path:
                raise ChannelError("retirement effect differs from its owned header temporary")
        elif path != target or effect.source != target + ".tmp2" or expected[0] != effect.source:
            raise ChannelError("transfer effect differs from its owned header temporary")
    return effect


def validate_confirmation(value, *, count_limit, file_limit):
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "slot", "owner", "operation", "path", "source", "before", "after", "directories"}
        or value["kind"] != "filesystem" or type(value["slot"]) is not int or value["slot"] < 0
        or not isinstance(value["owner"], str) or not re.fullmatch("[0-9a-f]{64}", value["owner"])
        or not isinstance(value["operation"], str) or value["operation"] not in OPERATIONS
        or not isinstance(value["directories"], list) or len(value["directories"]) > count_limit
    ):
        raise ChannelError("malformed header filesystem confirmation")
    path = _path(value["path"])
    seen = set()
    for directory in value["directories"]:
        if (
            not isinstance(directory, list) or len(directory) != 4
            or any(type(item) is not int or not 0 <= item < 1 << 64 for item in directory[1:])
            or not stat.S_ISDIR(directory[3]) or directory[3] & 0o700 != 0o700
        ):
            raise ChannelError("invalid created header parent")
        name = _path(directory[0])
        if (
            name in seen or not (name == "build" or name.startswith("build/"))
            or not (name == path or path.startswith(name + "/"))
        ):
            raise ChannelError("unrelated or duplicate created header parent")
        seen.add(name)
    if value["operation"] == "directory":
        if (
            not (path == "build" or path.startswith("build/"))
            or value["before"] is not None or value["after"] is not None or value["source"] is not None
        ):
            raise ChannelError("directory effect claims a file transfer")
    else:
        before = validate_record(value["before"])
        if before[3] > file_limit or value["directories"]:
            raise ChannelError("invalid header effect input extent")
        if value["operation"] == "retire":
            if (
                not path.endswith(".tmp") or before[0] != path
                or value["after"] is not None or value["source"] is not None
            ):
                raise ChannelError("invalid header temporary retirement result")
            header_target(path[:-4])
        else:
            header_target(path)
            after = validate_record(value["after"])
            if (
                value["source"] != path + ".tmp2" or value["source"] != before[0]
                or after[0] != path or after[1] != value["owner"]
                or after[2:5] != before[2:5] or after[5][:5] != before[5][:5]
                or after[5][6] != before[5][6] or after[3] > file_limit
            ):
                raise ChannelError("header transfer did not preserve its actual input")
    return value
