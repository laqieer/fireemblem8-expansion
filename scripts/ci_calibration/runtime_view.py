"""Actual system runtime aliases shared by the host and readonly private view."""

from collections import deque
import os
from pathlib import Path
import stat

if __package__:
    from . import policy
else:
    import policy


ROOT_ALIASES = ("bin", "sbin", "lib", "lib64")
PROGRAMS = tuple(sorted({
    "/bin/sh", "/bin/bash", "/bin/env", "/bin/mkdir", "/bin/arm-none-eabi-gcc",
    *("/usr/bin/" + name for name in (
        "python3", "unshare", "make", "git", "cc", "c++", "gcc", "g++", "cpp",
        "as", "ld", "ar", "nm", "ranlib", "readelf", "objcopy", "objdump", "strip",
        "ldd", "find", "iconv", "mkdir", "mv", "printf", "rm", "sed", "uname",
        "true", "echo", "env", "arm-none-eabi-as", "arm-none-eabi-gcc",
        "arm-none-eabi-ar", "arm-none-eabi-ld", "arm-none-eabi-nm",
        "arm-none-eabi-objcopy", "arm-none-eabi-objdump", "arm-none-eabi-readelf",
    )),
}))


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns]


def observe(paths=PROGRAMS):
    programs, parents = {}, {}
    for requested in paths:
        pending = deque(Path(requested).parts[1:])
        resolved, links = [], []
        while pending:
            part = pending.popleft()
            if part in {"", "."}:
                continue
            if part == "..":
                if not resolved:
                    raise policy.GuardError("runtime alias escapes its system root")
                resolved.pop()
                continue
            parent = "/" + "/".join(resolved)
            parents[parent] = identity(os.lstat(parent))
            path = str(Path(parent) / part)
            if not (
                path == "/usr" or path.startswith("/usr/") or path in {"/" + name for name in ROOT_ALIASES}
                or path in {"/etc", "/etc/alternatives"} or path.startswith("/etc/alternatives/")
            ):
                raise policy.GuardError("runtime alias leaves the readonly system closure")
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                if len(links) >= 40:
                    raise policy.GuardError("runtime alias chain exceeds the kernel link bound")
                target = os.readlink(path)
                links.append({"path": path, "target": target, "identity": identity(info)})
                target_parts = Path(target).parts
                if target.startswith("/"):
                    resolved = []
                    target_parts = target_parts[1:]
                pending.extendleft(reversed(target_parts))
            else:
                resolved.append(part)
        actual = "/" + "/".join(resolved)
        programs[requested] = {"resolved": actual, "links": links, "identity": identity(os.stat(actual))}
    root_aliases = {
        name: {"target": os.readlink("/" + name), "identity": identity(os.lstat("/" + name))}
        for name in ROOT_ALIASES
    }
    return {"programs": programs, "parents": parents, "root_aliases": root_aliases}


def validate(value):
    for info in value["parents"].values():
        if info[3] != 0 or not stat.S_ISDIR(info[2]) or info[2] & 0o022:
            raise policy.GuardError("runtime alias parent is not immutable host-root-owned")
    for record in value["programs"].values():
        info = record["identity"]
        if info[3] != 0 or not stat.S_ISREG(info[2]) or info[2] & 0o6022 or not info[2] & 0o111:
            raise policy.GuardError("runtime executable ownership/mode is not preserved")
        for link in record["links"]:
            if link["identity"][3] != 0 or not stat.S_ISLNK(link["identity"][2]):
                raise policy.GuardError("runtime alias is not host-root-owned")
    for link in value["root_aliases"].values():
        if link["identity"][3] != 0 or not stat.S_ISLNK(link["identity"][2]):
            raise policy.GuardError("merged-root runtime alias is not host-root-owned")


def compare(expected, actual):
    if set(expected["programs"]) != set(actual["programs"]) or set(expected["parents"]) != set(actual["parents"]):
        raise policy.GuardError("runtime alias closure membership changed")
    for name, original in expected["programs"].items():
        current = actual["programs"][name]
        if original["resolved"] != current["resolved"] or original["identity"] != current["identity"]:
            raise policy.GuardError("runtime executable differs from its actual host object")
        if len(original["links"]) != len(current["links"]):
            raise policy.GuardError("runtime alias chain changed")
        for before, after in zip(original["links"], current["links"]):
            if (before["path"], before["target"]) != (after["path"], after["target"]):
                raise policy.GuardError("runtime alias target changed")
            keys = slice(2, 5) if before["path"] in {"/" + item for item in ROOT_ALIASES} else slice(None)
            if before["identity"][keys] != after["identity"][keys]:
                raise policy.GuardError("runtime alias identity/ownership changed")
    for name, before in expected["root_aliases"].items():
        after = actual["root_aliases"][name]
        if before["target"] != after["target"] or before["identity"][2:5] != after["identity"][2:5]:
            raise policy.GuardError("merged-root alias no longer reproduces the actual system target")
    for path, before in expected["parents"].items():
        after = actual["parents"][path]
        keys = slice(2, 5) if path in {"/", "/etc"} else slice(None)
        if before[keys] != after[keys]:
            raise policy.GuardError("runtime directory identity/ownership changed")
