"""Fixed, test-only null-mount fixture; never a command or namespace service."""

from __future__ import annotations

import ast
import ctypes
import errno
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import select
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[3]
PROGRAM = SOURCE / "scripts/validation_ownership/tests/null_mount_fixture.py"
WORKSPACE = SOURCE / "build/test-artifacts/ownership-foundation-tests"
MODES = ("readonly", "writable", "wrong-device", "substituted", "unsupported", "locked", "old")
ORDINARY_PREFIX = (
    "/usr/bin/unshare", "--user", "--map-current-user", "--keep-caps", "--mount",
    "--fork", "--kill-child", "--propagation", "private",
)
AVAILABILITY = (*ORDINARY_PREFIX, "/usr/bin/true")
PERMISSION_UNAVAILABLE = (
    b"unshare: write failed /proc/self/uid_map: Operation not permitted\n",
    b"unshare: unshare failed: Operation not permitted\n",
    b"unshare: unshare failed: Permission denied\n",
)
WATCHDOG_SECONDS, WAIT_SECONDS, OUTER_SECONDS = 30, 35, 45
FIXTURE_BYTES, CAPTURE_BYTES, FRAME_BYTES = 1048576, 262144, 4096
RECORD_BYTES = 16384
FULL_MAP = ((0, 0, 4294967295),)
ENVIRONMENT = {
    "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1",
}
NEWUSER, NEWNS = 0x10000000, 0x20000
NS_GET_USERNS, NS_GET_PARENT, NS_GET_NSTYPE, NS_GET_OWNER_UID = 0xB701, 0xB702, 0xB703, 0xB704
CAP_KILL, CAP_SETGID, CAP_SETUID, CAP_SETPCAP, CAP_SYS_ADMIN = 5, 6, 7, 8, 21
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_BIND, MS_REC = 4096, 16384
OLD_REMOUNT = 0x102A
SELECTIVE_CALL = (442, 4096, 0, 4, 0, 0, 32)
TOKENS = (b"CREATOR_READY\n", b"CREATE\n", b"NS_CREATED\n", b"MAPS_COMPLETE\n", b"GO\n", b"W0_READY\n")
CAP_KEYS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
_cleanup_life = _cleanup_report = None


class Refusal(RuntimeError):
    """A failed fixture boundary, not permission to try a different backend."""


class FixtureFailure(AssertionError):
    def __init__(self, facts):
        self.facts = facts
        super().__init__("null-mount fixture failed: " + json.dumps(facts, sort_keys=True))


def require(condition, tag):
    if not condition:
        raise Refusal(tag)


def remaining(deadline):
    require(type(deadline) in (int, float) and math.isfinite(deadline), "deadline")
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("null fixture deadline")
    return value


def integer(value, low=0, high=(1 << 64) - 1):
    return type(value) is int and low <= value <= high


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


def close_temporary(descriptor, primary):
    def close():
        try:
            os.close(descriptor)
        except BaseException:
            if _cleanup_report is not None:
                _cleanup_report.unsure("cleanup")
            raise

    require(_cleanup_report is not None, "admitted temporary cleanup")
    _cleanup_life.finish_cleanup([("cleanup", close)], primary=primary, report=_cleanup_report)


def read_file(path, bound=4096, *, directory=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        value = os.read(descriptor, bound + 1)
        require(len(value) <= bound, "fixed file bound")
        return value
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def canonical(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve(strict=True), "canonical path")
    return path


def load_control():
    root = canonical(SOURCE / "scripts/validation_ownership")
    name = "_null_mount_fixture_control"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    budgeting = __import__(name + ".budget", fromlist=["budget"])
    life = __import__(name + ".lifecycle", fromlist=["lifecycle"])
    for leaf in ("budget", "lifecycle", "producer_channel"):
        require(Path(sys.modules[name + "." + leaf].__file__).resolve() == root / (leaf + ".py"),
                "fixed control code origin")
    require(budgeting._lifecycle is life and life._FRAME_BYTES == FRAME_BYTES, "existing custody API")
    return budgeting, life


def verify_environment():
    tree = ast.parse(read_file(SOURCE / "scripts/validation_ownership/authority.py", 131072))
    values = [
        node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ENVIRONMENT" for target in node.targets)
    ]
    require(len(values) == 1 and ast.literal_eval(values[0]) == ENVIRONMENT, "sealed environment")


def source_revision(deadline, expected=None):
    prefix = [
        "/usr/bin/git", "--no-pager", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false", "-C", str(SOURCE),
    ]
    environment = {**ENVIRONMENT, "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_COUNT": "0"}
    revision = None
    for arguments in (("rev-parse", "HEAD"), ("diff", "--quiet", "--no-ext-diff",
                                           "--ignore-submodules=none", "HEAD", "--")):
        result = subprocess.run(
            [*prefix, *arguments], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, timeout=min(5, remaining(deadline)), check=False,
        )
        require(result.returncode == 0 and len(result.stdout) <= RECORD_BYTES, "unchanged source tree")
        if arguments[0] == "rev-parse":
            require(re.fullmatch(b"[0-9a-f]{40}\n", result.stdout) is not None, "source revision")
            revision = result.stdout
    require(expected is None or revision == expected, "source revision changed")
    return revision


def libc():
    return ctypes.CDLL(None, use_errno=True)


def checked(result):
    if result < 0:
        raise OSError(ctypes.get_errno(), "fixed fixture syscall")
    return result


def prctl(option, value=0):
    return checked(libc().prctl(option, value, 0, 0, 0))


def parent_guard():
    prctl(1, signal.SIGKILL)
    require(os.getppid() == 1, "owned PID1 parent")


def death_signal():
    value = ctypes.c_int()
    checked(libc().prctl(2, ctypes.byref(value), 0, 0, 0))
    return value.value


def capset(mask):
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                    ("inheritable", ctypes.c_uint32)]

    header, data = Header(0x20080522, 0), (Data * 2)()
    for index in range(2):
        data[index].effective = data[index].permitted = (mask >> (index * 32)) & 0xFFFFFFFF
    checked(libc().capset(ctypes.byref(header), ctypes.byref(data)))


def bounding(keep):
    for capability in range(64):
        if keep & (1 << capability):
            continue
        result = libc().prctl(24, capability, 0, 0, 0)
        if result < 0 and ctypes.get_errno() != errno.EINVAL:
            checked(result)
    prctl(47, 4)


def parse_status(data):
    values = {}
    for row in data.decode("ascii").splitlines():
        key, separator, text = row.partition(":")
        if separator and key in {"Uid", "Gid", "Groups", "NoNewPrivs", "NSpid", "PPid", *CAP_KEYS}:
            require(key not in values, "status duplicate")
            values[key] = tuple(int(item, 16 if key in CAP_KEYS else 10) for item in text.split())
    require(values.keys() == {"Uid", "Gid", "Groups", "NoNewPrivs", "NSpid", "PPid", *CAP_KEYS},
            "status fields")
    require(len(values["Uid"]) == len(values["Gid"]) == 4
            and all(len(values[key]) == 1 for key in ("NoNewPrivs", "PPid", *CAP_KEYS)),
            "status shape")
    return values


def maps(data):
    rows = tuple(tuple(int(item) for item in row.split()) for row in data.splitlines())
    require(len(rows) <= 1 and all(len(row) == 3 and all(integer(n, 0, 0xFFFFFFFF) for n in row)
                                  for row in rows), "map shape")
    return rows


def fd_identity(descriptor):
    value = os.fstat(descriptor)
    return value.st_dev, value.st_ino


def namespace(name, *, directory=None):
    descriptor = os.open(("ns/" if directory is not None else "/proc/self/ns/") + name,
                         os.O_RDONLY | os.O_CLOEXEC, dir_fd=directory)
    try:
        return fd_identity(descriptor)
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def mount_id(descriptor):
    data = read_file("/proc/self/fdinfo/" + str(descriptor))
    ids = [row.split(b":", 1)[1].strip() for row in data.splitlines() if row.startswith(b"mnt_id:")]
    require(len(ids) == 1 and ids[0].isdigit() and int(ids[0]) > 0, "mount identity")
    return int(ids[0])


@dataclass(frozen=True, slots=True)
class DirectoryIdentity:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    mount: int

    def metadata(self):
        return self.device, self.inode, self.mode, self.uid, self.gid


def directory_metadata(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def directory_identity(descriptor):
    info = os.fstat(descriptor)
    require(stat.S_ISDIR(info.st_mode), "directory type")
    return DirectoryIdentity(*directory_metadata(info), mount_id(descriptor))


def tmpfs_state(descriptor):
    class Statfs(ctypes.Structure):
        _fields_ = [
            ("kind", ctypes.c_long), ("block_size", ctypes.c_long),
            ("blocks", ctypes.c_ulong), ("free", ctypes.c_ulong), ("available", ctypes.c_ulong),
            ("files", ctypes.c_ulong), ("files_free", ctypes.c_ulong),
            ("fsid", ctypes.c_int * 2), ("name_length", ctypes.c_long),
            ("fragment_size", ctypes.c_long), ("flags", ctypes.c_long), ("spare", ctypes.c_long * 4),
        ]

    require(ctypes.sizeof(ctypes.c_long) == 8 and ctypes.sizeof(Statfs) == 120, "native statfs ABI")
    value = Statfs()
    checked(libc().fstatfs(descriptor, ctypes.byref(value)))
    return value.kind, value.block_size * value.blocks, value.flags


def verify_tmpfs(descriptor, original, uid=0, gid=0):
    mounted = directory_identity(descriptor)
    kind, capacity, flags = tmpfs_state(descriptor)
    require(mounted.metadata()[:2] != original.metadata()[:2] and mounted.mount != original.mount
            and mounted.mode == stat.S_IFDIR | 0o700 and mounted.uid == uid and mounted.gid == gid
            and kind == 0x01021994 and capacity == FIXTURE_BYTES and flags & 15 == 14,
            "distinct bounded tmpfs root")
    return mounted


def mount_state(path):
    descriptor = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        return (info.st_dev, info.st_ino, info.st_mode, info.st_rdev,
                mount_id(descriptor), os.fstatvfs(descriptor).f_flag)
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def self_state():
    values = parse_status(read_file("/proc/self/status", 16384))
    return {
        "uid": values["Uid"], "gid": values["Gid"], "groups": values["Groups"],
        "caps": tuple(values[key][0] for key in CAP_KEYS), "nnp": values["NoNewPrivs"][0],
        "user": namespace("user"), "mount": namespace("mnt"),
        "uid_map": maps(read_file("/proc/self/uid_map")), "gid_map": maps(read_file("/proc/self/gid_map")),
        "parent": values["PPid"][0], "pids": values["NSpid"],
        "label": read_file("/proc/self/attr/current", 4096).strip(),
    }


def validate_credentials(value, uid, gid, caps, *, groups=(), nnp=1):
    require(value["uid"] == (uid,) * 4 and value["gid"] == (gid,) * 4
            and value["groups"] == groups and value["caps"] == caps and value["nnp"] == nnp,
            "credential boundary")


def validate_preentry(value, binding, outer):
    groups = outer["groups"] if outer["ordinary"] else ()
    validate_credentials(value, binding.uid, binding.gid, (0,) * 5, groups=groups)
    uid_map = ((binding.uid, binding.uid, 1),) if outer["ordinary"] else FULL_MAP
    gid_map = ((binding.gid, binding.gid, 1),) if outer["ordinary"] else FULL_MAP
    require(value["user"] == outer["user"] and value["mount"] == outer["mount"]
            and value["uid_map"] == uid_map and value["gid_map"] == gid_map
            and value["label"] == outer["label"], "bound outer worker")


def validate_target(value, binding, outer, target):
    # The ordinary self-map has already denied setgroups. Preserve its inherited
    # membership, without guessing the host's unmapped-GID presentation.
    groups_ok = len(value["groups"]) == len(outer["groups"]) if outer["ordinary"] else value["groups"] == ()
    require(value["uid"] == (0,) * 4 and value["gid"] == (0,) * 4 and groups_ok
            and value["nnp"] == 1 and value["uid_map"] == ((0, binding.uid, 1),)
            and value["gid_map"] == ((0, binding.gid, 1),)
            and value["user"] == target.user and value["user"] != outer["user"]
            and value["mount"] == target.mount and value["mount"] != outer["mount"]
            and value["label"] == outer["label"], "descendant worker")


def validate_local_setup(value):
    inheritable, permitted, effective, bounding_set, ambient = value["caps"]
    needed = (1 << CAP_SYS_ADMIN) | (1 << 18)
    require(inheritable == ambient == 0 and effective == permitted == bounding_set
            and effective & needed == needed, "descendant-local setup capabilities")


def fd_inventory(*, ordinary_entry=False):
    require(type(ordinary_entry) is bool, "descriptor inventory mode")
    result = {}
    names = os.listdir("/proc/self/fd")
    require(len(names) <= (21 if ordinary_entry else 20), "descriptor bound")
    disappeared = 0
    for name in names:
        require(name.isdecimal(), "descriptor name")
        descriptor = int(name)
        try:
            info = os.fstat(descriptor)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
            if ordinary_entry:
                disappeared += 1
                require(disappeared <= 1, "entry descriptor scan changed")
            continue
        result[descriptor] = (
            info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE,
        )
    if ordinary_entry:
        require(len(result) <= 20, "descriptor bound")
    return result


def withdraw_entry_fifos(before, report):
    require(type(before) is dict and len(before) <= 20 and {0, 1, 2} <= before.keys()
            and all(integer(fd, 0, 0x7FFFFFFF) and type(value) is tuple and len(value) == 4
                    and all(type(part) is int for part in value)
                    and (fd <= 2 or value[2] == stat.S_IFIFO and value[3] in (0, 1, 2))
                    for fd, value in before.items()), "initial C descriptors")
    stdio = {fd: before[fd] for fd in (0, 1, 2)}
    primary = None
    for descriptor in sorted(fd for fd in before if fd > 2):
        try:
            info = os.fstat(descriptor)
            actual = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
                      fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE)
            require(actual == before[descriptor], "entry FIFO identity changed")
            try:
                os.close(descriptor)
            except BaseException:
                report.unsure("cleanup")
                raise
        except BaseException as error:
            report.error("cleanup", error)
            if primary is None:
                primary = error
            elif error is not primary:
                _cleanup_life._forget_error(error)
    if primary is not None:
        raise primary
    require(fd_inventory() == stdio, "initial C descriptors")
    return stdio


def validate_fds(actual, expected, capture):
    require(actual == expected and set(actual) >= {0, 1, 2}, "descriptor inventory")
    require(all(actual[fd][2] == stat.S_IFIFO for fd in (0, 1, 2))
            and [actual[fd][3] for fd in (0, 1, 2)] == [os.O_RDONLY, os.O_WRONLY, os.O_WRONLY]
            and len({actual[fd][:2] for fd in (0, 1, 2)}) == 3,
            "stdio pipe direction and identity")
    require(all(item[:2] != capture for item in actual.values()), "enclosing writer alias")
    require(all(item[2] != stat.S_IFCHR and item[2] != stat.S_IFDIR for item in actual.values()),
            "device or ancestor descriptor")


def mount(source, target, flags, kind=None, data=None):
    checked(libc().mount(
        None if source is None else os.fsencode(source), os.fsencode(target),
        None if kind is None else os.fsencode(kind), flags, data,
    ))


class Attributes(ctypes.Structure):
    _fields_ = [("attr_set", ctypes.c_uint64), ("attr_clr", ctypes.c_uint64),
                ("propagation", ctypes.c_uint64), ("userns_fd", ctypes.c_uint64)]


def restrictions(path, flags):
    descriptor = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        attributes = Attributes(attr_set=flags)
        checked(libc().syscall(
            ctypes.c_long(442), ctypes.c_int(descriptor), ctypes.c_char_p(b""),
            ctypes.c_uint(4096 | 32768), ctypes.byref(attributes), ctypes.c_size_t(32),
        ))
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def bind(source, target, flags):
    mount(source, target, MS_BIND | MS_REC)
    restrictions(target, flags)
    require(mount_state(target)[5] & flags == flags, "sealed mount flags")


@dataclass(frozen=True)
class Target:
    user: tuple
    mount: tuple
    root: tuple


@dataclass
class Child:
    pid: int
    pidfd: int | None = None
    proc: int | None = None
    start: int | None = None
    observed: int | None = None
    reaped: bool = False


def proc_start(directory):
    data = read_file("stat", directory=directory)
    fields = data.rsplit(b") ", 1)
    require(len(fields) == 2 and len(fields[1].split()) >= 20, "owned proc stat")
    return int(fields[1].split()[19])


def live_child(child):
    require(not child.reaped and child.pidfd is not None and child.proc is not None
            and child.start == proc_start(child.proc), "live child ownership")
    require(not select.select([child.pidfd], [], [], 0)[0], "exited child")
    require(os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None,
            "live waitable child")
    info = read_file("/proc/self/fdinfo/" + str(child.pidfd))
    pids = [row.split(b":", 1)[1].strip() for row in info.splitlines() if row.startswith(b"Pid:")]
    require(pids == [str(child.pid).encode()], "pidfd process binding")
    value = parse_status(read_file("status", 16384, directory=child.proc))
    require(value["PPid"] == (1,) and value["NSpid"] == (child.pid,), "private child proc view")
    return value


def send_token(descriptor, token, deadline):
    require(token in TOKENS, "fixed token")
    remaining(deadline)
    require(os.write(descriptor, token) == len(token), "partial handshake write")


def receive_token(descriptor, token, deadline):
    require(token in TOKENS, "fixed token")
    require(bool(select.select([descriptor], [], [], remaining(deadline))[0]), "handshake deadline")
    require(os.read(descriptor, len(token) + 1) == token, "partial or wrong handshake")


def receive_eof(descriptor, deadline):
    require(bool(select.select([descriptor], [], [], remaining(deadline))[0]), "EOF deadline")
    require(os.read(descriptor, 1) == b"", "handshake EOF")


def observe(child, deadline, life):
    require(not child.reaped, "released child observation")
    while True:
        value = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if value is not None:
            _, status = life._wait_value(child.pid, value)
            require(child.observed is None or child.observed == status, "first status changed")
            child.observed = status
            return status
        require(child.pidfd is not None, "wait requires owned pidfd")
        select.select([child.pidfd], [], [], remaining(deadline))


def reap(child, deadline):
    require(not child.reaped, "released child reap")
    while True:
        pid, status = os.waitpid(child.pid, os.WNOHANG)
        if pid:
            require(pid == child.pid, "foreign reap")
            child.reaped = True
            actual = os.waitstatus_to_exitcode(status)
            require(child.observed is None or actual == child.observed, "normal wait agreement")
            return actual
        select.select([child.pidfd] if child.pidfd is not None else [], [], [],
                      min(0.01, remaining(deadline)))


def stop_child(child, deadline):
    if child.reaped:
        return
    os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if child.pidfd is not None:
        try:
            signal.pidfd_send_signal(child.pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        os.kill(child.pid, signal.SIGKILL)
    reap(child, deadline)


def remote_fds(directory):
    descriptor = os.open("fd", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=directory)
    try:
        names = os.listdir(descriptor)
        require(len(names) <= 20 and all(name.isdecimal() for name in names), "remote descriptor bound")
        result = {}
        for name in names:
            info = os.stat(name, dir_fd=descriptor)
            rows = read_file("fdinfo/" + name, directory=directory).splitlines()
            flags = [row.split(b":", 1)[1].strip() for row in rows if row.startswith(b"flags:")]
            require(len(flags) == 1, "remote descriptor flags")
            result[int(name)] = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
                                 int(flags[0], 8) & os.O_ACCMODE)
        return result
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def root_context(binding, ordinary):
    uid, gid = (binding.uid, binding.gid) if ordinary else (0, 0)
    require(os.getpid() == 1 and os.getresuid() == (uid,) * 3
            and os.getresgid() == (gid,) * 3, "private setup role")
    host = parse_status(read_file("/proc/self/status", 16384))
    unshare_parent = parse_status(read_file("/proc/" + str(host["PPid"][0]) + "/status", 16384))
    leader = unshare_parent["PPid"][0]
    require(leader > 1 and host["NSpid"][-1] == 1 and len(host["NSpid"]) >= 2, "launcher ancestry")
    ancestor = {}
    for name in ("user", "mnt", "pid", "net"):
        descriptor = os.open("/proc/" + str(leader) + "/ns/" + name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            ancestor[name] = fd_identity(descriptor)
        finally:
            close_temporary(descriptor, sys.exc_info()[1])
    require(namespace("user") == ancestor["user"]
            and all(namespace(name) != ancestor[name] for name in ("mnt", "pid", "net")),
            "fixed launcher private topology")
    mount("proc", "/proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, "proc")
    require(os.readlink("/proc/self") == "1", "PID-aligned proc")
    value = self_state()
    uid_map = ((uid, uid, 1),) if ordinary else FULL_MAP
    gid_map = ((gid, gid, 1),) if ordinary else FULL_MAP
    require(value["pids"] == (1,) and value["parent"] == 0
            and value["uid_map"] == uid_map and value["gid_map"] == gid_map
            and value["user"] == ancestor["user"] and death_signal() == signal.SIGKILL,
            "bound PID1 lifecycle")
    mountfd = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    try:
        require(fcntl.ioctl(mountfd, NS_GET_NSTYPE) == NEWNS, "private mount namespace type")
        owner = fcntl.ioctl(mountfd, NS_GET_USERNS)
        try:
            require(fd_identity(owner) == value["user"], "outer mount owner")
        finally:
            close_temporary(owner, sys.exc_info()[1])
    finally:
        close_temporary(mountfd, sys.exc_info()[1])
    needed = sum(1 << bit for bit in (CAP_KILL, CAP_SETGID, CAP_SETUID, CAP_SETPCAP, CAP_SYS_ADMIN))
    require(value["caps"][1] & needed == needed, "inherited setup capabilities")
    if not ordinary:
        os.setgroups([])
        require(self_state()["groups"] == (), "setup supplementary group drop")
        value["groups"] = ()
    value["ordinary"] = ordinary
    return value


class Bootstrap:
    """One creator N and one worker W, with a fixed PID1 reaper R."""

    NAMES = frozenset({
        "fixture", "tmpfs", "volume", "eof.read", "eof.write", "request.read", "request.write",
        "response.read", "response.write", "N.pidfd", "N.proc", "user", "mount",
        "uid_map", "gid_map", "setgroups", "gate.read", "gate.write",
        "worker.read", "worker.write", "W.pidfd", "W.proc",
    })

    def __init__(self, life, binding, parent, *, ordinary=False):
        require(type(ordinary) is bool, "fixed backend")
        self.life, self.binding, self.parent, self.ordinary = life, binding, parent, ordinary
        self.volume = parent / "volume"
        self.report = life._CleanupReport("R", binding.deadline)
        self.fds, self.children = {}, {"N": None, "W": None}
        self.outer = self.target = self.capture = self.expected_worker = None
        self.stage, self.state = "setup", "new"
        self.setup_status = self.observation = self.result = self.primary = None
        self.publications, self.before_frame = 0, None
        self.mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())

    @property
    def deadline(self):
        return self.binding.deadline

    @property
    def setup_ids(self):
        return (self.binding.uid, self.binding.gid) if self.ordinary else (0, 0)

    def advance(self, old, new):
        require(self.state == old, "closed state transition")
        remaining(self.deadline)
        self.state = new

    def acquire(self, name, operation):
        require(name in self.NAMES and name not in self.fds, "fixed FD slot")

        def action():
            self.fds[name] = operation()

        self.life.finish_cleanup([("setup", action)], report=self.report)
        return self.fds[name]

    def pipe(self, left, right):
        require(left in self.NAMES and right in self.NAMES and left != right
                and left not in self.fds and right not in self.fds, "fixed pipe slots")

        def action():
            reader, writer = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
            self.fds[left], self.fds[right] = reader, writer

        self.life.finish_cleanup([("setup", action)], report=self.report)

    def close(self, name):
        descriptor = self.fds.pop(name, None)
        if descriptor is None:
            return
        for role, child in self.children.items():
            if child is not None:
                if name == role + ".pidfd":
                    child.pidfd = None
                if name == role + ".proc":
                    child.proc = None
        try:
            os.close(descriptor)
        except BaseException:
            self.report.unsure("cleanup")
            raise

    def closes(self, names, *, primary=None):
        self.life.finish_cleanup(
            [("cleanup", lambda name=name: self.close(name)) for name in names],
            primary=primary, report=self.report,
        )

    def inherited(self, keep):
        require(fd_inventory().keys() == {0, 1, 2, *self.fds.values()}, "unknown inherited descriptor")
        self.closes(tuple(name for name in self.fds if name not in keep))

    def install_volume(self):
        descriptor = self.acquire("fixture", lambda: os.open(
            self.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        ))
        original = directory_identity(descriptor)
        require((original.device, original.inode, original.uid) ==
                (self.binding.device, self.binding.inode, self.binding.owner)
                and original.gid == self.binding.gid and original.mode == stat.S_IFDIR | 0o700
                and not os.listdir(descriptor), "owned fresh fixture")
        mount("tmpfs", self.parent, 14, "tmpfs", b"size=1048576,mode=0700")
        self.close("fixture")
        descriptor = self.acquire("tmpfs", lambda: os.open(
            self.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        ))
        mounted = verify_tmpfs(descriptor, original, *self.setup_ids)
        os.fchown(descriptor, self.binding.uid, self.binding.gid)
        os.mkdir("volume", 0o700, dir_fd=descriptor)
        volume = self.acquire("volume", lambda: os.open(
            "volume", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor,
        ))
        child = directory_identity(volume)
        uid, gid = self.setup_ids
        require(child.device == mounted.device and child.inode != mounted.inode
                and child.mount == mounted.mount and child.mode == stat.S_IFDIR | 0o700
                and child.uid == uid and child.gid == gid, "fresh tmpfs volume")
        os.fchown(volume, self.binding.uid, self.binding.gid)

    def create_fixture(self):
        self.advance("new", "fixture")
        self.install_volume()
        for name in ("source", "runtime", "selected"):
            target = self.volume / name
            target.mkdir(mode=0o700)
            os.chown(target, self.binding.uid, self.binding.gid)
        for name in ("source", "runtime"):
            target = self.volume / name / "canary"
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            try:
                require(os.write(descriptor, name.encode("ascii")) == len(name), "canary write")
                os.fchown(descriptor, self.binding.uid, self.binding.gid)
            finally:
                close_temporary(descriptor, sys.exc_info()[1])
            bind(self.volume / name, self.volume / name, 15)
        for name, minor in (("null", 3), ("zero", 5)):
            target = self.volume / name
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            try:
                os.fchown(descriptor, self.binding.uid, self.binding.gid)
            finally:
                close_temporary(descriptor, sys.exc_info()[1])
            original = mount_state("/dev/" + name)
            require(stat.S_ISCHR(original[2]) and original[3] == os.makedev(1, minor)
                    and not original[5] & os.ST_NODEV, "existing device identity")
            flags = 10 if self.binding.mode == "writable" else 11
            bind(Path("/dev") / name, target, flags)
            sealed = mount_state(target)
            require(sealed[:4] == original[:4] and sealed[5] & 15 == flags, "outer device restrictions")
        bind(SOURCE, self.volume / "selected", 15)
        self.pipe("eof.read", "eof.write")
        self.close("eof.write")
        os.dup2(self.fds["eof.read"], 0, inheritable=False)
        self.close("eof.read")
        actual = fd_inventory()
        require(actual[0][2:] == (stat.S_IFIFO, os.O_RDONLY)
                and actual[1][2:] == (stat.S_IFIFO, os.O_WRONLY)
                and actual[2][2:] == (stat.S_IFIFO, os.O_WRONLY)
                and len({actual[fd][:2] for fd in (0, 1, 2)}) == 3
                and os.fpathconf(1, "PC_PIPE_BUF") >= FRAME_BYTES, "trusted capture pipes")
        self.capture = actual[1][:2]
        os.set_blocking(1, False)
        self.closes(("volume", "tmpfs"))

    def fork_role(self, role):
        require(role in ("N", "W") and self.children[role] is None, "one fixed child per role")

        def action():
            pid = os.fork()
            if pid == 0:
                status = 125
                try:
                    signal.pthread_sigmask(signal.SIG_SETMASK, self.mask)
                    if role == "N":
                        self.creator()
                    else:
                        self.worker()
                    status = 0
                except BaseException as error:
                    self.life._forget_error(error)
                os._exit(status)
            require(integer(pid, 1, 0x7FFFFFFF), "owned fork")
            child = self.children[role] = Child(pid)
            child.pidfd = self.fds[role + ".pidfd"] = os.pidfd_open(pid)
            child.proc = self.fds[role + ".proc"] = os.open(
                "/proc/" + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
            child.start = proc_start(child.proc)

        self.life.finish_cleanup([("setup", action)], report=self.report)
        return self.children[role]

    def creator(self):
        self.inherited({"request.read", "response.write"})
        if not self.ordinary:
            os.setgroups([])
        os.setresgid(-1, self.binding.gid, -1)
        os.setresuid(-1, self.binding.uid, -1)
        # Credential changes can reset dumpability to fs.suid_dumpable.
        prctl(4, 0)
        require(prctl(3) == 0, "creator identity-change nondumpability")
        require(self_state()["caps"][1] & (1 << CAP_SYS_ADMIN), "creator inherited authority")
        capset(1 << CAP_SYS_ADMIN)
        prctl(47, 4)
        prctl(38, 1)
        parent_guard()
        value = self_state()
        self.validate_creator(value)
        dumpable = self.mapping_dumpability()
        send_token(self.fds["response.write"], b"CREATOR_READY\n", self.deadline)
        receive_token(self.fds["request.read"], b"CREATE\n", self.deadline)
        checked(libc().unshare(NEWUSER | NEWNS))
        parent_guard()
        require(self_state()["label"] == self.outer["label"], "creator unchanged LSM label")
        require(prctl(3) == dumpable, "created namespace dumpability")
        send_token(self.fds["response.write"], b"NS_CREATED\n", self.deadline)
        receive_token(self.fds["request.read"], b"MAPS_COMPLETE\n", self.deadline)
        require(maps(read_file("/proc/self/uid_map")) == ((0, self.binding.uid, 1),)
                and maps(read_file("/proc/self/gid_map")) == ((0, self.binding.gid, 1),)
                and read_file("/proc/self/setgroups").strip() == b"deny", "creator map readback")
        prctl(4, 0)
        require(prctl(3) == 0, "creator dumpability restoration")
        os.setresgid(0, 0, 0)
        os.setresuid(0, 0, 0)
        bounding(0)
        capset(0)
        prctl(38, 1)
        parent_guard()
        require(prctl(3) == 0, "retired creator nondumpability")
        value = self_state()
        groups = value["groups"] if self.ordinary else ()
        require(not self.ordinary or len(groups) == len(self.outer["groups"]), "mapped creator groups")
        validate_credentials(value, 0, 0, (0,) * 5, groups=groups)
        self.closes(("request.read", "response.write"))

    def mapping_dumpability(self):
        # Only the proved nonzero, self-mapped ordinary N may expose owner-
        # writable proc maps; nondumpable files otherwise have unmapped root IDs.
        value = int(self.ordinary)
        prctl(4, value)
        require(prctl(3) == value, "creator mapping dumpability")
        return value

    def validate_creator(self, value):
        uid, gid = self.setup_ids
        uid_map = ((self.binding.uid, self.binding.uid, 1),) if self.ordinary else FULL_MAP
        gid_map = ((self.binding.gid, self.binding.gid, 1),) if self.ordinary else FULL_MAP
        require(value["uid"] == (uid, self.binding.uid, uid, self.binding.uid)
                and value["gid"] == (gid, self.binding.gid, gid, self.binding.gid)
                and value["groups"] == self.outer["groups"]
                and value["caps"][:3] == (0, 1 << CAP_SYS_ADMIN, 1 << CAP_SYS_ADMIN)
                and value["caps"][4] == 0 and value["nnp"] == 1
                and value["uid_map"] == uid_map and value["gid_map"] == gid_map
                and value["user"] == self.outer["user"] and value["mount"] == self.outer["mount"]
                and value["label"] == self.outer["label"], "creator boundary")
        require(not self.ordinary or (
            self.binding.uid > 0 and self.binding.gid > 0 and self.outer["ordinary"] is True
            and value["uid"] == (self.binding.uid,) * 4 and value["gid"] == (self.binding.gid,) * 4
        ), "ordinary creator has no host-root identity")

    def verify_creator(self, child):
        values = live_child(child)
        self.validate_creator({
            "uid": values["Uid"], "gid": values["Gid"], "groups": values["Groups"],
            "caps": tuple(values[key][0] for key in CAP_KEYS), "nnp": values["NoNewPrivs"][0],
            "user": namespace("user", directory=child.proc), "mount": namespace("mnt", directory=child.proc),
            "uid_map": maps(read_file("uid_map", directory=child.proc)),
            "gid_map": maps(read_file("gid_map", directory=child.proc)),
            "label": read_file("attr/current", directory=child.proc).strip(),
        })

    def validate_map_writer(self):
        value = self_state()
        uid, gid = self.setup_ids
        uid_map = ((uid, uid, 1),) if self.ordinary else FULL_MAP
        gid_map = ((gid, gid, 1),) if self.ordinary else FULL_MAP
        needed = sum(1 << bit for bit in (CAP_SYS_ADMIN, CAP_SETUID, CAP_SETGID))
        require(value["uid"] == (uid,) * 4 and value["gid"] == (gid,) * 4
                and value["uid_map"] == uid_map and value["gid_map"] == gid_map
                and value["user"] == self.outer["user"] and value["mount"] == self.outer["mount"]
                and value["label"] == self.outer["label"] and value["caps"][2] & needed == needed,
                "bound parent map writer")

    def namespace_relation(self, descriptor, operation, expected):
        temporary = fcntl.ioctl(descriptor, operation)
        try:
            require(fd_identity(temporary) == expected, "namespace ownership relation")
        finally:
            close_temporary(temporary, sys.exc_info()[1])

    def pin_target(self, child):
        live_child(child)
        user = self.acquire("user", lambda: os.open("ns/user", os.O_RDONLY | os.O_CLOEXEC, dir_fd=child.proc))
        mountfd = self.acquire("mount", lambda: os.open("ns/mnt", os.O_RDONLY | os.O_CLOEXEC, dir_fd=child.proc))
        require(fcntl.ioctl(user, NS_GET_NSTYPE) == NEWUSER
                and fcntl.ioctl(mountfd, NS_GET_NSTYPE) == NEWNS, "typed namespace handles")
        user_id, mount_identity = fd_identity(user), fd_identity(mountfd)
        require(user_id != self.outer["user"] and mount_identity != self.outer["mount"], "new descendant namespaces")
        self.namespace_relation(user, NS_GET_PARENT, self.outer["user"])
        owner = bytearray(4)
        fcntl.ioctl(user, NS_GET_OWNER_UID, owner, True)
        require(struct.unpack("=I", owner)[0] == self.binding.uid, "ordinary owner UID")
        self.namespace_relation(mountfd, NS_GET_USERNS, user_id)
        self.target = Target(user_id, mount_identity, mount_state("/proc/" + str(child.pid) + "/root/."))
        live_child(child)

    def map_creator(self, child):
        names = ("setgroups", "uid_map", "gid_map")
        primary = None
        try:
            self.validate_map_writer()
            live_child(child)
            for name in names:
                descriptor = self.acquire(name, lambda name=name: os.open(
                    name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=child.proc,
                ))
                info = os.fstat(descriptor)
                uid, gid = self.setup_ids
                require(info.st_mode == stat.S_IFREG | 0o644 and info.st_uid == uid and info.st_gid == gid,
                        "creator proc-map ownership")
            initial_groups = b"deny" if self.ordinary else b"allow"
            require(os.read(self.fds["uid_map"], 128) == b"" and os.read(self.fds["gid_map"], 128) == b""
                    and os.read(self.fds["setgroups"], 128).strip() == initial_groups, "fresh map presence")
            for name, data in (
                ("setgroups", b"deny"), ("uid_map", ("0 %d 1\n" % self.binding.uid).encode()),
                ("gid_map", ("0 %d 1\n" % self.binding.gid).encode()),
            ):
                live_child(child)
                descriptor = self.fds[name]
                os.lseek(descriptor, 0, os.SEEK_SET)
                require(os.write(descriptor, data) == len(data), "partial map write")
                os.lseek(descriptor, 0, os.SEEK_SET)
                require(os.read(descriptor, 128).split() == data.split(), "exact map readback")
            self.pin_target_recheck(child)
        except BaseException as error:
            primary = error
            raise
        finally:
            self.closes(names, primary=primary)

    def pin_target_recheck(self, child):
        live_child(child)
        require(namespace("user", directory=child.proc) == self.target.user
                and namespace("mnt", directory=child.proc) == self.target.mount
                and maps(read_file("uid_map", directory=child.proc)) == ((0, self.binding.uid, 1),)
                and maps(read_file("gid_map", directory=child.proc)) == ((0, self.binding.gid, 1),)
                and read_file("setgroups", directory=child.proc).strip() == b"deny", "mapped live target")
        self.namespace_relation(self.fds["user"], NS_GET_PARENT, self.outer["user"])
        self.namespace_relation(self.fds["mount"], NS_GET_USERNS, self.target.user)

    def setup_creator(self):
        self.advance("fixture", "creator")
        self.stage = "creator"
        self.pipe("request.read", "request.write")
        self.pipe("response.read", "response.write")
        child = self.fork_role("N")
        self.closes(("request.read", "response.write"))
        receive_token(self.fds["response.read"], b"CREATOR_READY\n", self.deadline)
        self.verify_creator(child)
        send_token(self.fds["request.write"], b"CREATE\n", self.deadline)
        receive_token(self.fds["response.read"], b"NS_CREATED\n", self.deadline)
        self.pin_target(child)
        self.map_creator(child)
        send_token(self.fds["request.write"], b"MAPS_COMPLETE\n", self.deadline)
        self.close("request.write")
        receive_eof(self.fds["response.read"], self.deadline)
        self.setup_status = observe(child, self.deadline, self.life)
        if self.setup_status != 0:
            self.observation = ("creator", child.pid, self.setup_status)
            return False
        reap(child, self.deadline)
        self.closes(("N.pidfd", "N.proc", "response.read"))
        require(self.report.value().complete and set(self.fds) == {"user", "mount"},
                "creator teardown before worker")
        self.advance("creator", "mapped")
        return True

    def retire(self):
        require(set(self.fds) == {"W.pidfd", "gate.write", "worker.read"}, "reaper FD authority")
        bounding(1 << CAP_KILL)
        capset(1 << CAP_KILL)
        prctl(38, 1)
        value = self_state()
        uid, gid = self.setup_ids
        validate_credentials(value, uid, gid, (0, 1 << CAP_KILL, 1 << CAP_KILL, 1 << CAP_KILL, 0),
                             groups=self.outer["groups"])
        require(os.getpid() == 1 and value["pids"] == (1,) and value["parent"] == 0
                and value["user"] == self.outer["user"] and value["mount"] == self.outer["mount"]
                and value["label"] == self.outer["label"] and death_signal() == signal.SIGKILL,
                "retired PID1 reaper")
        require(fd_inventory().keys() == {0, 1, 2, *self.fds.values()}, "retired descriptors")

    def setup_worker(self):
        self.advance("mapped", "blocked")
        self.stage = "worker"
        self.pipe("gate.read", "gate.write")
        self.pipe("worker.read", "worker.write")
        original = fd_inventory()
        self.expected_worker = {
            0: original[0], 1: original[self.fds["worker.write"]], 2: original[2],
            **{self.fds[name]: original[self.fds[name]] for name in ("user", "mount", "gate.read")},
        }
        child = self.fork_role("W")
        self.closes(("gate.read", "worker.write"))
        receive_token(self.fds["worker.read"], b"W0_READY\n", self.deadline)
        live_child(child)
        validate_fds(remote_fds(child.proc), self.expected_worker, self.capture)
        self.closes(("W.proc", "user", "mount"))
        self.retire()
        self.advance("blocked", "retired")
        send_token(self.fds["gate.write"], b"GO\n", self.deadline)
        self.close("gate.write")
        self.advance("retired", "released")
        return child

    def worker(self):
        require(fd_inventory().keys() == {0, 1, 2, *self.fds.values()}, "worker inherited descriptors")
        os.dup2(self.fds["worker.write"], 1, inheritable=False)
        self.inherited({"user", "mount", "gate.read"})
        validate_fds(fd_inventory(), self.expected_worker, self.capture)
        send_token(1, b"W0_READY\n", self.deadline)
        receive_token(self.fds["gate.read"], b"GO\n", self.deadline)
        self.close("gate.read")
        expected = {fd: value for fd, value in self.expected_worker.items()
                    if fd in {0, 1, 2, self.fds["user"], self.fds["mount"]}}
        bounding(0)
        if not self.ordinary:
            os.setgroups([])
        os.setresgid(self.binding.gid, self.binding.gid, self.binding.gid)
        os.setresuid(self.binding.uid, self.binding.uid, self.binding.uid)
        capset(0)
        prctl(38, 1)
        parent_guard()
        os.environ.clear()
        os.environ.update(ENVIRONMENT)
        validate_preentry(self_state(), self.binding, self.outer)
        validate_fds(fd_inventory(), expected, self.capture)
        checked(libc().setns(self.fds["user"], NEWUSER))
        checked(libc().setns(self.fds["mount"], NEWNS))
        os.chdir("/")
        require(mount_state("/") == self.target.root and mount_state(".") == self.target.root,
                "target root and fresh cwd")
        entered = self_state()
        validate_target(entered, self.binding, self.outer, self.target)
        validate_local_setup(entered)
        self.closes(("user", "mount"))
        parent_guard()
        validate_fds(fd_inventory(), {fd: expected[fd] for fd in (0, 1, 2)}, self.capture)
        require(self.report.value().complete, "worker setup close completeness")
        require(mount_state(self.volume / "selected")[5] & 15 == 15, "readonly trusted binding")
        data = json_bytes(mechanism(self.volume, self.binding, self.outer, self.target, self.life))
        require(len(data) + len(b"W0_READY\n") <= FRAME_BYTES, "private worker byte admission")
        self.life._private_worker_record(data)
        remaining(self.deadline)
        require(os.write(1, data) == len(data), "private worker short write")

    def collect_worker(self, child):
        accepted = bytearray()
        limit = FRAME_BYTES - len(b"W0_READY\n")
        while True:
            require(select.select([self.fds["worker.read"]], [], [], remaining(self.deadline))[0],
                    "private result deadline")
            chunk = os.read(self.fds["worker.read"], limit - len(accepted) + 1)
            if not chunk:
                break
            require(len(accepted) + len(chunk) <= limit, "private worker overflow")
            accepted.extend(chunk)
        status = observe(child, self.deadline, self.life)
        self.observation = ("worker", child.pid, status)
        if status == 0:
            value = self.life._private_worker_record(bytes(accepted))
            validate_mode(value, self.binding.mode)
            self.result = worker_wire(value)
        accepted.clear()

    def publish(self, value):
        require(not self.ordinary and self.publications < 2 and value["role"] == "R"
                and value["phase"] == ("before" if self.publications == 0 else "after"), "R publication order")
        self.publications += 1
        data = self.life._encode_frame(value)
        parsed = self.life._decode_frame(data[4:], "0" * 32, self.binding, self.before_frame)
        if parsed.phase == "before":
            self.before_frame = parsed
        write_result(data, self.deadline)

    def run(self):
        global _cleanup_life, _cleanup_report
        _cleanup_life, _cleanup_report = self.life, self.report
        handlers = {}
        try:
            for signum in self.life.TERMINATING:
                handlers[signum] = signal.signal(signum, self.life.interrupted)
            self.outer = root_context(self.binding, self.ordinary)
            self.create_fixture()
            if self.setup_creator():
                self.collect_worker(self.setup_worker())
        except BaseException as error:
            self.primary = error
        observed = self.observation
        before = {
            "v": 1, "role": "R", "phase": "before", "binding": self.binding.wire(),
            "pid": 0 if observed is None else observed[1],
            "kind": "exception" if observed is None else "normal-exit",
            "stage": self.stage if observed is None else observed[0],
            "status": None if observed is None else observed[2],
            "error": self.life._error_value(self.primary) if observed is None else None,
            "result": self.result, "setup_status": self.setup_status,
        }
        if observed is not None and self.primary is not None:
            self.report.error("capture", self.primary)
        actions = [] if self.ordinary else [("publication", lambda: self.publish(before))]
        for child in self.children.values():
            if child is not None and not child.reaped:
                def stop(child=child):
                    try:
                        stop_child(child, self.deadline)
                    finally:
                        if not child.reaped:
                            self.report.unsure("leader")
                actions.append(("leader", stop))
        actions.extend(("cleanup", lambda name=name: self.close(name)) for name in tuple(self.fds))
        try:
            self.life.finish_cleanup(actions, primary=self.primary, handlers=handlers, report=self.report)
        except BaseException as error:
            self.primary = error
        if self.fds or any(child is not None and not child.reaped for child in self.children.values()):
            self.report.unsure("ownership")
        status = None if observed is None else observed[2]
        clean = self.primary is None and self.report.value().complete and status == 0 and self.result is not None
        returning = status is not None and (status != 0 or clean)
        try:
            if not self.ordinary:
                self.publish({
                    "v": 1, "role": "R", "phase": "after", "binding": self.binding.wire(),
                    "pid": before["pid"], "before": "before", "cleanup": self.life._cleanup_wire(self.report.value()),
                    "disposition": "return" if returning else "raise", "status": status if returning else None,
                })
            elif clean:
                write_result(json_bytes(self.result), self.deadline)
        except BaseException as error:
            self.report.error("publication", error)
            if self.primary is None:
                self.primary = error
            elif error is not self.primary:
                self.life._forget_error(error)
            clean = False
        if self.primary is not None:
            self.life._forget_error(self.primary)
            self.primary = None
        self.result = self.before_frame = None
        before = actions = handlers = None
        if status:
            return status if status > 0 else 128 - status
        return 0 if clean else 125


def write_result(data, deadline):
    require(len(data) <= FRAME_BYTES, "atomic publication bound")
    while True:
        wait = remaining(deadline)
        try:
            size = os.write(1, data)
        except BlockingIOError:
            select.select([], [1], [], wait)
            continue
        require(size == len(data), "partial atomic publication")
        return


def load_subject(volume):
    path = canonical(volume / "selected/scripts/validation_ownership/sandbox_exec.py")
    spec = importlib.util.spec_from_file_location("_null_mount_fixture_subject", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(Path(module.__file__) == path, "subject origin")
    return module


def old_negative(target):
    before = mount_state(target)
    result = libc().mount(None, os.fsencode(target), None, OLD_REMOUNT, None)
    error = ctypes.get_errno()
    require(result == -1 and error == errno.EPERM, "old remount must actually fail EPERM")
    require(mount_state(target) == before, "old remount changed full mount state")


def helper_transition(setup, root, volume, mode, life):
    require(mode in MODES, "closed helper mode")
    calls, failure = [], None
    original, native = setup.ctypes, libc()
    target = root / "dev/null"

    class ObservedCall:
        def syscall(self, *args):
            attributes = ctypes.cast(args[4], ctypes.POINTER(setup.MountAttributes)).contents
            observed = (
                args[0].value, args[3].value, attributes.attr_set, attributes.attr_clr,
                attributes.propagation, attributes.userns_fd, args[5].value,
            )
            require(not calls and observed == SELECTIVE_CALL, "single selective operation")
            if mode == "old":
                calls.append(("legacy-remount", OLD_REMOUNT))
                return native.mount(None, os.fsencode(target), None, OLD_REMOUNT, None)
            calls.append(observed)
            if mode in ("unsupported", "locked"):
                # Explicit syscall fault controls, not observations of kernel policy.
                ctypes.set_errno(errno.ENOSYS if mode == "unsupported" else errno.EPERM)
                return -1
            if mode == "substituted":
                checked(native.mount(os.fsencode(volume / "null"), os.fsencode(target),
                                     None, MS_BIND | MS_REC, None))
                descriptor = os.open(target, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    restriction = setup.MountAttributes(attr_set=14)
                    checked(native.syscall(
                        ctypes.c_long(442), ctypes.c_int(descriptor), ctypes.c_char_p(b""),
                        ctypes.c_uint(4096), ctypes.byref(restriction), ctypes.c_size_t(32),
                    ))
                finally:
                    close_temporary(descriptor, sys.exc_info()[1])
            return native.syscall(*args)

        def mount(self, *args):
            raise Refusal("no remount fallback")

    setup.ctypes = SimpleNamespace(
        CDLL=lambda *args, **kwargs: ObservedCall(), c_long=ctypes.c_long, c_int=ctypes.c_int,
        c_char_p=ctypes.c_char_p, c_uint=ctypes.c_uint, c_size_t=ctypes.c_size_t,
        byref=ctypes.byref, sizeof=ctypes.sizeof, get_errno=ctypes.get_errno,
    )
    try:
        try:
            setup._enable_toolchain_null(root)
        except Refusal:
            raise
        except (OSError, RuntimeError) as error:
            failure = life._error_value(error)
            life._forget_error(error)
    finally:
        setup.ctypes = original
    return calls, failure


def readonly_denial(path):
    info = os.stat(path)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
            and stat.S_IMODE(info.st_mode) == 0o600, "owned canary DAC control")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        require(error.errno == errno.EROFS, "actual readonly denial, not DAC masking")
        return error.errno
    else:
        close_temporary(descriptor, None)
        raise Refusal("readonly canary became writable")


def device_denial(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        require(error.errno in (errno.EACCES, errno.EPERM), "other device denial")
        return error.errno
    else:
        close_temporary(descriptor, None)
        raise Refusal("other device became usable")


def null_io(path):
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        require(os.read(descriptor, 1) == b"" and os.write(descriptor, b"x") == 1, "null EOF/tiny write")
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def mechanism(volume, binding, outer, target, life):
    remaining(binding.deadline)
    entered = self_state()
    validate_target(entered, binding, outer, target)
    validate_local_setup(entered)
    before_fds = fd_inventory()
    require(set(before_fds) == {0, 1, 2} and all(v[2] == stat.S_IFIFO for v in before_fds.values()),
            "subject import boundary")
    setup = load_subject(volume)
    root = volume / "root"
    for name in ("dev", "repo", "usr"):
        (root / name).mkdir(parents=True)
    for name in ("null", "zero"):
        descriptor = os.open(root / "dev" / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        close_temporary(descriptor, None)
    setup.bind(root, root, executable=True)
    setup.bind(volume / "source", root / "repo")
    setup.bind(volume / "runtime", root / "usr", executable=True)
    setup.bind(volume / ("zero" if binding.mode == "wrong-device" else "null"), root / "dev/null", writable=True)
    setup.bind(volume / "zero", root / "dev/zero", writable=True)
    before = mount_state(root / "dev/null")
    if binding.mode == "readonly":
        old_negative(root / "dev/null")
    calls, failure = helper_transition(setup, root, volume, binding.mode, life)
    after = mount_state(root / "dev/null")
    bounding(0)
    capset(0)
    prctl(38, 1)
    value = self_state()
    validate_target(value, binding, outer, target)
    validate_credentials(value, 0, 0, (0,) * 5, groups=entered["groups"])
    require(fd_inventory() == before_fds, "helper descriptor release")
    denied = [readonly_denial(root / name / "canary") for name in ("repo", "usr")]
    denied.append(device_denial(root / "dev/zero"))
    io = False
    if failure is None:
        null_io(root / "dev/null")
        io = True
    require(fd_inventory() == before_fds, "I/O descriptor release")
    remaining(binding.deadline)
    result = {
        "mode": binding.mode, "before": list(before), "after": list(after), "failure": failure,
        "calls": [list(call) for call in calls], "caps": [*value["caps"], value["nnp"]],
        "denied": denied, "null_io": io, "fd_closed": True, "local_nonzero_topology": True,
    }
    validate_mode(life._private_worker_record(json_bytes(result)), binding.mode)
    return result


def validate_mode(value, expected=None):
    require(type(value) is tuple and len(value) == 10, "closed mode record")
    mode, before, after, failure, calls, caps, denied, io, closed, topology = value
    require(mode in MODES and (expected is None or mode == expected), "bound mode")
    require(stat.S_ISCHR(before[2]) and before[3] == os.makedev(1, 5 if mode == "wrong-device" else 3)
            and before[5] & 15 == (14 if mode == "writable" else 15)
            and caps == (0, 0, 0, 0, 0, 1) and denied[:2] == (errno.EROFS, errno.EROFS)
            and denied[2] in (errno.EPERM, errno.EACCES) and closed is True and topology is True,
            "common mode boundaries")
    if mode in ("readonly", "writable"):
        require(failure is None and calls == (SELECTIVE_CALL,) and io is True
                and before[:5] == after[:5] and after[5] == before[5] & ~os.ST_NODEV,
                "only local NODEV cleared")
    elif mode == "wrong-device":
        require(failure == (4, None) and calls == () and before == after and io is False,
                "wrong-device refusal before syscall")
    elif mode == "substituted":
        require(failure == (4, None) and calls == (SELECTIVE_CALL,) and before[:4] == after[:4]
                and before[4] != after[4] and before[5] == after[5] and io is False,
                "replacement remains nodev")
    else:
        expected_errno = errno.ENOSYS if mode == "unsupported" else errno.EPERM
        expected_calls = (("legacy-remount", OLD_REMOUNT),) if mode == "old" else (SELECTIVE_CALL,)
        require(failure == (1, expected_errno) and calls == expected_calls and before == after and io is False,
                "observed operation refusal")


def worker_wire(value):
    names = ("mode", "before", "after", "failure", "calls", "caps", "denied",
             "null_io", "fd_closed", "local_nonzero_topology")
    return dict(zip(names, value))


def public_result(value):
    validate_mode(value)
    result = worker_wire(value)
    for name in ("before", "after"):
        state = result[name]
        result[name] = {"identity": list(state[:4]), "mount_id": state[4], "flags": state[5]}
    failure = result["failure"]
    result["failure"] = None if failure is None else {"tag": failure[0], "errno": failure[1]}
    caps = result.pop("caps")
    result["post_drop"] = {**{key: format(number, "x") for key, number in zip(CAP_KEYS, caps[:5])},
                           "NoNewPrivs": str(caps[5])}
    result["denied"] = dict(zip(("repo", "usr", "other-device"), result["denied"]))
    result["calls"] = [list(call) for call in result["calls"]]
    return result


def fixture_name(directory, mode):
    require(type(mode) is str and mode in MODES, "closed fixture mode")
    directory = canonical(directory)
    require(directory.parent == WORKSPACE and re.fullmatch("[0-9a-f]{24}", directory.name) is not None,
            "Foundation-owned directory")
    return directory.name + "-null-" + mode


def role_arguments(arguments, life, context):
    require(len(arguments) == 9 and arguments[0] in ("--reaper", "--ordinary")
            and arguments[1] in MODES, "fixed role arguments")
    require(all(re.fullmatch("[1-9][0-9]{0,19}", arguments[index]) for index in (2, 3, 7, 8))
            and re.fullmatch("[0-9]{1,20}", arguments[6]) is not None, "role numeric arguments")
    uid, gid, deadline = int(arguments[2]), int(arguments[3]), float(arguments[4])
    ordinary = arguments[0] == "--ordinary"
    parent = canonical(arguments[5])
    require(0 < remaining(deadline) <= WATCHDOG_SECONDS
            and parent.name == "fixture" and parent.parent.parent == WORKSPACE
            and re.fullmatch("[0-9a-f]{24}-null-" + re.escape(arguments[1]), parent.parent.name) is not None,
            "fixed fixture path/deadline")
    if not ordinary:
        require(context.get("SUDO_UID") == str(uid) and context.get("SUDO_GID") == str(gid),
                "sudo caller binding")
    else:
        require("SUDO_UID" not in context and "SUDO_GID" not in context, "ordinary route without sudo")
    binding = life._FixtureBinding(arguments[1], uid, gid, deadline, int(arguments[6]),
                                   int(arguments[7]), int(arguments[8]))
    info = os.lstat(parent)
    require(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino, info.st_uid, info.st_gid) ==
            (binding.device, binding.inode, uid, gid) and stat.S_IMODE(info.st_mode) == 0o700,
            "role fixture identity")
    return binding, parent, ordinary


def fixed_argv(budgeting, binding, parent, backend):
    require(backend in ("ordinary", "restricted") and binding.mode in MODES
            and canonical(PROGRAM) == Path(__file__).resolve(), "fixed program identity")
    # The ordinary self-map remains outside the same private PID1 enclosure.
    prefix = [*ORDINARY_PREFIX] if backend == "ordinary" else []
    return [
        *prefix, *budgeting.NAMESPACE_LAUNCHER, "/usr/bin/python3", "-I", "-S", "-B", str(PROGRAM),
        "--ordinary" if backend == "ordinary" else "--reaper", binding.mode,
        str(binding.uid), str(binding.gid), str(binding.deadline),
        str(parent), str(binding.device), str(binding.inode), str(binding.owner),
    ]


class HostDirectories:
    """C's two original directories; never adopt or recursively remove a late object."""

    def __init__(self, name, uid, gid, life, report, deadline):
        self.workspace, self.name, self.uid, self.gid = WORKSPACE, name, uid, gid
        self.life, self.report, self.deadline = life, report, deadline
        self.fds, self.identities = {}, {}
        self.attempted = {"container": False, "fixture": False}
        self.created = {"container": None, "fixture": None}
        self.removal_attempted = {"container": False, "fixture": False}
        self.removed = {"container": None, "fixture": None}
        self.mount_namespace = None

    @property
    def fixture(self):
        return self.workspace / self.name / "fixture"

    def open(self, slot, name, *, parent=None):
        require(slot in ("workspace", "container", "fixture", "check") and slot not in self.fds,
                "C descriptor slot")

        def acquire():
            descriptor = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=None if parent is None else self.fds[parent],
            )
            require(integer(descriptor, 3, 0x7FFFFFFF) and descriptor not in self.fds.values(),
                    "distinct acquired C descriptor")
            self.fds[slot] = descriptor

        self.life.finish_cleanup([("setup", acquire)], report=self.report)
        return self.fds[slot]

    def close(self, slot):
        descriptor = self.fds.pop(slot, None)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException:
                self.report.unsure("ownership")
                raise

    def create(self):
        self.mount_namespace = namespace("mnt")
        descriptor = self.open("workspace", self.workspace)
        self.identities["workspace"] = directory_identity(descriptor)
        for slot, name, parent in (("container", self.name, "workspace"), ("fixture", "fixture", "container")):
            remaining(self.deadline)

            def acquire(slot=slot, name=name, parent=parent):
                self.attempted[slot] = True
                os.mkdir(name, 0o700, dir_fd=self.fds[parent])
                self.created[slot] = True
                info = os.stat(name, dir_fd=self.fds[parent], follow_symlinks=False)
                expected = DirectoryIdentity(*directory_metadata(info), self.identities[parent].mount)
                require(expected.mode == stat.S_IFDIR | 0o700 and expected.uid == self.uid
                        and expected.gid == self.gid and expected.device == self.identities[parent].device,
                        "new ordinary-owned directory")
                descriptor = self.open(slot, name, parent=parent)
                require(directory_identity(descriptor) == expected and not os.listdir(descriptor),
                        "new directory acquisition identity")
                self.identities[slot] = expected

            self.life.finish_cleanup([("setup", acquire)], report=self.report)

    def check_fds(self, baseline):
        actual = fd_inventory()
        require(actual.keys() == baseline.keys() | set(self.fds.values())
                and all(actual[fd] == value for fd, value in baseline.items()), "C unknown or replaced FD")
        for slot, descriptor in self.fds.items():
            known = self.identities.get(slot)
            require(known is not None and actual[descriptor] ==
                    (known.device, known.inode, stat.S_IFDIR, os.O_RDONLY), "C owned directory FD")

    def check_directory(self, slot, name, parent):
        require(slot in self.fds and slot in self.identities and parent in self.fds
                and parent in self.identities, "original cleanup pins unavailable")
        require(namespace("mnt") == self.mount_namespace, "C mount context changed")
        require(directory_identity(self.fds["workspace"]) == self.identities["workspace"]
                and directory_metadata(os.stat(self.workspace, follow_symlinks=False)) ==
                self.identities["workspace"].metadata(), "workspace ancestry changed")
        require(directory_identity(self.fds[parent]) == self.identities[parent]
                and directory_identity(self.fds[slot]) == self.identities[slot], "original directory pin changed")
        if parent == "container":
            require(directory_metadata(os.stat(self.name, dir_fd=self.fds["workspace"], follow_symlinks=False)) ==
                    self.identities["container"].metadata(), "container ancestry replacement")
        require(directory_metadata(os.stat(name, dir_fd=self.fds[parent], follow_symlinks=False)) ==
                self.identities[slot].metadata(), "cleanup path replacement")
        descriptor = self.open("check", name, parent=parent)
        primary = None
        try:
            require(directory_identity(descriptor) == self.identities[slot]
                    and not os.listdir(descriptor), "original directory not empty or still mounted")
        except BaseException as error:
            primary = error
            raise
        finally:
            self.life.finish_cleanup([("cleanup", lambda: self.close("check"))],
                                     primary=primary, report=self.report)

    def remove(self, slot, name, parent, lifecycle_closed):
        if not self.attempted[slot]:
            return
        require(lifecycle_closed, "lifecycle custody not closed")
        require(not self.removal_attempted[slot] and self.created[slot] is True,
                "unconfirmed directory creation/removal")
        if slot == "container":
            require(not self.attempted["fixture"] or self.removed["fixture"] is True,
                    "fixture retained or removal uncertain")
        self.check_directory(slot, name, parent)
        self.close(slot)
        require(directory_metadata(os.stat(name, dir_fd=self.fds[parent], follow_symlinks=False)) ==
                self.identities[slot].metadata(), "cleanup path changed after pin close")
        self.removal_attempted[slot] = True
        try:
            os.rmdir(name, dir_fd=self.fds[parent])
        except BaseException:
            self.report.unsure("ownership")
            raise
        self.removed[slot] = True

    def cleanup(self, lifecycle_closed, baseline):
        safe, primary = False, None
        try:
            self.check_fds(baseline)
            safe = lifecycle_closed
        except BaseException as error:
            primary = error
            self.report.unsure("ownership")
            self.report.error("ownership", error)
        actions = [
            ("ownership", lambda: self.remove("fixture", "fixture", "container", safe)),
            ("cleanup", lambda: self.close("fixture")),
            ("ownership", lambda: self.remove("container", self.name, "workspace", safe)),
            ("cleanup", lambda: self.close("check")),
            ("cleanup", lambda: self.close("container")),
            ("cleanup", lambda: self.close("workspace")),
        ]
        try:
            self.life.finish_cleanup(actions, primary=primary, report=self.report)
        finally:
            if primary is not None:
                raise primary


def classify_availability(result):
    require(type(result) is subprocess.CompletedProcess and result.args == list(AVAILABILITY)
            and type(result.returncode) is int and type(result.stdout) is bytes and type(result.stderr) is bytes
            and len(result.stdout) + len(result.stderr) <= FRAME_BYTES, "availability result shape")
    if result.returncode == 0 and result.stdout == result.stderr == b"":
        return "ordinary"
    if result.returncode == 1 and result.stdout == b"" and result.stderr in PERMISSION_UNAVAILABLE:
        return "restricted"
    raise Refusal("unexpected availability result")


def new_budget(budgeting, started):
    require(type(started) in (int, float) and math.isfinite(started)
            and started <= time.monotonic() < started + WATCHDOG_SECONDS, "original coordinator start")
    budget = budgeting.ProbeBudget(budgeting.Limits(
        seconds=WATCHDOG_SECONDS, runs=2, states=2, pending=1, entries=52,
        cache_bytes=552960, process_output_bytes=CAPTURE_BYTES, output_bytes=CAPTURE_BYTES,
        sandbox_bytes=FIXTURE_BYTES,
    ))
    budget.started = started
    budget.remaining()
    return budget


def run_capture(budget, owner, binding, parent):
    budgeting = sys.modules[type(budget).__module__]
    require(type(budget) is budgeting.ProbeBudget and budget.limits.seconds == WATCHDOG_SECONDS
            and binding.deadline == budget.deadline and 0 < budget.remaining() <= WATCHDOG_SECONDS,
            "single original fixed budget deadline")
    owner._bind_fixture(binding)
    return budget.run(
        fixed_argv(budgeting, binding, parent, "restricted"), env=dict(ENVIRONMENT), cwd=SOURCE,
        output_limit=CAPTURE_BYTES, privileged=True, outcome=owner,
    )


def capture_complete(snapshot, mode):
    if (
        snapshot.qualified or snapshot.outer_returncode != 0 or snapshot.outer_error is not None
        or not snapshot.api_returned or not snapshot.reaped or not snapshot.capture_complete
        or not snapshot.capture_available or not snapshot.custody_complete or not snapshot.cleanup.complete
        or any(frame is None for frame in snapshot.frames)
    ):
        return False
    rb, ra, lb, la = snapshot.frames
    if not (
        rb.kind == lb.kind == "normal-exit" and rb.stage == "worker" and rb.setup_status == 0
        and rb.status == ra.status == lb.status == la.status == 0
        and ra.disposition == la.disposition == "return" and ra.cleanup.complete and la.cleanup.complete
        and rb.result is not None
    ):
        return False
    validate_mode(rb.result, mode)
    return True


def lifecycle_closed(snapshot, budget):
    return bool(
        budget.closed and not budget.children and snapshot is not None
        and snapshot.capture_available and snapshot.capture_complete and snapshot.custody_complete
        and snapshot.reaped and snapshot.cleanup.complete
        and all(snapshot.frames[index] is not None and snapshot.frames[index].cleanup.complete
                for index in (1, 3))
    )


class Coordinator:
    def __init__(self, budgeting, life, mode, directory, started):
        self.budgeting, self.life, self.mode, self.directory = budgeting, life, mode, directory
        self.budget = new_budget(budgeting, started)
        self.report = life._CleanupReport("C", self.budget.deadline)
        self.owner = self.directories = self.primary = self.backend = self.baseline = None
        self.revision = self.result = None
        self.invoked = self.returned = self.selection_returned = False
        self.closed_lifecycle = self.fd_restored = self.source_checked = False
        self.facts = {
            "backend": None, "stage": "admission", "first_error": None,
            "first_error_stage": None,
            "availability_status": None, "outer_status": None, "observations": None,
        }

    def save_error(self, error, stage=None):
        self.budget.failed = True
        if self.primary is None:
            self.primary = error
            self.facts["first_error"] = self.life._error_value(error)
            self.facts["first_error_stage"] = self.facts["stage"] if stage is None else stage
        elif error is not self.primary:
            self.life._forget_error(error)

    def snapshot(self):
        return None if self.owner is None or not self.owner._terminal else self.owner.snapshot()

    def close_budget(self):
        self.budget.close(**({} if self.owner is None else {"report": self.owner._cleanup}))

    def inspect_lifecycle(self):
        view = self.snapshot()
        try:
            if self.owner is not None and self.owner._spent:
                self.closed_lifecycle = lifecycle_closed(view, self.budget)
            else:
                self.closed_lifecycle = bool(
                    self.budget.closed and not self.budget.children and self.selection_returned
                    and (not self.invoked or self.returned) and self.report.value().complete
                )
        finally:
            view = None
        if not self.closed_lifecycle:
            self.report.unsure("ownership")

    def source_after(self):
        if self.closed_lifecycle and self.revision is not None:
            source_revision(self.budget.deadline, self.revision)
            self.source_checked = True

    def cleanup_directories(self):
        if self.directories is not None:
            self.directories.cleanup(self.closed_lifecycle, self.baseline)

    def check_final_fds(self):
        if self.baseline is not None:
            self.fd_restored = fd_inventory() == self.baseline
            require(self.fd_restored, "C final FD inventory")

    def run(self):
        global _cleanup_life, _cleanup_report
        previous = _cleanup_life, _cleanup_report
        _cleanup_life, _cleanup_report = self.life, self.report
        view = completed = None
        handlers = {}
        try:
            for signum in self.life.TERMINATING:
                handlers[signum] = signal.signal(signum, self.life.interrupted)
            self.facts["stage"] = "caller"
            uid, gid = os.getuid(), os.getgid()
            require(uid > 0 and gid > 0 and os.getresuid() == (uid,) * 3
                    and os.getresgid() == (gid,) * 3, "ordinary coordinator")
            name = fixture_name(self.directory, self.mode)
            self.facts["stage"] = "caller-state"
            initial = self_state()
            require(initial["uid"] == (uid,) * 4 and initial["gid"] == (gid,) * 4
                    and all(initial["caps"][index] == 0 for index in (0, 1, 2, 4)),
                    "ordinary coordinator authority")
            self.facts["stage"] = "entry-descriptors"
            self.baseline = withdraw_entry_fifos(fd_inventory(ordinary_entry=True), self.report)
            self.facts["stage"] = "environment"
            verify_environment()
            self.facts["stage"] = "source-before"
            self.revision = source_revision(self.budget.deadline)
            self.facts["stage"] = "system-executables"
            for path in ("/usr/bin/unshare", "/usr/bin/true", "/usr/bin/python3"):
                self.life.ordinary_executable(path)
            self.facts["stage"] = "admission"
            self.budget.charge("sandbox", FIXTURE_BYTES)
            self.budget.charge("control", 8 * RECORD_BYTES)
            self.facts["stage"] = "selection"
            self.budget.plan(1)
            # This fixed control operation spends control bytes, not the selected
            # mode's unchanged 256 KiB output/custody subdivision.
            completed = self.budget.run(
                list(AVAILABILITY), env=dict(ENVIRONMENT), cwd=SOURCE,
                output_limit=FRAME_BYTES, category="control",
            )
            self.selection_returned = True
            if type(completed) is subprocess.CompletedProcess and self.life._status(completed.returncode):
                self.facts["availability_status"] = completed.returncode
            self.budget.remaining()
            self.backend = classify_availability(completed)
            self.facts["backend"] = self.backend
            completed = None
            require(self.report.value().complete, "selection cleanup")
            if self.backend == "restricted":
                require(initial["uid_map"] == initial["gid_map"] == FULL_MAP, "restricted full-map coordinator")
                self.owner = self.budget.reserve_outcome(output_limit=CAPTURE_BYTES)
                self.report = self.owner._cleanup
                _cleanup_report = self.report
            else:
                self.budget.plan(1)
            self.facts["stage"] = "acquisition"
            self.directories = HostDirectories(name, uid, gid, self.life, self.report, self.budget.deadline)
            self.directories.create()
            self.directories.check_fds(self.baseline)
            original = self.directories.identities["fixture"]
            binding = self.life._FixtureBinding(
                self.mode, uid, gid, self.budget.deadline, original.device, original.inode, original.uid,
            )
            self.facts["stage"], self.invoked = "launch", True
            if self.backend == "restricted":
                completed = run_capture(self.budget, self.owner, binding, self.directories.fixture)
            else:
                completed = self.budget.run(
                    fixed_argv(self.budgeting, binding, self.directories.fixture, "ordinary"),
                    env=dict(ENVIRONMENT), cwd=SOURCE, output_limit=CAPTURE_BYTES,
                )
            self.returned = True
            if type(completed) is subprocess.CompletedProcess and self.life._status(completed.returncode):
                self.facts["outer_status"] = completed.returncode
            self.facts["stage"] = "capture"
            self.budget.remaining()
            if self.owner is not None:
                completed = None
                view = self.snapshot()
                require(view is not None and capture_complete(view, self.mode), "complete restricted capture")
                self.result = view.frames[0].result
            else:
                require(type(completed) is subprocess.CompletedProcess and type(completed.returncode) is int
                        and completed.returncode == 0 and type(completed.stdout) is bytes
                        and completed.stderr == b"", "ordinary normal mode result")
                self.result = self.life._private_worker_record(completed.stdout)
                validate_mode(self.result, self.mode)
        except BaseException as error:
            self.save_error(error)
        finally:
            view = completed = None
        try:
            self.life.finish_cleanup([
                ("leader", self.close_budget), ("ownership", self.inspect_lifecycle),
                ("ownership", self.source_after), ("ownership", self.cleanup_directories),
                ("cleanup", self.check_final_fds),
            ], primary=self.primary, handlers=handlers, report=self.report)
        except BaseException as error:
            self.save_error(error, "cleanup")
        try:
            view = self.snapshot()
            if view is not None:
                self.facts["outer_status"] = view.outer_returncode
                self.facts["observations"] = [
                    None if frame is None else {
                        "role": frame.role, "phase": frame.phase, "kind": frame.kind, "stage": frame.stage,
                        "status": frame.status, "error": frame.error, "setup_status": frame.setup_status,
                        "disposition": frame.disposition,
                        "cleanup": None if frame.cleanup is None else self.life._cleanup_wire(frame.cleanup),
                    }
                    for frame in view.frames
                ]
                self.facts["capture_error"] = view.outer_error
                self.facts["custody_complete"] = view.custody_complete
                require(view.cleanup.complete, "outcome cleanup")
        except BaseException as error:
            self.save_error(error, "snapshot")
        finally:
            view = None
            if self.owner is not None:
                try:
                    self.owner.release()
                except BaseException as error:
                    self.report.unsure("ownership")
                    self.save_error(error, "outcome-release")
            _cleanup_life, _cleanup_report = previous
        passed = bool(
            self.primary is None and self.result is not None and self.closed_lifecycle
            and self.fd_restored and self.source_checked and self.report.value().complete
            and self.directories is not None and not self.directories.fds
            and all(value is True for value in self.directories.removed.values())
            and (self.owner is None or self.owner._released)
        )
        self.facts["cleanup"] = self.life._cleanup_wire(self.report.value())
        self.facts["runs"], self.facts["states"] = self.budget.runs, self.budget.states
        self.facts["lifecycle_closed"] = self.closed_lifecycle
        if self.primary is not None:
            self.life._forget_error(self.primary)
            self.primary = None
        if not passed:
            self.result = None
            raise FixtureFailure(self.facts)
        result, self.result = self.result, None
        return result


def coordinator_arguments(arguments, life):
    require(len(arguments) == 10 and arguments[0] == "--coordinator" and arguments[1] in MODES,
            "fixed coordinator arguments")
    require(all(re.fullmatch("[1-9][0-9]{0,19}", arguments[index]) for index in (2, 3, 8, 9))
            and re.fullmatch("[0-9]{1,20}", arguments[7]) is not None, "coordinator numeric arguments")
    started, deadline = float(arguments[4]), float(arguments[5])
    require(math.isfinite(started) and math.isfinite(deadline) and deadline == started + WATCHDOG_SECONDS
            and started <= time.monotonic() < deadline, "inherited coordinator deadline")
    directory = canonical(arguments[6])
    fixture_name(directory, arguments[1])
    binding = life._FixtureBinding(
        arguments[1], int(arguments[2]), int(arguments[3]), deadline,
        int(arguments[7]), int(arguments[8]), int(arguments[9]),
    )
    require(os.getresuid() == (binding.uid,) * 3 and os.getresgid() == (binding.gid,) * 3
            and dict(os.environ) == ENVIRONMENT and canonical(Path.cwd()) == SOURCE,
            "ordinary coordinator origin")
    info = os.lstat(directory)
    require(stat.S_ISDIR(info.st_mode)
            and (info.st_dev, info.st_ino, info.st_uid, info.st_gid) ==
            (binding.device, binding.inode, binding.uid, binding.gid), "coordinator original directory")
    actual = fd_inventory()
    require(set(actual) == {0, 1, 2} and all(value[2] == stat.S_IFIFO for value in actual.values())
            and tuple(actual[fd][3] for fd in (0, 1, 2)) == (os.O_RDONLY, os.O_WRONLY, os.O_WRONLY)
            and len({value[:2] for value in actual.values()}) == 3
            and death_signal() == signal.SIGKILL, "coordinator stdio/death binding")
    return binding, directory, started


def write_coordinator_result(value, deadline):
    data = json_bytes(value)
    require(len(data) <= RECORD_BYTES, "coordinator semantic output bound")
    os.set_blocking(1, False)
    offset = 0
    while offset < len(data):
        wait = remaining(deadline)
        try:
            size = os.write(1, data[offset:offset + FRAME_BYTES])
        except BlockingIOError:
            select.select([], [1], [], wait)
            continue
        require(type(size) is int and 0 < size <= min(FRAME_BYTES, len(data) - offset),
                "coordinator semantic write")
        offset += size


def coordinator_failure(data, life):
    require(type(data) is bytes and 0 < len(data) <= RECORD_BYTES and data.isascii(),
            "coordinator failure capture")
    value = json.loads(data, object_pairs_hook=life._pairs, parse_constant=life._constant,
                       parse_float=life._finite_float)
    required = {
        "backend", "stage", "first_error", "first_error_stage", "availability_status",
        "outer_status", "observations", "cleanup", "runs", "states", "lifecycle_closed",
    }
    require(type(value) is dict and required <= value.keys()
            and value.keys() <= required | {"capture_error", "custody_complete"},
            "coordinator failure fields")
    stages = {
        "admission", "caller", "caller-state", "entry-descriptors", "environment", "source-before",
        "system-executables", "selection", "acquisition", "launch", "capture",
        "cleanup", "snapshot", "outcome-release",
    }
    require(value["backend"] in (None, "ordinary", "restricted") and value["stage"] in stages
            and value["first_error_stage"] in stages | {None}
            and all(value[key] is None or life._status(value[key])
                    for key in ("availability_status", "outer_status"))
            and all(integer(value[key], 0, 3) for key in ("runs", "states"))
            and type(value["lifecycle_closed"]) is bool, "coordinator failure values")
    life._error_read(value["first_error"])
    life._cleanup_read(value["cleanup"])
    if "capture_error" in value:
        life._error_read(value["capture_error"])
    if "custody_complete" in value:
        require(type(value["custody_complete"]) is bool, "coordinator custody flag")
    observations = value["observations"]
    require(observations is None or type(observations) is list and len(observations) == 4,
            "coordinator observations")
    if observations is not None:
        for slot, row in enumerate(observations):
            if row is None:
                continue
            require(type(row) is dict and row.keys() == {
                "role", "phase", "kind", "stage", "status", "error", "setup_status", "disposition", "cleanup",
            }, "coordinator observation fields")
            require((row["role"], row["phase"]) ==
                    (("R" if slot < 2 else "L"), ("after" if slot % 2 else "before"))
                    and row["kind"] in (None, "normal-exit", "exception", "unavailable")
                    and row["stage"] in (*life._STAGES, None)
                    and row["disposition"] in (None, "return", "raise")
                    and all(row[key] is None or life._status(row[key]) for key in ("status", "setup_status")),
                    "coordinator observation values")
            life._error_read(row["error"])
            if row["cleanup"] is not None:
                life._cleanup_read(row["cleanup"])
    return value


class Enclosure:
    """The test driver's one fixed ordinary C child, not another budget run."""

    def __init__(self, life, mode, directory, started):
        require(type(started) in (int, float) and math.isfinite(started), "enclosure start")
        self.life, self.mode, self.directory, self.started = life, mode, directory, started
        self.deadline = started + WATCHDOG_SECONDS
        self.wait_deadline, self.cleanup_deadline = started + WAIT_SECONDS, started + OUTER_SECONDS
        self.report = life._CleanupReport("C", self.cleanup_deadline)
        self.child = self.pidfd = self.directory_fd = self.selector = self.baseline = None
        self.identity = self.binding = self.primary = self.result = None
        self.status = self.wait_value = self.reap_status = None
        self.reaped = self.capture_complete = self.fd_restored = self.launch_attempted = False
        self.buffers = (bytearray(RECORD_BYTES), bytearray(CAPTURE_BYTES - RECORD_BYTES))
        self.sizes = [0, 0]
        self.facts = {"stage": "setup", "first_error": None, "first_error_stage": None,
                      "status": None, "wait": None, "reap_status": None, "coordinator_failure": None}

    def save_error(self, error, stage):
        if self.primary is None:
            self.primary = error
            self.facts["first_error"], self.facts["first_error_stage"] = self.life._error_value(error), stage
        elif error is not self.primary:
            self.life._forget_error(error)

    def close_fd(self, name):
        descriptor = getattr(self, name)
        setattr(self, name, None)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException:
                self.report.unsure("ownership")
                raise

    def prepare(self):
        uid, gid = os.getuid(), os.getgid()
        require(uid > 0 and gid > 0 and os.getresuid() == (uid,) * 3
                and os.getresgid() == (gid,) * 3, "ordinary enclosure owner")
        fixture_name(self.directory, self.mode)
        value = self_state()
        require(value["uid"] == (uid,) * 4 and value["gid"] == (gid,) * 4
                and all(value["caps"][index] == 0 for index in (0, 1, 2, 4)),
                "enclosure has no setup authority")
        self.baseline = withdraw_entry_fifos(fd_inventory(ordinary_entry=True), self.report)
        self.life.ordinary_executable("/usr/bin/python3")
        require(canonical(PROGRAM) == Path(__file__).resolve(), "enclosure fixed source")

        def pin():
            self.directory_fd = os.open(
                self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )

        self.life.finish_cleanup([("setup", pin)], report=self.report)
        self.identity = directory_identity(self.directory_fd)
        require(self.identity.uid == uid and self.identity.gid == gid, "enclosure owned directory")
        self.binding = self.life._FixtureBinding(
            self.mode, uid, gid, self.deadline, self.identity.device, self.identity.inode, uid,
        )
        remaining(self.deadline)

    def arguments(self):
        require(self.binding is not None, "enclosure bound launch")
        value = self.binding
        return [
            "/usr/bin/python3", "-I", "-S", "-B", str(PROGRAM), "--coordinator", self.mode,
            str(value.uid), str(value.gid), str(self.started), str(self.deadline), str(self.directory),
            str(value.device), str(value.inode), str(value.owner),
        ]

    def launch(self):
        require(not self.launch_attempted and self.child is None, "one enclosed coordinator")
        remaining(self.deadline)
        parent = os.getpid()
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())

        def acquire():
            self.launch_attempted = True
            self.child = subprocess.Popen(
                self.arguments(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=dict(ENVIRONMENT), cwd=SOURCE, close_fds=True, pass_fds=(), start_new_session=True,
                preexec_fn=lambda: self.life.parent_death(parent, mask),
            )
            require(integer(self.child.pid, 1, 0x7FFFFFFF), "owned coordinator PID")
            self.pidfd = os.pidfd_open(self.child.pid)

        self.life.finish_cleanup([("setup", acquire)], report=self.report)
        info = read_file("/proc/self/fdinfo/" + str(self.pidfd))
        pids = [line.split(b":", 1)[1].strip() for line in info.splitlines() if line.startswith(b"Pid:")]
        require(pids == [str(self.child.pid).encode()], "enclosure pidfd binding")
        identities = []
        for name, access in (("stdin", os.O_WRONLY), ("stdout", os.O_RDONLY), ("stderr", os.O_RDONLY)):
            descriptor = getattr(self.child, name).fileno()
            info = os.fstat(descriptor)
            require(stat.S_ISFIFO(info.st_mode)
                    and fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE == access,
                    "enclosure capture direction")
            identities.append((info.st_dev, info.st_ino))
        require(len(set(identities)) == 3, "enclosure distinct capture pipes")

    def collect(self):
        self.selector = selectors.DefaultSelector()
        for index, stream in enumerate((self.child.stdout, self.child.stderr)):
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ, index)
        self.selector.register(self.pidfd, selectors.EVENT_READ, 2)
        eof = 0
        while eof != 3 or self.status is None:
            remaining(self.wait_deadline)
            if self.status is None:
                observed = self.waitable()
                if observed is not None:
                    self.wait_value, self.status = self.life._wait_value(self.child.pid, observed)
                    self.selector.unregister(self.pidfd)
            if eof == 3 and self.status is not None:
                break
            for key, _ in self.selector.select(remaining(self.wait_deadline)):
                if key.data == 2:
                    continue
                index = key.data
                available = len(self.buffers[index]) - self.sizes[index]
                try:
                    chunk = os.read(key.fd, min(FRAME_BYTES, available + 1))
                except BlockingIOError:
                    continue
                remaining(self.wait_deadline)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    eof |= 1 << index
                    continue
                require(len(chunk) <= available, "enclosure capture overflow")
                end = self.sizes[index] + len(chunk)
                self.buffers[index][self.sizes[index]:end] = chunk
                self.sizes[index] = end
        self.capture_complete = True
        self.reap(self.wait_deadline)
        remaining(self.wait_deadline)

    def waitable(self):
        try:
            observed = os.waitid(os.P_PID, self.child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if observed is not None:
                self.life._wait_value(self.child.pid, observed)
            return observed
        except BaseException:
            self.report.unsure("ownership")
            raise

    def reap(self, deadline):
        require(self.child is not None, "owned coordinator reap")
        observed = self.waitable()
        try:
            result = self.child.wait(timeout=max(0, deadline - time.monotonic()))
            require(self.life._status(result) and self.child.returncode == result, "coordinator reap agreement")
            require(observed is None or self.life._wait_value(self.child.pid, observed)[1] == result,
                    "coordinator kernel reap agreement")
            require(self.status is None or result == self.status, "coordinator first wait agreement")
            self.reap_status, self.reaped = result, True
        except BaseException:
            if observed is not None and self.life._status(self.child.returncode) and (
                self.life._wait_value(self.child.pid, observed)[1] == self.child.returncode
            ):
                self.reaped, self.reap_status = True, self.child.returncode
            else:
                self.report.unsure("leader")
            raise

    def stop(self):
        if self.child is None or self.reaped:
            return
        # A still-waitable child pins the numeric identity even if pidfd
        # acquisition failed. No poll, group kill, or foreign-child adoption.
        def signal_child():
            self.waitable()
            try:
                if self.pidfd is None:
                    os.kill(self.child.pid, signal.SIGKILL)
                else:
                    signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass

        self.life.finish_cleanup([
            ("leader", signal_child), ("wait", lambda: self.reap(self.cleanup_deadline)),
        ], report=self.report)

    def close_pidfd(self):
        if self.child is not None and not self.reaped:
            self.report.unsure("ownership")
            return
        self.close_fd("pidfd")

    def close_selector(self):
        selector, self.selector = self.selector, None
        if selector is not None:
            try:
                selector.close()
            except BaseException:
                self.report.unsure("selector")
                raise

    def check_directory(self):
        if self.identity is not None:
            require(directory_identity(self.directory_fd) == self.identity
                    and directory_metadata(os.lstat(self.directory)) == self.identity.metadata(),
                    "enclosure directory changed")

    def check_fds(self):
        if self.baseline is not None:
            self.fd_restored = fd_inventory() == self.baseline
            require(self.fd_restored, "enclosure final descriptors")

    def run(self):
        global _cleanup_life, _cleanup_report
        previous = _cleanup_life, _cleanup_report
        _cleanup_life, _cleanup_report = self.life, self.report
        handlers = {}
        try:
            for signum in self.life.TERMINATING:
                handlers[signum] = signal.signal(signum, self.life.interrupted)
            self.prepare()
            self.facts["stage"] = "launch"
            self.launch()
            self.facts["stage"] = "capture"
            self.collect()
            if self.status != 0:
                data = bytes(memoryview(self.buffers[0])[:self.sizes[0]])
                if data:
                    self.facts["coordinator_failure"] = coordinator_failure(data, self.life)
                raise Refusal("enclosed coordinator failed")
            require(self.sizes[1] == 0, "unexpected coordinator stderr")
            self.result = self.life._private_worker_record(bytes(memoryview(self.buffers[0])[:self.sizes[0]]))
            validate_mode(self.result, self.mode)
        except BaseException as error:
            self.save_error(error, self.facts["stage"])
        self.buffers = None
        actions = [("selector", self.close_selector)]
        if self.child is not None:
            actions.extend([
                ("lifetime", lambda: self.life._close_stream(self.child, "stdin", self.report)),
                ("leader", self.stop),
                ("stdout", lambda: self.life._close_stream(self.child, "stdout", self.report)),
                ("stderr", lambda: self.life._close_stream(self.child, "stderr", self.report)),
            ])
        actions.extend([
            ("pidfd", self.close_pidfd), ("ownership", self.check_directory),
            ("cleanup", lambda: self.close_fd("directory_fd")), ("cleanup", self.check_fds),
        ])
        try:
            self.life.finish_cleanup(actions, primary=self.primary, handlers=handlers, report=self.report)
        except BaseException as error:
            self.save_error(error, "cleanup")
        finally:
            _cleanup_life, _cleanup_report = previous
        if self.launch_attempted and (self.child is None or not self.reaped):
            self.report.unsure("ownership")
        if time.monotonic() > self.cleanup_deadline:
            self.report.unsure("wait")
        self.facts.update({
            "status": self.status, "wait": self.wait_value, "reap_status": self.reap_status,
            "reaped": self.reaped, "capture_complete": self.capture_complete,
            "cleanup": self.life._cleanup_wire(self.report.value()), "fd_restored": self.fd_restored,
        })
        if self.primary is not None:
            self.life._forget_error(self.primary)
            self.primary = None
        if self.result is None or self.facts["first_error"] is not None or not (
            self.reaped and self.capture_complete and self.fd_restored and self.report.value().complete
        ):
            self.result = None
            error = FixtureFailure(self.facts)
            if self.launch_attempted and not self.reaped:
                error.retained_enclosure = self
            raise error
        result, self.result = public_result(self.result), None
        return result


def run_fixture(mode, directory):
    started = time.monotonic()
    _, life = load_control()
    return Enclosure(life, mode, directory, started).run()


def main():
    require(sys.flags.isolated and sys.flags.no_site and not sys.flags.optimize, "isolated interpreter")
    budgeting, life = load_control()
    if sys.argv[1:2] == ["--coordinator"]:
        binding, directory, started = coordinator_arguments(sys.argv[1:], life)
        try:
            result = Coordinator(budgeting, life, binding.mode, directory, started).run()
        except FixtureFailure as error:
            facts = error.facts
            life._forget_error(error)
        else:
            write_coordinator_result(worker_wire(result), started + WAIT_SECONDS)
            return 0
        write_coordinator_result(facts, started + WAIT_SECONDS)
        return 125
    binding, parent, ordinary = role_arguments(sys.argv[1:], life, os.environ)
    return Bootstrap(life, binding, parent, ordinary=ordinary).run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        for attribute in ("__traceback__", "__context__", "__cause__"):
            BaseException.__setattr__(error, attribute, None)
        print("null-mount fixture refused", file=sys.stderr)
        raise SystemExit(125) from None
