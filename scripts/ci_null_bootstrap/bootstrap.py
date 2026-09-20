"""One fixed readonly mechanism, requiring a separate restricted-host launch freeze."""

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
import signal
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from types import SimpleNamespace


REPOSITORY = "laqieer/fireemblem8-expansion"
OWNER = "laqieer"
BRANCH = "diagnostic/issue-180-null-bootstrap-3"
WORKFLOW = ".github/workflows/issue180-null-bootstrap-3.yml"
ORIGINAL_WORKFLOW = ".github/workflows/issue180-null-bootstrap-1.yml"
SECOND_WORKFLOW = ".github/workflows/issue180-null-bootstrap-2.yml"
RUN_PREFIX = "issue180-null-bootstrap-3-"
BASE = "ec1dc8553419c8833a687fd8d4a6521a4e29ff7a"
PREPARATION = "20478394860b673b98fb32a4dd292fa0fc02a5d4"
RECOVERY = "3302f790e944e81be4ea0777682f5282e560fd9c"
FIRST_BOOTSTRAP = "496ed2ac184c48d6bdd6ec3f651183e67df9045b"
SECOND_BOOTSTRAP = "9d61413261cb813fd1be5e48080ac876eaf3eb85"
SOURCE = "c8b365da1be29bc58352cf1edb8b836a2cf18321"
PROGRAM = "scripts/ci_null_bootstrap/bootstrap.py"
FILES = frozenset({
    WORKFLOW, ORIGINAL_WORKFLOW, SECOND_WORKFLOW, PROGRAM, "scripts/ci_null_bootstrap/__init__.py",
    "scripts/ci_null_bootstrap/test_bootstrap.py", "scripts/ci_null_bootstrap/README.md",
})
ARTIFACTS = ("scope.json", "launch.json", "custody.json", "mode.json", "cleanup.json")
WATCHDOG_SECONDS, WAIT_SECONDS, OUTER_SECONDS = 30, 35, 45
FIXTURE_BYTES, CAPTURE_BYTES, FRAME_BYTES = 1048576, 262144, 4096
RECORD_BYTES, ARTIFACT_BYTES = 16384, 65536
FULL_MAP = ((0, 0, 4294967295),)
ENVIRONMENT = {
    "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1",
}
GIT_ENV = {
    **ENVIRONMENT, "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_COUNT": "0",
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
PLACEMENT = "tmpfs-on-original-fixture"
PREFLIGHT_REFUSALS = {
    "ordinary coordinator": "ordinary-id-binding",
    "ordinary full-map coordinator": "ordinary-state-binding",
    "initial C descriptors": "ordinary-fd-closure",
    "status duplicate": "status-duplicate",
    "status fields": "status-fields",
    "status shape": "status-shape",
    "map shape": "map-shape",
    "descriptor bound": "fd-bound",
    "descriptor name": "fd-name",
    "fixed file bound": "proc-file-bound",
    "entry FIFO identity changed": "entry-fifo-identity",
}


class Refusal(RuntimeError):
    """Finite diagnostic refusal; never interpolate source, environment or streams."""


def require(condition, tag):
    if not condition:
        raise Refusal(tag)


def remaining(deadline):
    require(type(deadline) in (int, float) and math.isfinite(deadline), "deadline")
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("bootstrap deadline")
    return value


def integer(value, low=0, high=(1 << 64) - 1):
    return type(value) is int and low <= value <= high


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


def read_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate field")
            result[key] = value
        return result

    def constant(_):
        raise Refusal("nonfinite JSON")

    require(type(data) is bytes and len(data) <= RECORD_BYTES, "record bound")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def read_file(path, bound=4096, *, directory=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        value = os.read(descriptor, bound + 1)
        require(len(value) <= bound, "fixed file bound")
        return value
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def close_temporary(descriptor, primary):
    def close():
        try:
            os.close(descriptor)
        except BaseException:
            if _cleanup_report is not None:
                _cleanup_report.unsure("cleanup")
            raise

    if _cleanup_report is not None:
        _cleanup_life.finish_cleanup([("cleanup", close)], primary=primary, report=_cleanup_report)
    else:
        # Pre-launch Git/plan inspection has no admitted run report yet.
        try:
            close()
        except BaseException as error:
            if primary is None:
                raise
            for name in ("__traceback__", "__context__", "__cause__"):
                BaseException.__setattr__(error, name, None)


def canonical(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve(strict=True), "canonical path")
    return path


def git(root, *arguments, deadline=None):
    # Only immutable source inspection; never a candidate command or a workload.
    result = subprocess.run(
        ["/usr/bin/git", "--no-pager", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",
         "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=GIT_ENV, timeout=5 if deadline is None else min(5, remaining(deadline)), check=False,
    )
    require(result.returncode == 0 and len(result.stdout) <= RECORD_BYTES, "source inspection")
    return result.stdout


def verify_checkout(root, sha, *, harness=False, deadline=None):
    canonical(root)
    require(git(root, "rev-parse", "HEAD", deadline=deadline).strip() == sha.encode(), "selected revision")
    git(root, "diff", "--quiet", "--no-ext-diff", "--ignore-submodules=none", sha, "--", deadline=deadline)
    if harness:
        chain = (sha, SECOND_BOOTSTRAP, FIRST_BOOTSTRAP, RECOVERY, PREPARATION, BASE)
        for child, parent in zip(chain, chain[1:]):
            parents = git(root, "rev-list", "--parents", "-n", "1", child, deadline=deadline).split()
            require(parents == [child.encode(), parent.encode()], "exact normal preparation lineage")
        rows = git(root, "diff", "--name-status", "-z", BASE, sha, deadline=deadline).split(b"\0")
        require(rows[-1:] == [b""] and len(rows) == 2 * len(FILES) + 1, "additive inventory")
        actual = list(zip(rows[:-1:2], rows[1:-1:2]))
        require(all(kind == b"A" for kind, _ in actual)
                and {path.decode("ascii") for _, path in actual} == FILES, "closed additive files")
        changed = git(root, "diff", "--name-status", "-z", SECOND_BOOTSTRAP, sha, deadline=deadline).split(b"\0")
        require(len(changed) >= 3 and len(changed) % 2 == 1 and changed[-1] == b"", "nonempty correction")
        delta = list(zip(changed[:-1:2], changed[1:-1:2]))
        allowed = {
            WORKFLOW: b"A", PROGRAM: b"M", "scripts/ci_null_bootstrap/test_bootstrap.py": b"M",
            "scripts/ci_null_bootstrap/README.md": b"M",
        }
        require((b"A", WORKFLOW.encode()) in delta
                and all(kind == allowed.get(path.decode("ascii")) for kind, path in delta)
                and len({path for _, path in delta}) == len(delta), "closed correction paths")
        for name in FILES:
            info = os.lstat(root / name)
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o644,
                    "harness regular-file mode")


def verify_environment(source):
    # Inspect this one literal, without importing authority or any graph consumer.
    tree = ast.parse(read_file(source / "scripts/validation_ownership/authority.py", 131072))
    values = [
        node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ENVIRONMENT" for target in node.targets)
    ]
    require(len(values) == 1 and ast.literal_eval(values[0]) == ENVIRONMENT, "selected environment")


def load_control(source):
    root = canonical(source / "scripts/validation_ownership")
    name = "_issue180_bootstrap_selected"
    require(name not in sys.modules, "single selected control import")
    spec = importlib.util.spec_from_file_location(
        name, root / "__init__.py", submodule_search_locations=[str(root)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    budget = __import__(name + ".budget", fromlist=["budget"])
    life = __import__(name + ".lifecycle", fromlist=["lifecycle"])
    for leaf in ("budget", "lifecycle", "producer_channel"):
        module = sys.modules[name + "." + leaf]
        require(Path(module.__file__).resolve() == root / (leaf + ".py"), "control code origin")
    require(budget._lifecycle is life and life._FRAME_BYTES == FRAME_BYTES, "selected custody API")
    return budget, life


def identity(event, context):
    sha, run = context.get("GITHUB_SHA", ""), context.get("GITHUB_RUN_ID", "")
    require(type(event) is dict, "push object")
    repository, sender = event.get("repository"), event.get("sender")
    require(type(repository) is dict and type(sender) is dict, "push identity")
    expected = {
        "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/" + BRANCH,
        "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_ACTOR": OWNER, "GITHUB_TRIGGERING_ACTOR": OWNER,
        "GITHUB_RUN_NUMBER": "1", "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
        "GITHUB_WORKFLOW_REF": REPOSITORY + "/" + WORKFLOW + "@refs/heads/" + BRANCH,
        "GITHUB_WORKFLOW_SHA": sha,
    }
    require(
        all(context.get(key) == value for key, value in expected.items())
        and re.fullmatch("[0-9a-f]{40}", sha) is not None
        and re.fullmatch("[1-9][0-9]{0,19}", run) is not None
        and event.get("ref") == expected["GITHUB_REF"] and event.get("before") == "0" * 40
        and event.get("after") == sha and event.get("created") is True
        and event.get("deleted") is False and repository.get("full_name") == REPOSITORY
        and repository.get("private") is False and sender.get("login") == OWNER,
        "first public owner-created push/run1/attempt1",
    )
    return {
        "repository": REPOSITORY, "branch": BRANCH, "workflow": WORKFLOW,
        "base": BASE, "preparation": PREPARATION, "recovery": RECOVERY,
        "first_bootstrap": FIRST_BOOTSTRAP, "second_bootstrap": SECOND_BOOTSTRAP,
        "source": SOURCE, "harness": sha, "run_id": run,
        "run_number": 1, "run_attempt": 1, "creation_allocation_consumed": True,
        "never_merge": True, "qualified": False, "seven_modes": False, "fixture_placement": PLACEMENT,
    }


def paths(context):
    workspace = canonical(context["GITHUB_WORKSPACE"])
    harness, source = workspace / "harness", workspace / "candidate"
    require(Path(__file__).resolve() == harness / PROGRAM, "committed program path")
    run = context["GITHUB_RUN_ID"]
    require(re.fullmatch("[1-9][0-9]{0,19}", run) is not None, "run identity")
    return harness, source, workspace / (RUN_PREFIX + run + "-records")


def write_record(output, name, value):
    require(name in ARTIFACTS, "artifact allowlist")
    data = json_bytes(value) + b"\n"
    require(len(data) <= RECORD_BYTES, "artifact bound")
    total = sum((output / item).stat().st_size for item in ARTIFACTS if (output / item).exists())
    require(total + len(data) <= ARTIFACT_BYTES, "artifact aggregate")
    descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600)
    try:
        require(os.write(descriptor, data) == len(data), "artifact short write")
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def libc():
    return ctypes.CDLL(None, use_errno=True)


def checked(result):
    if result < 0:
        raise OSError(ctypes.get_errno(), "fixed bootstrap syscall")
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


def namespace(name, *, directory=None):
    descriptor = os.open(("ns/" if directory is not None else "/proc/self/ns/") + name,
                         os.O_RDONLY | os.O_CLOEXEC, dir_fd=directory)
    try:
        return fd_identity(descriptor)
    finally:
        close_temporary(descriptor, sys.exc_info()[1])


def fd_identity(descriptor):
    value = os.fstat(descriptor)
    return value.st_dev, value.st_ino


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

    def wire(self):
        return [*self.metadata(), self.mount]


def directory_metadata(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def directory_identity(descriptor):
    info = os.fstat(descriptor)
    require(stat.S_ISDIR(info.st_mode), "directory type")
    return DirectoryIdentity(*directory_metadata(info), mount_id(descriptor))


def tmpfs_state(descriptor):
    # Linux native statfs ABI; the selected helper also requires Linux x86-64.
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


def verify_tmpfs(descriptor, original):
    mounted = directory_identity(descriptor)
    kind, capacity, flags = tmpfs_state(descriptor)
    require(mounted.metadata()[:2] != original.metadata()[:2] and mounted.mount != original.mount
            and mounted.mode == stat.S_IFDIR | 0o700 and mounted.uid == mounted.gid == 0
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


def validate_credentials(value, uid, gid, caps, *, nnp=1):
    require(value["uid"] == (uid,) * 4 and value["gid"] == (gid,) * 4
            and value["groups"] == () and value["caps"] == caps and value["nnp"] == nnp,
            "credential boundary")


def validate_preentry(value, binding, outer):
    validate_credentials(value, binding.uid, binding.gid, (0,) * 5)
    require(value["user"] == outer["user"] and value["mount"] == outer["mount"]
            and value["uid_map"] == FULL_MAP and value["gid_map"] == FULL_MAP
            and value["label"] == outer["label"], "full-map U0 worker")


def validate_target(value, binding, outer, target):
    require(value["uid"] == (0,) * 4 and value["gid"] == (0,) * 4 and value["groups"] == ()
            and value["nnp"] == 1 and value["uid_map"] == ((0, binding.uid, 1),)
            and value["gid_map"] == ((0, binding.gid, 1),)
            and value["user"] == target.user and value["user"] != outer["user"]
            and value["mount"] == target.mount and value["mount"] != outer["mount"]
            and value["label"] == outer["label"], "descendant worker")


def validate_local_setup(value):
    inheritable, permitted, effective, bounding_set, ambient = value["caps"]
    needed = (1 << CAP_SYS_ADMIN) | (1 << 18)  # CAP_SYS_CHROOT for target mount entry.
    require(inheritable == ambient == 0 and effective == permitted == bounding_set
            and effective & needed == needed, "descendant-local setup capabilities")


def fd_inventory():
    result = {}
    names = os.listdir("/proc/self/fd")
    require(len(names) <= 20, "descriptor bound")
    for name in names:
        require(name.isdecimal(), "descriptor name")
        descriptor = int(name)
        try:
            info = os.fstat(descriptor)
        except OSError as error:
            # The already-closed directory iterator is the sole disappearing FD.
            if error.errno != errno.EBADF:
                raise
            continue
        result[descriptor] = (
            info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE,
        )
    return result


def withdraw_entry_fifos(before, report):
    require(type(before) is dict and len(before) <= 20 and {0, 1, 2} <= before.keys()
            and all(integer(fd, 0, 0x7FFFFFFF) and type(value) is tuple and len(value) == 4
                    and all(type(part) is int for part in value)
                    and (fd <= 2 or value[2] == stat.S_IFIFO and value[3] in (0, 1, 2))
                    for fd, value in before.items()), "initial C descriptors")
    stdio = {fd: before[fd] for fd in (0, 1, 2)}
    pending = [(fd, before[fd]) for fd in sorted(before) if fd > 2]
    report["descriptors"] = [
        {"fd": fd, "state": "pending", "error": {"kind": None, "errno": None}}
        for fd, _ in pending
    ]
    primary = None
    report["phase"] = "disposal"
    for index, row in enumerate(report["descriptors"]):
        descriptor, original = pending[index]
        pending[index] = None
        try:
            row["state"] = "identity-refused"
            info = os.fstat(descriptor)
            actual = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
                      fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE)
            require(actual == original, "entry FIFO identity changed")
            row["state"] = "close-uncertain"
            os.close(descriptor)
            row["state"] = "closed"
        except BaseException as error:
            if primary is None:
                primary = error
            if row["state"] == "closed":
                row["state"] = "close-uncertain"
            row["error"]["kind"] = (
                "os-error" if isinstance(error, OSError) else
                "interrupt" if isinstance(error, KeyboardInterrupt) else
                "refusal" if isinstance(error, Refusal) else "other-error"
            )
            row["error"]["errno"] = (
                error.errno if isinstance(error, OSError) and integer(error.errno, 0, 4095) else None
            )
            if error is not primary:
                for attribute in ("__traceback__", "__context__", "__cause__"):
                    BaseException.__setattr__(error, attribute, None)
    if primary is not None:
        raise primary
    report["phase"] = "final-inventory"
    after = fd_inventory()
    require(after == stdio, "initial C descriptors")
    report["phase"], report["complete"] = "complete", True
    return after


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
        None if kind is None else os.fsencode(kind), flags,
        None if data is None else data,
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
        # A failed pidfd acquisition still leaves this exclusive waitable child.
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


def root_context():
    require(os.getpid() == 1 and os.getresuid() == (0, 0, 0)
            and os.getresgid() == (0, 0, 0), "private root setup role")
    # Before replacing proc, its host-relative PPid links lead R -> unshare -> L.
    # These are read-only ancestry observations, never signal or map targets.
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
            "exact launcher private topology")
    mount("proc", "/proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, "proc")
    require(os.readlink("/proc/self") == "1", "PID-aligned proc")
    value = self_state()
    require(value["pids"] == (1,) and value["parent"] == 0
            and value["uid_map"] == FULL_MAP and value["gid_map"] == FULL_MAP
            and value["user"] == ancestor["user"] and death_signal() == signal.SIGKILL,
            "full-map PID1 lifecycle")
    mountfd = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    try:
        require(fcntl.ioctl(mountfd, NS_GET_NSTYPE) == NEWNS, "private mount namespace type")
        owner = fcntl.ioctl(mountfd, NS_GET_USERNS)
        try:
            require(fd_identity(owner) == value["user"], "M0 owner is U0")
        finally:
            close_temporary(owner, sys.exc_info()[1])
    finally:
        close_temporary(mountfd, sys.exc_info()[1])
    needed = sum(1 << bit for bit in (CAP_KILL, CAP_SETGID, CAP_SETUID, CAP_SETPCAP, CAP_SYS_ADMIN))
    require(value["caps"][1] & needed == needed, "inherited setup capabilities")
    os.setgroups([])
    return value


class Bootstrap:
    """Closed R/N/W roles; no request dispatcher, callback payload or alternate backend."""

    NAMES = frozenset({
        "fixture", "tmpfs", "volume", "eof.read", "eof.write", "request.read", "request.write",
        "response.read", "response.write", "N.pidfd", "N.proc", "user", "mount",
        "uid_map", "gid_map", "setgroups", "gate.read", "gate.write",
        "worker.read", "worker.write", "W.pidfd", "W.proc",
    })

    def __init__(self, life, binding, parent, source, harness):
        self.life, self.binding, self.parent = life, binding, parent
        self.source, self.harness, self.volume = source, harness, parent / "volume"
        self.report = life._CleanupReport("R", binding.deadline)
        self.fds, self.children = {}, {"N": None, "W": None}
        self.outer = self.target = self.capture = self.expected_worker = None
        self.stage, self.state = "setup", "new"
        self.setup_status = self.observation = self.result = self.primary = None
        self.publications = 0
        self.before_frame = None
        self.mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())

    @property
    def deadline(self):
        return self.binding.deadline

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
        actual = fd_inventory()
        require(actual.keys() == {0, 1, 2, *self.fds.values()}, "unknown inherited descriptor")
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
        mounted = verify_tmpfs(descriptor, original)
        os.fchown(descriptor, self.binding.uid, self.binding.gid)
        os.mkdir("volume", 0o700, dir_fd=descriptor)
        volume = self.acquire("volume", lambda: os.open(
            "volume", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor,
        ))
        child = directory_identity(volume)
        require(child.device == mounted.device and child.inode != mounted.inode
                and child.mount == mounted.mount and child.mode == stat.S_IFDIR | 0o700
                and child.uid == child.gid == 0, "fresh tmpfs volume")
        os.fchown(volume, self.binding.uid, self.binding.gid)

    def create_fixture(self):
        self.advance("new", "fixture")
        self.install_volume()
        for name in ("source", "runtime", "selected", "harness"):
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
            bind(Path("/dev") / name, target, 11)
            sealed = mount_state(target)
            require(sealed[:4] == original[:4] and sealed[5] & 15 == 11, "outer device restrictions")
        bind(self.source, self.volume / "selected", 15)
        bind(self.harness, self.volume / "harness", 15)
        self.pipe("eof.read", "eof.write")
        self.close("eof.write")
        os.dup2(self.fds["eof.read"], 0, inheritable=False)
        self.close("eof.read")
        actual = fd_inventory()
        require(actual[0][2:] == (stat.S_IFIFO, os.O_RDONLY)
                and actual[1][2:] == (stat.S_IFIFO, os.O_WRONLY)
                and actual[2][2:] == (stat.S_IFIFO, os.O_WRONLY)
                and len({actual[fd][:2] for fd in (0, 1, 2)}) == 3
                and os.fpathconf(1, "PC_PIPE_BUF") >= FRAME_BYTES,
                "trusted capture pipes")
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
        os.setgroups([])
        os.setresgid(-1, self.binding.gid, -1)
        os.setresuid(-1, self.binding.uid, -1)
        prctl(4, 0)
        value = self_state()
        require(value["caps"][1] & (1 << CAP_SYS_ADMIN), "creator inherited authority")
        capset(1 << CAP_SYS_ADMIN)
        prctl(47, 4)
        prctl(38, 1)
        parent_guard()
        value = self_state()
        require(value["uid"] == (0, self.binding.uid, 0, self.binding.uid)
                and value["gid"] == (0, self.binding.gid, 0, self.binding.gid)
                and value["groups"] == () and value["caps"][:3] == (0, 1 << CAP_SYS_ADMIN, 1 << CAP_SYS_ADMIN)
                and value["caps"][4] == 0 and value["nnp"] == 1
                and value["user"] == self.outer["user"] and value["mount"] == self.outer["mount"]
                and value["label"] == self.outer["label"] and prctl(3) == 0, "creator boundary")
        send_token(self.fds["response.write"], b"CREATOR_READY\n", self.deadline)
        receive_token(self.fds["request.read"], b"CREATE\n", self.deadline)
        checked(libc().unshare(NEWUSER | NEWNS))
        parent_guard()
        require(self_state()["label"] == self.outer["label"], "creator unchanged LSM label")
        send_token(self.fds["response.write"], b"NS_CREATED\n", self.deadline)
        receive_token(self.fds["request.read"], b"MAPS_COMPLETE\n", self.deadline)
        require(maps(read_file("/proc/self/uid_map")) == ((0, self.binding.uid, 1),)
                and maps(read_file("/proc/self/gid_map")) == ((0, self.binding.gid, 1),)
                and read_file("/proc/self/setgroups").strip() == b"deny", "creator map readback")
        os.setresgid(0, 0, 0)
        os.setresuid(0, 0, 0)
        bounding(0)
        capset(0)
        prctl(38, 1)
        parent_guard()
        validate_credentials(self_state(), 0, 0, (0,) * 5)
        self.closes(("request.read", "response.write"))

    def verify_creator(self, child):
        values = live_child(child)
        require(values["Uid"] == (0, self.binding.uid, 0, self.binding.uid)
                and values["Gid"] == (0, self.binding.gid, 0, self.binding.gid)
                and values["Groups"] == () and values["NoNewPrivs"] == (1,)
                and tuple(values[key][0] for key in CAP_KEYS[:3]) == (0, 1 << CAP_SYS_ADMIN, 1 << CAP_SYS_ADMIN)
                and values["CapAmb"] == (0,) and namespace("user", directory=child.proc) == self.outer["user"]
                and namespace("mnt", directory=child.proc) == self.outer["mount"]
                and read_file("attr/current", directory=child.proc).strip() == self.outer["label"],
                "live creator credentials")
        # Dumpable=0 is N's actual self-prctl gate before its fixed READY.
        # Current proc/status does not export that value; do not invent one here.

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
        user_id, mount_id = fd_identity(user), fd_identity(mountfd)
        require(user_id != self.outer["user"] and mount_id != self.outer["mount"], "new descendant namespaces")
        self.namespace_relation(user, NS_GET_PARENT, self.outer["user"])
        owner = bytearray(4)
        fcntl.ioctl(user, NS_GET_OWNER_UID, owner, True)
        require(struct.unpack("=I", owner)[0] == self.binding.uid, "ordinary owner UID")
        self.namespace_relation(mountfd, NS_GET_USERNS, user_id)
        root = mount_state("/proc/" + str(child.pid) + "/root/.")
        self.target = Target(user_id, mount_id, root)
        live_child(child)

    def map_creator(self, child):
        names = ("setgroups", "uid_map", "gid_map")
        primary = None
        try:
            live_child(child)
            for name in names:
                self.acquire(name, lambda name=name: os.open(
                    name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=child.proc,
                ))
            require(os.read(self.fds["uid_map"], 128) == b"" and os.read(self.fds["gid_map"], 128) == b""
                    and os.read(self.fds["setgroups"], 128).strip() == b"allow", "fresh map presence")
            for name, data in (
                ("setgroups", b"deny"),
                ("uid_map", ("0 %d 1\n" % self.binding.uid).encode()),
                ("gid_map", ("0 %d 1\n" % self.binding.gid).encode()),
            ):
                live_child(child)
                descriptor = self.fds[name]
                os.lseek(descriptor, 0, os.SEEK_SET)
                require(os.write(descriptor, data) == len(data), "partial map write")
                os.lseek(descriptor, 0, os.SEEK_SET)
                actual = os.read(descriptor, 128)
                require(actual.split() == data.split(), "exact map readback")
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
        validate_credentials(value, 0, 0, (0, 1 << CAP_KILL, 1 << CAP_KILL, 1 << CAP_KILL, 0))
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
        # Rebind before READY/GO; a correct prefix cannot repair a leaked writer.
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
        for name in ("selected", "harness"):
            require(mount_state(self.volume / name)[5] & 15 == 15, "readonly trusted bindings")
        result = mechanism(self.volume, self.binding, self.outer, self.target, self.life)
        data = json_bytes(result)
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
            validate_mode(value)
            self.result = worker_wire(value)
        accepted.clear()

    def publish(self, value):
        require(self.publications < 2 and value["role"] == "R"
                and value["phase"] == ("before" if self.publications == 0 else "after"), "R publication order")
        self.publications += 1
        data = self.life._encode_frame(value)
        parsed = self.life._decode_frame(
            data[4:], "0" * 32, self.binding, self.before_frame,
        )
        if parsed.phase == "before":
            self.before_frame = parsed
        while True:
            wait = remaining(self.deadline)
            try:
                size = os.write(1, data)
            except BlockingIOError:
                select.select([], [1], [], wait)
                continue
            require(size == len(data), "R partial atomic publication")
            return

    def secondary(self, stage, error):
        self.report.error(stage, error)
        if self.primary is None:
            self.primary = error
        elif error is not self.primary:
            self.life._forget_error(error)

    def run(self):
        global _cleanup_life, _cleanup_report
        _cleanup_life, _cleanup_report = self.life, self.report
        handlers = {}
        try:
            for signum in self.life.TERMINATING:
                handlers[signum] = signal.signal(signum, self.life.interrupted)
            self.outer = root_context()
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
        actions = [("publication", lambda: self.publish(before))]
        for role, child in self.children.items():
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
            self.publish({
                "v": 1, "role": "R", "phase": "after", "binding": self.binding.wire(),
                "pid": before["pid"], "before": "before", "cleanup": self.life._cleanup_wire(self.report.value()),
                "disposition": "return" if returning else "raise", "status": status if returning else None,
            })
        except BaseException as error:
            self.secondary("publication", error)
            clean = False
        if self.primary is not None:
            self.life._forget_error(self.primary)
            self.primary = None
        self.result = self.before_frame = None
        before = actions = handlers = None
        if status:
            return status if status > 0 else 128 - status
        return 0 if clean else 125


def load_subject(volume):
    path = canonical(volume / "selected/scripts/validation_ownership/sandbox_exec.py")
    spec = importlib.util.spec_from_file_location("_issue180_readonly_subject", path)
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


def helper_transition(setup, root):
    calls = []
    original = setup.ctypes
    native = libc()

    class ObservedCall:
        def syscall(self, *args):
            attributes = ctypes.cast(args[4], ctypes.POINTER(setup.MountAttributes)).contents
            observed = (
                args[0].value, args[3].value, attributes.attr_set, attributes.attr_clr,
                attributes.propagation, attributes.userns_fd, args[5].value,
            )
            require(not calls and observed == SELECTIVE_CALL, "single selective operation")
            calls.append(observed)
            return native.syscall(*args)

        def mount(self, *args):
            raise Refusal("no remount fallback")

    setup.ctypes = SimpleNamespace(
        CDLL=lambda *args, **kwargs: ObservedCall(), c_long=ctypes.c_long, c_int=ctypes.c_int,
        c_char_p=ctypes.c_char_p, c_uint=ctypes.c_uint, c_size_t=ctypes.c_size_t,
        byref=ctypes.byref, sizeof=ctypes.sizeof, get_errno=ctypes.get_errno,
    )
    try:
        setup._enable_toolchain_null(root)
    finally:
        setup.ctypes = original
    require(calls == [SELECTIVE_CALL], "actual helper operation observed")
    return calls


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
    setup.bind(volume / "null", root / "dev/null", writable=True)
    setup.bind(volume / "zero", root / "dev/zero", writable=True)
    before = mount_state(root / "dev/null")
    require(stat.S_ISCHR(before[2]) and before[3] == os.makedev(1, 3) and before[5] & 15 == 15,
            "correct readonly inner null")
    old_negative(root / "dev/null")
    # c8 has one calls slot. The old errno/unchanged-state check above is an
    # actual gate, not a separately exported observation or a second mode.
    calls = helper_transition(setup, root)
    after = mount_state(root / "dev/null")
    require(before[:5] == after[:5] and after[5] == before[5] & ~os.ST_NODEV,
            "only local NODEV cleared")
    bounding(0)
    capset(0)
    prctl(38, 1)
    value = self_state()
    validate_target(value, binding, outer, target)
    validate_credentials(value, 0, 0, (0,) * 5)
    require(fd_inventory() == before_fds, "helper descriptor release")
    denied = [readonly_denial(root / name / "canary") for name in ("repo", "usr")]
    denied.append(device_denial(root / "dev/zero"))
    null_io(root / "dev/null")
    require(fd_inventory() == before_fds, "I/O descriptor release")
    remaining(binding.deadline)
    result = {
        "mode": "readonly", "before": list(before), "after": list(after), "failure": None,
        "calls": [list(call) for call in calls], "caps": [*value["caps"], value["nnp"]],
        "denied": denied, "null_io": True, "fd_closed": True, "local_nonzero_topology": True,
    }
    validate_mode(life._private_worker_record(json_bytes(result)))
    return result


def validate_mode(value):
    require(type(value) is tuple and len(value) == 10, "closed mode record")
    mode, before, after, failure, calls, caps, denied, io, closed, topology = value
    require(mode == "readonly" and failure is None and stat.S_ISCHR(before[2])
            and before[3] == os.makedev(1, 3) and before[5] & 15 == 15
            and before[:5] == after[:5] and after[5] == before[5] & ~os.ST_NODEV
            and calls == (SELECTIVE_CALL,) and caps == (0, 0, 0, 0, 0, 1)
            and denied[:2] == (errno.EROFS, errno.EROFS) and denied[2] in (errno.EPERM, errno.EACCES)
            and io is True and closed is True and topology is True, "readonly semantic checks")


def worker_wire(value):
    names = ("mode", "before", "after", "failure", "calls", "caps", "denied",
             "null_io", "fd_closed", "local_nonzero_topology")
    return dict(zip(names, value))


def reaper_arguments(arguments, life, harness, context):
    require(len(arguments) == 9 and arguments[:2] == ["--reaper", "readonly"], "fixed reaper arguments")
    require(all(re.fullmatch("[1-9][0-9]{0,19}", arguments[index]) for index in (2, 3, 7, 8))
            and re.fullmatch("[0-9]{1,20}", arguments[6]) is not None, "reaper numeric arguments")
    uid, gid, deadline = int(arguments[2]), int(arguments[3]), float(arguments[4])
    parent = canonical(arguments[5])
    require(0 < remaining(deadline) <= WATCHDOG_SECONDS
            and context.get("SUDO_UID") == str(uid) and context.get("SUDO_GID") == str(gid)
            and parent.name == "fixture" and parent.parent.parent == harness.parent
            and re.fullmatch(re.escape(RUN_PREFIX) + "[1-9][0-9]{0,19}", parent.parent.name) is not None,
            "sudo caller/fixed fixture binding")
    binding = life._FixtureBinding("readonly", uid, gid, deadline, int(arguments[6]),
                                   int(arguments[7]), int(arguments[8]))
    info = os.lstat(parent)
    require(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino, info.st_uid, info.st_gid) ==
            (binding.device, binding.inode, uid, gid) and stat.S_IMODE(info.st_mode) == 0o700,
            "reaper fixture identity")
    return binding, parent


def fixed_argv(budgeting, binding, parent, harness):
    require(binding.mode == "readonly" and canonical(harness / PROGRAM) == Path(__file__).resolve(),
            "fixed program identity")
    return [
        *budgeting.NAMESPACE_LAUNCHER, "/usr/bin/python3", "-I", "-S", "-B", str(harness / PROGRAM),
        "--reaper", "readonly", str(binding.uid), str(binding.gid), str(binding.deadline),
        str(parent), str(binding.device), str(binding.inode), str(binding.owner),
    ]


class HostDirectories:
    """C's two originally acquired directories; no recursive or late-adoption cleanup."""

    def __init__(self, workspace, name, uid, gid, life, report, deadline):
        self.workspace, self.name, self.uid, self.gid = workspace, name, uid, gid
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
        # Withdraw path authority before rmdir; never retry an ambiguous unlink.
        self.removal_attempted[slot] = True
        try:
            os.rmdir(name, dir_fd=self.fds[parent])
        except BaseException:
            self.report.unsure("ownership")
            raise
        self.removed[slot] = True

    def cleanup(self, lifecycle_closed, baseline):
        safe = False
        primary = None
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

    def value(self):
        return {
            "original": {name: value.wire() for name, value in self.identities.items() if name != "workspace"},
            "creation_attempted": dict(self.attempted), "created": dict(self.created),
            "removal_attempted": dict(self.removal_attempted), "removed": dict(self.removed),
            "pins_withdrawn": not self.fds,
        }


def summary(snapshot, life):
    frames = []
    for frame in snapshot.frames:
        if frame is not None:
            frames.append({
                "role": frame.role, "phase": frame.phase, "pid": frame.pid, "kind": frame.kind,
                "stage": frame.stage, "status": frame.status, "error": frame.error,
                "setup_status": frame.setup_status, "disposition": frame.disposition,
            })
    return {
        "qualified": snapshot.qualified, "outer_returncode": snapshot.outer_returncode,
        "outer_error": snapshot.outer_error, "outer_stage": snapshot.outer_stage,
        "capture_complete": snapshot.capture_complete, "capture_available": snapshot.capture_available,
        "custody_complete": snapshot.custody_complete, "frame_error": snapshot.frame_error,
        "api_returned": snapshot.api_returned, "reaped": snapshot.reaped, "observations": frames,
        "cleanup": life._cleanup_wire(snapshot.cleanup),
    }


def capture_complete(snapshot):
    if (
        snapshot.qualified or snapshot.outer_returncode != 0 or snapshot.outer_error is not None
        or not snapshot.api_returned or not snapshot.reaped or not snapshot.capture_complete
        or not snapshot.capture_available or not snapshot.custody_complete or not snapshot.cleanup.complete
    ):
        return False
    rb, ra, lb, la = snapshot.frames
    if any(frame is None for frame in snapshot.frames):
        return False
    if not (
        rb.kind == lb.kind == "normal-exit" and rb.stage == "worker" and rb.setup_status == 0
        and rb.status == ra.status == lb.status == la.status == 0
        and ra.disposition == la.disposition == "return" and ra.cleanup.complete and la.cleanup.complete
        and rb.result is not None
    ):
        return False
    validate_mode(rb.result)
    return True


def run_capture(budget, owner, binding, parent, harness):
    """Existing API seam, exercised only with completely inert boundaries in this preparation."""
    budgeting = sys.modules[type(budget).__module__]
    require(type(budget) is budgeting.ProbeBudget and budget.limits.seconds == WATCHDOG_SECONDS
            and binding.deadline == budget.deadline and 0 < budget.remaining() <= WATCHDOG_SECONDS,
            "single original fixed budget deadline")
    owner._bind_fixture(binding)
    return budget.run(
        fixed_argv(budgeting, binding, parent, harness), env=dict(ENVIRONMENT), cwd=harness.parent,
        output_limit=CAPTURE_BYTES, privileged=True, outcome=owner,
    )


def new_budget(budgeting):
    return budgeting.ProbeBudget(budgeting.Limits(
        seconds=WATCHDOG_SECONDS, runs=1, states=1, pending=1, entries=52,
        cache_bytes=552960, process_output_bytes=CAPTURE_BYTES, output_bytes=CAPTURE_BYTES,
        sandbox_bytes=FIXTURE_BYTES,
    ))


def lifecycle_closed(snapshot, budget):
    if not budget.closed or budget.children:
        return False
    if budget.runs == 0:
        return True
    if snapshot is None or not (
        snapshot.capture_available and snapshot.capture_complete and snapshot.custody_complete
        and snapshot.reaped and snapshot.cleanup.complete
    ):
        return False
    return all(snapshot.frames[index] is not None and snapshot.frames[index].cleanup.complete
               for index in (1, 3))


class Coordinator:
    """One C owner; acquisition, custody, source checks and empty-directory cleanup."""

    def __init__(self, budgeting, life, scope, harness, source, output, uid, gid, baseline, source_checks,
                 entry_fifo_cleanup):
        self.budgeting, self.life, self.scope = budgeting, life, scope
        self.harness, self.source, self.output = harness, source, output
        self.uid, self.gid, self.baseline = uid, gid, baseline
        self.entry_fifo_cleanup = entry_fifo_cleanup
        self.budget = self.owner = self.directories = self.primary = None
        self.stage, self.error_stage = "admission", None
        self.invoked = self.closed_lifecycle = self.capture_ok = self.fd_restored = False
        self.owner_released = None
        require(source_checks == {
            "harness_before": True, "selected_before": True, "harness_after": None, "selected_after": None,
        }, "completed source preflight")
        self.source_checks = dict(source_checks)

    def save_error(self, error, stage):
        if self.budget is not None:
            self.budget.failed = True
        if self.primary is None:
            self.primary, self.error_stage = error, stage
        elif error is not self.primary:
            self.life._forget_error(error)

    def snapshot(self):
        if self.owner is None or not self.owner._terminal:
            return None
        return self.owner.snapshot()

    def close_budget(self):
        if self.budget is not None:
            self.budget.close(**({} if self.owner is None else {"report": self.owner._cleanup}))

    def inspect_lifecycle(self):
        view = self.snapshot()
        try:
            self.closed_lifecycle = self.budget is not None and lifecycle_closed(view, self.budget)
        finally:
            view = None
        if not self.closed_lifecycle:
            self.owner._cleanup.unsure("ownership")

    def source_after(self, name, root, sha, harness=False):
        if not self.closed_lifecycle:
            return
        try:
            verify_checkout(root, sha, harness=harness, deadline=self.budget.deadline)
        except BaseException:
            self.source_checks[name] = False
            raise
        self.source_checks[name] = True

    def cleanup_directories(self):
        if self.directories is not None:
            self.directories.cleanup(self.closed_lifecycle, self.baseline)

    def check_final_fds(self):
        self.fd_restored = fd_inventory() == self.baseline
        require(self.fd_restored, "C final FD inventory")

    def run(self):
        global _cleanup_life, _cleanup_report
        view = completed = None
        report = None
        handlers = {}
        try:
            self.budget = new_budget(self.budgeting)
            self.owner = self.budget.reserve_outcome(output_limit=CAPTURE_BYTES)
            report = self.owner._cleanup
            _cleanup_life, _cleanup_report = self.life, report
            for signum in self.life.TERMINATING:
                handlers[signum] = signal.signal(signum, self.life.interrupted)
            # Prepay bounded after-check/artifact representations before a failure
            # can close the budget. No late admission, refund or new budget.
            self.budget.charge("sandbox", FIXTURE_BYTES)
            self.budget.charge("control", 8 * RECORD_BYTES + ARTIFACT_BYTES)
            self.stage = "acquisition"
            name = RUN_PREFIX + self.scope["run_id"]
            self.directories = HostDirectories(
                self.harness.parent, name, self.uid, self.gid, self.life, report, self.budget.deadline,
            )
            self.directories.create()
            self.directories.check_fds(self.baseline)
            original = self.directories.identities["fixture"]
            binding = self.life._FixtureBinding(
                "readonly", self.uid, self.gid, self.budget.deadline,
                original.device, original.inode, original.uid,
            )
            self.stage, self.invoked = "launch", True
            completed = run_capture(self.budget, self.owner, binding, self.directories.fixture, self.harness)
            completed = None
            self.stage = "capture"
            view = self.snapshot()
            self.capture_ok = view is not None and capture_complete(view)
            view = None
            if not self.capture_ok:
                self.budget.failed = True
        except BaseException as error:
            self.save_error(error, self.stage)
            if self.budget is not None:
                self.budget.failed = True
        finally:
            view = completed = None
        if report is not None:
            actions = [
                ("leader", self.close_budget), ("ownership", self.inspect_lifecycle),
                ("ownership", lambda: self.source_after("harness_after", self.harness, self.scope["harness"], True)),
                ("ownership", lambda: self.source_after("selected_after", self.source, SOURCE)),
                ("ownership", self.cleanup_directories), ("cleanup", self.check_final_fds),
            ]
            try:
                self.life.finish_cleanup(actions, primary=self.primary, handlers=handlers, report=report)
            except BaseException as error:
                self.save_error(error, "cleanup")
            actions = handlers = None
        else:
            # No host fixture or run can precede successful outcome admission.
            try:
                self.close_budget()
            except BaseException as error:
                self.save_error(error, "cleanup-before-outcome")
            try:
                self.check_final_fds()
            except BaseException as error:
                self.save_error(error, "cleanup-before-outcome")
        custody = {"available": False, "qualified": False, "outer_returncode": None}
        mode = {"available": False, "result": None, "qualified": False,
                "old_operation_separately_exported": False}
        try:
            view = self.snapshot()
            if view is not None:
                custody = {"available": True, **summary(view, self.life)}
                frame = view.frames[0]
                if frame is not None and frame.result is not None:
                    mode["available"], mode["result"] = True, worker_wire(frame.result)
        except BaseException as error:
            if report is not None:
                report.error("freeze", error)
            self.save_error(error, "snapshot")
        finally:
            view = None
            if self.owner is not None:
                try:
                    self.owner.release()
                    self.owner_released = True
                except BaseException as error:
                    self.owner_released = self.owner._released
                    report.unsure("ownership")
                    report.error("ownership", error)
                    self.save_error(error, "outcome-release")
            _cleanup_life = _cleanup_report = None
        directories = None if self.directories is None else self.directories.value()
        cleanup = None if report is None else self.life._cleanup_wire(report.value())
        passed = bool(
            self.primary is None and self.capture_ok and self.closed_lifecycle and self.fd_restored
            and self.owner_released is True and report is not None and report.value().complete
            and all(value is True for value in self.source_checks.values())
            and directories is not None and directories["pins_withdrawn"]
            and all(value is True for value in directories["removed"].values())
        )
        error_value = None if self.primary is None else self.life._error_value(self.primary)
        if self.primary is not None:
            self.life._forget_error(self.primary)
            self.primary = None
        records = {
            "launch.json": {
                "capture_adapter_invoked": self.invoked,
                "run_admissions": None if self.budget is None else self.budget.runs,
                "original": None if directories is None else directories["original"],
                "uid": self.uid, "gid": self.gid, "source": SOURCE, "harness": self.scope["harness"],
                "deadline": None if self.budget is None else self.budget.deadline,
                "watchdog_seconds": WATCHDOG_SECONDS, "wait_ceiling_seconds": WAIT_SECONDS,
                "outer_ceiling_seconds": OUTER_SECONDS, "qualified": False,
            },
            "custody.json": custody,
            "mode.json": mode,
            "cleanup.json": {
                "coordinator_first_error": error_value, "coordinator_error_stage": self.error_stage,
                "report": cleanup, "directories": directories, "source_checks": self.source_checks,
                "entry_fifo_cleanup": self.entry_fifo_cleanup,
                "lifecycle_closed": self.closed_lifecycle, "fd_inventory_restored": self.fd_restored,
                "outcome_released": self.owner_released,
                "checks_complete_before_artifact_publication": passed, "qualified": False,
                "publication_completion_attested": False,
            },
        }
        publication_failed = False
        for name in ARTIFACTS[1:]:
            try:
                write_record(self.output, name, records[name])
            except BaseException as error:
                publication_failed = True
                self.life._forget_error(error)
        records = mode = custody = directories = cleanup = None
        return 0 if passed and not publication_failed else 125


def plan(context):
    scope = identity(read_json(read_file(context["GITHUB_EVENT_PATH"], RECORD_BYTES)), context)
    harness, _, output = paths(context)
    verify_checkout(harness, scope["harness"], harness=True)
    require(os.getresuid() == (os.getuid(),) * 3 and os.getuid() > 0
            and os.getresgid() == (os.getgid(),) * 3 and os.getgid() > 0, "ordinary coordinator")
    output.mkdir(mode=0o700)
    write_record(output, "scope.json", scope)


def coordinate(context):
    scope = identity(read_json(read_file(context["GITHUB_EVENT_PATH"], RECORD_BYTES)), context)
    harness, source, output = paths(context)
    require(read_json(read_file(output / "scope.json", RECORD_BYTES)) == scope, "first-creation scope")
    canonical(output)
    checks = {key: None for key in ("harness_before", "selected_before", "harness_after", "selected_after")}
    observed = {"ids": {"uid": None, "gid": None, "resuid": None, "resgid": None},
                "state": None, "fds": None}
    entry_fifo_cleanup = {"complete": False, "phase": "validation", "descriptors": []}
    stage = "ordinary-id-read"
    error_stage = None
    error_fact = None
    try:
        uid = observed["ids"]["uid"] = os.getuid()
        gid = observed["ids"]["gid"] = os.getgid()
        stage = "ordinary-id-check"
        require(uid > 0 and gid > 0, "ordinary coordinator")
        for name, read, expected in (
            ("resuid", os.getresuid, (uid,) * 3), ("resgid", os.getresgid, (gid,) * 3),
        ):
            stage = "ordinary-id-read"
            observed["ids"][name] = read()
            stage = "ordinary-id-check"
            require(observed["ids"][name] == expected, "ordinary coordinator")
        stage = "ordinary-state-read"
        initial = self_state()
        observed["state"] = {name: initial[name] for name in ("uid", "gid", "caps", "uid_map", "gid_map", "nnp")}
        stage = "ordinary-state-check"
        require(initial["uid"] == (uid,) * 4 and initial["gid"] == (gid,) * 4
                and initial["uid_map"] == initial["gid_map"] == FULL_MAP
                and all(initial["caps"][index] == 0 for index in (0, 1, 2, 4)), "ordinary full-map coordinator")
        stage = "ordinary-fd-read"
        baseline = fd_inventory()
        observed["fds"] = [[fd, value[2], value[3]] for fd, value in sorted(baseline.items())]
        stage = "ordinary-fd-check"
        baseline = withdraw_entry_fifos(baseline, entry_fifo_cleanup)
        require(set(baseline) == {0, 1, 2}, "initial C descriptors")
        for key, root, sha, is_harness in (
            ("harness_before", harness, scope["harness"], True), ("selected_before", source, SOURCE, False),
        ):
            stage = key
            try:
                verify_checkout(root, sha, harness=is_harness)
            except BaseException:
                checks[key] = False
                raise
            checks[key] = True
        stage = "selected-environment"
        verify_environment(source)
        stage = "selected-control-import"
        budgeting, life = load_control(source)
    except BaseException as error:
        error_stage = stage
        code = None
        if isinstance(error, Refusal):
            code = "unclassified-refusal"
            if type(error) is Refusal and len(error.args) == 1 and type(error.args[0]) is str:
                code = PREFLIGHT_REFUSALS.get(error.args[0], code)
        error_fact = {
            "kind": ("os-error" if isinstance(error, OSError) else
                     "interrupt" if isinstance(error, KeyboardInterrupt) else
                     "value-error" if isinstance(error, ValueError) else
                     "refusal" if isinstance(error, Refusal) else "other-error"),
            "errno": error.errno if isinstance(error, OSError) and integer(error.errno, 0, 4095) else None,
            "code": code,
        }
        for attribute in ("__traceback__", "__context__", "__cause__"):
            BaseException.__setattr__(error, attribute, None)
    if error_stage is not None:
        records = {
            "launch.json": {"capture_adapter_invoked": False, "run_admissions": 0, "qualified": False},
            "custody.json": {"available": False, "qualified": False, "outer_returncode": None},
            "mode.json": {"available": False, "result": None, "qualified": False,
                          "old_operation_separately_exported": False},
            "cleanup.json": {
                "preflight_refusal_stage": error_stage, "preflight_first_error": error_fact, "source_checks": checks,
                "preflight_observations": observed,
                "entry_fifo_cleanup": entry_fifo_cleanup,
                "launch_owners_acquired": False, "checks_complete_before_artifact_publication": False,
                "qualified": False, "publication_completion_attested": False,
            },
        }
        for name in ARTIFACTS[1:]:
            try:
                write_record(output, name, records[name])
            except BaseException as error:
                for attribute in ("__traceback__", "__context__", "__cause__"):
                    BaseException.__setattr__(error, attribute, None)
        return 125
    return Coordinator(budgeting, life, scope, harness, source, output, uid, gid, baseline, checks,
                       entry_fifo_cleanup).run()


def main():
    require(sys.flags.isolated and sys.flags.no_site and not sys.flags.optimize, "isolated interpreter")
    arguments = sys.argv[1:]
    if arguments == ["plan"]:
        plan(os.environ)
        return 0
    if arguments == ["run"]:
        return coordinate(os.environ)
    if arguments[:1] == ["--reaper"]:
        harness = canonical(Path(__file__).resolve().parents[2])
        source = canonical(harness.parent / "candidate")
        _, life = load_control(source)
        binding, parent = reaper_arguments(arguments, life, harness, os.environ)
        return Bootstrap(life, binding, parent, source, harness).run()
    raise Refusal("only fixed plan/run/reaper roles")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        for attribute in ("__traceback__", "__context__", "__cause__"):
            BaseException.__setattr__(error, attribute, None)
        print("null bootstrap refused; preparation is not runtime qualification", file=sys.stderr)
        raise SystemExit(125) from None
