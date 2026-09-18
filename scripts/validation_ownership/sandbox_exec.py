#!/usr/bin/env python3
"""Trusted namespace setup; candidate processes never import this program."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import stat
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


def _enable_toolchain_null(root):
    flags = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
    source_path, target_path = Path("/dev/null"), root / "dev/null"

    def state(descriptor):
        info = os.fstat(descriptor)
        if not stat.S_ISCHR(info.st_mode) or info.st_rdev != os.makedev(1, 3):
            raise RuntimeError("toolchain null output is not the actual null device")
        with open(f"/proc/self/fdinfo/{descriptor}", "rb") as stream:
            data = stream.read(4097)
        ids = [line.partition(b":")[2].strip() for line in data.splitlines() if line.startswith(b"mnt_id:")]
        if len(data) > 4096 or len(ids) != 1 or not ids[0].isdigit() or not 0 < int(ids[0]) < 1 << 64:
            raise RuntimeError("toolchain null output lacks a bounded mount identity")
        identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_rdev)
        return identity, int(ids[0]), os.fstatvfs(descriptor).f_flag

    def visible(path, expected):
        descriptor = os.open(path, flags)
        try:
            if state(descriptor) != expected:
                raise RuntimeError("toolchain null mount or object was substituted")
        finally:
            os.close(descriptor)

    source = os.open(source_path, flags)
    try:
        target = os.open(target_path, flags)
        try:
            original_source, before = state(source), state(target)
            if original_source[0] != before[0] or original_source[2] & os.ST_NODEV:
                raise RuntimeError("toolchain null output differs from its unblocked source device")
            required = os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC
            if before[2] & required != required:
                raise RuntimeError("toolchain null mount lacks its required restrictions")
            visible(source_path, original_source)
            visible(target_path, before)
            attributes = MountAttributes(attr_clr=MS_NODEV)
            libc = ctypes.CDLL(None, use_errno=True)
            # Only remove the local device restriction; inherited readonly stays intact.
            if libc.syscall(
                ctypes.c_long(SYS_MOUNT_SETATTR), ctypes.c_int(target), ctypes.c_char_p(b""),
                ctypes.c_uint(AT_EMPTY_PATH), ctypes.byref(attributes), ctypes.c_size_t(ctypes.sizeof(attributes)),
            ):
                error = ctypes.get_errno()
                raise OSError(error, "cannot enable the exact confined toolchain null device", str(target_path))
            expected = before[0], before[1], before[2] & ~os.ST_NODEV
            if state(target) != expected or state(source) != original_source:
                raise RuntimeError("toolchain null transition changed unrelated mount or device state")
            visible(source_path, original_source)
            visible(target_path, expected)
        finally:
            os.close(target)
    finally:
        os.close(source)


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
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from toolchain_runtime import validate_launch
    toolchain = validate_launch(config)
    root = Path(config["root"])
    # Seal inherited submounts before installing deliberate child exceptions.
    bind(root, root, executable=True)
    # A private proc mount belongs to the supervisor, not the candidate chroot.
    mount("proc", "/proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, "proc")
    for item in config["mounts"]:
        bind(item["source"], root / item["target"].lstrip("/"),
             writable=item["writable"], executable=item["executable"])
    if toolchain is not None and toolchain["stage"] >= 3:
        expected = {"source": "/dev/null", "target": "/dev/null", "writable": True, "executable": False}
        if [item for item in config["mounts"] if item["target"] == "/dev/null"] != [expected]:
            raise RuntimeError("toolchain null output lost its exact device mount")
        _enable_toolchain_null(root)
    from syscall_guard import supervise
    return supervise(config, lambda: drop_privileges(config))


if __name__ == "__main__":
    raise SystemExit(main())
