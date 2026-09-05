#!/usr/bin/env python3
"""Enter a closed mount namespace and fexecve one trusted executable."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import stat
import sys
from pathlib import Path


MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_BIND = 4096
MS_REC = 16384
MS_REMOUNT = 32
MNT_DETACH = 2
PR_SET_NO_NEW_PRIVS = 38
PR_SET_KEEPCAPS = 8
PR_CAPBSET_DROP = 24
LINUX_CAPABILITY_VERSION_3 = 0x20080522


class _CapabilityHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapabilityData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def _mount(source: str | None, target: str, flags: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.mount(
        None if source is None else os.fsencode(source),
        os.fsencode(target),
        None,
        flags,
        None,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _bind(source: Path, target: Path, *, read_only: bool, noexec: bool) -> None:
    _mount(str(source), str(target), MS_BIND | MS_REC)
    flags = MS_BIND | MS_REMOUNT | MS_NOSUID | MS_NODEV
    if read_only:
        flags |= MS_RDONLY
    if noexec:
        flags |= MS_NOEXEC
    _mount(None, str(target), flags)


def _unmount(target: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.umount2(os.fsencode(target), MNT_DETACH) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _drop_capabilities(*, uid: int | None, gid: int | None) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_KEEPCAPS, 0, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_SET_KEEPCAPS)")
    for capability in range(64):
        if libc.prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            if error not in {errno.EINVAL, errno.EPERM}:
                raise OSError(
                    error,
                    os.strerror(error),
                    "prctl(PR_CAPBSET_DROP)",
                )
    if (uid is None) != (gid is None):
        raise RuntimeError("sandbox identity drop must provide uid and gid")
    if uid is not None:
        if uid <= 0 or gid is None or gid <= 0:
            raise RuntimeError("sudo sandbox runner identity is invalid")
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    header = _CapabilityHeader(LINUX_CAPABILITY_VERSION_3, 0)
    data = (_CapabilityData * 2)()
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "capset")
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_SET_NO_NEW_PRIVS)")


def _fexecve(descriptor: int, argv: list[str], environment: dict[str, str]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    encoded_argv = [os.fsencode(value) for value in argv]
    encoded_environment = [
        os.fsencode(f"{name}={value}") for name, value in environment.items()
    ]
    argv_array = (ctypes.c_char_p * (len(encoded_argv) + 1))(
        *encoded_argv,
        None,
    )
    environment_array = (ctypes.c_char_p * (len(encoded_environment) + 1))(
        *encoded_environment,
        None,
    )
    libc.fexecve(descriptor, argv_array, environment_array)
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), "fexecve")


def _set_resource_limits() -> None:
    limits = (
        (resource.RLIMIT_AS, 2 * 1024 * 1024 * 1024),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 128),
        (resource.RLIMIT_NPROC, 1024),
    )
    for kind, value in limits:
        soft, hard = resource.getrlimit(kind)
        selected = min(value, hard) if hard != resource.RLIM_INFINITY else value
        if soft == resource.RLIM_INFINITY or soft > selected:
            resource.setrlimit(kind, (selected, selected))


def main() -> int:
    if not sys.flags.isolated or len(sys.argv) != 2:
        raise SystemExit("sandbox_exec requires isolated Python and one config")
    config_path = Path(sys.argv[1]).resolve(strict=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    expected = {
        "argv",
        "cwd",
        "environment",
        "executable",
        "read_only",
        "root",
        "runner_gid",
        "runner_uid",
        "sudo_drop",
        "writable",
    }
    if set(config) != expected:
        raise SystemExit("sandbox_exec config has unexpected fields")
    if (
        not isinstance(config["argv"], list)
        or not config["argv"]
        or not all(isinstance(item, str) for item in config["argv"])
        or not isinstance(config["environment"], dict)
        or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in config["environment"].items()
        )
    ):
        raise SystemExit("sandbox_exec config has invalid process fields")

    root = Path(config["root"]).resolve(strict=True)
    executable = root / config["executable"].lstrip("/")
    _mount(str(root), str(root), MS_BIND | MS_REC)
    for item in config["read_only"]:
        if set(item) != {"noexec", "source", "target"}:
            raise SystemExit("sandbox read-only mount has unexpected fields")
        source = Path(item["source"]).resolve(strict=True)
        target = root / item["target"].lstrip("/")
        _bind(source, target, read_only=True, noexec=bool(item["noexec"]))
    for item in config["writable"]:
        if set(item) != {"source", "target"}:
            raise SystemExit("sandbox writable mount has unexpected fields")
        source = Path(item["source"]).resolve(strict=True)
        target = root / item["target"].lstrip("/")
        _bind(source, target, read_only=False, noexec=True)

    descriptor = os.open(
        executable,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW,
    )
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & 0o111:
        raise SystemExit("sandbox executable is not a regular executable")
    _unmount(executable)
    _mount(
        None,
        str(root),
        MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV,
    )
    os.chroot(root)
    os.chdir(config["cwd"])
    _set_resource_limits()
    _drop_capabilities(
        uid=config["runner_uid"] if config["sudo_drop"] else None,
        gid=config["runner_gid"] if config["sudo_drop"] else None,
    )
    _fexecve(descriptor, config["argv"], config["environment"])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
