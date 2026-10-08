"""Fail-closed Linux x86-64 syscall authority, outside the candidate chroot.

The complete, readonly/noexec mount is the filesystem boundary. Ptrace adds violation
reporting (including caught failures), FD/channel separation, exec authority
and complete metadata/mmap/directory observations. No candidate code runs in
this Python process. Mutable shared memory and namespace aliases reject.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import posixpath
import re
import resource
import signal
import stat
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

if __package__:
    from . import read_epochs
    from .read_trace import NativeReadTrace
    from .authority import NATIVE_METADATA_DIRECTORY_PATHS, PYTHON_RUNTIME_DIRECTORY, _event_command, _read_events, encoded, native_command_owner, parse_json
    from .lifecycle import finish_cleanup
    from .metadata_transport import encode_metadata_transport
    from .producer_channel import (
        ChannelError, ProducerChannel, PUBLICATION_MAGIC, PUBLICATION_POLICIES,
        publication_identity, validate_publication_identity,
    )
else:
    import read_epochs
    from read_trace import NativeReadTrace
    from authority import NATIVE_METADATA_DIRECTORY_PATHS, PYTHON_RUNTIME_DIRECTORY, _event_command, _read_events, encoded, native_command_owner, parse_json
    from lifecycle import finish_cleanup
    from metadata_transport import encode_metadata_transport
    from producer_channel import (
        ChannelError, ProducerChannel, PUBLICATION_MAGIC, PUBLICATION_POLICIES,
        publication_identity, validate_publication_identity,
    )


LIBC = ctypes.CDLL(None, use_errno=True)
VO_SOURCE_IO = 0x564F4D4B00000008
VO_JOB_INPUTS = 0x564F4D4B00000009
VO_JOB_CONTEXT = 0x564F4D4B00000006
VO_JOB_POLICY = 0x564F4D4B00000007
LIBC.ptrace.restype = ctypes.c_long
WALL = 0x40000000
TRACEME, PEEKDATA, SYSCALL, GETREGS, SETREGS, SETOPTIONS = 0, 2, 24, 12, 13, 0x4200
OPTIONS = 1 | 2 | 4 | 8 | 16 | 32 | 64 | 0x100000  # syscall/process/exec/exit stops and exitkill
PROT_READ, PROT_WRITE, PROT_EXEC = 1, 2, 4
MAP_SHARED, MAP_PRIVATE, MAP_SHARED_VALIDATE = 1, 2, 3
MAP_ANONYMOUS = 0x20
# Fixed placement, loader hints and stacks do not alias pages or change their
# size. Growing/huge-page and unknown flags cannot bypass 4 KiB reservations.
MMAP_FLAGS = 3 | 0x10 | MAP_ANONYMOUS | 0x800 | 0x1000 | 0x20000 | 0x100000
VO_READY, VO_DISPATCH, VO_QUERY_KIND, VO_METADATA, VO_PRODUCE = (
    0x564F4D4B00000001, 0x564F4D4B00000002, 0x564F4D4B00000003, 0x564F4D4B00000004,
    0x564F4D4B00000005,
)
VO_RECIPE, VO_VALUE, VO_VALIDATE = 0x564F4D4B00000011, 0x564F4D4B00000012, 0x564F4D4B00000013
VO_LIVE = 0x564F4D4B00000014
STACK_LIMIT = 16 * 1024 * 1024
SYSCALL_MEMORY_LIMIT = 65536


class Violation(RuntimeError):
    pass


class Registers(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "r15", "r14", "r13", "r12", "rbp", "rbx", "r11", "r10", "r9", "r8",
        "rax", "rcx", "rdx", "rsi", "rdi", "orig_rax", "rip", "cs", "eflags",
        "rsp", "ss", "fs_base", "gs_base", "ds", "es", "fs", "gs",
    )]


def ptrace(request, pid, address=0, data=0):
    ctypes.set_errno(0)
    result = LIBC.ptrace(
        ctypes.c_ulong(request), ctypes.c_ulong(pid),
        ctypes.c_void_p(address),
        data if not isinstance(data, int) else ctypes.c_void_p(data),
    )
    error = ctypes.get_errno()
    if result == -1 and error:
        raise OSError(error, f"ptrace request {request:#x}: {os.strerror(error)}")
    return result


def memory(pid, address, count):
    if count < 0 or count > SYSCALL_MEMORY_LIMIT or not address:
        raise Violation("invalid syscall memory request")
    result = bytearray()
    start = address & ~7
    leading = address - start
    for offset in range(0, leading + count, 8):
        word = ptrace(PEEKDATA, pid, start + offset)
        result.extend((word & ((1 << 64) - 1)).to_bytes(8, "little"))
    return bytes(result[leading:leading + count])


def replace_memory(pid, address, data):
    offset = 0
    while offset < len(data):
        cursor = address + offset
        start = cursor & ~7
        leading = cursor - start
        amount = min(8 - leading, len(data) - offset)
        word = bytearray(memory(pid, start, 8))
        word[leading:leading + amount] = data[offset:offset + amount]
        ptrace(5, pid, start, int.from_bytes(word, "little"))
        offset += amount


def cstring(pid, address, *, limit=4096, charge=None):
    if not address:
        raise Violation("null pathname")
    result = bytearray()
    while len(result) < limit:
        cursor = address + len(result)
        count = min(8 - (cursor & 7), limit - len(result))
        if charge is not None:
            charge(count)
        word = memory(pid, cursor, count)
        if b"\0" in word:
            result.extend(word.split(b"\0", 1)[0])
            try:
                return result.decode("utf-8", "strict")
            except UnicodeDecodeError as error:
                raise Violation("pathname is not strict UTF-8") from error
        result.extend(word)
    raise Violation("pathname exceeds bound")


def directory_entries(data, *, wide):
    names = []
    offset = 0
    while offset < len(data):
        start = 19 if wide else 18
        if len(data) - offset < 24:
            raise Violation("truncated directory entry header")
        length = int.from_bytes(data[offset + 16:offset + 18], "little")
        if length < 24 or length % 8 or length > len(data) - offset:
            raise Violation("invalid directory entry length")
        # Legacy getdents stores d_type in the final byte; it is not name data.
        value = data[offset + start:offset + length - (0 if wide else 1)]
        end = value.find(b"\0")
        if end < 1 or end > 255 or b"/" in value[:end]:
            raise Violation("invalid directory entry name")
        try:
            name = value[:end].decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise Violation("directory entry is not strict UTF-8") from error
        inode = int.from_bytes(data[offset:offset + 8], "little")
        if inode and name not in {".", ".."}:
            names.append(name)
        offset += length
    return names


def trace_me(drop_privileges):
    drop_privileges()
    # setuid/setgid in the sudo route clears dumpability. TRACEME alone does
    # not restore the parent's memory access without CAP_SYS_PTRACE.
    if LIBC.prctl(4, 1, 0, 0, 0):  # PR_SET_DUMPABLE; credentials/caps stay dropped
        error = ctypes.get_errno()
        raise OSError(error, "cannot restore tracee memory observation")
    ptrace(TRACEME, 0)
    os.kill(os.getpid(), signal.SIGSTOP)


def signed(value):
    return ctypes.c_longlong(value).value


def execute_mode_allows(info, uid, gids):
    """POSIX class precedence after the caller and applicable ACL checks."""
    if uid == info.st_uid:
        return bool(info.st_mode & stat.S_IXUSR)
    if info.st_gid in gids:
        return bool(info.st_mode & stat.S_IXGRP)
    return bool(info.st_mode & stat.S_IXOTH)


@dataclass
class Process:
    role: str
    cwd: str = "/repo"
    fds: dict[int, str] = field(default_factory=lambda: {
        0: "<stdin>", 1: "<stdout>", 2: "<stderr>",
    })
    entering: bool = True
    pending: tuple | None = None
    observer_ranges: tuple = ()
    bootstrap: bool = True
    memory_reservation: int = 0
    break_end: int = 0
    dispatch: tuple | None = None
    helper_kind: int = 0
    observer_ready: bool = False
    memory_group: int = 0
    memory_limit: int = 0
    clone_shares_vm: bool = False
    vfork_child: int | None = None
    process_reservation: bool = False
    pidfd: int = -1
    observations: list[tuple[str, str]] = field(default_factory=list)
    observation_needs_bytes: bool = False
    metadata_pending: tuple | None = None
    metadata_index: int | None = None
    kernel_call: int | None = None
    kernel_io: str | None = None
    deferred_entry: object | None = None
    parked: bool = False
    producer_requested: bool = False
    producer_ready: bool = False
    producer_frame: bytes | None = None
    producer_slot: int | None = None
    producer_event_written: bool = False
    exec_path: str | None = None
    dependency_image: str | None = None
    dependency_stop: tuple[int, int, int] | None = None
    native_stop: tuple[int, int, int] | None = None
    native_signals: dict[int, int] = field(default_factory=dict)
    native_signal_origins: dict[int, list] = field(default_factory=dict)
    native_signal_buffer: tuple | None = None
    native_sigkill_outcome: bool = False
    native_exit_status: int | None = None
    delivery_signal: int = 0
    native_delivered: int = 0
    newborn_stop: bool = False
    native_dispatch: int | None = None
    native_parent: int | None = None
    native_execs: int = 0
    native_inputs: tuple[str, dict] | None = None
    native_admission: dict | None = None
    path_context: tuple[str, int, str | None] | None = None
    native_output_operation: object | None = None
    native_output_close: int | None = None
    native_unlink_flags: int | None = None

    def clone(self):
        return Process(
            role=self.role, cwd=self.cwd, fds=dict(self.fds), entering=False,
            observer_ranges=self.observer_ranges, bootstrap=self.bootstrap,
            break_end=self.break_end, dispatch=self.dispatch, observer_ready=self.observer_ready,
            memory_group=self.memory_group, memory_limit=self.memory_limit,
            dependency_image=self.dependency_image,
            native_dispatch=self.native_dispatch,
            native_inputs=self.native_inputs,
            native_admission=self.native_admission if self.role == "make" else None,
        )

    def close(self):
        if self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1


class Policy:
    def __init__(self, config):
        self.config = config
        self.mode = config["mode"]
        self.native_readonly = config.get("native_readonly", False)
        self.native_admission = config.get("native_admission", False)
        self.native_admit = None
        if type(self.native_admission) is not bool or self.native_admission and (
            not self.native_readonly or not config.get("producer_endpoint")
            or config.get("read_epochs", {}).get("version") not in {5, 6}
        ):
            raise Violation("native Command admission lacks its existing channel/runtime authority")
        native_shell = config.get("native_shell", "/bin/sh")
        native_executables = config.get("native_executables", ["/bin/sh"])
        if (
            not isinstance(native_executables, list) or not native_executables
            or any(not isinstance(path, str) or not path.startswith("/") for path in native_executables)
            or len(set(native_executables)) != len(native_executables)
            or native_shell not in {"/bin/sh", "/usr/bin/sh"}
            or native_executables[0] != native_shell or "/usr/bin/make" in native_executables
        ):
            raise Violation("invalid native executable resource declaration")
        self.native_executables = set(native_executables)
        metadata_directories = config.get("native_metadata_directories", [])
        if (
            not isinstance(metadata_directories, list)
            or len(metadata_directories) > len(NATIVE_METADATA_DIRECTORY_PATHS)
            or any(
                not isinstance(item, dict) or set(item) != {"path", "identity"}
                or not isinstance(item["path"], str)
                or item["path"] not in NATIVE_METADATA_DIRECTORY_PATHS
                or item["identity"] is not None and (
                    not isinstance(item["identity"], list) or len(item["identity"]) != 5
                    or any(type(value) is not int or value < 0 for value in item["identity"])
                    or any(value >= 1 << 64 for value in item["identity"][:2])
                    or any(value >= 1 << 32 for value in item["identity"][2:])
                    or not stat.S_ISDIR(item["identity"][2]) or item["identity"][3] != 0
                    or item["identity"][2] & (stat.S_IWGRP | stat.S_IWOTH)
                )
                for item in metadata_directories
            )
            or len({item["path"] for item in metadata_directories}) != len(metadata_directories)
            or metadata_directories and not self.native_readonly
        ):
            raise Violation("invalid native metadata-only directory authority")
        self.native_metadata_directories = {item["path"]: item["identity"] for item in metadata_directories}
        self.native_runtime_directories = config.get("native_runtime_directories", [])
        if (
            not isinstance(self.native_runtime_directories, list)
            or len(self.native_runtime_directories) > 4
            or any(
                not isinstance(path, str)
                or PYTHON_RUNTIME_DIRECTORY.fullmatch(path) is None
                for path in self.native_runtime_directories
            )
            or len(set(self.native_runtime_directories)) != len(self.native_runtime_directories)
            or self.native_runtime_directories and not self.native_readonly
        ):
            raise Violation("invalid native managed runtime directory authority")
        self.read_trace = None
        request = config.get("read_epochs")
        if request is not None:
            if (
                not self.native_readonly or not isinstance(request, dict)
                or set(request) != {"version", "scope", "abi"} | (
                    {"selection"} if request.get("version") in {4, 5, 6} else set()
                )
                or type(request["version"]) is not int or request["version"] not in {1, 4, 5, 6}
                or not isinstance(request["abi"], dict)
                or request["abi"].get("version") != (2 if request["version"] in {4, 5, 6} else 1)
                or request["version"] in {4, 5, 6} and (
                    not isinstance(request["selection"], dict)
                    or not isinstance(request["selection"].get("inventory"), list)
                    or any(not isinstance(row, dict) or row.get("kind") != "snapshot"
                           for row in request["selection"]["inventory"])
                )
                or not isinstance(request["scope"], str) or not request["scope"]
                or config.get("environment", {}).get("VO_OBSERVE_READS") != "1"
                or (request["version"] == 6) != bool(config.get("native_output_paths"))
            ):
                raise Violation("invalid readonly native read-trace authority")
        elif "VO_OBSERVE_READS" in config.get("environment", {}):
            raise Violation("unconfigured native read observation")
        if "native_root_observation" in config and (
            config["native_root_observation"] is not True
            or not self.native_readonly or request is None or request["version"] not in {5, 6}
        ):
            raise Violation("invalid native root observation authority")
        self.native_root = None
        if type(self.native_readonly) is not bool or (
            not self.native_readonly and "VO_OBSERVE_NATIVE_READONLY" in config.get("environment", {})
        ) or self.native_readonly and (
            self.mode != "make"
            or config["executables"] != ["/usr/bin/make", *native_executables]
            or config.get("producer_endpoint") and not self.native_admission or config.get("published")
            or config.get("mapping_entries") or config.get("metadata_validation")
            or config.get("dependency")
            or config.get("environment", {}).get("VO_OBSERVE_NATIVE_READONLY") != "1"
        ):
            raise Violation("invalid readonly native Make authority")
        self.code = {"/repo/" + path for path in config["code"]}
        self.sources = {"/repo/" + path for path in config["sources"]}
        self.enumerations = {posixpath.normpath("/repo/" + path) for path in config["enumerations"]}
        self.consumed = set()
        self.code_consumed = set()
        self.accessed = set()
        self.observation_attempts = {
            name: set() for name in ("consumed", "code_consumed", "accessed")
        }
        self.trace_observations = 0
        self.native_jobs = {}
        self.written = 0
        self.calls = 0
        self.created = 0
        self.observation_bytes = 0
        self.metadata = []
        self.metadata_seen = set()
        self.events = []
        self.executed = []
        self.producer_requests = deque()
        self.producer_issued = 0
        self.producer_completed = 0
        self.producer_pending_peak = 0
        self.published = {}
        self.publication_confirmation = None
        self.memory_peak = 0
        self.processes = {}
        self.newborn_stops = {}
        self.total_processes = 0
        self.live_process_peak = 0
        self.make_pid = 0
        self.make_restarts = 0
        for path in self.native_runtime_directories:
            expected_mount = {
                "source": path, "target": path, "writable": False, "executable": True,
            }
            if [item for item in config["mounts"] if item["target"] == path] != [expected_mount]:
                raise Violation("native managed runtime lacks its exact readonly mount")
            source = Path(path).stat()
            guest = (Path(config["root"]) / path.lstrip("/")).stat()
            mounted = os.statvfs(Path(config["root"]) / path.lstrip("/"))
            self.charge_metadata(len(encoded([list(source), list(guest), list(mounted)])))
            if (
                (source.st_dev, source.st_ino) != (guest.st_dev, guest.st_ino)
                or not stat.S_ISDIR(guest.st_mode) or guest.st_uid != source.st_uid
                or guest.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                or mounted.f_flag & (os.ST_RDONLY | os.ST_NOSUID | os.ST_NODEV)
                != os.ST_RDONLY | os.ST_NOSUID | os.ST_NODEV
            ):
                raise Violation("native managed runtime mount backing or readonly flags differ")
        for path, identity in self.native_metadata_directories.items():
            self.validate_native_metadata_mount(path, identity)
        self.executable = set(config["executables"])
        self.executable.update(self.resolve(path) for path in config["executables"])
        if self.native_readonly and (
            self.resolve("/bin/sh") != native_shell
            or any(self.resolve(path) != path for path in native_executables)
        ):
            raise Violation("native executable declaration differs from its actual runtime alias")
        self.runtime_closure = set(config.get("runtime_closure", ()))
        if self.native_readonly and not self.native_executables <= self.runtime_closure:
            raise Violation("native executable is outside captured runtime closure")
        dependency = config.get("dependency")
        if dependency:
            self.runtime_closure.update(dependency["runtime_files"])
        self.runtime_directories = set()
        for name in self.runtime_closure | self.executable | {"/lib/vo-observer.so"}:
            parent = posixpath.dirname(name)
            while parent:
                self.runtime_directories.add(parent)
                if parent == "/":
                    break
                parent = posixpath.dirname(parent)
        libraries = {posixpath.basename(name) for name in self.runtime_closure if ".so" in posixpath.basename(name)}
        search = {
            "/lib", "/lib64", "/usr/lib", "/usr/lib64",
            "/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu",
            *(posixpath.dirname(name) for name in self.runtime_closure if ".so" in posixpath.basename(name)),
        }
        search |= {directory + "/glibc-hwcaps/" + level for directory in tuple(search)
                   for level in ("x86-64-v2", "x86-64-v3", "x86-64-v4")}
        self.loader_probes = {
            "/etc/ld.so.cache", "/etc/ld.so.preload", *search,
            *(directory + "/" + name for directory in search for name in libraries),
        }
        if dependency:
            self.dependency_files = {
                self.resolve(path) for path in self.runtime_closure | self.executable
            }
            self.dependency_directories = {
                self.resolve(path)
                for path in self.runtime_directories | search | set(dependency["runtime_directories"])
            }
            self.dependency_stat_probes = {
                self.resolve(path) for path in dependency["runtime_stat_probes"]
            }
            self.dependency_loader_probes = {self.resolve(path) for path in self.loader_probes}
            self.dependency_interpreter = self.resolve(dependency["runtime_interpreter"])
            self.dependency_libc = self.resolve(dependency["runtime_libc"])
            purpose_images = {
                *dependency["executables"], self.dependency_interpreter, self.dependency_libc,
            }
            if not purpose_images <= self.dependency_files:
                raise Violation("dependency purpose image is outside the resolved runtime")
            self.dependency_image_ids = {
                path: self.dependency_image_identity(Path(config["root"]) / path.lstrip("/"))
                for path in purpose_images
            }
        version = config["python_version"]
        self.runtime_probes = {
            "/usr/bin/pybuilddir.txt", "/usr/bin/Modules/Setup.local",
            "/usr/bin/Lib/os.py", "/usr/bin/Lib/os.pyc",
            "/usr/bin/pyvenv.cfg", "/usr/pyvenv.cfg", "/usr/bin/python3._pth",
            "/usr/bin/python" + version + "._pth", "/usr/bin/python._pth",
            "/usr/bin/python" + version,
            *(prefix + "/lib/python" + version.replace(".", "") + ".zip"
              for prefix in ("", "/usr", "/usr/bin")),
            *("/usr/bin/lib/python" + version + "/os." + suffix for suffix in ("py", "pyc")),
            "/usr/bin/lib/python" + version + "/lib-dynload",
        }
        self.link_option_probes = {
            config.get("cwd", "/repo") + "/" + argument for argument in (
                "-lc", "-lm", "-lgcc", "-lgcc_s", "-lstdc++", *config["argv"],
            ) if argument.startswith("-l") and re.fullmatch(r"-l[A-Za-z0-9_+.-]+", argument)
        }
        self.link_option_probes.update({
            config.get("cwd", "/repo") + "/" + name for name in (
                "libgcc_s.so.1", "libgcc.a", "libc.so.6", "libc_nonshared.a",
                "ld-linux-x86-64.so.2",
            )
        })
        self.code_dirs = {"/repo"}
        self.source_dirs = set()
        for paths, directories in ((self.code, self.code_dirs), (self.sources, self.source_dirs)):
            for name in paths:
                parent = posixpath.dirname(name)
                while parent != "/":
                    directories.add(parent)
                    parent = posixpath.dirname(parent)
        if request is not None:
            self.read_trace = NativeReadTrace(self, request)
        self.native_outputs = None
        paths = config.get("native_output_paths")
        if paths is not None:
            if (
                not (
                    self.mode == "command" and config["argv"][0] == "/native/tool"
                    or self.mode == "make" and self.native_admission
                )
                or not isinstance(paths, list) or not paths
                or any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths)
                or len(paths) > config["creation_limit"]
            ):
                raise Violation("native output observer lacks its exact issued tool outputs")
            if __package__:
                from .native_outputs import NativeOutputObserver
            else:
                from native_outputs import NativeOutputObserver
            self.native_outputs = NativeOutputObserver(self, paths)

    def observation_count(self):
        return sum(map(len, self.observation_attempts.values())) + self.trace_observations

    def confirm_readonly_entry(self, pid, state):
        trace = self.read_trace
        if trace is None or trace.pending_barrier is None:
            return
        mounts = self.config.get("mounts", ())
        repository = [row for row in mounts if row["target"] == "/repo"]
        composite = self.config.get("readonly_source_composite", False)
        writable = trace.version == read_epochs.WRITABLE_VERSION
        source_mounts = [row for row in mounts if row["target"].startswith("/repo/")]
        if (
            not self.native_readonly or trace.version not in {4, 5, 6} or pid != self.make_pid or pid != trace.pid
            or self.processes.get(pid) is not state or state.role != "make"
            or not state.observer_ready or not state.parked or state.pidfd < 0
            or len(repository) != 1 or repository[0]["writable"] is not writable
            or type(composite) is not bool
            or any(row["target"] == "/" for row in mounts)
            or source_mounts and not (composite or writable)
            or (composite or writable) and any(row["writable"] is not False for row in source_mounts)
        ):
            raise Violation("readonly source entry lacks its actual stopped immutable backing")
        backing = os.statvfs(f"/proc/{pid}/root/repo")
        self.charge_metadata(sys.getsizeof(backing))
        if bool(backing.f_flag & os.ST_RDONLY) == writable:
            raise Violation("actual native source mount is not readonly")
        if composite or writable:
            for row in (*repository, *source_mounts):
                source = os.stat(row["source"])
                guest_path = f"/proc/{pid}/root" + row["target"]
                guest = os.stat(guest_path)
                flags = os.statvfs(guest_path)
                self.charge_metadata(len(encoded([list(source), list(guest), list(flags)])))
                if (
                    (source.st_dev, source.st_ino) != (guest.st_dev, guest.st_ino)
                    or flags.f_flag & (os.ST_RDONLY | os.ST_NOSUID | os.ST_NODEV)
                    != (0 if row["writable"] else os.ST_RDONLY) | os.ST_NOSUID | os.ST_NODEV
                ):
                    raise Violation("actual readonly composite source backing differs")
        if writable:
            for entry in trace.selection["inventory"]:
                path = "/repo/" + entry["path"]
                if not any(
                    path == row["target"] or path.startswith(row["target"] + "/")
                    for row in source_mounts
                ):
                    raise Violation("writable native source lacks its immutable source island")
        trace.confirm_barrier(dict(trace.pending_barrier), trace.selection["snapshot_sha256"])

    def reserve_trace_observation(self):
        self.charge_metadata(128)
        if self.observation_count() >= self.config["observation_count"]:
            raise Violation("aggregate native trace observation budget exhausted")
        self.trace_observations += 1

    def reserve_observation(self, name, value):
        attempted = self.observation_attempts[name]
        if value not in attempted:
            self.observation_bytes += len(value.encode("utf-8")) + 128
            if (
                self.observation_bytes > self.config["observation_limit"]
                or self.observation_count() >= self.config["observation_count"]
            ):
                raise Violation("aggregate filesystem-observation budget exhausted")
            attempted.add(value)

    def observe(self, name, value):
        self.reserve_observation(name, value)
        getattr(self, name).add(value)

    def native_job_event(self, row):
        self.reserve_trace_observation()
        self.charge_metadata(len(encoded(row)))

    def native_argv(self, pid, pointer):
        if not pointer:
            raise Violation("native dispatch has no original argv vector")
        argv, size = [], 0
        for index in range(1025):
            if time.monotonic() >= self.config["deadline"]:
                raise Violation("native input capture exhausted its original deadline")
            self.charge_metadata(8)
            address = int.from_bytes(memory(pid, pointer + 8 * index, 8), "little")
            if not address:
                inputs = read_epochs.native_execution_input(argv, "/repo")
                self.charge_metadata(len(encoded(inputs)))
                return inputs["argv"]
            argument = cstring(pid, address, limit=65537 - size, charge=self.charge_metadata)
            size += len(argument.encode("utf-8")) + 1
            if size > 65536:
                raise Violation("native dispatch argv exceeds its original byte bound")
            argv.append(argument)
        raise Violation("native dispatch argv exceeds its original word bound")

    def observe_native_inputs(self, pid, state, pointer, size):
        if (
            not self.native_readonly or pid != self.make_pid or state.role != "make"
            or not state.observer_ready or state.dispatch is not None
            or state.native_dispatch is not None or state.native_inputs is not None or size != 16
            or state.cwd != "/repo"
        ):
            raise Violation("native dispatch inputs lack their original pre-spawn owner")
        self.charge_metadata(size)
        frame = memory(pid, pointer, size)
        path_pointer, argv_pointer = (
            int.from_bytes(frame[offset:offset + 8], "little") for offset in (0, 8)
        )
        path = self.path(pid, state, path_pointer)
        if path not in self.native_executables:
            raise Violation(f"untrusted executable dispatch: {path} (original pre-spawn inputs)")
        inputs = read_epochs.native_execution_input(self.native_argv(pid, argv_pointer), state.cwd)
        if self.native_admission:
            if self.native_admit is None:
                raise Violation("native Command admission has no active supervisor rendezvous")
            state.native_admission = self.native_admit(path, inputs)
        state.native_inputs = path, inputs

    def begin_native_job(self, pid, state, path):
        if (
            pid != self.make_pid or state.role != "make" or not state.observer_ready
            or state.native_dispatch is not None or path not in self.native_executables
            or state.native_inputs is None or state.native_inputs[0] != path
            or self.native_admission and state.native_admission is None
        ):
            raise Violation("native job lacks its actual original Make dispatch")
        sequence = len(self.native_jobs) + 1
        if sequence > self.config["descendant_limit"]:
            raise Violation("native job dispatch count exceeds its process bound")
        row = {
            "sequence": sequence, "executable": path, "pid": None,
            "context": None, "returncode": None, "terminal_status": None,
            "waited": False, "ignored": None,
        }
        if self.native_admission:
            row["admission"] = dict(state.native_admission)
        self.native_job_event(row)
        self.native_jobs[sequence] = row
        state.native_dispatch = sequence

    def bind_native_job(self, pid, state):
        row = self.native_jobs.get(state.native_dispatch)
        tree = self.read_trace is not None and self.read_trace.runtime
        descendant = tree and row is not None and row["pid"] is not None
        if (
            row is None or not descendant and (
                row["pid"] is not None or row["executable"] != state.exec_path
                or state.native_parent != self.make_pid
                or any(job["pid"] == pid for job in self.native_jobs.values())
            )
            or descendant and not any(
                event.get("child", event["pid"]) == pid for event in row["tree"]
            )
            or state.pidfd < 0
        ):
            raise Violation("native job exec has a foreign or reused dispatch child")
        self.native_job_event({"sequence": state.native_dispatch, "pid": pid})
        if not descendant:
            row["pid"] = pid
        if tree:
            with open(f"/proc/{pid}/cmdline", "rb") as stream:
                data = stream.read(65537)
            self.charge_metadata(len(data))
            if not data or len(data) > 65536 or not data.endswith(b"\0"):
                raise Violation("native job command line is missing or exceeds its byte bound")
            try:
                argv = [item.decode("utf-8", "strict") for item in data[:-1].split(b"\0")]
            except UnicodeDecodeError as error:
                raise Violation("native job command line is not strict UTF-8") from error
            inputs = read_epochs.native_execution_input(argv, state.cwd)
            if not descendant and (
                state.native_inputs is None or state.native_inputs != (state.exec_path, inputs)
            ):
                raise Violation("native exec-stop inputs differ from original pre-spawn dispatch")
            self.native_job_event({"sequence": state.native_dispatch, **inputs})
            if not descendant:
                row.update(inputs)
                row["tree"] = []
            state.native_execs += 1
            state.native_inputs = None
            if self.native_outputs is not None:
                if descendant:
                    state.native_admission = self.native_admit(state.exec_path, inputs, {
                        "dispatch": state.native_dispatch, "pid": pid,
                        "generation": state.native_execs,
                    })
                    root_admission = row["admission"]
                    if (
                        any(path not in root_admission["outputs"] for path in state.native_admission["outputs"])
                        or any(resource not in root_admission.get("resources", [])
                               for resource in state.native_admission.get("resources", []))
                    ):
                        raise Violation("native image operands escape its original job authority")
                else:
                    state.native_admission = dict(row["admission"])
            else:
                state.native_admission = None
            return {
                "kind": "exec", "pid": pid, "parent": state.native_parent,
                "generation": state.native_execs, "path": state.exec_path, **inputs,
                **({"admission": dict(state.native_admission)} if self.native_outputs is not None else {}),
            }
        state.native_inputs = None

    def observe_native_root(self, pid):
        if self.native_root is not None or pid != self.make_pid:
            raise Violation("native root initial execution has a foreign or reused PID")
        captures = []
        for name in ("cmdline", "environ"):
            with open(f"/proc/{pid}/{name}", "rb") as stream:
                data = stream.read(min(self.config["file_limit"], SYSCALL_MEMORY_LIMIT) + 1)
            self.charge_metadata(len(data))
            if len(data) > min(self.config["file_limit"], SYSCALL_MEMORY_LIMIT):
                raise Violation("native root input exceeds its existing byte bound")
            captures.append(data)
        argv, environment = read_epochs.native_root_inputs(*captures)
        if argv != self.config["argv"] or environment != self.config["environment"]:
            raise Violation("native root actual inputs differ from its initial request")
        cwd = self.config.get("cwd", "/repo")
        actual = os.stat(f"/proc/{pid}/cwd")
        expected = os.stat(Path(self.config["root"]) / cwd.lstrip("/"))
        self.charge_metadata(256)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise Violation("native root actual CWD differs from its initial request")
        read_epochs.native_execution_input(argv, cwd)
        row = {
            "version": 1, "pid": pid, "argv": argv, "cwd": cwd, "environment": environment,
            "exit_stop": None, "wait": None,
        }
        self.reserve_trace_observation()
        self.charge_metadata(len(encoded(row)))
        self.native_root = row

    def native_tree_event(self, state, event):
        if self.read_trace is None or not self.read_trace.runtime:
            return
        row = self.native_jobs.get(state.native_dispatch)
        if row is None or row["pid"] is None or row["terminal_status"] is not None:
            raise Violation("native tree event lacks its original dispatched root")
        event = {"seq": len(row["tree"]) + 1, **event}
        self.native_job_event(event)
        row["tree"].append(event)
        self.read_trace.machine_event(
            "native-tree", event["pid"], dispatch=state.native_dispatch,
            event={**event}, sha256=hashlib.sha256(encoded(event)).hexdigest(),
        )

    @staticmethod
    def pending_signal_mask(data):
        if not isinstance(data, bytes) or not 0 < len(data) <= 4096:
            raise Violation("native pending-signal status exceeds its actual extent")
        result = 0
        for name in (b"SigPnd:", b"ShdPnd:"):
            rows = [line.split() for line in data.splitlines() if line.startswith(name)]
            if (
                len(rows) != 1 or len(rows[0]) != 2
                or re.fullmatch(b"[0-9a-fA-F]{16}", rows[0][1]) is None
            ):
                raise Violation("native pending-signal status has an invalid kernel mask")
            result |= int(rows[0][1], 16)
        return result

    def native_exec_descriptors(self, pid, state):
        retained = {}
        for descriptor, path in state.fds.items():
            self.reserve_trace_observation()
            try:
                target = os.readlink(f"/proc/{pid}/fd/{descriptor}")
            except FileNotFoundError:
                continue
            data = os.fsencode(target)
            self.charge_metadata(len(data))
            if len(data) > 4096:
                raise Violation("native exec descriptor target exceeds the observation bound")
            retained[descriptor] = path
        return retained

    def native_exec_signals(self, pid, state):
        if self.read_trace is None or not self.read_trace.runtime or not state.native_signals:
            state.native_signals.clear()
            state.native_signal_origins.clear()
            return
        with open(f"/proc/{pid}/status", "rb") as stream:
            data = stream.read(4097)
        self.charge_metadata(len(data))
        pending = self.pending_signal_mask(data)
        for number in tuple(state.native_signals):
            if not pending & (1 << (number - 1)):
                del state.native_signals[number]
                state.native_signal_origins.pop(number, None)

    def native_signal_grant(self, state, number, origin=None, *, thread=False):
        if sum(state.native_signals.values()) >= self.config["observation_count"]:
            raise Violation("native pending-signal grants exceed the observation bound")
        self.reserve_trace_observation()
        self.charge_metadata(16)
        origins = state.native_signal_origins.setdefault(number, [])
        if number >= 32 or not any(row[2] == thread for row in origins):
            origins.append((*(origin or (None, None)), thread))
        state.native_signals[number] = len(origins)

    def native_queue_entry(self, pid, state, number, address):
        if not address:
            return
        self.charge_metadata(48)
        try:
            information = memory(pid, address, 48)
        except OSError as error:
            if error.errno not in {errno.EIO, errno.EFAULT}:
                raise
            return
        if int.from_bytes(information[8:12], "little", signed=True) != -1:
            return
        marker = os.urandom(16)
        replace_memory(pid, address + 32, marker)
        state.native_signal_buffer = (address + 32, information[32:48], marker, number)

    def native_pending_signals(self, pid):
        rows = []
        for flags in (0, 1):
            offset = 0
            while True:
                self.reserve_trace_observation()
                arguments = ctypes.create_string_buffer(
                    offset.to_bytes(8, "little") + flags.to_bytes(4, "little")
                    + (16).to_bytes(4, "little"), 16,
                )
                information = (ctypes.c_ubyte * (128 * 16))()
                count = ptrace(0x4209, pid, ctypes.addressof(arguments), ctypes.byref(information))
                if not 0 <= count <= 16:
                    raise Violation("invalid kernel pending-signal queue extent")
                self.charge_metadata(128 * count)
                for index in range(count):
                    row = bytes(information[index * 128:(index + 1) * 128])
                    rows.append((flags == 0, row[:48]))
                if count < 16:
                    break
                offset += count
        return rows

    def native_reconcile_signal_origins(self, pid, state):
        pending = {}
        available = {number: list(origins)
                     for number, origins in state.native_signal_origins.items()}
        for thread, information in self.native_pending_signals(pid):
            number = int.from_bytes(information[:4], "little", signed=True)
            code = int.from_bytes(information[8:12], "little", signed=True)
            if number == signal.SIGCHLD and code > 0:
                continue
            origins = available.get(number, [])
            origin = next(
                (row for row in origins if row[2] == thread and (
                    row[0] == information[32:48] if code == -1 else row[0] is None
                )),
                None,
            )
            if code == -1 and origin is None:
                raise Violation("unissued queued signal origin")
            if (
                code not in {0, -1, -6}
                or int.from_bytes(information[16:20], "little", signed=True) != pid
                or origin not in origins
            ):
                raise Violation("pending signal lacks its successful self-send")
            origins.remove(origin)
            pending.setdefault(number, []).append(origin)
        state.native_signal_origins = pending
        state.native_signals = {number: len(origins) for number, origins in pending.items()}

    def native_signal_consumed(self, pid, state, number, information=None):
        origins = state.native_signal_origins.get(number, [])
        if number not in state.native_signals or not origins:
            raise Violation("native signal consumption lacks its successful self-send")
        if information is None:
            rows = self.native_pending_signals(pid)
            pending = {row[32:48] for _, row in rows
                       if int.from_bytes(row[8:12], "little", signed=True) == -1}
            retired = [origin for origin in origins if origin[0] is not None and origin[0] not in pending]
            for thread in (False, True):
                unqueued = sum(
                    bucket == thread
                    and int.from_bytes(row[:4], "little", signed=True) == number
                    and int.from_bytes(row[8:12], "little", signed=True) in {0, -6}
                    and int.from_bytes(row[16:20], "little", signed=True) == pid
                    for bucket, row in rows
                )
                count = sum(row[0] is None and row[2] == thread for row in origins) - unqueued
                if count not in {0, 1}:
                    raise Violation("native signal consumption has ambiguous retired queue origins")
                if count:
                    retired.append((None, None, thread))
            if len(retired) != 1:
                raise Violation("native signal consumption has ambiguous retired queue origins")
            origin = retired[0]
        else:
            code = int.from_bytes(information[8:12], "little", signed=True)
            if (
                int.from_bytes(information[:4], "little", signed=True) != number
                or code not in {0, -1, -6}
                or int.from_bytes(information[16:20], "little", signed=True) != pid
            ):
                raise Violation("native shell signal delivery is not its admitted self-signal")
            origin = next(
                (row for row in origins if row[0] == information[32:48]),
                None,
            ) if code == -1 else None
            if code == -1 and origin is None:
                raise Violation("unissued queued signal origin")
            if code != -1:
                return self.native_signal_consumed(pid, state, number)
        if origin not in origins:
            raise Violation("native signal consumption lacks its exact pending origin")
        origins.remove(origin)
        state.native_signals[number] -= 1
        if not state.native_signals[number]:
            del state.native_signals[number]
            del state.native_signal_origins[number]
        return origin[1]

    def native_sigkill_exit(self, pid, state, status):
        if not self.native_readonly or state.role != "native" or status != signal.SIGKILL:
            return
        registers = Registers()
        self.charge_metadata(ctypes.sizeof(registers))
        ptrace(GETREGS, pid, 0, ctypes.byref(registers))
        number = registers.orig_rax
        if (
            state.pending != ("native-signal", signal.SIGKILL)
            or state.kernel_call != number or signed(registers.rax) != 0
        ):
            raise Violation("native SIGKILL lacks an actual successful self-send outcome")
        if self.signal_request(
            pid, number, registers.rdi, registers.rsi, registers.rdx,
        ) != signal.SIGKILL:
            raise Violation("native SIGKILL lacks an actual successful self-send outcome")
        state.native_sigkill_outcome = True

    def native_child_signal(self, pid, state):
        if self.read_trace is None or not self.read_trace.runtime:
            return False
        information = (ctypes.c_ubyte * 128)()
        self.charge_metadata(ctypes.sizeof(information))
        ptrace(0x4202, pid, 0, ctypes.byref(information))
        code = int.from_bytes(bytes(information[8:12]), "little", signed=True)
        if code not in {1, 2, 3}:
            return False
        child = int.from_bytes(bytes(information[16:20]), "little", signed=True)
        row = self.native_jobs.get(state.native_dispatch)
        if (
            int.from_bytes(bytes(information[:4]), "little", signed=True) != signal.SIGCHLD
            or row is None or not any(
                event["kind"] == "fork" and event["pid"] == pid and event["child"] == child
                for event in row["tree"]
            )
        ):
            raise Violation("native SIGCHLD lacks its actual owned child")
        self.native_tree_event(state, {
            "kind": "signal", "pid": pid, "child": child, "code": code,
            "status": int.from_bytes(bytes(information[24:28]), "little", signed=True),
        })
        state.delivery_signal = signal.SIGCHLD
        return True

    def native_job_frame(self, pid, state, pointer, size):
        if (
            not self.native_readonly or pid != self.make_pid or state.role != "make"
            or not state.observer_ready or size != 24
        ):
            raise Violation("invalid native job notification sender or frame")
        self.charge_metadata(size)
        frame = memory(pid, pointer, size)
        values = tuple(int.from_bytes(frame[offset:offset + 8], "little") for offset in (0, 8, 16))
        child = values[0]
        jobs = [row for row in self.native_jobs.values() if row["pid"] == child]
        if not 0 < child < 1 << 31 or child == self.make_pid or len(jobs) != 1:
            raise Violation("native job notification refers to a foreign child")
        return jobs[0], values

    def observe_native_job_context(self, pid, state, pointer, size):
        row, (_, target_pointer, command_line) = self.native_job_frame(pid, state, pointer, size)
        target = cstring(pid, target_pointer, limit=4097) if target_pointer else None
        if command_line >= 1 << 32 or target_pointer and not target or not target_pointer and command_line:
            raise Violation("invalid native job target or command index")
        if target is not None:
            self.charge_metadata(len(target.encode("utf-8")) + 1)
        context = {
            "kind": "recipe" if target is not None else "expansion",
            "target": target, "command_line": command_line if target is not None else None,
        }
        if row["context"] is not None:
            if row["context"] != context:
                raise Violation("native job context changed for its actual child")
            return
        if row["waited"]:
            raise Violation("native job context arrived after retirement")
        self.native_job_event({"sequence": row["sequence"], "context": context})
        row["context"] = context

    def observe_native_job_policy(self, pid, state, pointer, size):
        row, (_, status, flags) = self.native_job_frame(pid, state, pointer, size)
        if (
            status >= 1 << 32 or flags & ~3 or row["context"] is None or row["waited"]
            or row["terminal_status"] is None or not (os.WIFEXITED(status) or os.WIFSIGNALED(status))
            or bool(flags & 2) != (row["context"]["kind"] == "recipe")
            or status != row["terminal_status"] or os.waitstatus_to_exitcode(status) != row["returncode"]
        ):
            raise Violation("native job wait result differs from its actual terminal lifecycle")
        if self.read_trace is not None and self.read_trace.runtime:
            read_epochs.native_job_tree(
                row["tree"], row, self.make_pid, self.native_executables,
                count_limit=self.config["observation_count"],
                writable=self.native_outputs is not None,
            )
        self.native_job_event({"sequence": row["sequence"], "wait_status": status, "flags": flags})
        row["waited"], row["ignored"] = True, bool(flags & 1)
        if self.read_trace is not None and self.read_trace.version in {4, 5, 6}:
            self.read_trace.machine_event(
                "native-policy", pid, dispatch=row["sequence"], child=row["pid"],
                context=dict(row["context"]), ignored=row["ignored"], status=status,
            )
        self.observe("accessed", "native-job:" + encoded(row).decode("ascii"))

    def retire_native_job(self, pid, state, status):
        row = self.native_jobs.get(state.native_dispatch)
        if self.read_trace is not None and self.read_trace.runtime:
            self.native_tree_event(state, {"kind": "exit", "pid": pid, "status": status})
            if row is not None and pid != row["pid"]:
                return
        if row is None or row["pid"] != pid or row["terminal_status"] is not None:
            raise Violation("native job terminal event lost its actual dispatched child")
        code = os.waitstatus_to_exitcode(status)
        self.native_job_event({"sequence": row["sequence"], "returncode": code, "terminal_status": status})
        row["returncode"], row["terminal_status"] = code, status

    def finish_native_jobs(self):
        if self.native_readonly and any(
            row["pid"] is None or row["context"] is None or row["terminal_status"] is None or not row["waited"]
            for row in self.native_jobs.values()
        ):
            raise Violation("native job observation ended with incomplete actual lifecycle")
        if self.read_trace is not None and self.read_trace.version == 4:
            executed = [
                (row["dispatch"], row["pid"]) for row in self.read_trace.machine
                if row["kind"] == "execute" and row["make"] is False
            ]
            if (
                any(type(dispatch) is not int or type(pid) is not int for dispatch, pid in executed)
                or sorted(executed) != sorted((key, row["pid"]) for key, row in self.native_jobs.items())
            ):
                raise Violation("native machine execution differs from actual readonly job dispatch")

    def defer_observation(self, state, collection, value):
        # Failed attempts still spend bounded bookkeeping, not evidence credit.
        self.reserve_observation(collection, value)
        state.observations.append((collection, value))

    def observe_directory(self, pid, state, value, returned):
        path, address, requested, wide = value
        if returned > requested or returned > SYSCALL_MEMORY_LIMIT:
            raise Violation("directory result exceeds its observed buffer")
        if returned == 0:
            return
        self.observation_bytes += returned
        if self.observation_bytes > self.config["observation_limit"]:
            raise Violation("aggregate directory-observation byte budget exhausted")
        names = directory_entries(memory(pid, address, returned), wide=wide)
        for name in names:
            entry = path.rstrip("/") + "/" + name
            # Enumeration observes this name/type, not a symlink's referent.
            if self.mode == "make" and (entry == "/repo" or entry.startswith("/repo/")):
                self.observe("accessed", entry)
            elif entry in self.sources:
                self.observe("consumed", entry.removeprefix("/repo/"))
            elif entry in self.code:
                self.observe("code_consumed", entry.removeprefix("/repo/"))

    @staticmethod
    def virtual_memory(pid):
        with open(f"/proc/{pid}/statm", "rb") as source:
            data = source.read(257)
        fields = data.split()
        if len(data) > 256 or len(fields) != 7 or not all(value.isdigit() for value in fields):
            raise Violation("cannot account stopped address space")
        size = int(fields[0]) * os.sysconf("SC_PAGE_SIZE")
        if size <= 0:
            raise Violation("stopped address space disappeared")
        return size

    def memory_members(self, state):
        return [
            (pid, record) for pid, record in self.processes.items()
            if record.memory_group == state.memory_group
        ]

    def memory_available(self, state, copies=0):
        members = self.memory_members(state)
        outside = sum(
            record.memory_limit for record in self.processes.values()
            if record.memory_group != state.memory_group
        ) + sum(record.memory_reservation for record in self.processes.values())
        return (self.config["memory_limit"] - outside) // (len(members) + copies)

    def assign_memory(self, state, limit, *, reservation=None):
        # All members of a shared-mm group are suspended vfork ancestors except
        # the stopped tracee. Other groups retain their kernel-enforced credits.
        members = self.memory_members(state)
        pending = state.memory_reservation if reservation is None else reservation
        used = sum(
            record.memory_limit for record in self.processes.values()
            if record.memory_group != state.memory_group
        ) + len(members) * limit + sum(
            record.memory_reservation for record in self.processes.values() if record is not state
        ) + pending
        if not members or limit <= 0 or pending < 0 or used > self.config["memory_limit"]:
            raise Violation("aggregate address-space credit invariant failed before kernel grant")
        for pid, record in members:
            resource.prlimit(pid, resource.RLIMIT_AS, (limit, self.config["memory_limit"]))
            record.memory_limit = limit
        state.memory_reservation = pending
        self.memory_peak = max(self.memory_peak, used)

    def reserve_memory(self, pid, state, additional, *, copies=0):
        additional = (additional + 4095) & ~4095
        needed = self.virtual_memory(pid) + additional
        available = self.memory_available(state, copies)
        if needed > available:
            raise Violation("aggregate address-space budget exhausted before allocation/fork")
        limit = min(available, needed + STACK_LIMIT)
        self.assign_memory(state, limit, reservation=copies * limit)

    def reserve_exec(self, pid, state):
        available = self.memory_available(state)
        if self.virtual_memory(pid) > available:
            raise Violation("aggregate address-space budget exhausted before exec")
        # Kernel exec mappings and initial stack may grow without mmap stops.
        # Fund that entire transition, including both sides of shared-mm exec.
        self.assign_memory(state, available)

    def finish_exec(self, pid, state):
        previous_group = state.memory_group
        state.memory_group = pid
        self.assign_memory(
            state, min(state.memory_limit, self.virtual_memory(pid) + STACK_LIMIT),
        )
        old = [
            (parent, record) for parent, record in self.processes.items()
            if parent != pid and record.memory_group == previous_group
        ]
        if old:
            parent, record = old[0]
            self.assign_memory(
                record, min(record.memory_limit, self.virtual_memory(parent) + STACK_LIMIT),
            )

    def reserve_creation(self):
        self.created += 1
        if self.created > self.config["creation_limit"]:
            raise Violation("aggregate file-creation budget exhausted")

    def account_processes(self):
        live = len(self.processes) + len(self.newborn_stops)
        self.live_process_peak = max(self.live_process_peak, live)
        if live > self.config["process_limit"]:
            raise Violation("live descendant-process capacity exhausted")
        if self.total_processes > self.config["descendant_limit"]:
            raise Violation("aggregate descendant-process budget exhausted")
        if len(self.newborn_stops) > sum(
            record.process_reservation for record in self.processes.values()
        ):
            raise Violation("unreserved newborn process")

    def reserve_process(self, state):
        if state.process_reservation:
            raise Violation("overlapping process-creation reservation")
        reserved = sum(record.process_reservation for record in self.processes.values())
        if len(self.processes) + reserved >= self.config["process_limit"]:
            raise Violation("live descendant-process capacity exhausted before creation")
        # A newborn-first stop is already in total_processes, but still owns
        # its parent's reservation until the fork event authenticates it.
        if self.total_processes + reserved - len(self.newborn_stops) >= self.config["descendant_limit"]:
            raise Violation("aggregate descendant-process budget exhausted before creation")
        state.process_reservation = True

    def publication_name(self, name):
        reserved = self.config.get("reserved_paths")
        if reserved is None:
            raise Violation("query has no generated publication authority")
        if (
            not isinstance(name, str) or not 1 <= len(name.encode("utf-8")) <= 4096
            or name.startswith("/") or "\\" in name
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise Violation("generated output path escapes the readonly view")
        if any(
            name == path or name.startswith(path + "/") or path.startswith(name + "/")
            for path in reserved
        ):
            raise Violation("generated result conflicts with admitted source authority")

    def adopt_published(self, records):
        if not isinstance(records, list) or len(records) > self.config["publication_limit"]:
            raise Violation("published context exceeds the existing creation bound")
        adopted = {}
        for record in records:
            if not isinstance(record, list) or len(record) != 6:
                raise Violation("malformed completed publication")
            name, owner, mode, size, digest, identity = record
            self.publication_name(name)
            if (
                name in adopted or type(mode) is not int or not 0 <= mode <= 0o777
                or type(size) is not int or not 0 <= size <= self.config["file_limit"]
                or not isinstance(owner, str) or not re.fullmatch("[0-9a-f]{64}", owner)
                or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
            ):
                raise Violation("invalid completed publication identity")
            try:
                identity = validate_publication_identity(identity, mode, size)
            except ChannelError as error:
                raise Violation(str(error)) from error
            producer = bytes.fromhex(owner)
            if name in self.published and self.published[name][0] != producer:
                raise Violation("conflicting generated output producers")
            self.reserve_observation("accessed", "published:" + hashlib.sha256(encoded(record)).hexdigest())
            self.charge_metadata(len(encoded(record)))
            # Re-read through the existing readonly mount, never the writable
            # backing alias: validation must not change source atime.
            directory = os.open(
                Path(self.config["root"]) / "repo", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                parts = name.split("/")
                for part in parts[:-1]:
                    following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = following
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(descriptor, "rb") as source:
                    before = os.fstat(source.fileno())
                    if publication_identity(before) != identity:
                        raise Violation("published source type, mode or size changed")
                    self.charge_metadata(size)
                    remaining, actual = size, hashlib.sha256()
                    while remaining:
                        if time.monotonic() >= self.config["deadline"]:
                            raise Violation("aggregate deadline exhausted during readonly publication validation")
                        data = source.read(min(remaining, SYSCALL_MEMORY_LIMIT))
                        if not data:
                            raise Violation("published source was truncated")
                        actual.update(data)
                        remaining -= len(data)
                    after = os.fstat(source.fileno())
                    if (
                        actual.hexdigest() != digest or before != after
                        or publication_identity(os.stat(
                            parts[-1], dir_fd=directory, follow_symlinks=False,
                        )) != identity
                        or any(getattr(before, field) != getattr(after, field) for field in (
                            "st_atime_ns", "st_mtime_ns", "st_ctime_ns", "st_blksize", "st_blocks", "st_rdev",
                        ))
                    ):
                        raise Violation("published source changed during readonly validation")
            finally:
                os.close(directory)
            adopted[name] = producer, identity
        self.published.update(adopted)

    def _content_matches(self, mapping, directory, name, size, identity):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as current:
            if publication_identity(os.fstat(current.fileno())) != identity:
                raise Violation("published output identity changed before comparison")
            offset, equal = 0, True
            position = mapping.tell()
            while offset < size:
                if time.monotonic() >= self.config["deadline"]:
                    raise Violation("aggregate deadline exhausted during content comparison")
                count = min(size - offset, SYSCALL_MEMORY_LIMIT)
                self.charge_metadata(2 * count)
                wanted = os.pread(mapping.fileno(), count, position + offset)
                actual = current.read(count)
                if len(wanted) != count or len(actual) != count:
                    raise Violation("publication comparison input was truncated")
                equal = equal and wanted == actual
                offset += count
            if (
                publication_identity(os.fstat(current.fileno())) != identity
                or publication_identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != identity
            ):
                raise Violation("published output changed during content comparison")
        return equal

    def publish(self, key, *, owner, outputs, policy="replace"):
        if type(policy) is not str or policy not in PUBLICATION_POLICIES or (
            policy != "replace" and not outputs
        ):
            raise Violation("invalid generated publication policy")
        mapping = Path(self.config["root"]) / "control/map"
        path = mapping / f"{key:016x}.files"
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if outputs:
                raise Violation("declared generated result is missing")
            return []
        names, effective = [], []
        with os.fdopen(descriptor, "rb") as source:
            status = os.fstat(source.fileno())
            if not stat.S_ISREG(status.st_mode) or not 48 <= status.st_size <= self.config["file_limit"]:
                raise Violation("invalid generated mapping file bound")
            self.observation_bytes += status.st_size
            if self.observation_bytes > self.config["observation_limit"]:
                raise Violation("aggregate generated mapping observation budget exhausted")
            remaining = status.st_size
            def take(size):
                nonlocal remaining
                if time.monotonic() >= self.config["deadline"]:
                    raise Violation("aggregate probe deadline exhausted during publication")
                if size > remaining:
                    raise Violation("truncated generated mapping")
                data = source.read(size)
                if len(data) != size:
                    raise Violation("truncated generated mapping")
                remaining -= size
                return data
            def integer():
                return int.from_bytes(take(4), "little")
            if take(8) != PUBLICATION_MAGIC:
                raise Violation("invalid generated mapping protocol")
            producer = take(32)
            if producer.hex() != owner:
                raise Violation("generated result has a foreign producer identity")
            if integer() != PUBLICATION_POLICIES.index(policy):
                raise Violation("generated mapping publication policy differs from its request")
            count = integer()
            if count != len(outputs) or not 1 <= count <= self.config["creation_limit"]:
                raise Violation("generated output count exceeds creation bound")
            view = next(item["source"] for item in self.config["mounts"] if item["target"] == "/repo")
            for _ in range(count):
                length, mode, size = integer(), integer(), integer()
                if not 1 <= length <= 4096 or mode & ~0o777 or size > self.config["file_limit"]:
                    raise Violation("invalid generated output declaration")
                try:
                    name = take(length).decode("utf-8", "strict")
                except UnicodeDecodeError as error:
                    raise Violation("generated output path is not UTF-8") from error
                self.publication_name(name)
                if name not in outputs or name in names:
                    raise Violation("generated result differs from its exact declared outputs")
                directory = os.open(view, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    parts = name.split("/")
                    for part in parts[:-1]:
                        created = False
                        try:
                            following = os.open(
                                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory,
                            )
                        except FileNotFoundError:
                            self.reserve_creation()
                            os.mkdir(part, 0o755, dir_fd=directory)
                            following = os.open(
                                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory,
                            )
                            created = True
                        os.close(directory)
                        directory = following
                        if created and self.config["sudo_drop"]:
                            # Transfer before adding children, so even failed publication
                            # remains removable by the unprivileged report owner.
                            os.fchown(directory, self.config["runner_uid"], self.config["runner_gid"])
                    if name in self.published and self.published[name][0] != producer:
                        raise Violation("conflicting generated output producers")
                    try:
                        current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        current = None
                    identity = None if current is None else publication_identity(current)
                    if current is not None:
                        if name not in self.published:
                            raise Violation("generated output conflicts with immutable source")
                        if (
                            not stat.S_ISREG(current.st_mode) or current.st_mode & 0o7000
                            or identity != self.published[name][1]
                        ):
                            raise Violation("published output identity or type changed")
                    retain = (
                        policy == "if-content-changed" and current is not None
                        and current.st_size == size
                        and self._content_matches(source, directory, parts[-1], size, identity)
                    )
                    digest = hashlib.sha256()
                    if retain:
                        left = size
                        while left:
                            data = take(min(left, SYSCALL_MEMORY_LIMIT))
                            digest.update(data)
                            left -= len(data)
                        if publication_identity(os.stat(
                            parts[-1], dir_fd=directory, follow_symlinks=False,
                        )) != identity:
                            raise Violation("retained output identity changed")
                        mode = stat.S_IMODE(current.st_mode)
                        effect = "retained"
                    else:
                        self.written += size
                        if self.written > self.config["write_limit"]:
                            raise Violation("aggregate generated publication byte budget exhausted")
                        if current is not None:
                            if publication_identity(os.stat(
                                parts[-1], dir_fd=directory, follow_symlinks=False,
                            )) != identity:
                                raise Violation("published output changed before replacement")
                            os.unlink(parts[-1], dir_fd=directory)
                        self.reserve_creation()
                        try:
                            output = os.open(
                                parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=directory,
                            )
                        except FileExistsError as error:
                            raise Violation("generated output conflicts with immutable source") from error
                        with os.fdopen(output, "wb", buffering=0) as destination:
                            if self.config["sudo_drop"]:
                                os.fchown(destination.fileno(), self.config["runner_uid"], self.config["runner_gid"])
                            left = size
                            while left:
                                data = take(min(left, SYSCALL_MEMORY_LIMIT))
                                digest.update(data)
                                written = 0
                                while written < len(data):
                                    amount = destination.write(memoryview(data)[written:])
                                    if not amount:
                                        raise Violation("incomplete generated output write")
                                    written += amount
                                left -= len(data)
                            os.fchmod(destination.fileno(), mode)
                            identity = publication_identity(os.fstat(destination.fileno()))
                        if (
                            identity != publication_identity(os.stat(
                                parts[-1], dir_fd=directory, follow_symlinks=False,
                            ))
                            or identity[2] != stat.S_IFREG | mode or identity[3] != size
                        ):
                            raise Violation("generated output changed during publication")
                        effect = "created" if current is None else "replaced"
                    self.published[name] = producer, identity
                    result = {
                        "path": name, "mode": mode, "size": size, "sha256": digest.hexdigest(),
                        "effect": effect, "identity": list(identity),
                    }
                    self.charge_metadata(len(encoded(result)))
                    effective.append(result)
                    names.append(name)
                finally:
                    os.close(directory)
            if remaining:
                raise Violation("trailing generated mapping bytes")
            if publication_identity(os.fstat(source.fileno())) != publication_identity(status):
                raise Violation("generated mapping changed during publication")
        return effective

    def counters(self):
        return {
            "processes": self.total_processes, "syscalls": self.calls,
            "written_bytes": self.written, "created_files": self.created,
            "observation_bytes": self.observation_bytes,
            "observations": self.observation_count(),
            "live_process_peak": self.live_process_peak, "memory_peak": self.memory_peak,
        }

    def reservations(self):
        return {
            "live": len(self.processes) + len(self.newborn_stops),
            "processes": len(self.processes) + sum(record.process_reservation for record in self.processes.values()),
            "memory": sum(record.memory_limit + record.memory_reservation for record in self.processes.values()),
            "pending": len(self.producer_requests),
        }

    def apply_producer_limits(self, values, ceilings):
        spent = {
            "descendant_limit": self.total_processes, "syscall_limit": self.calls,
            "write_limit": self.written, "creation_limit": self.created,
            "observation_count": self.observation_count(),
            "observation_limit": self.observation_bytes,
            "process_limit": self.reservations()["processes"],
            "memory_limit": self.reservations()["memory"],
        }
        if not isinstance(values, dict) or set(values) != set(spent) or any(
            type(values[name]) is not int or not spent[name] <= values[name] <= ceilings[name]
            for name in spent
        ):
            raise Violation("invalid or unfunded producer resumption grant")
        self.config.update(values)


    def observer(self, state, registers):
        return state.role == "make" and any(
            start <= registers.rip < end for start, end in state.observer_ranges
        )

    def resolve(self, name, *, follow_final=True, native_maps_omission=False):
        # Only trusted, immutable symlinks remain: candidate symlinks and
        # ancestor relocation are forbidden. Resolve in the guest root, not
        # through the supervisor's host-root interpretation of absolute links.
        if native_maps_omission and name != "/proc/self/maps":
            raise Violation("native maps omission requires its exact optional spelling")
        pending = deque(name.split("/"))
        resolved = []
        links = 0
        while pending:
            part = pending.popleft()
            if part in {"", "."}:
                continue
            if part == "..":
                if resolved:
                    resolved.pop()
                continue
            if pending or follow_final and not native_maps_omission:
                alias = "/" + "/".join((*resolved, part))
                if (self.mode == "make" or self.config.get("metadata_validation")) and alias in self.config.get("runtime_aliases", ()):
                    spelling = posixpath.normpath(name)
                    if ".." in name.split("/") or (
                        spelling not in self.config["executables"] and not self.runtime_metadata(spelling)
                        and not (self.native_readonly and spelling == "/bin/sh")
                        and not (native_maps_omission and alias == "/proc/self")
                    ):
                        raise Violation(f"unrequested stock runtime alias spelling: {name}")
                try:
                    target = os.readlink(Path(self.config["root"]).joinpath(*resolved, part))
                except OSError as error:
                    if error.errno not in {errno.EINVAL, errno.ENOENT, errno.ENOTDIR}:
                        raise Violation("cannot resolve confined pathname") from error
                else:
                    links += 1
                    try:
                        target_size = len(target.encode("utf-8", "strict"))
                    except UnicodeEncodeError as error:
                        raise Violation("symlink target is not strict UTF-8") from error
                    if links > 40 or target_size + sum(len(item) + 1 for item in pending) > 4096:
                        raise Violation("confined symlink resolution exceeds bound")
                    if target.startswith("/"):
                        resolved.clear()
                    pending.extendleft(reversed(target.split("/")))
                    continue
            resolved.append(part)
        result = "/" + "/".join(resolved)
        if native_maps_omission:
            self.charge_metadata(128)
            if (
                re.fullmatch(r"/proc/(?:self|[1-9][0-9]*)/maps", result) is None
                or self.source_mode(result) is not None
            ):
                raise Violation("native optional maps probe requires its genuinely omitted guest leaf")
        return result

    def native_maps_omission(self, state):
        return (
            self.mode == "make" and self.native_readonly and state.role == "native"
            and state.path_context is not None and state.path_context[0] == "/proc/self/maps"
        )

    def path(self, pid, state, address, dirfd=-100, *, follow_final=True):
        dirfd = ctypes.c_int(dirfd).value
        name = cstring(pid, address)
        if "\0" in name:
            raise Violation("embedded NUL path")
        if not name:
            base = state.cwd if dirfd == -100 else self.fd(state, dirfd)
            state.path_context = (name, dirfd, base)
            return base
        spelling, base = name, None
        if not name.startswith("/"):
            base = state.cwd if dirfd == -100 else self.fd(state, dirfd)
            if base.startswith("<"):
                raise Violation("relative path through a non-directory descriptor")
            name = base.rstrip("/") + "/" + name
        state.path_context = (spelling, dirfd, base)
        return self.resolve(
            name, follow_final=follow_final, native_maps_omission=self.native_maps_omission(state),
        )

    def fd(self, state, fd):
        if fd not in state.fds:
            raise Violation(f"unavailable inherited/unknown descriptor {fd}")
        return state.fds[fd]

    def source_mode(self, path):
        for forbidden in self.config["forbidden_paths"]:
            if path == forbidden or path.startswith(forbidden + "/"):
                raise Violation(f"nonregular candidate source denied: {path}")
        full = Path(self.config["root"]) / path.lstrip("/")
        try:
            return full.lstat().st_mode
        except FileNotFoundError:
            return None

    def execute_credentials(self, pid, effective):
        with open(f"/proc/{pid}/status", "rb") as source:
            data = source.read(SYSCALL_MEMORY_LIMIT + 1)
        self.charge_metadata(len(data))
        if len(data) > SYSCALL_MEMORY_LIMIT:
            raise Violation("Make execute credentials exceed the observation bound")
        fields = {}
        for line in data.splitlines():
            name, separator, value = line.partition(b":")
            if name in {b"Uid", b"Gid", b"Groups", b"CapPrm", b"CapEff"}:
                if not separator or name in fields:
                    raise Violation("malformed Make execute credentials")
                fields[name] = value.split()
        if set(fields) != {b"Uid", b"Gid", b"Groups", b"CapPrm", b"CapEff"}:
            raise Violation("incomplete Make execute credentials")
        for name in (b"Uid", b"Gid", b"Groups"):
            values = fields[name]
            if (name != b"Groups" and len(values) != 4) or any(
                not value.isdigit() or int(value) >= 1 << 32 for value in values
            ):
                raise Violation("invalid Make execute credential identity")
            fields[name] = tuple(map(int, values))
        for name in (b"CapPrm", b"CapEff"):
            if len(fields[name]) != 1 or not re.fullmatch(b"[0-9a-fA-F]{16}", fields[name][0]):
                raise Violation("invalid Make execute capabilities")
            if int(fields[name][0], 16):
                raise Violation("Make execute permission requires its existing capability-free caller")
        index = 3 if effective else 0
        return fields[b"Uid"][index], {fields[b"Gid"][index], *fields[b"Groups"]}

    def source_execute_allowed(self, pid, path, effective):
        uid, gids = self.execute_credentials(pid, effective)
        full = Path(self.config["root"]) / path.lstrip("/")
        info = full.lstat()
        if not stat.S_ISREG(info.st_mode):
            return False
        # Status and stat IDs use this supervisor's user namespace. Refuse an
        # unmapped/overflow identity instead of comparing its lossy spelling.
        for kind, values in (("uid", {uid, info.st_uid}), ("gid", {info.st_gid})):
            with open(f"/proc/self/{kind}_map", "rb") as source:
                data = source.read(SYSCALL_MEMORY_LIMIT + 1)
            self.charge_metadata(len(data))
            if len(data) > SYSCALL_MEMORY_LIMIT:
                raise Violation("Make execute identity mapping exceeds the observation bound")
            ranges = []
            for line in data.splitlines():
                values_in_range = line.split()
                if len(values_in_range) != 3 or not all(value.isdigit() for value in values_in_range):
                    raise Violation("invalid Make execute identity mapping")
                first, _, count = map(int, values_in_range)
                if count <= 0 or first + count > 1 << 32:
                    raise Violation("invalid Make execute identity mapping")
                ranges.append((first, first + count))
            if any(not any(first <= value < last for first, last in ranges) for value in values):
                raise Violation("Make execute permission has an unrepresentable source or caller identity")
            if kind == "uid" and uid == info.st_uid:
                return execute_mode_allows(info, uid, gids)
            if kind == "gid":
                gids = {gid for gid in gids if any(first <= gid < last for first, last in ranges)}
        try:
            acl = os.getxattr(full, "system.posix_acl_access", follow_symlinks=False)
        except OSError as error:
            if error.errno not in {errno.ENODATA, errno.EOPNOTSUPP}:
                raise
        else:
            self.charge_metadata(len(acl))
            raise Violation("Make non-owner execute permission requires a source without an extended ACL")
        return execute_mode_allows(info, uid, gids)

    def absent_source(self, state, path, operation):
        if self.source_mode(path) is not None:
            raise Violation(f"undeclared source {operation}: {path}")
        self.defer_observation(state, "accessed", path)

    def charge_metadata(self, size):
        self.observation_bytes += size
        if self.observation_bytes > self.config["observation_limit"]:
            raise Violation("aggregate metadata observation byte budget exhausted")

    def metadata_buffer(self, pid, address, size):
        if not size:
            return b""
        if size > SYSCALL_MEMORY_LIMIT:
            return None
        self.charge_metadata(size)
        if not address:
            return None
        try:
            return memory(pid, address, size)
        except OSError as error:
            if error.errno not in {errno.EIO, errno.EFAULT}:
                raise
            return None

    def directory_offset(self, pid, descriptor):
        with open(f"/proc/{pid}/fdinfo/{descriptor}", "rb") as source:
            data = source.read(4097)
        self.charge_metadata(len(data))
        if len(data) > 4096:
            raise Violation("directory descriptor metadata exceeds bound")
        match = re.search(rb"^pos:\s+([0-9]+)$", data, re.MULTILINE)
        if match is None or int(match[1]) >= 1 << 64:
            raise Violation("directory descriptor has no bounded offset")
        return int(match[1])

    def begin_metadata(self, pid, state, r, path):
        optional = self.runtime_metadata(
            path, parents=self.mode == "make" or bool(self.config.get("metadata_validation")),
        )
        if (
            not (path == "/repo" or path.startswith("/repo/") or optional)
            or self.mode == "make" and state.role != "helper" and not (
                optional and (state.observer_ready or self.native_readonly and state.role == "native")
            )
        ):
            return
        number = r.orig_rax
        flags = mask = size = address = offset = 0
        if number in {4, 5, 6}:
            size, address = 144, r.rsi
        elif number in {137, 138}:
            size, address = 120, r.rsi
        elif number == 262:
            size, address, flags = 144, r.rdx, r.r10 & 0xFFFFFFFF
        elif number == 332:
            size, address = 256, r.r8
            flags, mask = r.rdx & 0xFFFFFFFF, r.r10 & 0xFFFFFFFF
        elif number == 21:
            flags = r.rsi & 0xFFFFFFFF
        elif number in {269, 439}:
            mask = r.rdx & 0xFFFFFFFF
            flags = r.r10 & 0xFFFFFFFF if number == 439 else 0
        elif number == 89:
            size, address = r.rdx, r.rsi
        elif number == 267:
            size, address = r.r10, r.rdx
        elif number in {78, 217}:
            size, address = r.rdx, r.rsi
            offset = self.directory_offset(pid, r.rdi)
        else:
            return
        request = (number, path, flags, mask, size, offset)
        if state.role == "helper":
            records = self.metadata_authority(state)
            if not any(tuple(record[:6]) == request for record in records):
                raise Violation(f"metadata helper request exceeds its recorded operation: {path}")
            self.reserve_observation("accessed", "revalidation:" + repr(request))
            state.metadata_pending = (request, address, None)
        else:
            self.reserve_observation("accessed", "metadata-attempt:" + repr(request))
            seed = self.metadata_buffer(pid, address, size)
            state.metadata_pending = (request, address, seed)

    def finish_metadata(self, pid, state, result):
        pending, state.metadata_pending = state.metadata_pending, None
        if pending is None:
            return
        request, address, before = pending
        size = request[4]
        if state.role == "helper":
            self.charge_metadata(min(size, SYSCALL_MEMORY_LIMIT))
            return
        after = self.metadata_buffer(pid, address, size)
        record = [
            *request, result,
            None if before is None else before.hex(),
            None if after is None else after.hex(),
        ]
        payload = json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        self.charge_metadata(len(payload))
        key = hashlib.sha256(payload).hexdigest()
        self.reserve_observation("accessed", "metadata:" + key)
        if key not in self.metadata_seen:
            self.metadata_seen.add(key)
            self.metadata.append(record)

    def metadata_authority(self, state):
        index = state.metadata_index
        entries = self.config.get("mapping_entries", ())
        if index is None or not 0 <= index < len(entries):
            return ()
        return entries[index]["metadata"]

    def validate_native_metadata_mount(self, path, identity):
        mounts = [item for item in self.config["mounts"] if item["target"] == path]
        guest_path = Path(self.config["root"]) / path.lstrip("/")
        if identity is None:
            for source in (Path(path), guest_path):
                try:
                    source.lstat()
                except FileNotFoundError:
                    continue
                raise Violation("absent native metadata directory has present backing")
            if mounts:
                raise Violation("absent native metadata directory has present backing")
            return
        expected = {"source": path, "target": path, "writable": False, "executable": False}
        source, guest = Path(path).lstat(), guest_path.lstat()
        actual = [source.st_dev, source.st_ino, source.st_mode, source.st_uid, source.st_gid]
        mounted = os.statvfs(guest_path)
        self.charge_metadata(len(encoded([list(source), list(guest), list(mounted)])))
        required = os.ST_RDONLY | os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC
        if (
            mounts != [expected] or actual[:3] != identity[:3]
            or (guest.st_dev, guest.st_ino, guest.st_mode, guest.st_uid, guest.st_gid)
            != tuple(actual) or mounted.f_flag & required != required
        ):
            raise Violation(
                f"native metadata directory mount identity or flags differ: "
                f"{path} source={actual} expected={identity} "
                f"guest={[guest.st_dev, guest.st_ino, guest.st_mode, guest.st_uid, guest.st_gid]} "
                f"flags={mounted.f_flag} mounts={mounts}"
            )

    def runtime_metadata(self, path, *, parents=True):
        return (
            path in self.native_metadata_directories
            or path in self.config.get("runtime_files", ())
            or parents and path in self.config.get("runtime_parents", ())
            or any(path.startswith(absent + "/") for absent in self.config.get("runtime_absent", ()))
        )

    def check_optional_make_spelling(self, state, path, operation):
        if (
            self.mode != "make" or state.path_context is None
            or state.role != "make" and not (self.native_readonly and state.role == "native")
        ):
            return
        # Shared mandatory directory metadata does not need the optional grant.
        if operation == "metadata" and path in self.runtime_directories:
            return
        spelling, dirfd, base = state.path_context
        if ".." in spelling.split("/"):
            raise Violation(
                f"optional Make runtime parent spelling denied: {spelling!r} "
                f"(dirfd={dirfd}, base={base!r})"
            )

    def check_enumeration(self, path):
        if path not in self.enumerations:
            raise Violation(f"undeclared source directory enumeration: {path}")
        mode = self.source_mode(path)
        if mode is None or not stat.S_ISDIR(mode):
            raise Violation(f"source enumeration has no complete active directory: {path}")
        prefix = path.rstrip("/") + "/"
        if any(name.startswith(prefix) for name in self.config["forbidden_paths"]):
            raise Violation(f"nonregular namespace in source enumeration: {path}")

    def native_managed_runtime(self, path):
        return self.native_readonly and any(
            path == root or path.startswith(root + "/")
            for root in self.native_runtime_directories
        )

    def make_runtime_access(self, state, path, operation):
        if (
            self.native_maps_omission(state) and operation in {"read", "metadata"}
            and path == self.resolve("/proc/self/maps", native_maps_omission=True)
        ):
            return
        if path in self.native_metadata_directories:
            if operation != "metadata":
                raise Violation(f"metadata-only runtime operation denied: {operation} {path}")
            self.check_optional_make_spelling(state, path, operation)
            self.defer_observation(state, "accessed", path)
            return
        if self.native_managed_runtime(path) and operation in {"read", "metadata", "directory"}:
            self.check_optional_make_spelling(state, path, operation)
            self.defer_observation(state, "accessed", path)
            return
        if (
            self.native_readonly and operation in {"read", "metadata"}
            and self.runtime_metadata(path, parents=operation == "metadata")
        ):
            self.check_optional_make_spelling(state, path, operation)
            self.defer_observation(state, "accessed", path)
            return
        if operation in {"read", "metadata"} and path in self.runtime_closure | self.executable | {"/lib/vo-observer.so"}:
            return
        if operation == "metadata" and path in self.runtime_directories:
            return
        if not state.observer_ready and operation in {"read", "metadata"} and path in self.loader_probes and (
            state.role != "native" or self.native_loader_origin(state)
        ):
            try:
                (Path(self.config["root"]) / path.lstrip("/")).lstat()
            except FileNotFoundError:
                return
        raise Violation(f"uncaptured Make runtime access: {operation} {path}")

    def dependency_image_identity(self, path):
        self.charge_metadata(144)
        try:
            info = Path(path).stat()
        except OSError as error:
            raise Violation("dependency executable identity is unavailable") from error
        if not stat.S_ISREG(info.st_mode):
            raise Violation("dependency executable identity is not a regular image")
        return info.st_dev, info.st_ino

    def verify_dependency_image(self, pid, path):
        expected = self.dependency_image_ids.get(path)
        if expected is None or (
            self.dependency_image_identity(Path(self.config["root"]) / path.lstrip("/")) != expected
            or self.dependency_image_identity(f"/proc/{pid}/exe") != expected
        ):
            raise Violation("dependency executable differs from its verified image")

    def verify_dependency_mapping_span(self, image, start, end, raw_offset, ip, instruction, *, identity=None):
        if not re.fullmatch(rb"[0-9a-fA-F]{1,16}", raw_offset):
            raise Violation("dependency mapping offset is malformed")
        offset = int(raw_offset, 16)
        maximum = (1 << 63) - 1
        if offset > maximum or offset % os.sysconf("SC_PAGE_SIZE"):
            raise Violation("dependency mapping offset is outside the supported range")
        if not 0 <= start <= ip - len(instruction) < ip < end <= 1 << 64:
            raise Violation("dependency instruction span escapes its mapping")
        position = offset + ip - len(instruction) - start
        if not 0 <= position <= maximum - len(instruction):
            raise Violation("dependency instruction offset is outside the supported range")
        path = Path(self.config["root"]) / image.lstrip("/")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            with os.fdopen(descriptor, "rb") as source:
                self.charge_metadata(144)
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or (
                    before.st_dev, before.st_ino
                ) != (self.dependency_image_ids[image] if identity is None else identity):
                    raise Violation("opened dependency mapping image has a different identity")
                if position > before.st_size - len(instruction):
                    raise Violation("dependency instruction span exceeds its runtime image")
                self.charge_metadata(len(instruction))
                actual = os.pread(source.fileno(), len(instruction), position)
                self.charge_metadata(144)
                after = os.fstat(source.fileno())
                fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(getattr(before, field) != getattr(after, field) for field in fields):
                    raise Violation("dependency mapping image changed during its bounded read")
                if actual != instruction:
                    raise Violation("dependency mapped instruction differs from its runtime image")
        except OSError as error:
            raise Violation("dependency runtime instruction span is unavailable") from error

    def syscall_origin(self, state, stop, label):
        if stop is None:
            raise Violation(f"{label} lacks its owned syscall stop")
        pid, number, ip = stop
        if self.processes.get(pid) is not state or state.pidfd < 0 or state.kernel_call != number or ip < 2:
            raise Violation(f"{label} is not owned by the stopped tracee")
        information = (ctypes.c_ubyte * 128)()
        ptrace(0x420E, pid, len(information), ctypes.byref(information))
        if (
            information[0] != 1
            or int.from_bytes(bytes(information[4:8]), "little") != 0xC000003E
            or int.from_bytes(bytes(information[8:16]), "little") != ip
            or int.from_bytes(bytes(information[24:32]), "little") != number
        ):
            raise Violation(f"{label} lost its actual syscall-entry stop")
        self.charge_metadata(2)
        instruction = memory(pid, ip - 2, 2)
        if instruction != b"\x0f\x05":
            raise Violation(f"{label} has an unsupported syscall instruction")
        with open(f"/proc/{pid}/maps", "rb") as source:
            data = source.read(SYSCALL_MEMORY_LIMIT + 1)
        self.charge_metadata(len(data))
        if len(data) > SYSCALL_MEMORY_LIMIT:
            raise Violation(f"{label} mapping exceeds observation bound")
        found = None
        for line in data.splitlines():
            fields = line.split(None, 5)
            if len(fields) < 5:
                raise Violation(f"malformed {label} mapping")
            try:
                start, end = (int(value, 16) for value in fields[0].split(b"-"))
                major, minor = (int(value, 16) for value in fields[3].split(b":"))
                inode = int(fields[4])
            except ValueError as error:
                raise Violation(f"malformed {label} mapping identity") from error
            if start <= ip - 2 < ip < end:
                if fields[1] != b"r-xp" or inode <= 0 or found is not None:
                    raise Violation(f"{label} lacks one readonly executable image mapping")
                found = (os.makedev(major, minor), inode), (start, end, fields[2])
        if found is None:
            raise Violation(f"{label} syscall has no mapped origin")
        return found[0], found[1], ip, instruction

    def native_loader_origin(self, state):
        if not self.native_readonly:
            raise Violation("native loader probe is outside its readonly lane")
        origin, mapping, ip, instruction = self.syscall_origin(
            state, state.native_stop, "native loader probe",
        )
        image = self.config["native_interpreter"]
        if image not in self.runtime_closure:
            raise Violation("native interpreter is not in captured runtime closure")
        identity = self.dependency_image_identity(Path(self.config["root"]) / image.lstrip("/"))
        if origin != identity:
            return False
        self.verify_dependency_mapping_span(image, *mapping, ip, instruction, identity=identity)
        return True

    def dependency_negative_purpose(self, state, path, operation):
        if state.dependency_stop is None or state.dependency_image not in self.config["dependency"]["executables"]:
            raise Violation("dependency negative probe has no verified syscall context")
        pid, number, ip = state.dependency_stop
        self.reserve_observation("accessed", f"dependency-purpose:{pid}:{number}:{ip}:{operation}:{path}")
        origin, mapping, ip, instruction = self.syscall_origin(
            state, state.dependency_stop, "dependency negative probe",
        )
        self.verify_dependency_image(pid, state.dependency_image)
        for image in (self.dependency_interpreter, self.dependency_libc):
            if self.dependency_image_identity(Path(self.config["root"]) / image.lstrip("/")) != self.dependency_image_ids[image]:
                raise Violation("dependency purpose image changed after resolution")
        loader = (
            path in self.dependency_loader_probes and operation in {"read", "metadata"}
            and origin == self.dependency_image_ids[self.dependency_interpreter]
        )
        driver = (
            operation == "metadata"
            and path in self.dependency_stat_probes | self.dependency_directories
            and state.dependency_image == self.config["dependency"]["executables"][0]
            and origin in {
                self.dependency_image_ids[state.dependency_image],
                self.dependency_image_ids[self.dependency_libc],
            }
        )
        if loader:
            image = self.dependency_interpreter
        elif driver:
            image = state.dependency_image if origin == self.dependency_image_ids[state.dependency_image] else self.dependency_libc
        else:
            return False
        self.verify_dependency_mapping_span(image, *mapping, ip, instruction)
        return True

    def dependency_runtime_access(self, state, path, operation):
        full = Path(self.config["root"]) / path.lstrip("/")
        try:
            mode = full.lstat().st_mode
        except FileNotFoundError:
            mode = None
        except OSError as error:
            raise Violation(f"dependency runtime path has an unsupported type: {path}") from error
        allowed = (
            operation in {"read", "metadata"} and path in self.dependency_files
            and mode is not None and stat.S_ISREG(mode)
            or operation == "metadata" and path in self.dependency_directories
            and mode is not None and stat.S_ISDIR(mode)
            or mode is None and (
                operation == "metadata" and path in self.dependency_stat_probes | self.dependency_directories
                or operation in {"read", "metadata"} and path in self.dependency_loader_probes
            ) and self.dependency_negative_purpose(state, path, operation)
        )
        if not allowed:
            raise Violation(f"undeclared dependency host {operation}: {path}")
        self.defer_observation(state, "accessed", path)

    def check(self, state, path, operation, *, observer=False):
        if path.startswith("<"):
            return
        if state.role == "make" and state.observer_ready:
            if path == "/lib/vo-observer.so" and not observer:
                raise Violation("supervisor observer image access denied")
            if operation == "directory" and not (path == "/repo" or path.startswith("/repo/")):
                if self.native_managed_runtime(path):
                    self.make_runtime_access(state, path, operation)
                    return
                raise Violation("Make runtime directory enumeration denied")
        if path == "/control" or path.startswith("/control/"):
            if state.role == "helper":
                if path == "/control/events" and operation == "write":
                    return
                if (path == "/control/map" or path.startswith("/control/map/")) and operation in {
                    "read", "metadata",
                }:
                    return
            if observer and path == "/control/result" and operation == "write":
                return
            raise Violation(f"supervisor channel denied: {operation} {path}")
        if self.config.get("dependency") and not (
            path in {"/repo", "/work"} or path.startswith(("/repo/", "/work/"))
        ):
            # No generic runtime prefix may authorize a dependency source.
            self.dependency_runtime_access(state, path, operation)
            return
        if path == "/dev/null":
            return
        if self.native_readonly and operation == "write":
            if self.native_outputs is not None:
                job = self.native_jobs.get(state.native_dispatch)
                admission = state.native_admission or {}
                outputs = admission.get("outputs", [])
                if job is not None and state.role == "native" and self.config.get("native_resources"):
                    if __package__:
                        from .native_resources import resource_operation
                    else:
                        from native_resources import resource_operation
                    selected = {
                        1: "write", 18: "write", 20: "write", 2: "open", 85: "open", 257: "open",
                        82: "replace", 264: "replace", 316: "replace", 87: "remove", 91: "mode",
                        83: "mkdir", 258: "mkdir", 84: "rmdir",
                        263: "rmdir" if state.native_unlink_flags == 0x200 else "remove",
                    }.get(state.kernel_call)
                    if resource_operation(
                        admission.get("resources", ()), path, job["pid"], outputs, selected,
                    ):
                        return
                elif state.role == "native" and state.kernel_call in {1, 18, 20, 2, 85, 257} and path in {"/repo/" + output for output in outputs}:
                    return
                raise Violation(f"native job write lacks its issued output authority: {path}")
            raise Violation(f"readonly native Make filesystem write denied: {path}")
        if state.role == "helper":
            if operation == "metadata" and path in {"/", "/bin", "/usr", "/usr/bin", "/proc/self/exe"}:
                return
            records = [record for record in self.metadata_authority(state) if record[1] == path]
            if records and (
                operation == "metadata"
                or operation in {"read", "directory"} and any(record[0] in {78, 217} for record in records)
            ):
                self.defer_observation(state, "accessed", path)
                return
            raise Violation(f"interceptor attempted nonprotocol filesystem access: {path}")
        if self.mode == "make" and (
            state.role == "make" and not state.observer_ready
            or self.native_readonly and state.role == "native"
        ):
            if not (path == "/repo" or path.startswith("/repo/")):
                self.make_runtime_access(state, path, operation)
                return
        # These trusted runtime configuration probes are deliberately absent in
        # the chroot. No proc/etc mount or candidate-writable ancestor exists.
        if self.mode != "make" and path in {
            "/proc/sys/crypto/fips_enabled", "/proc/self/stat",
            "/etc/ssl/openssl.cnf", "/usr/lib/ssl/openssl.cnf",
        }:
            if operation in {"read", "metadata"}:
                return
        if self.mode == "compile" and operation == "metadata" and path == "/proc/self/exe":
            return
        if path.startswith(("/proc", "/sys", "/dev/")):
            raise Violation(f"descriptor/device namespace denied: {path}")
        if self.mode == "make" and operation == "metadata" and path in {
            "/usr/gnu/include", "/usr/local/include", "/usr/include",
        }:
            return
        if self.mode == "compile" and operation == "metadata" and path in {
            prefix + "/" + name for prefix in ("/usr/bin", "/bin")
            for name in ("gnm", "gstrip", "gld")
        }:
            return
        if self.mode == "compile" and path in self.config.get("repository_outputs", ()):
            if state.role == "compiler" and operation in {"read", "metadata", "write"}:
                return
            raise Violation(f"original compiler output operation denied: {operation} {path}")
        if operation == "write":
            if self.mode in {"command", "compile"} and (path == "/work" or path.startswith("/work/")):
                return
            raise Violation(f"write outside private command output: {path}")
        if self.mode == "make":
            if operation == "read" and path in self.config.get("intercepted_runtime", ()):
                raise Violation("intercepted runtime program is metadata/dispatch only")
            if (
                operation == "metadata" and self.runtime_metadata(path)
                or operation == "read" and (
                    path in self.config.get("runtime_files", ())
                    or any(path.startswith(absent + "/") for absent in self.config.get("runtime_absent", ()))
                )
            ):
                self.check_optional_make_spelling(state, path, operation)
                self.defer_observation(state, "accessed", path)
                return
        if self.mode == "make":
            if not (path == "/repo" or path.startswith("/repo/")):
                self.make_runtime_access(state, path, operation)
                return
        runtime = (
            self.mode != "make" and path.startswith(("/usr/lib/python3.", "/usr/lib/x86_64-linux-gnu/",
                             "/lib/x86_64-linux-gnu/", "/lib64/"))
            or path in self.executable
            or path == "/lib/vo-observer.so"
        )
        if self.mode == "compile" and path.startswith((
            "/usr/lib/gcc/", "/usr/libexec/", "/usr/include/", "/usr/local/include/",
            "/usr/x86_64-linux-gnu/", "/usr/lib64/", "/usr/local/lib/", "/usr/local/lib64/",
        )):
            runtime = True
        if self.mode == "compile" and path in {
            "/usr/lib/gcc", "/usr/libexec", "/usr/include", "/usr/local/include",
            "/usr/x86_64-linux-gnu", "/usr/lib64", "/usr/local/lib", "/usr/local/lib64",
            "/usr/local",
        }:
            runtime = True
        if runtime or path in self.runtime_probes or path in {
            "/", "/usr", "/lib", "/lib64", "/bin", "/etc", "/etc/ld.so.cache",
            "/etc/ld.so.preload", "/etc/localtime", "/usr/lib",
            "/usr/share/zoneinfo/UTC", "/usr/share/zoneinfo/Etc/UTC",
            "/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu",
            "/usr/bin",
        }:
            return
        if path == "/work" or path.startswith("/work/"):
            if self.mode in {"command", "compile"}:
                return
            raise Violation("Make/registry cannot use a command scratch directory")
        if self.mode != "make" and operation == "directory":
            self.check_enumeration(path)
            return
        if path.startswith("/repo/"):
            for forbidden in self.config["forbidden_paths"]:
                if path == forbidden or path.startswith(forbidden + "/"):
                    raise Violation(f"nonregular candidate source denied: {path}")
        if self.mode != "make" and path in self.enumerations:
            self.check_enumeration(path)
        if self.mode == "make":
            if path == "/repo" or path.startswith("/repo/"):
                for forbidden in self.config["forbidden_paths"]:
                    if path == forbidden or path.startswith(forbidden + "/"):
                        raise Violation(f"nonregular candidate source denied: {path}")
                self.defer_observation(state, "accessed", path)
                return
        elif path in self.sources:
            self.defer_observation(state, "consumed", path.removeprefix("/repo/"))
            return
        elif path in self.code:
            self.defer_observation(state, "code_consumed", path.removeprefix("/repo/"))
            return
        elif self.config.get("dependency") and path.startswith("/repo/") and operation in {"read", "metadata"}:
            mode = self.source_mode(path)
            if mode is None:
                self.absent_source(state, path, operation)
                return
            if (
                operation == "metadata" and stat.S_ISDIR(mode)
                and path in set(self.config["dependency"]["include_dirs"]) | self.code_dirs | self.source_dirs
            ):
                return
        elif self.mode == "compile" and operation == "metadata" and path.endswith(".gch") and path[:-4] in self.code:
            self.absent_source(state, path, operation)
            return
        elif self.mode == "compile" and operation in {"metadata", "read"} and path in self.link_option_probes:
            self.absent_source(state, path, operation)
            return
        elif path in self.code_dirs | self.source_dirs | self.enumerations:
            mode = self.source_mode(path)
            if mode is None or not stat.S_ISDIR(mode):
                raise Violation(f"source ancestor is not an active directory: {path}")
            return
        elif operation in {"metadata", "read"} and "__pycache__" in path.split("/"):
            # -B prevents cache writes; importlib may probe the absent cache
            # corresponding to an admitted code file, never a data directory.
            parent = path.split("/__pycache__", 1)[0]
            name = posixpath.basename(path)
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\.cpython-[0-9]+(?:\.opt-[0-9]+)?\.pyc", name)
            if parent in self.code_dirs and (
                path == parent + "/__pycache__"
                or (match and parent + "/" + match[1] + ".py" in self.code)
            ):
                self.absent_source(state, path, operation)
                return
        elif operation == "metadata" and posixpath.dirname(path) in self.code_dirs:
            filename = posixpath.basename(path)
            match = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)(?:\.cpython-[0-9]+-[A-Za-z0-9_-]+|\.abi3)?\.(?:so|py|pyc)",
                filename,
            )
            if match and (
                match[1] == "__init__"
                or posixpath.dirname(path) + "/" + match[1] + ".py" in self.code
            ):
                self.absent_source(state, path, operation)
                return
        raise Violation(f"undeclared source {operation}: {path}")

    def check_fd(self, state, descriptor, operation, registers):
        path = self.fd(state, descriptor)
        if (
            operation == "write" and self.native_outputs is not None
            and state.role == "native" and state.native_stop is not None
            and state.kernel_call in {1, 18, 20, 91}
            and (state.kernel_call != 91 or self.config.get("native_resources"))
        ):
            binding = (state.native_stop[0], descriptor)
            item = self.native_outputs.custody.descriptors.get(binding)
            if item is not None and binding in item.writers and item.path == path:
                return path
        self.check(state, path, operation, observer=self.observer(state, registers))
        return path

    @staticmethod
    def signal_target(pid, *targets):
        # Candidate threads are not admitted: its PID, TGID and TID are equal.
        # Match the kernel's pid_t conversion, rejecting group/broadcast forms.
        if any(ctypes.c_int(target).value != pid for target in targets):
            raise Violation("cross-process signal target denied")

    @classmethod
    def signal_request(cls, pid, number, a, b, c):
        two_targets = number in {234, 297}
        if not two_targets and number not in {62, 129, 200}:
            raise Violation("unsupported self-signal syscall")
        targets = (a, b) if two_targets else (a,)
        cls.signal_target(pid, *targets)
        return ctypes.c_int(c if two_targets else b).value

    def entry(self, pid, state, r):
        self.calls += 1
        if self.calls > self.config["syscall_limit"]:
            raise Violation("aggregate syscall budget exhausted")
        n = r.orig_rax
        a, b, c, d, e = r.rdi, r.rsi, r.rdx, r.r10, r.r8
        state.native_unlink_flags = ctypes.c_int(c).value if n == 263 else None
        state.pending = None
        state.kernel_io = None
        state.metadata_pending = None
        state.path_context = None
        state.dependency_stop = (pid, r.orig_rax, r.rip) if self.config.get("dependency") else None
        state.native_stop = (pid, r.orig_rax, r.rip) if self.native_readonly and state.role == "native" else None
        state.observations.clear()
        state.observation_needs_bytes = False
        trusted = self.observer(state, r)
        if n == 39 and a in {
            VO_READY, VO_DISPATCH, VO_QUERY_KIND, VO_METADATA, VO_PRODUCE,
            VO_SOURCE_IO, VO_JOB_CONTEXT, VO_JOB_POLICY,
            VO_JOB_INPUTS,
        }:
            if a == VO_QUERY_KIND:
                if state.role != "helper" or state.helper_kind not in {VO_RECIPE, VO_VALUE, VO_VALIDATE, VO_LIVE}:
                    raise Violation("unauthenticated interceptor kind query")
                state.pending = ("helper_kind", state.helper_kind)
            elif a == VO_METADATA:
                entries = self.config.get("mapping_entries", ())
                if (
                    state.role != "helper" or state.helper_kind != VO_VALUE
                    or not 0 <= b < len(entries) or entries[b]["key"] != f"{c:016x}"
                ):
                    raise Violation("unauthenticated metadata mapping request")
                state.metadata_index = b
                state.pending = ("helper_kind", 0)
            elif a == VO_PRODUCE:
                if (
                    state.role != "helper" or state.helper_kind != VO_LIVE
                    or not self.config.get("producer_endpoint") or state.producer_requested
                    or not 20 <= c <= SYSCALL_MEMORY_LIMIT
                ):
                    raise Violation("unauthenticated or repeated producer request")
                frame = memory(pid, b, c)
                events = _read_events(frame, expected_mapping_count=0)
                if len(events) != 1 or events[0]["match"] != -1:
                    raise Violation("invalid live producer request frame")
                if len(self.producer_requests) >= self.config["pending_limit"]:
                    raise Violation("pending live producer requests exceed report allowance")
                self.charge_metadata(len(frame))
                self.reserve_observation("accessed", "producer-request:" + str(self.calls))
                state.producer_requested = True
                state.producer_frame = frame
                state.pending = ("producer", None)
                self.producer_requests.append(pid)
                self.producer_pending_peak = max(self.producer_pending_peak, len(self.producer_requests))
            else:
                if not trusted:
                    raise Violation("unauthenticated Make dispatch notification")
                if a == VO_READY:
                    if b or c or state.observer_ready:
                        raise Violation("invalid observer bootstrap notification")
                    state.observer_ready = True
                    if self.read_trace is not None:
                        self.read_trace.ready(pid)
                elif a == VO_SOURCE_IO:
                    if self.read_trace is None or state.role != "make" or not state.observer_ready:
                        raise Violation("unconfigured original source stream notification")
                    self.read_trace.source_io(pid, state, b, c)
                elif a == VO_JOB_CONTEXT:
                    self.observe_native_job_context(pid, state, b, c)
                elif a == VO_JOB_POLICY:
                    self.observe_native_job_policy(pid, state, b, c)
                elif a == VO_JOB_INPUTS:
                    self.observe_native_inputs(pid, state, b, c)
                elif b:
                    path = self.path(pid, state, b)
                    if path not in self.executable or path == "/control/interceptor" or c not in {0, 1}:
                        raise Violation(f"untrusted executable dispatch: {path}")
                    if self.runtime_metadata(path, parents=False):
                        self.check_optional_make_spelling(state, path, "execute")
                    if self.native_readonly:
                        self.begin_native_job(pid, state, path)
                    state.dispatch = (path, c)
                else:
                    if c:
                        raise Violation("invalid Make dispatch completion")
                    state.dispatch = None
                    state.native_dispatch = None
                    state.native_inputs = None
        elif n in {2, 85, 257}:  # open, creat, openat
            flags = c if n == 257 else b
            follow = n == 85 or not (
                flags & os.O_NOFOLLOW or flags & os.O_CREAT and flags & os.O_EXCL
            )
            path = self.path(
                pid, state, b if n == 257 else a, signed(a) if n == 257 else -100,
                follow_final=follow,
            )
            creating = n == 85 or flags & os.O_CREAT or flags & os.O_TMPFILE == os.O_TMPFILE
            writing = creating or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_TRUNC))
            operation = "write" if writing else "metadata" if state.role == "helper" and flags & os.O_PATH else "read"
            self.check(state, path, operation, observer=trusted)
            if creating:
                self.reserve_creation()
            state.pending = ("open", path)
        elif n in {4, 6, 21, 89, 137, 262, 267, 269, 332, 439}:  # metadata, access, readlink
            at = n in {262, 267, 269, 332, 439}
            follow = n not in {6, 89, 267} and not (
                n in {262, 439} and d & 0x100 or n == 332 and c & 0x100
            )
            path = self.path(
                pid, state, b if at else a, signed(a) if at else -100, follow_final=follow,
            )
            self.check(state, path, "metadata", observer=trusted)
            state.observation_needs_bytes = n in {89, 267}
            # Make can otherwise turn the noexec mount's X_OK denial into
            # successful empty $(shell) output before any dispatch is observed.
            if (
                state.role == "make" and state.observer_ready and path.startswith("/repo/")
                and n in {21, 269, 439} and ((b if n == 21 else c) & 0xFFFFFFFF) == os.X_OK
            ):
                mode = self.source_mode(path)
                if mode is not None and stat.S_ISREG(mode):
                    state.pending = ("make-source-exec", (path, n == 439 and bool(d & 0x200)))
            self.begin_metadata(pid, state, r, path)
        elif n in {5, 138}:  # fstat, fstatfs
            path = self.check_fd(state, a, "metadata", r)
            self.begin_metadata(pid, state, r, path)
        elif n in {0, 17, 19}:  # read/pread/readv
            path = self.check_fd(state, a, "read", r)
            state.kernel_io = path
            state.observation_needs_bytes = True
            if state.role == "helper" and path.startswith("/control/map/") and path.endswith(".meta"):
                state.pending = ("metadata-input", None)
        elif n in {1, 18, 20}:  # write/pwrite/writev
            path = self.check_fd(state, a, "write", r)
            state.kernel_io = path
            if n == 20:
                if c > 1024:
                    raise Violation("oversized writev vector")
                lengths = memory(pid, b, c * 16)
                amount = sum(int.from_bytes(lengths[i + 8:i + 16], "little") for i in range(0, len(lengths), 16))
            else:
                amount = c
            self.written += amount
            if self.written > self.config["write_limit"]:
                raise Violation("aggregate capsule write budget exhausted")
            if self.mode == "make" and state.role == "helper" and path == "/control/events":
                if n != 1 or not 20 <= c <= SYSCALL_MEMORY_LIMIT:
                    raise Violation("invalid native event write")
                frame = memory(pid, b, c)
                if state.helper_kind == VO_LIVE:
                    if state.producer_slot is None or state.producer_event_written:
                        raise Violation("unfulfilled or repeated live producer event")
                    event, = _read_events(frame, expected_mapping_count=state.producer_slot + 1)
                    requested, = _read_events(state.producer_frame, expected_mapping_count=0)
                    if event["match"] != state.producer_slot or event["arguments"] != requested["arguments"]:
                        raise Violation("live producer event differs from its request")
                state.pending = ("event", frame)
        elif n == 3:
            state.pending = ("close", ctypes.c_int(a).value)
        elif n == 436:
            first, last, flags = (value & 0xFFFFFFFF for value in (a, b, c))
            if not self.native_readonly or state.role != "native":
                raise Violation("close_range requires an admitted native image")
            if flags:
                raise Violation("native close_range flags are not admitted")
            if self.native_outputs is not None:
                custody = self.native_outputs.custody
                selected = [
                    (descriptor, item)
                    for (owner, descriptor), item in custody.descriptors.items()
                    if owner == pid and first <= descriptor <= last
                ]
                if selected and (
                    not self.config.get("native_resources")
                    or any(
                        item.path not in custody.shared_paths
                        or not any(
                            process != pid
                            for process, _ in item.descriptions[(pid, descriptor)].bindings
                        )
                        for descriptor, item in selected
                    )
                ):
                    raise Violation("native close_range intersects a generated output descriptor")
            state.pending = ("close-range", tuple(
                descriptor for descriptor in sorted(state.fds) if first <= descriptor <= last
            ))
        elif n in {8, 74, 75, 73}:
            self.check_fd(state, ctypes.c_int(a).value if n == 73 else a, "read", r)
        elif n in {78, 217}:
            path = self.check_fd(state, a, "directory", r)
            if c > SYSCALL_MEMORY_LIMIT:
                raise Violation("directory request exceeds the syscall memory bound")
            state.pending = ("directory", (path, b, c, n == 217))
            self.begin_metadata(pid, state, r, path)
        elif n == 9:
            descriptor = signed(e)
            kind = d & 0xF
            if d & ~MMAP_FLAGS or kind not in {MAP_SHARED, MAP_PRIVATE, MAP_SHARED_VALIDATE}:
                raise Violation("unadmitted mmap flags")
            if c & ~(PROT_READ | PROT_WRITE | PROT_EXEC):
                raise Violation("unadmitted mmap protection")
            if kind != MAP_PRIVATE:
                if d & MAP_ANONYMOUS:
                    raise Violation("shared anonymous mappings/argument races are forbidden")
                if c & PROT_WRITE:
                    raise Violation("shared writable mappings/argument races are forbidden")
            if not d & MAP_ANONYMOUS:
                path = self.check_fd(state, descriptor, "read", r)
                # Even MAP_PRIVATE + O_RDONLY can observe another process's
                # writes to the backing inode until COW. Closing/duplicating/
                # hardlinking the FD does not make /work immutable.
                if path.startswith("<") or path == "/dev/null" or path == "/work" or path.startswith("/work/"):
                    raise Violation("mutable backing-file mappings/argument races are forbidden")
                if (
                    self.mode == "make" and c & PROT_EXEC
                    and path not in self.executable | self.runtime_closure | {"/lib/vo-observer.so"}
                    and not self.native_managed_runtime(path)
                ):
                    raise Violation("optional runtime image execution denied")
                if c & PROT_EXEC and path not in self.executable and not path.startswith(("/usr/", "/lib/", "/lib64/", "/bin/")):
                    raise Violation("candidate executable mmap denied")
            elif c & PROT_EXEC:
                raise Violation("anonymous executable mmap denied")
            self.reserve_memory(pid, state, b)
        elif n == 12:
            self.reserve_memory(pid, state, max(0, a - state.break_end) if a else 0)
        elif n == 25:
            if not b or d & ~1:
                raise Violation("remap aliases/fixed relocation are forbidden")
            self.reserve_memory(pid, state, max(0, c - b))
        elif n == 10:
            if c & PROT_WRITE:
                raise Violation("adding writable memory protection denied")
            if c & ~PROT_READ:
                raise Violation("adding executable/unknown memory protection denied")
        elif n == 59:
            if self.mode == "command" and not state.bootstrap:
                raise Violation("registered command post-bootstrap exec denied")
            path = self.path(pid, state, a)
            if path not in self.executable:
                raise Violation(f"untrusted executable dispatch: {path}")
            if self.runtime_metadata(path, parents=False):
                self.check_optional_make_spelling(state, path, "execute")
            if self.config.get("dependency"):
                expected = self.config["dependency"]["executables"]
                if len(self.executed) >= len(expected) or path != expected[len(self.executed)]:
                    raise Violation("dependency execution escaped the driver/cc1 profile")
                state.exec_path = path
            if self.mode == "make":
                if state.bootstrap and path == "/usr/bin/make":
                    role = "make"
                    self.make_pid = pid
                elif (
                    path == "/usr/bin/make" and trusted and state.observer_ready
                    and pid == self.make_pid and not state.dispatch
                ):
                    self.make_restarts += 1
                    if self.make_restarts > 64:
                        raise Violation("Make restart exceeded the existing pass bound")
                    role = "make"
                elif self.native_readonly and path in self.native_executables and state.dispatch:
                    if state.dispatch[0] != path:
                        raise Violation("native shell differs from authenticated Make dispatch")
                    inputs = read_epochs.native_execution_input(self.native_argv(pid, b), state.cwd)
                    if state.native_inputs is None or state.native_inputs != (path, inputs):
                        raise Violation("native exec entry differs from original pre-spawn dispatch inputs")
                    if self.native_admission and (
                        state.native_admission is None
                        or state.native_admission != self.native_jobs[state.native_dispatch]["admission"]
                        or state.native_admission["input_sha256"]
                        != hashlib.sha256(encoded(inputs)).hexdigest()
                    ):
                        raise Violation("native exec entry differs from its issued Command admission")
                    role = "native"
                    state.exec_path = path
                    self.reserve_observation("accessed", "native-shell:" + str(pid) + ":" + path)
                    state.dispatch = None
                elif (
                    self.native_readonly and self.read_trace is not None and self.read_trace.runtime
                    and state.role == "native" and state.native_dispatch in self.native_jobs
                    and path in self.native_executables
                ):
                    role = "native"
                    state.exec_path = path
                elif not self.native_readonly and path == "/control/interceptor" and state.dispatch:
                    role = "helper"
                    source, required = state.dispatch
                    state.helper_kind = VO_VALUE if (
                        required or source == "/usr/bin/make" or self.fd(state, 1) == "<pipe>"
                    ) else VO_RECIPE
                    if state.helper_kind == VO_VALUE and self.config.get("producer_endpoint"):
                        state.helper_kind = VO_LIVE
                    state.dispatch = None
                else:
                    raise Violation("Make execution escaped authenticated native dispatch")
            else:
                role = "helper" if self.config.get("metadata_validation") else (
                    "compiler" if self.mode == "compile" else "command"
                )
            self.reserve_exec(pid, state)
            state.pending = ("exec", role)
        elif n in {56, 57, 58}:
            if n == 56:
                allowed = 0x100 | 0x4000 | 0x100000 | 0x200000 | 0x1000000 | 0xFF
                if a & ~allowed or (a & 0xFF) != signal.SIGCHLD:
                    raise Violation("untraced/reparented/shared-state clone denied")
                if a & 0x100 and not a & 0x4000:
                    raise Violation("shared-memory candidate threads denied")
            state.clone_shares_vm = n == 58 or n == 56 and bool(a & 0x100)
            self.reserve_process(state)
            self.reserve_memory(pid, state, 0, copies=1)
        elif n == 435:
            if b < 64 or b > 88:
                raise Violation("unknown clone3 structure")
            flags = int.from_bytes(memory(pid, a, 8), "little")
            exit_signal = int.from_bytes(memory(pid, a + 32, 8), "little")
            if (
                state.role not in {"make", "compiler"} or flags & ~(0x100 | 0x4000 | 0x100000000)
                or flags & (0x100 | 0x4000) != (0x100 | 0x4000)
                or exit_signal != signal.SIGCHLD
            ):
                raise Violation(f"clone3 outside trusted Make's suspended-parent spawn: {flags:#x}")
            state.clone_shares_vm = True
            self.reserve_process(state)
            self.reserve_memory(pid, state, 0, copies=1)
        elif n in {22, 293}:
            state.pending = ("pipe", a)
        elif n in {32, 33, 292}:
            path = self.fd(state, a)
            self.check(state, path, "read", observer=trusted)
            state.pending = ("dup", path)
        elif n == 72:
            path = self.fd(state, a)
            if b in {0, 1030}:
                self.check(state, path, "read", observer=trusted)
                state.pending = ("dup", path)
            elif b not in {1, 2, 3, 4, 5, 6, 7, 1031, 1032}:
                raise Violation("unknown fcntl operation")
        elif n == 16:
            self.fd(state, a)
            if b not in {0x5401, 0x5413, 0x541B, 0x5450, 0x5451}:
                raise Violation(f"unknown ioctl operation {b:#x}")
        elif n == 80:
            path = self.path(pid, state, a)
            self.check(state, path, "metadata")
            state.pending = ("cwd", path)
        elif n == 81:
            state.pending = ("cwd", self.check_fd(state, a, "metadata", r))
        elif n in {90, 268}:
            # Pathname chmod can race a regular file's replacement by a
            # directory. Keep owner traversal even for a claimed regular file.
            path = self.path(pid, state, b if n == 268 else a, signed(a) if n == 268 else -100)
            self.check(state, path, "write")
            mode = c if n == 268 else b
            if mode & 0o700 != 0o700:
                raise Violation("candidate pathname permission loss is forbidden")
        elif n == 91:
            descriptor = ctypes.c_int(a).value
            self.check_fd(state, descriptor, "write", r)
            if not stat.S_ISREG(os.stat(f"/proc/{pid}/fd/{descriptor}").st_mode):
                raise Violation("candidate directory permission changes are forbidden")
        elif n == 95:
            if a & 0o700:
                raise Violation("candidate owner permission masking is forbidden")
        elif n in {76, 83, 84, 87, 92, 94}:
            path = self.path(pid, state, a, follow_final=n in {76, 92})
            self.check(state, path, "write")
            if n == 76:
                self.written += b
            if n == 83:
                if b & 0o700 != 0o700:
                    raise Violation("candidate untraversable directory creation is forbidden")
                self.reserve_creation()
        elif n in {77, 93}:
            self.check_fd(state, a, "write", r)
            if n == 77:
                self.written += b
        elif n in {88, 266}:
            raise Violation("candidate symlink creation is forbidden")
        elif n in {82, 264, 316}:
            # Moving a cwd/dirfd ancestor changes the kernel's '..' meaning
            # without changing its recorded path. No supported tool needs it.
            if self.native_outputs is None:
                raise Violation("candidate directory-entry relocation is forbidden")
        elif n == 86:
            for pointer in (a, b):
                self.check(state, self.path(pid, state, pointer, follow_final=False), "write")
            self.reserve_creation()
        elif n in {258, 260, 263, 280}:
            follow = n == 260 and not e & 0x100 or n == 280 and not d & 0x100
            self.check(state, self.path(pid, state, b, signed(a), follow_final=follow), "write")
            if n == 258:
                if c & 0o700 != 0o700:
                    raise Violation("candidate untraversable directory creation is forbidden")
                self.reserve_creation()
        elif n == 265:
            self.check(state, self.path(pid, state, b, signed(a), follow_final=bool(e & 0x400)), "write")
            self.check(state, self.path(pid, state, d, signed(c), follow_final=False), "write")
            self.reserve_creation()
        elif n in {62, 129, 200, 234, 297}:
            number = self.signal_request(pid, n, a, b, c)
            if self.native_readonly and state.role == "native" and 0 < number <= 64:
                state.pending = ("native-signal", number)
                if n in {129, 297}:
                    self.native_queue_entry(pid, state, number, c if n == 129 else d)
        elif n == 128 and self.native_readonly and state.role == "native":
            self.native_reconcile_signal_origins(pid, state)
            state.pending = ("native-signal-wait", b)
        elif n == 424:
            raise Violation("candidate pidfd signal authority is not admitted")
        elif n in {105, 106, 113, 114, 117, 119}:
            identity = (
                self.config["runner_gid"] if n in {106, 114, 119}
                else self.config["runner_uid"]
            ) if self.config["sudo_drop"] else 0
            arguments = (a,) if n in {105, 106} else (a, b) if n in {113, 114} else (a, b, c)
            if state.role != "make" or any(
                ctypes.c_int(value).value not in {-1, identity} for value in arguments
            ):
                raise Violation("process identity change denied")
        elif n == 302:
            if a not in {0, pid}:
                raise Violation("other-process resource limit access denied")
            if c:
                limits = memory(pid, c, 16)
                soft = int.from_bytes(limits[:8], "little")
                hard = int.from_bytes(limits[8:], "little")
                if self.mode != "compile" or b != resource.RLIMIT_STACK or not soft <= hard <= STACK_LIMIT:
                    raise Violation(f"resource limit changes denied: {b}")
        elif n == 157:
            if a not in {15, 16}:  # PR_SET_NAME/GET_NAME only
                raise Violation("process privilege/control operation denied")
        elif n == 158:
            if a not in {0x1001, 0x1002, 0x1003, 0x1004}:
                raise Violation("unknown arch_prctl")
        elif n not in {
            7, 11, 13, 14, 15, 23, 24, 26, 27, 28, 35, 36, 37, 38,
            39, 60, 61, 63, 79, 96, 97, 98, 99, 100, 102, 104, 107, 108,
            110, 111, 115, 118, 120, 121, 124, 127, 128, 130, 131, 186, 202, 204,
            218, 219, 228, 229, 230, 231, 232, 233, 247, 270, 271,
            273, 281, 291, 292, 309, 318, 324, 334,
        }:
            raise Violation(f"unadmitted syscall {n}")
        if self.written > self.config["write_limit"]:
            raise Violation("aggregate capsule storage budget exhausted")
        if self.native_outputs is not None:
            self.native_outputs.entry(pid, state, r)

    def close_return(self, pid, state, descriptor, result):
        selected, state.native_output_close = state.native_output_close, None
        if selected is not None:
            if self.native_outputs is None or selected != descriptor:
                raise Violation("native close return differs from its shared descriptor binding")
            operation, state.native_output_operation = state.native_output_operation, None
            if operation is None or operation.kind != "close" or operation.descriptor != descriptor:
                raise Violation("native close return lost its paired entry custody")
            self.native_outputs.actor, self.native_outputs.dispatch = pid, state.native_dispatch
            self.native_outputs.custody.leave_close(operation, result=result)
        if self.read_trace is not None:
            self.read_trace.fd_closed(pid, descriptor)
        state.fds.pop(descriptor, None)

    def leave(self, pid, state, r):
        state.dependency_stop = None
        result = signed(r.rax)
        if self.native_outputs is not None:
            self.native_outputs.leave(pid, state, result)
        self.finish_metadata(pid, state, result)
        state.memory_reservation = 0
        state.process_reservation = False
        observations = state.observations
        state.observations = []
        pending, state.pending = state.pending, None
        queued, state.native_signal_buffer = state.native_signal_buffer, None
        if queued is not None:
            replace_memory(pid, queued[0], queued[1])
        if r.orig_rax == 12 and result > 0:
            state.break_end = result
        operation, value = pending if pending is not None else (None, None)
        if result < 0:
            if operation == "close" and state.native_output_close is not None:
                self.close_return(pid, state, value, result)
            if operation == "native-signal-wait" and result == -errno.EFAULT:
                queued_origins = {
                    origin for origins in state.native_signal_origins.values()
                    for origin in origins if origin[0] is not None
                }
                self.native_reconcile_signal_origins(pid, state)
                retained = {
                    origin for origins in state.native_signal_origins.values()
                    for origin in origins if origin[0] is not None
                }
                if value and queued_origins - retained:
                    self.charge_metadata(1)
                    try:
                        memory(pid, value + 32, 1)
                    except OSError as error:
                        if error.errno not in {errno.EIO, errno.EFAULT}:
                            raise
                    else:
                        raise Violation("unsupported partially accessible siginfo output")
            if (
                self.native_readonly and self.read_trace is not None and self.read_trace.runtime
                and state.role == "native" and result == -errno.EPIPE
                and r.orig_rax in {1, 18, 20} and state.kernel_io == "<pipe>"
            ):
                self.native_signal_grant(state, signal.SIGPIPE, thread=True)
                self.native_tree_event(state, {
                    "kind": "pipe-error", "pid": pid, "syscall": r.orig_rax, "error": errno.EPIPE,
                })
            if operation == "make-source-exec" and result == -errno.EACCES and self.source_execute_allowed(pid, *value):
                raise Violation(f"Make source executable lookup denied by noexec view: {value[0]}")
            return
        if operation == "native-signal":
            self.native_signal_grant(
                state, value, None if queued is None else (queued[2], queued[1]),
                thread=r.orig_rax in {200, 234, 297},
            )
        elif operation == "native-signal-wait":
            self.charge_metadata(48 if value else 0)
            padding = self.native_signal_consumed(
                pid, state, result, memory(pid, value, 48) if value else None,
            )
            if padding is not None and value:
                replace_memory(pid, value + 32, padding)
        if operation in {"open", "dup"}:
            state.fds[result] = value
        elif operation == "close":
            self.close_return(pid, state, value, result)
        elif operation == "close-range":
            if result != 0:
                raise Violation("native close_range returned an invalid successful result")
            for descriptor in value:
                self.close_return(pid, state, descriptor, result)
        elif operation == "pipe":
            data = memory(pid, value, 8)
            for offset in (0, 4):
                state.fds[int.from_bytes(data[offset:offset + 4], "little")] = "<pipe>"
        elif operation == "cwd":
            state.cwd = value
        elif operation == "helper_kind":
            r.rax = value
            ptrace(SETREGS, pid, 0, ctypes.byref(r))
        elif operation == "directory":
            self.observe_directory(pid, state, value, result)
        elif operation == "metadata-input":
            self.charge_metadata(result)
        elif operation == "event":
            if result != len(value):
                raise Violation("partial native event write")
            self.charge_metadata(len(value))
            self.reserve_observation("accessed", "native-event:" + str(len(self.events)))
            self.events.append(value.hex())
            if state.helper_kind == VO_LIVE:
                state.producer_event_written = True
        elif operation == "producer":
            state.producer_ready = True
        if not state.observation_needs_bytes or result > 0:
            for collection, path in observations:
                self.observe(collection, path)


def observer_ranges(pid):
    result = []
    for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[-1].endswith("/lib/vo-observer.so") and "x" in fields[1]:
            first, last = fields[0].split("-")
            result.append((int(first, 16), int(last, 16)))
    return tuple(result)


def signal_tracees(processes):
    for pid, record in processes.items():
        try:
            signal.pidfd_send_signal(record.pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if record.parked:
            try:
                ptrace(7, pid, 0, signal.SIGKILL)
            except OSError as error:
                if error.errno != errno.ESRCH:
                    raise


def supervise(config, drop_privileges):
    if (
        not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal")
        or not hasattr(resource, "prlimit")
    ):
        raise Violation("syscall supervisor requires pidfd and per-tracee prlimit support")
    own_descriptor = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(own_descriptor, 0)
    finally:
        os.close(own_descriptor)
    policy = Policy(config)
    channel = None
    ceilings = {name: config[name] for name in (
        "descendant_limit", "syscall_limit", "write_limit", "creation_limit",
        "observation_count", "observation_limit", "process_limit", "memory_limit",
    )}
    if config.get("producer_endpoint") is not None:
        channel = ProducerChannel.connect(
            config["producer_endpoint"],
            owner_uid=config["runner_uid"] if config["sudo_drop"] else os.geteuid(),
            server_pid=0, deadline=config["deadline"], limit=config["file_limit"],
        )
    processes = {}
    policy.processes = processes
    newborn_stops = {}
    policy.newborn_stops = newborn_stops
    vfork_waiters = {}
    error = None
    primary = None
    result = None
    def admit_native(path, inputs, context=None):
        if channel is None or not policy.native_admission:
            raise Violation("native admission lost its existing private channel")
        sequence = policy.producer_issued + 1
        policy.producer_issued = sequence
        policy.producer_pending_peak = max(policy.producer_pending_peak, 1)
        request = {
            "kind": "native-request" if context is None else "native-exec-request",
            "scope": config["producer_scope"],
            "sequence": sequence, "path": path, **inputs, "counters": policy.counters(),
            **({} if context is None else context),
        }
        raw = channel.exchange(
            encoded(request), watch=(processes[policy.make_pid].pidfd,),
        )
        reply = parse_json(raw, "native Command admission reply")
        if (
            not isinstance(reply, dict)
            or set(reply) != {"kind", "scope", "sequence", "owner", "input_sha256", "limits"} | (
                {"closure", "outputs"} if policy.native_outputs is not None else set()
            ) | (
                {"resources"} if config.get("native_resources") else set()
            )
            or reply["kind"] != "native-authorized" or reply["scope"] != config["producer_scope"]
            or type(reply["sequence"]) is not int or reply["sequence"] != sequence
            or not isinstance(reply["owner"], str) or re.fullmatch("[0-9a-f]{64}", reply["owner"]) is None
            or reply["input_sha256"] != hashlib.sha256(encoded(inputs)).hexdigest()
        ):
            raise Violation("native Command reply is foreign, stale or changes actual inputs")
        if policy.native_outputs is not None and (
            not isinstance(reply["outputs"], list)
            or any(path not in config["native_output_paths"] for path in reply["outputs"])
            or len(set(reply["outputs"])) != len(reply["outputs"])
            or not isinstance(reply["closure"], str)
            or re.fullmatch("[0-9a-f]{64}", reply["closure"]) is None
            or reply["owner"] != native_command_owner(
                reply["closure"], reply["outputs"], reply.get("resources", ()),
            )
        ):
            raise Violation("native Command reply escapes its issued output namespace")
        if config.get("native_resources"):
            if __package__:
                from .native_resources import resource_plan
            else:
                from native_resources import resource_plan
            issued = resource_plan(config["native_resources"])
            if any(row not in issued for row in resource_plan(reply["resources"])):
                raise Violation("native Command reply escapes its issued resource roles")
        policy.apply_producer_limits(reply["limits"], ceilings)
        channel.ensure_idle()
        policy.producer_completed = sequence
        return {
            "owner": reply["owner"], "input_sha256": reply["input_sha256"],
            **({"sequence": sequence} if policy.native_outputs is not None else {}),
            **({"closure": reply["closure"]} if policy.native_outputs is not None else {}),
            **({"outputs": reply["outputs"]} if policy.native_outputs is not None else {}),
            **({"resources": reply["resources"]} if config.get("native_resources") else {}),
        }

    if policy.native_admission:
        policy.native_admit = admit_native
    main_status = None
    finished_trace = None
    native_job_headers, native_job_accessed = None, None
    if config["process_limit"] < 1 or config["descendant_limit"] < 1:
        raise Violation("no remaining guest-process capacity")
    pid = os.fork()
    if pid == 0:
        try:
            os.chroot(config["root"])
            os.chdir(config.get("cwd", "/repo"))
            os.umask(0o022)
            os.closerange(3, 65536)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
            resource.setrlimit(resource.RLIMIT_FSIZE, (config["file_limit"], config["file_limit"]))
            resource.setrlimit(resource.RLIMIT_AS, (config["memory_limit"], config["memory_limit"]))
            resource.setrlimit(resource.RLIMIT_STACK, (STACK_LIMIT, STACK_LIMIT))
            cpu = max(1, math.ceil(config["deadline"] - time.monotonic()))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            if policy.native_readonly:
                for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
                    if hasattr(signal, name):
                        signal.signal(getattr(signal, name), signal.SIG_DFL)
            trace_me(drop_privileges)
            os.execve(config.get("initial_executable", config["argv"][0]),
                      config["argv"], config["environment"])
        except BaseException as failure:
            os.write(2, ("capsule exec failed: " + repr(failure)).encode("utf-8")[:4096])
            os._exit(125)
    processes[pid] = Process(
        "make" if config["mode"] == "make" else "compiler" if config["mode"] == "compile" else "command",
        memory_group=pid, pidfd=os.pidfd_open(pid), cwd=config.get("cwd", "/repo"),
    )
    if config.get("metadata_validation"):
        processes[pid].helper_kind = VO_VALIDATE
        processes[pid].metadata_index = 0
    policy.total_processes = 1
    policy.account_processes()
    parking = False

    def resume(child):
        state = processes.get(child)
        if state is None:
            return
        if parking or state.producer_ready:
            state.parked = True
            return
        if state.deferred_entry is not None:
            registers, state.deferred_entry = state.deferred_entry, None
            state.kernel_call = registers.orig_rax
            policy.entry(child, state, registers)
        state.parked = False
        delivered, state.delivery_signal = state.delivery_signal, 0
        if delivered:
            state.native_delivered = delivered
        ptrace(SYSCALL, child, 0, delivered)

    def release_vfork(child):
        for parent, waited_child in tuple(vfork_waiters.items()):
            if waited_child == child:
                del vfork_waiters[parent]
                if parent in processes:
                    resume(parent)

    def handle_stop(stopped, status):
        nonlocal main_status
        state = processes.get(stopped)
        if state is None:
            if os.WIFSTOPPED(status) and os.WSTOPSIG(status) == signal.SIGSTOP:
                if stopped not in newborn_stops:
                    policy.total_processes += 1
                    newborn_stops[stopped] = os.pidfd_open(stopped)
                policy.account_processes()
                return
            raise Violation("unrecorded sandbox descendant")
        if os.WIFEXITED(status) or os.WIFSIGNALED(status):
            code = os.waitstatus_to_exitcode(status)
            if policy.native_outputs is not None:
                policy.native_outputs.actor, policy.native_outputs.dispatch = stopped, state.native_dispatch
                policy.native_outputs.custody.retire_process(stopped)
            if policy.native_readonly and (state.role == "native" or stopped == pid) and (
                type(state.native_exit_status) is not int or state.native_exit_status != status
            ):
                raise Violation("native terminal status differs from its actual kernel exit stop")
            if policy.native_readonly and state.role == "native" and os.WIFSIGNALED(status):
                terminated = os.WTERMSIG(status)
                if terminated != state.native_delivered and not (
                    terminated == signal.SIGKILL and state.native_sigkill_outcome
                ):
                    raise Violation("native shell termination lacks an admitted self-signal")
            if policy.native_readonly and state.role == "native":
                policy.retire_native_job(stopped, state, status)
            unfulfilled = state.producer_requested and (
                state.producer_slot is None or not state.producer_event_written
            )
            del processes[stopped]
            state.close()
            vfork_waiters.pop(stopped, None)
            release_vfork(stopped)
            if stopped == pid:
                if config.get("native_root_observation"):
                    if policy.native_root is None or policy.native_root["pid"] != stopped:
                        raise Violation("native root wait lacks its actual initial execution")
                    policy.charge_metadata(8)
                    policy.native_root["wait"] = status
                main_status = code
            if unfulfilled:
                raise Violation("parked or unfulfilled producer helper exited")
            if code != 0 and not (
                stopped == pid and (config["mode"] == "make"
                or config.get("metadata_validation") and code in {1, 2})
                or policy.native_readonly and state.role == "native"
            ):
                raise Violation(f"sandbox process exited unsuccessfully: {code}")
            return
        state.parked = True
        sig = os.WSTOPSIG(status)
        event = status >> 16
        tracing_stop = sig == signal.SIGSTOP and state.newborn_stop
        if tracing_stop:
            state.newborn_stop = False
            if policy.read_trace is not None and stopped != policy.read_trace.pid:
                policy.read_trace.clear(stopped)
                if state.role == "native" and policy.read_trace.runtime:
                    policy.native_tree_event(state, {"kind": "start", "pid": stopped})
        if sig == signal.SIGTRAP and event == 6:
            outcome = ctypes.c_ulong()
            policy.charge_metadata(ctypes.sizeof(outcome))
            ptrace(0x4201, stopped, 0, ctypes.byref(outcome))
            if policy.native_readonly and (state.role == "native" or stopped == pid):
                if state.native_exit_status is not None:
                    raise Violation("native process reused its terminal kernel exit stop")
                state.native_exit_status = outcome.value
                if stopped == pid and config.get("native_root_observation"):
                    if policy.native_root is None or policy.native_root["pid"] != stopped:
                        raise Violation("native root terminal lacks its actual initial execution")
                    policy.charge_metadata(8)
                    policy.native_root["exit_stop"] = outcome.value
            policy.native_sigkill_exit(stopped, state, outcome.value)
            state.parked = False
            ptrace(7, stopped, 0, 0)
            return
        if sig == signal.SIGTRAP and event in {1, 2, 3}:
            child = ctypes.c_ulong()
            ptrace(0x4201, stopped, 0, ctypes.byref(child))
            if child.value not in newborn_stops:
                policy.total_processes += 1
            record = state.clone()
            record.native_parent = stopped
            if not state.clone_shares_vm:
                record.memory_group = child.value
            record.pidfd = newborn_stops.pop(child.value, -1)
            already_stopped = record.pidfd >= 0
            record.newborn_stop = not already_stopped
            if not already_stopped:
                record.pidfd = os.pidfd_open(child.value)
            processes[child.value] = record
            if state.role == "native" and policy.read_trace is not None and policy.read_trace.runtime:
                policy.native_tree_event(
                    state, {"kind": "fork", "pid": stopped, "child": child.value},
                )
            if policy.native_outputs is not None:
                descriptors = tuple(
                    descriptor for owner_pid, descriptor in policy.native_outputs.custody.descriptors
                    if owner_pid == stopped
                )
                policy.native_outputs.actor, policy.native_outputs.dispatch = stopped, state.native_dispatch
                policy.native_outputs.custody.inherited(stopped, child.value, descriptors)
            if not state.process_reservation:
                raise Violation("unreserved process creation")
            state.process_reservation = False
            state.memory_reservation = 0
            if event == 2:
                state.vfork_child = child.value
            policy.account_processes()
            if already_stopped:
                if policy.read_trace is not None:
                    policy.read_trace.clear(child.value)
                    if record.role == "native" and policy.read_trace.runtime:
                        policy.native_tree_event(record, {"kind": "start", "pid": child.value})
                resume(child.value)
        elif sig == signal.SIGTRAP and event == 4:
            tree_exec = None
            if state.pending is None or state.pending[0] != "exec":
                raise Violation("unapproved executable transition")
            if policy.native_readonly and state.pending[1] == "native":
                if state.exec_path not in policy.native_executables:
                    raise Violation("native exec has no admitted image")
                tree_exec = policy.bind_native_job(stopped, state)
                if tree_exec is None or state.native_execs == 1 and stopped == policy.native_jobs[state.native_dispatch]["pid"]:
                    policy.observe(
                        "accessed",
                        ("native-shell:" if state.exec_path == config.get("native_shell", "/bin/sh") else "native-exec:")
                        + str(stopped) + ":" + state.exec_path,
                    )
                state.exec_path = None
            if config.get("dependency"):
                if state.exec_path is None:
                    raise Violation("dependency exec has no admitted image")
                policy.verify_dependency_image(stopped, state.exec_path)
                state.dependency_image = state.exec_path
                state.dependency_stop = None
                policy.reserve_observation("accessed", "dependency-exec:" + str(len(policy.executed)) + state.exec_path)
                policy.executed.append(state.exec_path)
                state.exec_path = None
            if state.bootstrap:
                if config.get("native_root_observation") and stopped == pid:
                    policy.observe_native_root(stopped)
                if config.get("repository_outputs") and stopped == pid:
                    with open(f"/proc/{stopped}/cmdline", "rb") as stream:
                        data = stream.read(65537)
                    policy.charge_metadata(len(data))
                    if not data or len(data) > 65536 or not data.endswith(b"\0"):
                        raise Violation("original compiler command line exceeds its byte bound")
                    try:
                        argv = [item.decode("utf-8", "strict") for item in data[:-1].split(b"\0")]
                    except UnicodeDecodeError as error:
                        raise Violation("original compiler command line is not strict UTF-8") from error
                    actual = os.stat(f"/proc/{stopped}/cwd")
                    expected = os.stat(Path(config["root"]) / config["cwd"].lstrip("/"))
                    policy.charge_metadata(256)
                    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                        raise Violation("original compiler CWD differs from its owned directory")
                    inputs = read_epochs.native_execution_input(argv, config["cwd"])
                    policy.observe("accessed", "compiler-input:" + json.dumps(inputs))
                descriptors = {entry.name for entry in Path(f"/proc/{stopped}/fd").iterdir()}
                policy.charge_metadata(sum(len(name) + 16 for name in descriptors))
                if descriptors != {"0", "1", "2"}:
                    raise Violation("initial guest exec inherited a nonstandard descriptor")
            state.role = state.pending[1]
            state.bootstrap = False
            state.fds = (
                policy.native_exec_descriptors(stopped, state)
                if state.role == "native" and policy.read_trace is not None and policy.read_trace.runtime
                else {0: "<stdin>", 1: "<stdout>", 2: "<stderr>"}
            )
            state.observer_ranges = ()
            state.observer_ready = False
            policy.native_exec_signals(stopped, state)
            state.delivery_signal = 0
            state.native_delivered = 0
            state.native_sigkill_outcome = False
            state.memory_reservation = 0
            state.break_end = 0
            state.kernel_call = None
            policy.finish_exec(stopped, state)
            if policy.read_trace is not None:
                if tree_exec is not None and (
                    state.native_execs > 1 or stopped != policy.native_jobs[state.native_dispatch]["pid"]
                ):
                    policy.read_trace.clear(stopped)
                else:
                    policy.read_trace.actual_exec(
                        stopped, state.role == "make",
                        None if state.role == "make" else state.native_dispatch,
                        **({"inputs": None if state.role == "make" else {
                            key: policy.native_jobs[state.native_dispatch][key] for key in ("argv", "cwd")
                        }} if policy.read_trace.runtime else {}),
                    )
                if tree_exec is not None:
                    policy.native_tree_event(state, tree_exec)
            if policy.native_outputs is not None and state.role == "native":
                policy.native_outputs.successful_exec(stopped, state)
            release_vfork(stopped)
        elif sig == signal.SIGTRAP and event == 5:
            child = ctypes.c_ulong()
            ptrace(0x4201, stopped, 0, ctypes.byref(child))
            record = processes.get(child.value)
            if record is not None and record.memory_group == state.memory_group:
                vfork_waiters[stopped] = child.value
                return
        elif sig == (signal.SIGTRAP | 0x80):
            registers = Registers()
            ptrace(GETREGS, stopped, 0, ctypes.byref(registers))
            information = (ctypes.c_ubyte * 128)()
            ptrace(0x420E, stopped, len(information), ctypes.byref(information))
            if int.from_bytes(bytes(information[4:8]), "little") != 0xC000003E:
                raise Violation("unadmitted syscall architecture")
            if information[0] == 1:
                if state.role == "make" and not state.observer_ranges:
                    state.observer_ranges = observer_ranges(stopped)
                if parking:
                    state.deferred_entry = registers
                    state.kernel_call = None
                else:
                    state.kernel_call = registers.orig_rax
                    policy.entry(stopped, state, registers)
            elif information[0] == 2:
                policy.leave(stopped, state, registers)
                if state.kernel_call in {56, 58, 435}:
                    state.vfork_child = None
                state.kernel_call = None
            else:
                raise Violation("kernel did not identify syscall entry/exit")
        elif sig == signal.SIGTRAP and event == 0 and policy.read_trace is not None:
            policy.read_trace.trap(stopped, state)
            policy.confirm_readonly_entry(stopped, state)
        elif policy.native_readonly and state.role == "native" and sig == signal.SIGTRAP:
            raise Violation("unauthenticated native shell trap")
        elif policy.native_readonly and state.role == "native" and sig == signal.SIGSTOP and not tracing_stop:
            raise Violation("unsupported native shell stop")
        elif policy.native_readonly and state.role == "native" and sig == signal.SIGCHLD and policy.native_child_signal(stopped, state):
            pass
        elif sig not in {signal.SIGSTOP, signal.SIGCHLD, signal.SIGTRAP} or (
            policy.native_readonly and state.role == "native" and sig == signal.SIGCHLD
        ):
            if not policy.native_readonly or state.role != "native" or sig not in state.native_signals:
                raise Violation(f"sandbox signal {sig}")
            information = (ctypes.c_ubyte * 128)()
            policy.charge_metadata(ctypes.sizeof(information))
            ptrace(0x4202, stopped, 0, ctypes.byref(information))
            padding = policy.native_signal_consumed(stopped, state, sig, bytes(information))
            if padding is not None:
                information[32:48] = padding
                ptrace(0x4203, stopped, 0, ctypes.byref(information))
            state.delivery_signal = sig
        resume(stopped)

    def unsettled(state):
        if state.parked or state.kernel_call is None:
            return False
        if state.metadata_pending is not None or state.observations:
            return True
        # The kernel VFORK event has already accounted for this child.
        # Its parent cannot return from clone/vfork until the child execs or
        # exits; waiting for that return while the child is parked deadlocks.
        if state.vfork_child is not None and state.kernel_call in {56, 58, 435} and not state.process_reservation:
            return False
        if state.kernel_call in {7, 23, 35, 61, 202, 230, 232, 247, 270, 271, 281}:
            return False
        if state.kernel_call in {0, 17, 19} and state.kernel_io in {"<pipe>", "<stdin>"}:
            return False
        if state.kernel_call in {1, 18, 20} and state.kernel_io == "<pipe>" and state.pending is None:
            return False
        return True

    def read_slot(name, maximum):
        path = Path(config["root"]) / "control/map" / name
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            status = os.fstat(stream.fileno())
            if not stat.S_ISREG(status.st_mode) or not 0 <= status.st_size <= min(maximum, config["file_limit"]):
                raise Violation("producer result slot is nonregular or oversized")
            policy.charge_metadata(status.st_size)
            data = stream.read(status.st_size + 1)
            if len(data) != status.st_size:
                raise Violation("producer result slot changed during read")
            return data

    def fulfill_producer():
        nonlocal parking
        requester = policy.producer_requests[0]
        state = processes.get(requester)
        if state is None or not state.producer_ready or channel is None:
            raise Violation("missing parked producer request")
        parking = True
        while True:
            if time.monotonic() >= config["deadline"]:
                raise Violation("producer parking exhausted the report deadline")
            stopped, status = os.waitpid(-1, os.WNOHANG | WALL)
            if stopped:
                handle_stop(stopped, status)
                continue
            if not any(unsettled(record) for record in processes.values()):
                break
            time.sleep(0.0001)
        if requester not in processes or pid not in processes:
            raise Violation("producer context died before request notification")
        policy.producer_issued += 1
        sequence = policy.producer_issued
        request = {
            "kind": "request", "scope": config["producer_scope"], "sequence": sequence,
            "completed": policy.producer_completed, "frame": state.producer_frame.hex(),
            "counters": policy.counters(), "reserved": policy.reservations(),
            "publication": policy.publication_confirmation,
        }
        raw = channel.exchange(
            encoded(request),
            watch=[record.pidfd for record in processes.values()] + list(newborn_stops.values()),
        )
        reply = parse_json(raw, "producer reply")
        required = {
            "kind", "scope", "sequence", "slot", "owner", "outputs", "stdout_sha256", "limits",
            "publication_policy",
        }
        if (
            not isinstance(reply, dict)
            or set(reply) not in (required, required | {"adopt_sha256"})
            or reply["kind"] != "result" or reply["scope"] != config["producer_scope"]
            or type(reply["sequence"]) is not int or reply["sequence"] != sequence
            or type(reply["slot"]) is not int or reply["slot"] != sequence - 1
            or not isinstance(reply["owner"], str) or not re.fullmatch(r"[0-9a-f]{64}", reply["owner"])
            or not isinstance(reply["stdout_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", reply["stdout_sha256"])
            or not isinstance(reply["outputs"], list)
            or len(reply["outputs"]) > config["creation_limit"]
            or any(not isinstance(name, str) for name in reply["outputs"])
            or len(set(reply["outputs"])) != len(reply["outputs"])
            or type(reply["publication_policy"]) is not str
            or reply["publication_policy"] not in PUBLICATION_POLICIES
            or reply["publication_policy"] != "replace" and not reply["outputs"]
            or "adopt_sha256" in reply and (
                not isinstance(reply["adopt_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", reply["adopt_sha256"])
            )
        ):
            raise Violation("malformed, foreign or out-of-order producer reply")
        policy.apply_producer_limits(reply["limits"], ceilings)
        request_event, = _read_events(state.producer_frame, expected_mapping_count=0)
        key = f"{sequence - 1:016x}"
        if read_slot(key + ".cmd", 65536) != _event_command(request_event).encode("utf-8"):
            raise Violation("producer result slot differs from original command")
        stdout = read_slot(key + ".out", 1024*1024)
        if hashlib.sha256(stdout).hexdigest() != reply["stdout_sha256"]:
            raise Violation("producer stdout differs from its validated result")
        try:
            data = read_slot(key + ".adopt", config["file_limit"])
        except FileNotFoundError as failure:
            if "adopt_sha256" in reply:
                raise Violation("missing nested publication transfer") from failure
        else:
            if "adopt_sha256" not in reply:
                raise Violation("unacknowledged nested publication transfer")
            if hashlib.sha256(data).hexdigest() != reply["adopt_sha256"]:
                raise Violation("nested publication transfer differs from its protected result slot")
            records = parse_json(data, "completed nested publications")
            if records == []:
                raise Violation("empty nested publication transfer")
            policy.adopt_published(records)
        channel.require_live([record.pidfd for record in processes.values()] + list(newborn_stops.values()))
        channel.ensure_idle()
        effective = policy.publish(
            sequence - 1, owner=reply["owner"], outputs=reply["outputs"],
            policy=reply["publication_policy"],
        )
        policy.publication_confirmation = {
            "slot": sequence - 1, "owner": reply["owner"],
            "policy": reply["publication_policy"], "outputs": effective,
        }
        policy.producer_completed = sequence
        state.producer_slot = sequence - 1
        state.producer_ready = False
        registers = Registers()
        ptrace(GETREGS, requester, 0, ctypes.byref(registers))
        registers.rax = sequence - 1
        ptrace(SETREGS, requester, 0, ctypes.byref(registers))
        policy.producer_requests.popleft()
        parking = False
        for child, record in tuple(processes.items()):
            if record.parked:
                resume(child)

    try:
        waited, status = os.waitpid(pid, 0)
        if waited != pid or not os.WIFSTOPPED(status):
            raise Violation("sandbox child did not enter traced confinement")
        for mapping in Path(f"/proc/{pid}/maps").read_text().splitlines():
            if mapping.endswith("[heap]"):
                processes[pid].break_end = int(mapping.split()[0].split("-")[1], 16)
        ptrace(SETOPTIONS, pid, 0, OPTIONS)
        policy.reserve_memory(pid, processes[pid], 0)
        if "published" in config:
            policy.adopt_published(config["published"])
        ptrace(SYSCALL, pid)
        while processes:
            if time.monotonic() >= config["deadline"]:
                raise Violation("aggregate probe deadline exhausted in syscall supervisor")
            if channel is not None:
                channel.ensure_idle()
            if policy.producer_requests and processes[policy.producer_requests[0]].producer_ready:
                fulfill_producer()
                continue
            stopped, status = os.waitpid(-1, os.WNOHANG | WALL)
            if stopped == 0:
                time.sleep(0.0001)
                continue
            handle_stop(stopped, status)
        if newborn_stops:
            raise Violation("unresolved descendant at completion")
    except BaseException as failure:
        primary = failure
        error = str(failure)
    finally:
        def reap_owned():
            for child, descriptor in newborn_stops.items():
                processes.setdefault(child, Process("unresolved", pidfd=descriptor))
            signal_tracees(processes)
            while processes:
                try:
                    child, status = os.waitpid(-1, WALL)
                    if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                        record = processes.pop(child, None)
                        if record is not None:
                            record.close()
                    else:
                        try:
                            ptrace(SYSCALL, child, 0, signal.SIGKILL)
                        except OSError:
                            pass
                except ChildProcessError:
                    for record in processes.values():
                        record.close()
                    processes.clear()
        def finish_trace():
            nonlocal error, finished_trace, native_job_headers, native_job_accessed
            if error is None and policy.native_outputs is not None:
                try:
                    policy.native_outputs.finish()
                except BaseException as failure:
                    error = str(failure)
                    raise
            if error is None:
                try:
                    policy.finish_native_jobs()
                except BaseException as failure:
                    error = str(failure)
                    raise
            if error is None and main_status == 0 and policy.read_trace is not None:
                try:
                    trace = policy.read_trace.finish()
                    if trace["version"] == read_epochs.WRITABLE_VERSION:
                        headers = [
                            {key: value for key, value in row.items() if key != "tree"}
                            for row in policy.native_jobs.values()
                        ]
                        policy.charge_metadata(
                            sys.getsizeof(headers) + sum(sys.getsizeof(row) for row in headers)
                        )
                        accessed = sorted(
                            value for value in policy.accessed
                            if not value.startswith(("native-job:", "native-output:"))
                        )
                        policy.charge_metadata(sys.getsizeof(accessed))
                        native_job_headers, native_job_accessed = headers, accessed
                    finished_trace = trace
                except BaseException as failure:
                    error = str(failure)
                    raise

        def write_report():
            nonlocal result
            result = {
                "ok": error is None,
                "returncode": main_status,
                "error": error,
                "consumed": sorted(policy.consumed),
                "code_consumed": sorted(policy.code_consumed),
                "accessed": sorted(policy.accessed) if native_job_accessed is None else native_job_accessed,
                "processes": policy.total_processes,
                "live_process_peak": policy.live_process_peak,
                "syscalls": policy.calls,
                "written_bytes": policy.written,
                "created_files": policy.created,
                "memory_peak": policy.memory_peak,
                "observation_bytes": policy.observation_bytes,
                "observations": policy.observation_count(),
                "metadata": encode_metadata_transport(policy.metadata),
                "events": policy.events,
            }
            if config.get("native_root_observation"):
                result["native_root"] = policy.native_root
            if finished_trace is not None:
                result["read_trace"] = finished_trace
            if native_job_headers is not None:
                result["native_jobs"] = native_job_headers
            if config.get("dependency"):
                result["executed"] = policy.executed
            if channel is not None:
                result["rendezvous"] = {
                    "issued": policy.producer_issued, "completed": policy.producer_completed,
                    "pending_peak": policy.producer_pending_peak,
                    "publication": policy.publication_confirmation,
                }
            Path(config["report"]).write_text(
                json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="ascii",
            )
        def finish_channel():
            nonlocal error
            if channel is not None:
                try:
                    channel.finish(encoded({
                        "kind": "finished", "scope": config["producer_scope"],
                        "issued": policy.producer_issued, "completed": policy.producer_completed,
                        "publication": policy.publication_confirmation,
                    }))
                except BaseException as failure:
                    if error is None:
                        error = str(failure)
                    raise
        finish_cleanup([
            reap_owned, finish_channel, finish_trace, write_report,
            *([] if policy.native_outputs is None else [policy.native_outputs.custody.close]),
            *([] if policy.read_trace is None else [policy.read_trace.close]),
            *([] if channel is None else [channel.close]),
        ], primary=primary)
    return 0 if result["ok"] else 125
