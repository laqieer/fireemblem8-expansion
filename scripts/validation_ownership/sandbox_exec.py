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
AT_EMPTY_PATH, AT_RECURSIVE = 0x1000, 0x8000
SYS_MOUNT_SETATTR = 442  # Linux x86-64; available since Linux 5.12.


class MountAttributes(ctypes.Structure):
    _fields_ = [
        ("attr_set", ctypes.c_uint64), ("attr_clr", ctypes.c_uint64),
        ("propagation", ctypes.c_uint64), ("userns_fd", ctypes.c_uint64),
    ]


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
    flags = MS_NOSUID | MS_NODEV
    if not writable:
        flags |= MS_RDONLY
    if not executable:
        flags |= MS_NOEXEC
    recursive_attributes(target, flags)


def recursive_attributes(target, flags):
    # A top-level MS_REMOUNT does not restrict copied submounts. Pin this
    # namespace's bind and add restrictions atomically to the entire subtree.
    descriptor = os.open(target, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        attributes = MountAttributes(attr_set=flags)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        if libc.syscall(
            ctypes.c_long(SYS_MOUNT_SETATTR), ctypes.c_int(descriptor), ctypes.c_char_p(b""),
            ctypes.c_uint(AT_EMPTY_PATH | AT_RECURSIVE), ctypes.byref(attributes),
            ctypes.c_size_t(ctypes.sizeof(attributes)),
        ):
            error = ctypes.get_errno()
            raise OSError(
                error, f"recursive mount attributes require Linux 5.12+ and namespace authority: {os.strerror(error)}",
                str(target),
            )
    finally:
        os.close(descriptor)


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
    if not sys.flags.isolated or not sys.flags.no_site or len(sys.argv) != 2:
        raise SystemExit("sandbox launcher requires Python -I -S and trusted config")
    config = json.loads(Path(sys.argv[1]).read_bytes())
    root = Path(config["root"])
    # Seal inherited submounts before installing deliberate child exceptions.
    bind(root, root, executable=True)
    # A private proc mount belongs to the supervisor, not the candidate chroot.
    mount("proc", "/proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, "proc")
    for item in config["mounts"]:
        bind(item["source"], root / item["target"].lstrip("/"),
             writable=item["writable"], executable=item["executable"])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from syscall_guard import supervise
    return supervise(config, lambda: drop_privileges(config))


if __name__ == "__main__":
    raise SystemExit(main())
