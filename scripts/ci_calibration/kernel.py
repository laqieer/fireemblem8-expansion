"""Small Linux observations shared by the diagnostic supervisor and worker."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import signal
import stat
import struct
import sys

if __package__:
    from .policy import GuardError
else:
    from policy import GuardError


CGROUP2_MAGIC = 0x63677270
PROC_MAGIC = 0x9FA0
PROC_MOUNT_FLAGS = 2 | 4 | 8
LIBC = ctypes.CDLL(None, use_errno=True)


def read(path, maximum=65536):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise GuardError(f"nonregular control observation: {path}")
        data = bytearray()
        while True:
            chunk = os.read(descriptor, min(65536, maximum - len(data) + 1))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > maximum:
                raise GuardError(f"control observation exceeds bound: {path}")
    finally:
        os.close(descriptor)


def number(path, *, maximum=False):
    value = read(path, 128).strip()
    if maximum and value == b"max":
        return None
    if not value.isdigit():
        raise GuardError(f"invalid numeric kernel observation: {path}")
    return int(value)


def fields(path):
    result = {}
    for line in read(path).decode("ascii").splitlines():
        key, value = line.split()
        if key in result or not value.isdigit():
            raise GuardError(f"invalid kernel counter: {path}")
        result[key] = int(value)
    return result


def status(pid="self"):
    return {
        key: value.strip()
        for key, value in (line.split(":", 1) for line in read(f"/proc/{pid}/status").decode().splitlines())
    }


def namespaces(pid="self"):
    return {
        name: os.stat(f"/proc/{pid}/ns/{name}").st_ino
        for name in ("mnt", "pid", "net", "ipc", "uts", "user")
    }


def membership(pid="self"):
    rows = read(f"/proc/{pid}/cgroup").decode().splitlines()
    if len(rows) != 1 or not rows[0].startswith("0::/"):
        raise GuardError("unified cgroup-v2 membership is required")
    path = rows[0][3:]
    if any(part in {".", ".."} for part in path.split("/")):
        raise GuardError("invalid cgroup membership path")
    return path


def filesystem_type(path):
    buffer = ctypes.create_string_buffer(256)
    if LIBC.statfs(os.fsencode(path), ctypes.byref(buffer)):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))
    return struct.unpack_from("l", buffer)[0]


def prctl(option, value):
    if LIBC.prctl(option, value, 0, 0, 0):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def parent_death_signal():
    value = ctypes.c_int()
    prctl(2, ctypes.byref(value))
    return value.value


def drop_identity(uid, gid):
    if uid <= 0 or gid <= 0:
        raise GuardError("worker must use a dedicated non-root host identity")
    for capability in range(64):
        if LIBC.prctl(24, capability, 0, 0, 0) and ctypes.get_errno() != errno.EINVAL:
            raise OSError(ctypes.get_errno(), "cannot drop capability bounding set")
    prctl(47, 4)  # PR_CAP_AMBIENT_CLEAR_ALL.
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)

    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [
            ("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        ]

    header, data = Header(0x20080522, 0), (Data * 2)()
    if LIBC.capset(ctypes.byref(header), ctypes.byref(data)):
        raise OSError(ctypes.get_errno(), "cannot clear host capabilities")
    prctl(38, 1)
    prctl(1, signal.SIGKILL)


def mount_tmpfs(path, *, size, flags):
    if LIBC.mount(
        b"tmpfs", os.fsencode(path), b"tmpfs", flags,
        f"size={size},mode=0755".encode(),
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))


def mount_private_proc(path):
    if LIBC.mount(b"proc", os.fsencode(path), b"proc", PROC_MOUNT_FLAGS, None):
        error = ctypes.get_errno()
        raise OSError(error, "cannot mount the private read-write process view", str(path))


def pivot_root(path):
    os.chdir(path)
    if LIBC.syscall(ctypes.c_long(155), ctypes.c_char_p(b"."), ctypes.c_char_p(b".")):
        error = ctypes.get_errno()
        raise OSError(error, "cannot pivot the private mount namespace root")
    if LIBC.umount2(b".", 2):
        error = ctypes.get_errno()
        raise OSError(error, "cannot detach the old host root during initial isolation")
    os.chdir("/")


def owned_config(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222:
        raise GuardError("diagnostic config must be an immutable root-owned regular file")
    if info.st_size > 65536:
        raise GuardError("diagnostic config exceeds its bound")
    if __package__:
        from .policy import parse_json
    else:
        from policy import parse_json
    return parse_json(read(path))


def config_identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns]


def read_config_fd(descriptor):
    result = bytearray()
    while True:
        data = os.pread(descriptor, 65537 - len(result), len(result))
        if not data:
            return bytes(result)
        result.extend(data)
        if len(result) > 65536:
            raise GuardError("config descriptor exceeds its original byte bound")


def open_owned_config(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_mode & 0o222 or before.st_size > 65536:
            raise GuardError("config descriptor is not an immutable root-owned regular file")
        signature = config_identity(before)
        data = read_config_fd(descriptor)
        if config_identity(os.fstat(descriptor)) != signature or config_identity(os.lstat(path)) != signature:
            raise GuardError("root-owned config changed while opening its descriptor")
        receipt = {"identity": signature, "sha256": hashlib.sha256(data).hexdigest(),
                   "parent_user_namespace": namespaces()["user"]}
        return descriptor, receipt
    except BaseException:
        os.close(descriptor)
        raise


def mapped_owner(owner, mapping, overflow_path):
    rows = [tuple(map(int, row.split())) for row in read(mapping).decode().splitlines()]
    if len(rows) != 1 or len(rows[0]) != 3 or rows[0][0] != 0 or rows[0][2] != 1:
        raise GuardError("config handoff requires the original one-ID user namespace mapping")
    inner, outer, length = rows[0]
    return inner + owner - outer if outer <= owner < outer + length else number(overflow_path)


def inherited_config(descriptor, receipt, path="/guard/config.json"):
    if (
        type(descriptor) is not int or descriptor < 3 or not isinstance(receipt, dict)
        or set(receipt) != {"identity", "sha256", "parent_user_namespace"}
        or not isinstance(receipt["identity"], list) or len(receipt["identity"]) != 9
        or any(type(value) is not int for value in receipt["identity"])
        or receipt["identity"][3] != 0
        or not isinstance(receipt["sha256"], str) or len(receipt["sha256"]) != 64
        or type(receipt["parent_user_namespace"]) is not int
        or namespaces()["user"] == receipt["parent_user_namespace"]
    ):
        raise GuardError("invalid parent-validated config descriptor receipt")
    if fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
        raise GuardError("inherited config descriptor is writable")
    expected = list(receipt["identity"])
    expected[3] = mapped_owner(expected[3], "/proc/self/uid_map", "/proc/sys/kernel/overflowuid")
    expected[4] = mapped_owner(expected[4], "/proc/self/gid_map", "/proc/sys/kernel/overflowgid")
    before = config_identity(os.fstat(descriptor))
    if (
        before != expected or config_identity(os.lstat(path)) != expected
        or not stat.S_ISREG(before[2]) or before[2] & 0o222
        or not os.statvfs(path).f_flag & os.ST_RDONLY
    ):
        raise GuardError("inherited config is not the same readonly parent-owned object")
    data = read_config_fd(descriptor)
    if hashlib.sha256(data).hexdigest() != receipt["sha256"] or (
        config_identity(os.fstat(descriptor)) != before or config_identity(os.lstat(path)) != before
    ):
        raise GuardError("inherited config content or identity changed")
    if __package__:
        from .policy import parse_json
    else:
        from policy import parse_json
    return parse_json(data), {
        "parent_identity": receipt["identity"], "visible_identity": before,
        "sha256": receipt["sha256"], "parent_user_namespace": receipt["parent_user_namespace"],
        "user_namespace": namespaces()["user"],
    }


def emit(scope, kind, data):
    if __package__:
        from .policy import encoded
    else:
        from policy import encoded
    payload = encoded({"scope": scope, "kind": kind, "data": data}) + b"\n"
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
