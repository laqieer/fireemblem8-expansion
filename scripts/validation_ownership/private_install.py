"""Closed private regular-install configuration and outcome protocol."""

from __future__ import annotations

import hashlib
import json
import posixpath
import stat
from dataclasses import dataclass
from pathlib import Path


PREFIX = "private-install:"


class InstallError(ValueError):
    pass


@dataclass(frozen=True)
class InstallSpec:
    scope: str
    parents: tuple
    destinations: tuple[str, ...]


@dataclass(frozen=True)
class InstallRecord:
    sequence: int
    source: str
    destination: str
    result: int
    identity: tuple | None


def directory_identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def launch_scope(root):
    root = Path(root)
    return root.parent.name + "/" + root.name


def launch_binding(config):
    value = {name: config[name] for name in ("argv", "code", "sources", "enumerations", "environment")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def private_path(value, *, directory=False):
    if not isinstance(value, str):
        raise InstallError("private install path is not text")
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeEncodeError as error:
        raise InstallError("private install path is not strict UTF-8") from error
    if (
        not 0 < size <= 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or posixpath.normpath(value) != value
        or any(part in {".", "..", ""} for part in value.split("/")[1:])
        or not (value.startswith("/work/") or directory and value == "/work")
    ):
        raise InstallError("private install path is not canonical within /work")
    return value


def validate_config(value, config):
    if value is None:
        return None
    if (
        not isinstance(value, dict) or set(value) != {"version", "scope", "binding", "parents", "destinations"}
        or type(value["version"]) is not int or value["version"] != 1
        or config["mode"] != "command" or config.get("metadata_validation") or config.get("dependency")
        or value["scope"] != launch_scope(config["root"]) or value["binding"] != launch_binding(config)
        or not isinstance(value["parents"], list) or not isinstance(value["destinations"], list)
        or not 1 <= len(value["parents"]) <= config["observation_count"]
        or not 1 <= len(value["destinations"]) <= config["creation_limit"]
    ):
        raise InstallError("malformed or unbound private install configuration")
    parents = []
    for row in value["parents"]:
        if (
            not isinstance(row, list) or len(row) != 4
            or any(type(item) is not int or not 0 <= item < 1 << 64 for item in row[1:])
            or not row[2] or not stat.S_ISDIR(row[3]) or row[3] & 0o7000 or row[3] & 0o700 != 0o700
        ):
            raise InstallError("invalid private install parent identity")
        parents.append((private_path(row[0], directory=True), tuple(row[1:])))
    names = [row[0] for row in parents]
    destinations = tuple(private_path(path) for path in value["destinations"])
    if (
        names != sorted(set(names)) or "/work" not in names
        or list(destinations) != sorted(set(destinations))
        or any(posixpath.dirname(path) not in names for path in (*names, *destinations) if path != "/work")
    ):
        raise InstallError("incomplete or ambiguous private install namespace")
    return InstallSpec(value["scope"], tuple(parents), destinations)


def validate_records(accessed, spec):
    def unique_object(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise InstallError("duplicate private install outcome field")
            result[name] = value
        return result

    records = []
    for value in accessed:
        if not value.startswith(PREFIX):
            continue
        if spec is None:
            raise InstallError("unrequested private install outcome")
        try:
            record = json.loads(value[len(PREFIX):], object_pairs_hook=unique_object)
        except (ValueError, UnicodeError) as error:
            raise InstallError("malformed private install outcome") from error
        if (
            not isinstance(record, dict)
            or set(record) != {"version", "scope", "sequence", "source", "destination", "result", "identity"}
            or type(record["version"]) is not int or record["version"] != 1 or record["scope"] != spec.scope
            or type(record["sequence"]) is not int or not 1 <= record["sequence"] <= len(spec.destinations)
            or type(record["result"]) is not int or not -(1 << 63) <= record["result"] <= 0
        ):
            raise InstallError("unbound private install outcome")
        source, destination = private_path(record["source"]), private_path(record["destination"])
        if (
            source == destination or destination not in spec.destinations
            or posixpath.dirname(source) != posixpath.dirname(destination)
        ):
            raise InstallError("private install outcome escaped its destination")
        identity = record["identity"]
        if record["result"] == 0:
            if (
                not isinstance(identity, list) or len(identity) != 7
                or any(type(item) is not int for item in identity)
                or any(not 0 <= identity[index] < 1 << 64 for index in (0, 1, 2, 3, 6))
                or any(not -(1 << 63) <= identity[index] < 1 << 63 for index in (4, 5))
                or not identity[1] or not stat.S_ISREG(identity[2]) or identity[2] & 0o7000 or identity[6] != 1
            ):
                raise InstallError("invalid installed regular-file identity")
            identity = tuple(identity)
        elif identity is not None:
            raise InstallError("failed private install claims an identity")
        records.append(InstallRecord(record["sequence"], source, destination, record["result"], identity))
    records.sort(key=lambda item: item.sequence)
    if spec is not None and (
        [item.sequence for item in records] != list(range(1, len(spec.destinations) + 1))
        or {item.destination for item in records} != set(spec.destinations)
        or any(item.result != 0 for item in records)
    ):
        raise InstallError("incomplete or failed private install outcomes")
    return tuple(records)
