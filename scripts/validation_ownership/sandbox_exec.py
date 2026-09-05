#!/usr/bin/env python3
"""Trusted namespace setup; candidate processes never import this program."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import sys
from pathlib import Path


MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_REC = 32, 4096, 16384


def mount(source, target, flags, kind=None):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.mount(
        None if source is None else os.fsencode(source),
        os.fsencode(target), None if kind is None else os.fsencode(kind),
        flags, None,
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def bind(source, target, *, writable=False, executable=False):
    mount(source, target, MS_BIND | MS_REC)
    flags = MS_BIND | MS_REMOUNT | MS_NOSUID | MS_NODEV
    if not writable:
        flags |= MS_RDONLY
    if not executable:
        flags |= MS_NOEXEC
    mount(None, target, flags)


def drop_privileges(config):
    libc = ctypes.CDLL(None, use_errno=True)
    # UID 0 in a private user namespace is still stripped of all capabilities.
    for capability in range(64):
        if libc.prctl(24, capability, 0, 0, 0) and ctypes.get_errno() != errno.EINVAL:
            raise OSError(ctypes.get_errno(), "cannot drop capability bounding set")
    if config["sudo_drop"]:
        if config["runner_uid"] <= 0 or config["runner_gid"] <= 0:
            raise RuntimeError("privileged launcher requires a non-root runner identity")
        os.setgroups([])
        os.setgid(config["runner_gid"])
        os.setuid(config["runner_uid"])
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [
            ("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        ]
    header = Header(0x20080522, 0)
    data = (Data * 2)()
    if libc.capset(ctypes.byref(header), ctypes.byref(data)):
        raise OSError(ctypes.get_errno(), "cannot clear capabilities")
    if libc.prctl(38, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "cannot set no_new_privs")


def main():
    if not sys.flags.isolated or len(sys.argv) != 2:
        raise SystemExit("sandbox launcher requires isolated Python and trusted config")
    config = json.loads(Path(sys.argv[1]).read_bytes())
    root = Path(config["root"])
    mount(root, root, MS_BIND | MS_REC)
    # A private proc mount belongs to the supervisor, not the candidate chroot.
    mount("proc", "/proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, "proc")
    for item in config["mounts"]:
        bind(item["source"], root / item["target"].lstrip("/"),
             writable=item["writable"], executable=item["executable"])
    mount(None, root, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from syscall_guard import supervise
    return supervise(config, lambda: drop_privileges(config))


if __name__ == "__main__":
    raise SystemExit(main())
