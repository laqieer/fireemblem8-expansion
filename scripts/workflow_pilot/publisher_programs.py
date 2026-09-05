"""Canonical isolated publisher programs; no plugins or caller-supplied code.

The trusted wrapper installs this exact-tree file on its read-only control
mount. Each CLI mode has one fixed command signature in publisher_inventory.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys


MAX_MOUNT_BYTES = 1048576
MAX_FILESYSTEMS = 512
MAX_MEMBERSHIP_BYTES = 1024
MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/cgroup.procs"
DEV_MOUNTS_ARGV = (
    "/usr/bin/findmnt", "--json", "--submounts", "--output", "TARGET", "/dev",
)
WRITABLE_MOUNTS_ARGV = (
    "/usr/bin/findmnt", "--json", "--list", "--uniq",
    "--output", "TARGET,OPTIONS", "-R", "/",
)
_PID = re.compile(rb"[1-9][0-9]*")


class ProgramError(ValueError):
    """An input failed the fixed publisher program's closed contract."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ProgramError("duplicate findmnt JSON key")
        result[key] = value
    return result


def _mount_payload(argv: tuple[str, ...], *, writable: bool = False) -> dict:
    completed = subprocess.run(list(argv), check=False, capture_output=True)
    label = "findmnt writable mount audit" if writable else "findmnt"
    if completed.returncode != 0:
        raise ProgramError(f"{label} failed")
    if completed.stderr:
        raise ProgramError(f"{label} wrote stderr")
    if len(completed.stdout) > MAX_MOUNT_BYTES:
        raise ProgramError("findmnt JSON exceeds bounds")
    try:
        payload = json.loads(
            completed.stdout.decode("utf-8"), object_pairs_hook=_unique_object,
        )
    except ProgramError as error:
        if writable:
            raise ProgramError("duplicate writable mount JSON key") from error
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ProgramError(
            "invalid writable mount audit JSON" if writable else "invalid findmnt JSON"
        ) from error
    if not isinstance(payload, dict) or set(payload) != {"filesystems"}:
        raise ProgramError("unexpected findmnt root keys")
    return payload


def dev_mount_targets() -> bytes:
    filesystems = _mount_payload(DEV_MOUNTS_ARGV)["filesystems"]
    if not isinstance(filesystems, list) or len(filesystems) != 1:
        raise ProgramError("unexpected findmnt filesystem count")
    seen: set[str] = set()
    targets: list[str] = []

    def visit(node: object) -> None:
        if not isinstance(node, dict) or set(node) - {"children", "target"}:
            raise ProgramError("unexpected findmnt node")
        target = node.get("target")
        if not isinstance(target, str) or not target:
            raise ProgramError("findmnt target is invalid")
        if "\0" in target:
            raise ProgramError("findmnt target contains NUL")
        if target != "/dev" and not target.startswith("/dev/"):
            raise ProgramError("findmnt target escapes /dev")
        if target in seen:
            raise ProgramError("findmnt target repeats")
        seen.add(target)
        targets.append(target)
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ProgramError("findmnt children must be a list")
        for child in children:
            visit(child)

    try:
        visit(filesystems[0])
    except RecursionError as error:
        raise ProgramError("findmnt tree exceeds bounds") from error
    if not targets or targets[0] != "/dev":
        raise ProgramError("findmnt root target is not /dev")
    return b"".join(target.encode("utf-8") + b"\0" for target in targets)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProgramError(f"findmnt {field} is invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ProgramError(f"findmnt {field} contains control character")
    return value


def writable_mount_records() -> bytes:
    filesystems = _mount_payload(WRITABLE_MOUNTS_ARGV, writable=True)["filesystems"]
    if (
        not isinstance(filesystems, list)
        or not filesystems
    ):
        raise ProgramError("unexpected writable mount audit mount count")
    if len(filesystems) > MAX_FILESYSTEMS:
        raise ProgramError("writable mount audit mount count exceeds bounds")
    seen: set[str] = set()
    records: list[tuple[str, str]] = []
    for filesystem in filesystems:
        if not isinstance(filesystem, dict) or set(filesystem) - {"options", "target"}:
            raise ProgramError("unexpected writable mount audit row keys")
        target = _text(filesystem.get("target"), "target")
        if not target.startswith("/"):
            raise ProgramError("findmnt target is not absolute")
        if target in seen:
            raise ProgramError("findmnt target repeats after --uniq")
        seen.add(target)
        options = _text(filesystem.get("options"), "options")
        tokens = options.split(",")
        if any(
            not token
            or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in token)
            for token in tokens
        ):
            raise ProgramError("findmnt option tokens are invalid")
        records.append((target, options))
    return b"".join(
        item.encode("utf-8") + b"\0"
        for record in records
        for item in record
    )


def validate_membership_snapshot(data: bytes, wrapper: int, checker: int) -> None:
    if (
        type(wrapper) is not int
        or type(checker) is not int
        or wrapper <= 0
        or checker <= 0
        or wrapper == checker
        or len(data) > MAX_MEMBERSHIP_BYTES
        or not data.endswith(b"\n")
    ):
        raise ProgramError("invalid membership snapshot")
    records = data[:-1].split(b"\n")
    if len(records) != 2 or any(_PID.fullmatch(record) is None for record in records):
        raise ProgramError("invalid membership records")
    try:
        members = {int(record) for record in records}
    except ValueError as error:
        raise ProgramError("invalid membership PID") from error
    if len(members) != 2 or members != {wrapper, checker}:
        raise ProgramError("membership differs from wrapper and checker")


def membership(wrapper: str) -> None:
    try:
        encoded = wrapper.encode("ascii")
    except UnicodeEncodeError as error:
        raise ProgramError("invalid wrapper PID") from error
    if len(encoded) > 20 or _PID.fullmatch(encoded) is None:
        raise ProgramError("invalid wrapper PID")
    with open(MEMBERSHIP_PATH, "rb") as handle:
        data = handle.read(MAX_MEMBERSHIP_BYTES + 1)
    validate_membership_snapshot(data, int(wrapper), os.getpid())


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    try:
        if arguments == ["dev-mount-targets"]:
            sys.stdout.buffer.write(dev_mount_targets())
        elif arguments == ["writable-mount-records"]:
            sys.stdout.buffer.write(writable_mount_records())
        elif len(arguments) == 2 and arguments[0] == "membership":
            membership(arguments[1])
        else:
            raise ProgramError("unregistered publisher program invocation")
    except (ProgramError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 125
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
