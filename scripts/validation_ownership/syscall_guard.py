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
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

if __package__:
    from .authority import _event_command, _read_events, encoded, parse_json
    from .lifecycle import finish_cleanup
    from .metadata_transport import encode_metadata_transport
    from . import private_install as install_protocol
    from . import header_effects
    from . import arm_headers
    from . import header_runtime as header_protocol
    from .read_trace import NativeReadTrace
    from .read_epochs import ReadEpochError
    from . import source_phases
    from .source_effects import NativeSourceEffects
    from . import source_journal
    from .producer_channel import (
        ChannelError, ProducerChannel, PUBLICATION_MAGIC, PUBLICATION_POLICIES,
        publication_identity, validate_publication_identity, validate_dispatch_context, validate_job_context,
    )
else:
    from authority import _event_command, _read_events, encoded, parse_json
    from lifecycle import finish_cleanup
    from metadata_transport import encode_metadata_transport
    import private_install as install_protocol
    import header_effects
    import arm_headers
    import header_runtime as header_protocol
    from read_trace import NativeReadTrace
    from read_epochs import ReadEpochError
    import source_phases
    from source_effects import NativeSourceEffects
    import source_journal
    from producer_channel import (
        ChannelError, ProducerChannel, PUBLICATION_MAGIC, PUBLICATION_POLICIES,
        publication_identity, validate_publication_identity, validate_dispatch_context, validate_job_context,
    )


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.ptrace.restype = ctypes.c_long
WALL = 0x40000000
TRACEME, PEEKDATA, SYSCALL, GETREGS, SETREGS, SETOPTIONS = 0, 2, 24, 12, 13, 0x4200
OPTIONS = 1 | 2 | 4 | 8 | 16 | 32 | 0x100000  # syscall/fork/vfork/clone/exec/vforkdone/exitkill
PROT_READ, PROT_WRITE, PROT_EXEC = 1, 2, 4
MAP_SHARED, MAP_PRIVATE, MAP_SHARED_VALIDATE = 1, 2, 3
MAP_ANONYMOUS = 0x20
# Fixed placement, loader hints and stacks do not alias pages or change their
# size. Growing/huge-page and unknown flags cannot bypass 4 KiB reservations.
MMAP_FLAGS = 3 | 0x10 | MAP_ANONYMOUS | 0x800 | 0x1000 | 0x20000 | 0x100000
VO_READY, VO_DISPATCH, VO_QUERY_KIND, VO_METADATA, VO_PRODUCE, VO_JOB_POLICY = (
    0x564F4D4B00000001, 0x564F4D4B00000002, 0x564F4D4B00000003, 0x564F4D4B00000004,
    0x564F4D4B00000005, 0x564F4D4B00000006,
)
VO_RECIPE, VO_VALUE, VO_VALIDATE = 0x564F4D4B00000011, 0x564F4D4B00000012, 0x564F4D4B00000013
VO_LIVE = 0x564F4D4B00000014
VO_JOB_CONTEXT = 0x564F4D4B00000007
VO_SOURCE_IO = 0x564F4D4B00000008
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


def cstring(pid, address):
    if not address:
        raise Violation("null pathname")
    result = bytearray()
    while len(result) < 4096:
        cursor = address + len(result)
        count = min(8 - (cursor & 7), 4096 - len(result))
        word = memory(pid, cursor, count)
        if b"\0" in word:
            result.extend(word.split(b"\0", 1)[0])
            try:
                return result.decode("utf-8", "strict")
            except UnicodeDecodeError as error:
                raise Violation("pathname is not strict UTF-8") from error
        result.extend(word)
    raise Violation("pathname exceeds bound")


def recipe_arguments(pid, address, *, label="argument", byte_limit=SYSCALL_MEMORY_LIMIT, allow_empty=False):
    arguments = []
    size = 0
    for index in range(1025):
        pointer = int.from_bytes(memory(pid, address + 8 * index, 8), "little")
        if not pointer:
            if not arguments and not allow_empty:
                raise Violation("empty native recipe " + label + " vector")
            return arguments
        if index == 1024:
            break
        value = cstring(pid, pointer)
        size += len(value.encode("utf-8")) + 1
        if size > byte_limit:
            break
        arguments.append(value)
    raise Violation("native recipe " + label + " vector exceeds its frame bound")


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
    dispatch_origin: int | None = None
    helper_kind: int = 0
    native_dispatch_sequence: int | None = None
    native_dispatch_context: dict | None = None
    native_job_context: dict | None = None
    namespace_pid: int | None = None
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
    path_context: tuple[str, int, str | None] | None = None

    def clone(self):
        return Process(
            role=self.role, cwd=self.cwd, fds=dict(self.fds), entering=False,
            observer_ranges=self.observer_ranges, bootstrap=self.bootstrap,
            break_end=self.break_end, dispatch=self.dispatch, observer_ready=self.observer_ready,
            dispatch_origin=self.dispatch_origin,
            memory_group=self.memory_group, memory_limit=self.memory_limit,
            dependency_image=self.dependency_image,
        )

    def close(self):
        if self.pending is not None and self.pending[0] == "private-install":
            self.pending[1].close()
            self.pending = None
        if self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1


@dataclass
class _PendingInstall:
    source: str
    destination: str
    source_identity: tuple
    parent: str
    parent_fd: int
    source_fd: int
    arguments: tuple
    sequence: int

    def close(self):
        descriptors = self.source_fd, self.parent_fd
        self.source_fd = self.parent_fd = -1
        for descriptor in descriptors:
            if descriptor >= 0:
                os.close(descriptor)


class Policy:
    def __init__(self, config):
        self.config = config
        self.mode = config["mode"]
        self.read_trace = None
        self.source_effects = None
        self.journal_receipts = None
        try:
            header_protocol.validate_launch(config)
        except ChannelError as error:
            raise Violation(str(error)) from error
        try:
            self.private_install = install_protocol.validate_config(config.get("private_install"), config)
        except install_protocol.InstallError as error:
            raise Violation(str(error)) from error
        self.install_parents = dict(self.private_install.parents) if self.private_install else {}
        self.install_parent_fds = {}
        self.install_attempts = set()
        self.install_completed = set()
        self.code = {"/repo/" + path for path in config["code"]}
        self.sources = {"/repo/" + path for path in config["sources"]}
        self.enumerations = {posixpath.normpath("/repo/" + path) for path in config["enumerations"]}
        self.consumed = set()
        self.code_consumed = set()
        self.accessed = set()
        self.observation_attempts = {
            name: set() for name in ("consumed", "code_consumed", "accessed")
        }
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
        self.closed_processes = set()
        self.make_restarts = 0
        self.dispatch_sequence = 0
        self.executable = set(config["executables"])
        self.executable.update(self.resolve(path) for path in config["executables"])
        self.runtime_closure = set(config.get("runtime_closure", ()))
        dependency = config.get("dependency")
        self.filter_kernel = None if dependency is None else dependency.get("filter_kernel")
        self.kernel_streams = {}
        self.kernel_sequence = 0
        self.header_roots, self.header_entries, self.header_files, self.header_verified = {}, {}, {}, {}
        if dependency and "header_search" in dependency:
            try:
                self.header_roots, self.header_entries, self.header_files = arm_headers.validate_search(
                    dependency["header_search"], dependency["executables"],
                    count_limit=config["observation_count"], file_limit=config["file_limit"],
                )
            except ChannelError as error:
                raise Violation(str(error)) from error
        if dependency:
            self.runtime_closure.update(dependency["runtime_files"])
        self.runtime_directories = set()
        for name in self.runtime_closure | self.executable | set(self.header_roots) | {"/lib/vo-observer.so"}:
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
            self.dependency_metadata_descendants = {
                self.resolve(path).rstrip("/") for path in dependency["metadata_descendants"]
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
            "/repo/" + argument for argument in (
                "-lc", "-lm", "-lgcc", "-lgcc_s", "-lstdc++", *config["argv"],
            ) if argument.startswith("-l") and re.fullmatch(r"-l[A-Za-z0-9_+.-]+", argument)
        }
        self.link_option_probes.update({
            "/repo/libgcc_s.so.1", "/repo/libgcc.a", "/repo/libc.so.6",
            "/repo/libc_nonshared.a", "/repo/ld-linux-x86-64.so.2",
        })
        if "read_epochs" in config:
            request = config["read_epochs"]
            if (
                self.mode != "make" or not isinstance(request, dict)
                or set(request) != {"version", "scope", "abi"}
                or type(request["version"]) is not int or request["version"] not in {1, 2}
                or request["scope"] != config.get("producer_scope")
            ):
                raise Violation("original-read observation lacks its exact Make scope")
            self.read_trace = NativeReadTrace(self, request)
        if "source_effects" in config:
            self.source_effects = NativeSourceEffects(self, config["source_effects"])
        if "source_journal" in config:
            source_journal.validate_config(config["source_journal"], config.get("producer_scope"))
            if self.source_effects is None:
                raise Violation("source journal requires actual originating-read observation")
            self.journal_receipts = []
        self.code_dirs = {"/repo"}
        self.source_dirs = set()
        for paths, directories in ((self.code, self.code_dirs), (self.sources, self.source_dirs)):
            for name in paths:
                parent = posixpath.dirname(name)
                while parent != "/":
                    directories.add(parent)
                    parent = posixpath.dirname(parent)

    def reserve_observation(self, name, value):
        attempted = self.observation_attempts[name]
        if value not in attempted:
            self.observation_bytes += len(value.encode("utf-8")) + 128
            if (
                self.observation_bytes > self.config["observation_limit"]
                or sum(map(len, self.observation_attempts.values())) >= self.config["observation_count"]
            ):
                raise Violation("aggregate filesystem-observation budget exhausted")
            attempted.add(value)

    def observe(self, name, value):
        self.reserve_observation(name, value)
        getattr(self, name).add(value)

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
                        policy != "replace" and current is not None
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
                        if policy == "if-content-changed-preserve-mode" and current is not None:
                            mode = stat.S_IMODE(current.st_mode)
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

    def _effect_parent(self, path, *, create=False):
        view = next(item["source"] for item in self.config["mounts"] if item["target"] == "/repo")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME
        descriptor = os.open(view, flags)
        created, parts = [], []
        try:
            for part in path.split("/") if path else ():
                if time.monotonic() >= self.config["deadline"]:
                    raise Violation("aggregate deadline exhausted during header directory creation")
                parts.append(part)
                try:
                    following = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise Violation("header effect parent is absent")
                    self.reserve_creation()
                    os.mkdir(part, 0o755, dir_fd=descriptor)
                    following = os.open(part, flags, dir_fd=descriptor)
                    if self.config["sudo_drop"]:
                        os.fchown(following, self.config["runner_uid"], self.config["runner_gid"])
                    info = os.fstat(following)
                    created.append(["/".join(parts), info.st_dev, info.st_ino, info.st_mode])
                os.close(descriptor)
                descriptor = following
            return descriptor, created
        except BaseException:
            os.close(descriptor)
            raise

    def _effect_source(self, expected, parent):
        expected = header_effects.validate_record(expected)
        path, owner, mode, size, digest, identity = expected
        if size > self.config["file_limit"] or self.published.get(path) != (bytes.fromhex(owner), identity):
            raise Violation("header effect source is foreign or stale")
        descriptor = os.open(
            posixpath.basename(path),
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME | os.O_CLOEXEC,
            dir_fd=parent,
        )
        try:
            if publication_identity(os.fstat(descriptor)) != identity:
                raise Violation("header effect source identity changed")
            self.charge_metadata(size)
            actual = hashlib.sha256()
            remaining = size
            while remaining:
                if time.monotonic() >= self.config["deadline"]:
                    raise Violation("aggregate deadline exhausted during header source verification")
                data = os.read(descriptor, min(remaining, SYSCALL_MEMORY_LIMIT))
                if not data:
                    raise Violation("header effect source was truncated")
                actual.update(data)
                remaining -= len(data)
            if os.read(descriptor, 1) or actual.hexdigest() != digest:
                raise Violation("header effect source contents changed")
            if publication_identity(os.fstat(descriptor)) != identity:
                raise Violation("header effect source changed during verification")
            return descriptor, expected
        except BaseException:
            os.close(descriptor)
            raise

    def apply_header_effect(self, request, state, slot):
        if (
            state.native_job_context is None or state.native_job_context["kind"] != "recipe"
            or state.native_dispatch_context is None
            or not state.native_dispatch_context["rebuilding_makefiles"]
        ):
            raise Violation("header effect lacks its actual remake job")
        target = state.native_job_context["target"]
        effect = header_effects.Effect(
            request["operation"], request["path"], request["source"],
            request["expected"], request["owner"],
        )
        try:
            header_effects.validate_effect(effect, target)
        except (ChannelError, ValueError) as error:
            raise Violation(str(error)) from error
        required_line = {"directory": 1, "retire": 4, "transfer": 5}[effect.operation]
        if state.native_job_context["command_line"] != required_line:
            raise Violation("header effect has the wrong actual recipe ordinal")
        self.publication_name(target)
        result = {
            "kind": "filesystem", "slot": slot, "owner": effect.owner,
            "operation": effect.operation, "path": effect.path, "source": effect.source,
            "before": None, "after": None, "directories": [],
        }
        if effect.operation == "directory":
            directory, created = self._effect_parent(effect.path, create=True)
            os.close(directory)
            result["directories"] = created
        else:
            source = effect.path if effect.operation == "retire" else effect.source
            self.publication_name(source)
            directory, _ = self._effect_parent(posixpath.dirname(source))
            pinned = -1
            try:
                pinned, before = self._effect_source(effect.expected, directory)
                if publication_identity(os.stat(
                    posixpath.basename(source), dir_fd=directory, follow_symlinks=False,
                )) != before[5]:
                    raise Violation("header effect source name changed")
                result["before"] = list(before)
                if effect.operation == "retire":
                    os.unlink(posixpath.basename(source), dir_fd=directory)
                    retired = publication_identity(os.fstat(pinned))
                    if retired[:5] != before[5][:5] or retired[6] != 0:
                        raise Violation("header retirement did not unlink its actual source object")
                    del self.published[source]
                else:
                    try:
                        previous = os.stat(
                            posixpath.basename(effect.path), dir_fd=directory, follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        previous = None
                    if previous is not None and (
                        not stat.S_ISREG(previous.st_mode)
                        or self.published.get(effect.path) != (bytes.fromhex(effect.owner), publication_identity(previous))
                    ):
                        raise Violation("header transfer would replace a foreign or changed output")
                    os.rename(
                        posixpath.basename(source), posixpath.basename(effect.path),
                        src_dir_fd=directory, dst_dir_fd=directory,
                    )
                    info = os.stat(posixpath.basename(effect.path), dir_fd=directory, follow_symlinks=False)
                    identity = publication_identity(info)
                    if identity != publication_identity(os.fstat(pinned)) or identity[:5] != before[5][:5] or identity[6] != before[5][6]:
                        raise Violation("header transfer did not preserve the actual source object")
                    del self.published[source]
                    self.published[effect.path] = (bytes.fromhex(effect.owner), identity)
                    result["after"] = [effect.path, effect.owner, *before[2:5], list(identity)]
                try:
                    os.stat(posixpath.basename(source), dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise Violation("header filesystem effect retained its original source entry")
            finally:
                if pinned >= 0:
                    os.close(pinned)
                os.close(directory)
        try:
            header_effects.validate_confirmation(
                result, count_limit=self.config["creation_limit"], file_limit=self.config["file_limit"],
            )
        except (ChannelError, ValueError) as error:
            raise Violation(str(error)) from error
        self.charge_metadata(len(encoded(result)))
        return result

    def counters(self):
        return {
            "processes": self.total_processes, "syscalls": self.calls,
            "written_bytes": self.written, "created_files": self.created,
            "observation_bytes": self.observation_bytes,
            "observations": sum(map(len, self.observation_attempts.values())),
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
            "observation_count": sum(map(len, self.observation_attempts.values())),
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

    def observe_job_context(self, pid, state, pointer, size):
        if state.role != "make" or pid != self.make_pid or not state.observer_ready or size != 24:
            raise Violation("invalid native job-context sender or frame")
        record = memory(pid, pointer, size)
        self.charge_metadata(size)
        child_pid, target_pointer, command_line = (
            int.from_bytes(record[offset:offset + 8], "little") for offset in (0, 8, 16)
        )
        if not 0 < child_pid < 1 << 31 or command_line >= 1 << 32 or child_pid == self.make_pid:
            raise Violation("invalid native job-context process/index")
        child = self.processes.get(child_pid)
        if child is None:
            if child_pid in self.closed_processes:
                return
            raise Violation("native job context refers to an untracked process")
        if child.role not in {"make", "helper"}:
            raise Violation("native job context refers to a non-Make child")
        if child.namespace_pid is not None and child.namespace_pid != child_pid:
            raise Violation("native job context differs from the observed namespace PID")
        target = cstring(pid, target_pointer) if target_pointer else None
        if target_pointer and not target or not target_pointer and command_line:
            raise Violation("native job context has an invalid target/index")
        context = {
            "kind": "recipe" if target is not None else "expansion",
            "target": target, "command_line": command_line if target is not None else None,
        }
        if child.native_job_context is not None and child.native_job_context != context:
            raise Violation("native job context changed for its actual process")
        child.native_job_context = context
        self.emit_job_context(child)

    def emit_job_context(self, state):
        if state.native_job_context is None or state.native_dispatch_sequence is None:
            return
        context = {"sequence": state.native_dispatch_sequence, **state.native_job_context}
        try:
            validate_job_context(context)
        except ChannelError as error:
            raise Violation(str(error)) from error
        self.observe("accessed", "make-job-context:" + encoded(context).decode("ascii"))

    def resolve(self, name, *, follow_final=True):
        # Only trusted, immutable symlinks remain: candidate symlinks and
        # ancestor relocation are forbidden. Resolve in the guest root, not
        # through the supervisor's host-root interpretation of absolute links.
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
            if follow_final or pending:
                alias = "/" + "/".join((*resolved, part))
                if (self.mode == "make" or self.config.get("metadata_validation")) and alias in self.config.get("runtime_aliases", ()):
                    spelling = posixpath.normpath(name)
                    if ".." in name.split("/") or (
                        spelling not in self.config["executables"] and not self.runtime_metadata(spelling)
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
        return "/" + "/".join(resolved)

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
        return self.resolve(name, follow_final=follow_final)

    def fd(self, state, fd):
        if fd not in state.fds:
            raise Violation(f"unavailable inherited/unknown descriptor {fd}")
        return state.fds[fd]

    def pin_private_install_parents(self):
        for name, expected in self.install_parents.items():
            actual = Path(self.config["root"]) / name.lstrip("/")
            descriptor = os.open(actual, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.install_parent_fds[name] = descriptor
            self.charge_metadata(len(name.encode("utf-8")) + 64)
            if install_protocol.directory_identity(os.fstat(descriptor)) != expected:
                raise Violation("private install initial parent identity changed")

    def close_private_install_parents(self):
        descriptors = tuple(self.install_parent_fds.values())
        self.install_parent_fds.clear()
        for descriptor in descriptors:
            os.close(descriptor)

    def _install_parent(self, parent):
        for name, expected in self.install_parents.items():
            if parent != name and not parent.startswith(name + "/"):
                continue
            if (
                name not in self.install_parent_fds
                or install_protocol.directory_identity(os.fstat(self.install_parent_fds[name])) != expected
            ):
                raise Violation("private install lacks its original pinned parent")
            actual = Path(self.config["root"]) / name.lstrip("/")
            descriptor = os.open(actual, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                if install_protocol.directory_identity(os.fstat(descriptor)) != expected:
                    raise Violation("private install parent identity changed")
            finally:
                os.close(descriptor)
        return os.dup(self.install_parent_fds[parent])

    def _install_path(self, pid, state, address, dirfd):
        path = self.path(pid, state, address, dirfd, follow_final=False)
        spelling, descriptor, base = state.path_context
        literal = spelling if base is None else base.rstrip("/") + "/" + spelling
        try:
            install_protocol.private_path(path)
        except install_protocol.InstallError as error:
            raise Violation(str(error)) from error
        if literal != path or posixpath.dirname(path) not in self.install_parents:
            raise Violation("private install has an alias or unpinned parent")
        if base is not None:
            if base not in self.install_parents:
                raise Violation("private install relative lookup has an unpinned base")
            kernel = f"/proc/{pid}/cwd" if descriptor == -100 else f"/proc/{pid}/fd/{descriptor}"
            if install_protocol.directory_identity(os.stat(kernel)) != self.install_parents[base]:
                raise Violation("private install cwd/dirfd identity changed")
        return path, (address, spelling)

    def _install_aliases(self, pid, state, source, destination, identity):
        directory = Path(f"/proc/{pid}/fd")
        with os.scandir(directory) as entries:
            for entry in entries:
                self.charge_metadata(len(entry.name) + 64)
                if not entry.name.isdecimal():
                    raise Violation("private install has an unknown descriptor")
                descriptor = int(entry.name)
                if descriptor not in state.fds:
                    raise Violation("private install has an untracked descriptor")
                info = os.stat(entry.path)
                if (
                    stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity[:2]
                    or state.fds[descriptor] in {source, destination}
                ):
                    raise Violation("private install source/destination has an active descriptor alias")
        with open(f"/proc/{pid}/maps", "rb") as stream:
            data = stream.read(SYSCALL_MEMORY_LIMIT + 1)
        self.charge_metadata(len(data))
        if len(data) > SYSCALL_MEMORY_LIMIT:
            raise Violation("private install mapping inventory exceeds observation bound")
        for row in data.splitlines():
            fields = row.split(None, 5)
            if len(fields) < 5:
                raise Violation("private install mapping inventory is incomplete")
            try:
                device = tuple(int(part, 16) for part in fields[3].split(b":"))
                inode = int(fields[4])
            except ValueError as error:
                raise Violation("private install mapping identity is invalid") from error
            if (
                b"w" in fields[1] and b"s" in fields[1]
                or device == (os.major(identity[0]), os.minor(identity[0])) and inode == identity[1]
            ):
                raise Violation("private install has a shared or source-backed mapping")

    def prepare_private_install(self, pid, state, registers):
        if self.private_install is None:
            raise Violation("candidate directory-entry relocation is forbidden")
        if (
            self.mode != "command" or state.role != "command" or state.bootstrap
            or set(self.processes) != {pid} or self.newborn_stops
        ):
            raise Violation("private install requires its sole post-bootstrap command actor")
        if registers.orig_rax == 82:
            old_pointer, new_pointer = registers.rdi, registers.rsi
            old_dir = new_dir = -100
        else:
            if registers.orig_rax == 316 and registers.r8:
                raise Violation("private install does not admit rename flags")
            old_dir, old_pointer = signed(registers.rdi), registers.rsi
            new_dir, new_pointer = signed(registers.rdx), registers.r10
        source, old_argument = self._install_path(pid, state, old_pointer, old_dir)
        destination, new_argument = self._install_path(pid, state, new_pointer, new_dir)
        parent = posixpath.dirname(source)
        if (
            posixpath.dirname(destination) != parent
            or destination not in self.private_install.destinations or destination in self.install_attempts
            or source == destination
        ):
            raise Violation("private install destination is unissued, repeated or cross-parent")
        parent_fd = self._install_parent(parent)
        source_fd = -1
        try:
            try:
                os.stat(posixpath.basename(destination), dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Violation("private install destination already exists")
            source_fd = os.open(
                posixpath.basename(source), os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd,
            )
            info = os.fstat(source_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o7000:
                raise Violation("private install source is not a single-link regular file")
            identity = publication_identity(info)
            self._install_aliases(pid, state, source, destination, identity)
            self.reserve_creation()
            self.install_attempts.add(destination)
            state.pending = ("private-install", _PendingInstall(
                source, destination, identity, parent, parent_fd, source_fd,
                (old_argument, new_argument), len(self.install_attempts),
            ))
        except BaseException:
            if source_fd >= 0:
                os.close(source_fd)
            os.close(parent_fd)
            raise

    def finish_private_install(self, pid, pending, result):
        try:
            for address, spelling in pending.arguments:
                if cstring(pid, address) != spelling:
                    raise Violation("private install pathname changed across the kernel operation")
            if install_protocol.directory_identity(os.fstat(pending.parent_fd)) != self.install_parents[pending.parent]:
                raise Violation("private install pinned parent changed")
            checked = self._install_parent(pending.parent)
            os.close(checked)
            identity = None
            if result == 0:
                try:
                    os.stat(posixpath.basename(pending.source), dir_fd=pending.parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise Violation("private install did not retire its source name")
                installed = os.stat(
                    posixpath.basename(pending.destination), dir_fd=pending.parent_fd, follow_symlinks=False,
                )
                identity = publication_identity(installed)
                pinned = publication_identity(os.fstat(pending.source_fd))
                before = pending.source_identity
                if (
                    identity != pinned or identity[:5] != before[:5] or identity[6] != 1
                    or not stat.S_ISREG(identity[2])
                ):
                    raise Violation("private install changed the pinned regular-file contents or identity")
                self.install_completed.add(pending.destination)
            elif result > 0:
                raise Violation("private install returned an invalid kernel status")
            self.observe("accessed", install_protocol.PREFIX + encoded({
                "version": 1, "scope": self.private_install.scope, "sequence": pending.sequence,
                "source": pending.source, "destination": pending.destination,
                "result": result, "identity": identity,
            }).decode("ascii"))
        finally:
            pending.close()

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
            or self.mode == "make" and state.role != "helper" and not (optional and state.observer_ready)
        ):
            return
        number = r.orig_rax
        flags = mask = size = address = offset = 0
        if number in {4, 5, 6}:
            size, address = 144, r.rsi
        elif number == 138:
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

    def runtime_metadata(self, path, *, parents=True):
        return (
            path in self.config.get("runtime_files", ())
            or parents and path in self.config.get("runtime_parents", ())
            or any(path.startswith(absent + "/") for absent in self.config.get("runtime_absent", ()))
        )

    def check_optional_make_spelling(self, state, path, operation):
        if self.mode != "make" or state.role != "make" or state.path_context is None:
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

    def make_runtime_access(self, state, path, operation):
        if operation in {"read", "metadata"} and path in self.runtime_closure | self.executable | {"/lib/vo-observer.so"}:
            return
        if operation == "metadata" and path in self.runtime_directories:
            return
        if not state.observer_ready and operation in {"read", "metadata"} and path in self.loader_probes:
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

    def verify_dependency_mapping_span(self, image, start, end, raw_offset, ip, instruction):
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
                ) != self.dependency_image_ids[image]:
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

    def dependency_negative_purpose(self, state, path, operation):
        if state.dependency_stop is None or state.dependency_image not in self.config["dependency"]["executables"]:
            raise Violation("dependency negative probe has no verified syscall context")
        pid, number, ip = state.dependency_stop
        if self.processes.get(pid) is not state or state.pidfd < 0 or state.kernel_call != number:
            raise Violation("dependency negative probe is not owned by the stopped tracee")
        self.reserve_observation("accessed", f"dependency-purpose:{pid}:{number}:{ip}:{operation}:{path}")
        information = (ctypes.c_ubyte * 128)()
        ptrace(0x420E, pid, len(information), ctypes.byref(information))
        if (
            information[0] != 1
            or int.from_bytes(bytes(information[4:8]), "little") != 0xC000003E
            or int.from_bytes(bytes(information[8:16]), "little") != ip
            or int.from_bytes(bytes(information[24:32]), "little") != number
            or ip < 2
        ):
            raise Violation("dependency negative probe lost its actual syscall-entry stop")
        self.charge_metadata(2)
        instruction = memory(pid, ip - 2, 2)
        if instruction != b"\x0f\x05":
            raise Violation("dependency negative probe has an unsupported syscall instruction")
        self.verify_dependency_image(pid, state.dependency_image)
        with open(f"/proc/{pid}/maps", "rb") as source:
            data = source.read(SYSCALL_MEMORY_LIMIT + 1)
        self.charge_metadata(len(data))
        if len(data) > SYSCALL_MEMORY_LIMIT:
            raise Violation("dependency syscall mapping exceeds the observation bound")
        origin = mapping = None
        for line in data.splitlines():
            fields = line.split(None, 5)
            if len(fields) < 5:
                raise Violation("malformed dependency syscall mapping")
            try:
                start, end = (int(value, 16) for value in fields[0].split(b"-"))
                major, minor = (int(value, 16) for value in fields[3].split(b":"))
                inode = int(fields[4])
            except ValueError as error:
                raise Violation("malformed dependency syscall mapping identity") from error
            if start <= ip - 2 < ip < end:
                if fields[1] != b"r-xp" or inode <= 0 or origin is not None:
                    raise Violation("dependency syscall origin is not one readonly executable image")
                origin = os.makedev(major, minor), inode
                mapping = start, end, fields[2]
        if origin is None:
            raise Violation("dependency syscall origin has no executable mapping")
        for image in (self.dependency_interpreter, self.dependency_libc):
            if self.dependency_image_identity(Path(self.config["root"]) / image.lstrip("/")) != self.dependency_image_ids[image]:
                raise Violation("dependency purpose image changed after resolution")
        loader = (
            path in self.dependency_loader_probes and operation in {"read", "metadata"}
            and origin == self.dependency_image_ids[self.dependency_interpreter]
        )
        driver = (
            operation == "metadata"
            and (
                path in self.dependency_stat_probes | self.dependency_directories
                or any(
                    path.startswith(directory + "/")
                    for directory in self.dependency_metadata_descendants
                )
            )
            and state.dependency_image == self.config["dependency"]["executables"][0]
            and origin in {
                self.dependency_image_ids[state.dependency_image],
                self.dependency_image_ids[self.dependency_libc],
            }
        )
        frontend = (
            self.header_roots and operation in {"read", "metadata"}
            and state.dependency_image == self.config["dependency"]["executables"][1]
            and any(path == root or path.startswith(root + "/") for root in self.header_roots)
            and origin in {
                self.dependency_image_ids[state.dependency_image],
                self.dependency_image_ids[self.dependency_libc],
            }
        )
        filter_input = (
            self.filter_kernel is not None and operation in {"read", "metadata"}
            and path in {*header_protocol.STATFS_PATHS, *header_protocol.READ_PATHS, *header_protocol.ABSENT_PATHS}
            and state.dependency_image == "/usr/bin/sed"
            and origin in {
                self.dependency_image_ids[state.dependency_image],
                self.dependency_image_ids[self.dependency_libc],
            }
        )
        if loader:
            image = self.dependency_interpreter
        elif driver:
            image = state.dependency_image if origin == self.dependency_image_ids[state.dependency_image] else self.dependency_libc
        elif frontend:
            image = state.dependency_image if origin == self.dependency_image_ids[state.dependency_image] else self.dependency_libc
        elif filter_input:
            image = state.dependency_image if origin == self.dependency_image_ids[state.dependency_image] else self.dependency_libc
        else:
            return False
        self.verify_dependency_mapping_span(image, *mapping, ip, instruction)
        return True

    def kernel_record(self, operation, path, result, *, data=None, count=None, digest=None, eof=None):
        self.kernel_sequence += 1
        row = {
            "sequence": self.kernel_sequence, "operation": operation, "path": path,
            "result": result, "data": data, "bytes": count, "sha256": digest, "eof": eof,
        }
        self.observe("accessed", header_protocol.KERNEL_PREFIX + encoded(row).decode("ascii"))

    def header_runtime_access(self, state, path, operation, mode):
        if any(
            path == excluded or path.startswith(excluded + "/")
            for excluded in self.config["dependency"]["header_search"]["excluded"]
        ):
            raise Violation("unsupported C++ SDK namespace is not an absent C header")
        expected = self.header_files.get(path)
        if expected is not None:
            if (
                operation not in {"read", "metadata"}
                or state.dependency_image != self.config["dependency"]["executables"][1]
            ):
                raise Violation("ARM SDK input lacks its actual C frontend")
            descriptor = os.open(
                Path(self.config["root"]) / path.lstrip("/"),
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            )
            with os.fdopen(descriptor, "rb") as stream:
                before = publication_identity(os.fstat(stream.fileno()))
                if before[2:4] != (stat.S_IFREG | expected[1], expected[2]) or before[6] != 1:
                    raise Violation("captured ARM SDK input changed type, mode or extent")
                if path in self.header_verified:
                    if self.header_verified[path] != before:
                        raise Violation("captured ARM SDK input changed during execution")
                else:
                    self.charge_metadata(expected[2])
                    digest, remaining = hashlib.sha256(), expected[2]
                    while remaining:
                        if time.monotonic() >= self.config["deadline"]:
                            raise Violation("ARM SDK read exhausted the existing deadline")
                        data = stream.read(min(remaining, SYSCALL_MEMORY_LIMIT))
                        if not data:
                            raise Violation("captured ARM SDK input was truncated")
                        digest.update(data)
                        remaining -= len(data)
                    if stream.read(1) or digest.hexdigest() != expected[3]:
                        raise Violation("captured ARM SDK input differs from its exact bytes")
                    if publication_identity(os.fstat(stream.fileno())) != before:
                        raise Violation("captured ARM SDK input changed during its actual read")
                    self.header_verified[path] = before
            self.defer_observation(state, "accessed", arm_headers.PREFIX + encoded(expected).decode("ascii"))
            return
        if self.header_entries.get(path) == "directory" and operation == "metadata":
            if mode is None or not stat.S_ISDIR(mode):
                raise Violation("captured ARM SDK directory changed")
            self.defer_observation(state, "accessed", path)
            return
        if (
            path not in self.header_entries and mode is None and operation in {"read", "metadata"}
            and self.dependency_negative_purpose(state, path, operation)
        ):
            self.defer_observation(state, "accessed", path)
            return
        raise Violation("ungranted or changed ARM SDK entry is not a missing header")

    def dependency_runtime_access(self, state, path, operation):
        full = Path(self.config["root"]) / path.lstrip("/")
        try:
            mode = full.lstat().st_mode
        except FileNotFoundError:
            mode = None
        except OSError as error:
            raise Violation(f"dependency runtime path has an unsupported type: {path}") from error
        if self.filter_kernel is not None and path in header_protocol.READ_PATHS:
            if (
                operation not in {"read", "metadata"} or mode is None or not stat.S_ISREG(mode)
                or not self.dependency_negative_purpose(state, path, operation)
            ):
                raise Violation("sed kernel input escaped its actual readonly runtime purpose")
            self.defer_observation(state, "accessed", path)
            return
        if self.filter_kernel is not None and path in header_protocol.ABSENT_PATHS:
            if operation != "metadata" or mode is not None or not self.dependency_negative_purpose(state, path, operation):
                raise Violation("sed negative configuration input changed or escaped its runtime purpose")
            state.pending = ("kernel-absent", path)
            return
        if any(path == root or path.startswith(root + "/") for root in self.header_roots):
            self.header_runtime_access(state, path, operation, mode)
            return
        allowed = (
            operation in {"read", "metadata"} and path in self.dependency_files
            and mode is not None and stat.S_ISREG(mode)
            or operation == "metadata" and path in self.dependency_directories
            and mode is not None and stat.S_ISDIR(mode)
            or operation == "metadata" and path in self.dependency_stat_probes
            and self.dependency_negative_purpose(state, path, operation)
            or operation == "metadata" and any(
                path.startswith(directory + "/")
                for directory in self.dependency_metadata_descendants
            ) and self.dependency_negative_purpose(state, path, operation)
            or mode is None and (
                operation == "metadata" and path in self.dependency_directories
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
        if self.mode == "make" and state.role == "make" and not state.observer_ready:
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
        self.check(state, path, operation, observer=self.observer(state, registers))
        return path

    @staticmethod
    def signal_target(pid, *targets):
        # Candidate threads are not admitted: its PID, TGID and TID are equal.
        # Match the kernel's pid_t conversion, rejecting group/broadcast forms.
        if any(ctypes.c_int(target).value != pid for target in targets):
            raise Violation("cross-process signal target denied")

    def entry(self, pid, state, r):
        self.calls += 1
        if self.calls > self.config["syscall_limit"]:
            raise Violation("aggregate syscall budget exhausted")
        n = r.orig_rax
        a, b, c, d, e = r.rdi, r.rsi, r.rdx, r.r10, r.r8
        state.pending = None
        state.kernel_io = None
        state.metadata_pending = None
        state.path_context = None
        state.dependency_stop = (pid, r.orig_rax, r.rip) if self.config.get("dependency") else None
        state.observations.clear()
        state.observation_needs_bytes = False
        trusted = self.observer(state, r)
        if n == 39 and a in {VO_READY, VO_DISPATCH, VO_QUERY_KIND, VO_METADATA, VO_PRODUCE, VO_JOB_POLICY, VO_JOB_CONTEXT, VO_SOURCE_IO}:
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
                if a == VO_JOB_CONTEXT:
                    self.observe_job_context(pid, state, b, c)
                elif a == VO_READY:
                    if b or c or state.observer_ready:
                        raise Violation("invalid observer bootstrap notification")
                    state.observer_ready = True
                    if self.read_trace is not None:
                        self.read_trace.ready(pid)
                elif a == VO_SOURCE_IO:
                    if self.read_trace is None or state.role != "make" or not state.observer_ready:
                        raise Violation("unissued original-source stream notification")
                    self.read_trace.source_io(pid, state, b, c)
                elif a == VO_JOB_POLICY:
                    if (
                        state.role != "make" or pid != self.make_pid or not state.observer_ready
                        or not 0 < b < 1 << 31 or c not in {0, 1, 2, 3}
                    ):
                        raise Violation("invalid native Make job policy")
                    self.observe("accessed", "make-job-policy:" + encoded([b, c]).decode("ascii"))
                elif b:
                    path = self.path(pid, state, b)
                    if path not in self.executable or path == "/control/interceptor" or not 0 <= c <= 7:
                        raise Violation(f"untrusted executable dispatch: {path}")
                    if self.runtime_metadata(path, parents=False):
                        self.check_optional_make_spelling(state, path, "execute")
                    if self.source_effects is not None:
                        state.dispatch_origin = self.source_effects.originate(pid, state, path, bool(c & 4))
                    state.dispatch = (path, c)
                else:
                    if c:
                        raise Violation("invalid Make dispatch completion")
                    state.dispatch = None
                    state.dispatch_origin = None
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
            if (
                state.role == "make" and state.observer_ready and operation == "read"
                and not flags & (os.O_DIRECTORY | os.O_PATH)
                and (path == "/repo" or path.startswith("/repo/"))
            ):
                self.observe("accessed", "make-open:" + encoded([path, state.path_context[0]]).decode("ascii"))
            if creating:
                self.reserve_creation()
            state.pending = ("open", path)
        elif n in {4, 6, 21, 89, 262, 267, 269, 332, 439}:  # metadata, access, readlink
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
        elif n == 137:
            if self.filter_kernel is None:
                raise Violation("unadmitted syscall 137")
            raw = cstring(pid, a)
            path = self.path(pid, state, a)
            if (
                path != raw or path not in header_protocol.STATFS_PATHS
                or not self.dependency_negative_purpose(state, path, "metadata")
            ):
                raise Violation("sed statfs escaped its exact verified runtime input")
            present = dict(self.filter_kernel["statfs"])[path]
            try:
                info = (Path(self.config["root"]) / path.lstrip("/")).lstat()
            except FileNotFoundError:
                if present:
                    raise Violation("sed statfs lost its actual directory binding")
            else:
                if not present or not stat.S_ISDIR(info.st_mode):
                    raise Violation("sed statfs binding differs from its declared presence")
            state.pending = ("kernel-statfs", (path, b, present))
        elif n in {5, 138}:  # fstat, fstatfs
            if n == 138 and (pid, a) in self.kernel_streams:
                raise Violation("sed kernel stream filesystem queries are unsupported")
            path = self.check_fd(state, a, "metadata", r)
            self.begin_metadata(pid, state, r, path)
        elif n in {0, 17, 19}:  # read/pread/readv
            path = self.check_fd(state, a, "read", r)
            state.kernel_io = path
            state.observation_needs_bytes = True
            if state.role == "helper" and path.startswith("/control/map/") and path.endswith(".meta"):
                state.pending = ("metadata-input", None)
            if self.filter_kernel is not None and path in header_protocol.READ_PATHS:
                if n != 0 or (pid, a) not in self.kernel_streams or c > SYSCALL_MEMORY_LIMIT:
                    raise Violation("sed kernel stream escaped its sequential bounded read")
                state.pending = ("kernel-read", (a, b, c))
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
            state.pending = ("close", a)
        elif n in {8, 74, 75, 73}:
            if (pid, a) in self.kernel_streams:
                raise Violation("sed kernel stream seek/descriptor mutation is unsupported")
            self.check_fd(state, a, "read", r)
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
                if self.filter_kernel is not None and path in header_protocol.READ_PATHS:
                    raise Violation("sed kernel stream mappings are unsupported")
                # Even MAP_PRIVATE + O_RDONLY can observe another process's
                # writes to the backing inode until COW. Closing/duplicating/
                # hardlinking the FD does not make /work immutable.
                if path.startswith("<") or path == "/dev/null" or path == "/work" or path.startswith("/work/"):
                    raise Violation("mutable backing-file mappings/argument races are forbidden")
                if (
                    self.mode == "make" and c & PROT_EXEC
                    and path not in self.executable | self.runtime_closure | {"/lib/vo-observer.so"}
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
                elif path == "/control/interceptor" and state.dispatch:
                    role = "helper"
                    source, required = state.dispatch
                    state.helper_kind = VO_VALUE if (
                        required & 1 or source == "/usr/bin/make" or self.fd(state, 1) == "<pipe>"
                    ) else VO_RECIPE
                    arguments = recipe_arguments(pid, b)
                    remaining = SYSCALL_MEMORY_LIMIT - sum(len(value.encode("utf-8")) + 1 for value in arguments)
                    environment = {}
                    for value in recipe_arguments(
                        pid, c, label="environment", byte_limit=remaining, allow_empty=True,
                    ):
                        name, separator, content = value.partition("=")
                        if not separator or not name or name in environment:
                            raise Violation("malformed or duplicate native Make export")
                        environment[name] = content
                    self.dispatch_sequence += 1
                    state.native_dispatch_sequence = self.dispatch_sequence
                    context = {
                        "sequence": self.dispatch_sequence, "environment": environment,
                        "executable": source, "arguments": arguments,
                        "cwd": state.cwd, "rebuilding_makefiles": bool(required & 4),
                    }
                    try:
                        validate_dispatch_context(context)
                    except ChannelError as error:
                        raise Violation(str(error)) from error
                    state.native_dispatch_context = context
                    self.emit_job_context(state)
                    self.observe("accessed", "make-dispatch:" + encoded({
                        **context, "kind": "recipe" if state.helper_kind == VO_RECIPE else "value",
                        "global_ignore_errors": bool(required & 2),
                    }).decode("ascii"))
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
            if self.private_install is not None or self.filter_kernel is not None:
                raise Violation("private install command cannot create another actor")
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
            if self.private_install is not None or self.filter_kernel is not None:
                raise Violation("private install command cannot create another actor")
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
            if (pid, a) in self.kernel_streams or (pid, b) in self.kernel_streams:
                raise Violation("sed kernel stream descriptor aliases are unsupported")
            self.check(state, path, "read", observer=trusted)
            state.pending = ("dup", path)
        elif n == 72:
            path = self.fd(state, a)
            if (pid, a) in self.kernel_streams and b not in {1, 2, 3}:
                raise Violation("sed kernel stream fcntl mutation is unsupported")
            if b in {0, 1030}:
                self.check(state, path, "read", observer=trusted)
                state.pending = ("dup", path)
            elif b not in {1, 2, 3, 4, 5, 6, 7, 1031, 1032}:
                raise Violation("unknown fcntl operation")
        elif n == 16:
            if (pid, a) in self.kernel_streams:
                raise Violation("sed kernel stream ioctl queries are unsupported")
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
            self.check_fd(state, a, "write", r)
            if not stat.S_ISREG(os.stat(f"/proc/{pid}/fd/{a}").st_mode):
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
            self.prepare_private_install(pid, state, r)
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
        elif n in {62, 129, 200}:  # kill, rt_sigqueueinfo, tkill
            self.signal_target(pid, a)
        elif n in {234, 297}:  # tgkill, rt_tgsigqueueinfo
            self.signal_target(pid, a, b)
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
        elif n in {60, 231}:
            if any(owner == pid for owner, _ in self.kernel_streams):
                raise Violation("sed runtime exited with an unclosed kernel input")
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

    def leave(self, pid, state, r):
        state.dependency_stop = None
        result = signed(r.rax)
        self.finish_metadata(pid, state, result)
        state.memory_reservation = 0
        state.process_reservation = False
        observations = state.observations
        state.observations = []
        pending, state.pending = state.pending, None
        if r.orig_rax == 12 and result > 0:
            state.break_end = result
        operation, value = pending if pending is not None else (None, None)
        if operation == "private-install":
            self.finish_private_install(pid, value, result)
            return
        if operation == "kernel-statfs":
            path, address, present = value
            if result != (0 if present else -errno.ENOENT):
                raise Violation("sed statfs returned an unexpected actual result")
            data = None
            if present:
                self.charge_metadata(120)
                data = memory(pid, address, 120).hex()
            self.kernel_record("statfs", path, result, data=data)
            return
        if operation == "kernel-absent":
            if result != -errno.ENOENT:
                raise Violation("sed negative configuration observation changed")
            self.kernel_record("absent", value, result)
            return
        if operation == "kernel-read":
            descriptor, address, requested = value
            stream = self.kernel_streams.get((pid, descriptor))
            if stream is None or result < 0 or result > requested:
                raise Violation("sed kernel read failed or lost its actual descriptor")
            if result:
                if stream["bytes"] + result > self.config["file_limit"]:
                    raise Violation("sed kernel input exceeds its existing byte bound")
                self.charge_metadata(result)
                stream["digest"].update(memory(pid, address, result))
                stream["bytes"] += result
            else:
                stream["eof"] = True
        if result < 0:
            if operation == "make-source-exec" and result == -errno.EACCES and self.source_execute_allowed(pid, *value):
                raise Violation(f"Make source executable lookup denied by noexec view: {value[0]}")
            return
        if operation in {"open", "dup"}:
            state.fds[result] = value
            if operation == "open" and self.filter_kernel is not None and value in header_protocol.READ_PATHS:
                if (pid, result) in self.kernel_streams:
                    raise Violation("sed kernel descriptor was reused before actual close")
                self.kernel_streams[pid, result] = {
                    "path": value, "bytes": 0, "digest": hashlib.sha256(), "eof": False,
                }
        elif operation == "close":
            state.fds.pop(value, None)
            if self.read_trace is not None:
                self.read_trace.fd_closed(pid, value)
            stream = self.kernel_streams.pop((pid, value), None)
            if stream is not None:
                self.kernel_record(
                    "stream", stream["path"], 0, count=stream["bytes"],
                    digest=stream["digest"].hexdigest(), eof=stream["eof"],
                )
        elif operation == "pipe":
            data = memory(pid, value, 8)
            for offset in (0, 4):
                state.fds[int.from_bytes(data[offset:offset + 4], "little")] = "<pipe>"
        elif operation == "cwd":
            state.cwd = value
        elif operation == "helper_kind":
            if state.native_dispatch_sequence is not None and value == state.helper_kind:
                if not 0 < result < 1 << 31 or result != pid or state.namespace_pid not in {None, result}:
                    raise Violation("invalid native helper PID identity")
                state.namespace_pid = result
                self.observe("accessed", "make-helper:" + encoded([
                    state.native_dispatch_sequence, result,
                ]).decode("ascii"))
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
    for record in processes.values():
        try:
            signal.pidfd_send_signal(record.pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass


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
    main_status = None
    if config["process_limit"] < 1 or config["descendant_limit"] < 1:
        raise Violation("no remaining guest-process capacity")
    pid = os.fork()
    if pid == 0:
        try:
            os.chroot(config["root"])
            os.chdir("/repo")
            os.umask(0o022)
            os.closerange(3, 65536)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
            resource.setrlimit(resource.RLIMIT_FSIZE, (config["file_limit"], config["file_limit"]))
            resource.setrlimit(resource.RLIMIT_AS, (config["memory_limit"], config["memory_limit"]))
            resource.setrlimit(resource.RLIMIT_STACK, (STACK_LIMIT, STACK_LIMIT))
            cpu = max(1, math.ceil(config["deadline"] - time.monotonic()))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            trace_me(drop_privileges)
            os.execve(config["argv"][0], config["argv"], config["environment"])
        except BaseException as failure:
            os.write(2, ("capsule exec failed: " + repr(failure)).encode("utf-8")[:4096])
            os._exit(125)
    processes[pid] = Process(
        "make" if config["mode"] == "make" else "compiler" if config["mode"] == "compile" else "command",
        memory_group=pid, pidfd=os.pidfd_open(pid),
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
        if parking or state.producer_ready or (
            policy.read_trace is not None and child == policy.read_trace.pid
            and policy.read_trace.pending_barrier is not None
        ):
            state.parked = True
            return
        if state.deferred_entry is not None:
            registers, state.deferred_entry = state.deferred_entry, None
            state.kernel_call = registers.orig_rax
            policy.entry(child, state, registers)
        state.parked = False
        ptrace(SYSCALL, child)

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
                if policy.read_trace is not None:
                    policy.read_trace.clear(stopped)
                if stopped not in newborn_stops:
                    policy.total_processes += 1
                    newborn_stops[stopped] = os.pidfd_open(stopped)
                policy.account_processes()
                return
            raise Violation("unrecorded sandbox descendant")
        if os.WIFEXITED(status) or os.WIFSIGNALED(status):
            code = os.waitstatus_to_exitcode(status)
            unfulfilled = state.producer_requested and (
                state.producer_slot is None or not state.producer_event_written
            )
            del processes[stopped]
            state.close()
            if config["mode"] == "make":
                policy.charge_metadata(16)
                policy.closed_processes.add(stopped)
            vfork_waiters.pop(stopped, None)
            release_vfork(stopped)
            if stopped == pid:
                main_status = code
            if unfulfilled:
                raise Violation("parked or unfulfilled producer helper exited")
            if code != 0 and not (
                stopped == pid and (config["mode"] == "make"
                or config.get("metadata_validation") and code in {1, 2})
            ):
                raise Violation(f"sandbox process exited unsuccessfully: {code}")
            return
        state.parked = True
        sig = os.WSTOPSIG(status)
        event = status >> 16
        if sig == signal.SIGSTOP and policy.read_trace is not None and stopped != policy.read_trace.pid:
            policy.read_trace.clear(stopped)
        if sig == signal.SIGTRAP and event in {1, 2, 3}:
            child = ctypes.c_ulong()
            ptrace(0x4201, stopped, 0, ctypes.byref(child))
            if child.value not in newborn_stops:
                policy.total_processes += 1
            record = state.clone()
            if not state.clone_shares_vm:
                record.memory_group = child.value
            record.pidfd = newborn_stops.pop(child.value, -1)
            already_stopped = record.pidfd >= 0
            if not already_stopped:
                record.pidfd = os.pidfd_open(child.value)
            processes[child.value] = record
            if not state.process_reservation:
                raise Violation("unreserved process creation")
            state.process_reservation = False
            state.memory_reservation = 0
            if event == 2:
                state.vfork_child = child.value
            policy.account_processes()
            if already_stopped:
                resume(child.value)
        elif sig == signal.SIGTRAP and event == 4:
            if state.pending is None or state.pending[0] != "exec":
                raise Violation("unapproved executable transition")
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
                descriptors = {entry.name for entry in Path(f"/proc/{stopped}/fd").iterdir()}
                policy.charge_metadata(sum(len(name) + 16 for name in descriptors))
                if descriptors != {"0", "1", "2"}:
                    raise Violation("initial guest exec inherited a nonstandard descriptor")
            state.role = state.pending[1]
            if policy.read_trace is not None:
                policy.read_trace.actual_exec(stopped, state.role == "make")
            if policy.source_effects is not None and state.role == "helper":
                policy.source_effects.helper_exec(stopped, state)
            state.bootstrap = False
            state.fds = {0: "<stdin>", 1: "<stdout>", 2: "<stderr>"}
            state.observer_ranges = ()
            state.observer_ready = False
            state.memory_reservation = 0
            state.break_end = 0
            state.kernel_call = None
            policy.finish_exec(stopped, state)
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
        elif sig not in {signal.SIGSTOP, signal.SIGCHLD, signal.SIGTRAP}:
            raise Violation(f"sandbox signal {sig}")
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

    def park_invocation():
        nonlocal parking
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

    def release_invocation():
        nonlocal parking
        parking = False
        for child, record in tuple(processes.items()):
            if record.parked:
                resume(child)

    def fulfill_read_barrier():
        trace = policy.read_trace
        if trace is None or trace.pending_barrier is None or channel is None:
            raise Violation("missing actual source-entry barrier")
        park_invocation()
        if pid not in processes or trace.pid != pid:
            raise Violation("source-entry process died before its barrier")
        request = {
            "kind": "read-barrier", "scope": config["producer_scope"], **trace.pending_barrier,
            "issued": policy.producer_issued, "completed": policy.producer_completed,
            "publication": policy.publication_confirmation,
            "counters": policy.counters(), "reserved": policy.reservations(),
        }
        raw = channel.exchange(
            encoded(request),
            watch=[record.pidfd for record in processes.values()] + list(newborn_stops.values()),
        )
        reply = parse_json(raw, "source-entry acknowledgement")
        try:
            source_phases.validate_resume(reply, request)
        except ChannelError as error:
            raise Violation(str(error)) from error
        policy.apply_producer_limits(reply["limits"], ceilings)
        channel.require_live([record.pidfd for record in processes.values()] + list(newborn_stops.values()))
        channel.ensure_idle()
        trace.confirm_barrier(request, reply["image_sha256"])
        release_invocation()

    def journal_barrier(stage, sequence, origin, publication=None):
        if policy.journal_receipts is None:
            return
        if not parking or channel is None or pid not in processes:
            raise Violation("source publication lacks its actually parked native window")
        request = {
            "kind": "journal-barrier", "scope": config["producer_scope"],
            "barrier": len(policy.journal_receipts) + 1, "stage": stage, "producer": sequence,
            "origin": origin, "publication": publication,
            "counters": policy.counters(), "reserved": policy.reservations(),
        }
        raw = channel.exchange(
            encoded(request),
            watch=[record.pidfd for record in processes.values()] + list(newborn_stops.values()),
        )
        reply = parse_json(raw, "source-journal acknowledgement")
        source_journal.validate_resume(reply, request)
        policy.apply_producer_limits(reply["limits"], ceilings)
        channel.require_live([record.pidfd for record in processes.values()] + list(newborn_stops.values()))
        channel.ensure_idle()
        value = source_journal.receipt(reply)
        policy.charge_metadata(len(encoded(value)))
        policy.journal_receipts.append(value)

    def fulfill_producer():
        requester = policy.producer_requests[0]
        state = processes.get(requester)
        if state is None or not state.producer_ready or channel is None:
            raise Violation("missing parked producer request")
        park_invocation()
        if requester not in processes or pid not in processes:
            raise Violation("producer context died before request notification")
        policy.producer_issued += 1
        sequence = policy.producer_issued
        if state.native_dispatch_context is None:
            raise Violation("live producer has no authenticated native dispatch context")
        if state.native_job_context is None:
            raise Violation("live producer has no actual native job/expansion context")
        request = {
            "kind": "request", "scope": config["producer_scope"], "sequence": sequence,
            "completed": policy.producer_completed, "frame": state.producer_frame.hex(),
            "counters": policy.counters(), "reserved": policy.reservations(),
            "publication": policy.publication_confirmation,
            "dispatch": state.native_dispatch_context,
            "job": {"sequence": state.native_dispatch_sequence, **state.native_job_context},
        }
        if policy.source_effects is not None:
            request["source_origin"] = policy.source_effects.producer(requester, state, sequence)
        raw = channel.exchange(
            encoded(request),
            watch=[record.pidfd for record in processes.values()] + list(newborn_stops.values()),
        )
        reply = parse_json(raw, "producer reply")
        effect_request = None
        if isinstance(reply, dict) and reply.get("kind") == "effect-request":
            if (
                set(reply) != {"kind", "scope", "sequence", "slot", "owner", "operation", "path", "source", "expected", "limits"}
                or reply["scope"] != config["producer_scope"]
                or type(reply["sequence"]) is not int or reply["sequence"] != sequence
                or type(reply["slot"]) is not int or reply["slot"] != sequence - 1
                or not isinstance(reply["owner"], str) or not re.fullmatch("[0-9a-f]{64}", reply["owner"])
            ):
                raise Violation("malformed or foreign header effect request")
            effect_request = reply
            reply = {
                "kind": "result", "scope": reply["scope"], "sequence": sequence, "slot": sequence - 1,
                "owner": reply["owner"], "outputs": [], "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                "limits": reply["limits"], "publication_policy": "replace",
            }
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
        if policy.journal_receipts is not None and "adopt_sha256" in reply:
            raise Violation("fixed-directory source journal cannot attribute nested publication")
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
            if policy.source_effects is not None:
                policy.source_effects.adoption(sequence, records)
        channel.require_live([record.pidfd for record in processes.values()] + list(newborn_stops.values()))
        channel.ensure_idle()
        journal_barrier("begin", sequence, request.get("source_origin"))
        if effect_request is not None:
            try:
                read_slot(key + ".files", config["file_limit"])
            except FileNotFoundError:
                pass
            else:
                raise Violation("filesystem effect contains an unrelated file publication")
            policy.publication_confirmation = policy.apply_header_effect(effect_request, state, sequence - 1)
        else:
            effective = policy.publish(
                sequence - 1, owner=reply["owner"], outputs=reply["outputs"],
                policy=reply["publication_policy"],
            )
            policy.publication_confirmation = {
                "slot": sequence - 1, "owner": reply["owner"],
                "policy": reply["publication_policy"], "outputs": effective,
            }
        if policy.source_effects is not None:
            policy.source_effects.publication(sequence, policy.publication_confirmation)
        journal_barrier("end", sequence, request.get("source_origin"), policy.publication_confirmation)
        policy.producer_completed = sequence
        state.producer_slot = sequence - 1
        state.producer_ready = False
        registers = Registers()
        ptrace(GETREGS, requester, 0, ctypes.byref(registers))
        registers.rax = sequence - 1
        ptrace(SETREGS, requester, 0, ctypes.byref(registers))
        policy.producer_requests.popleft()
        release_invocation()

    try:
        waited, status = os.waitpid(pid, 0)
        if waited != pid or not os.WIFSTOPPED(status):
            raise Violation("sandbox child did not enter traced confinement")
        policy.pin_private_install_parents()
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
            if policy.read_trace is not None and policy.read_trace.pending_barrier is not None:
                fulfill_read_barrier()
                continue
            if policy.producer_requests:
                requested = processes[policy.producer_requests[0]]
                parent = processes.get(policy.make_pid)
                if (
                    requested.producer_ready and requested.native_job_context is None and parent is not None
                    and (parent.kernel_call == 61 or parent.kernel_call in {0, 17, 19} and parent.kernel_io == "<pipe>")
                ):
                    raise Violation("native read/wait boundary lacks the required job-context hook")
            if (
                policy.producer_requests and processes[policy.producer_requests[0]].producer_ready
                and processes[policy.producer_requests[0]].native_job_context is not None
            ):
                fulfill_producer()
                continue
            stopped, status = os.waitpid(-1, os.WNOHANG | WALL)
            if stopped == 0:
                time.sleep(0.0001)
                continue
            handle_stop(stopped, status)
        if newborn_stops:
            raise Violation("unresolved descendant at completion")
        if policy.private_install is not None and policy.install_completed != set(policy.private_install.destinations):
            raise Violation("private install command omitted a declared installation")
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
        def write_report():
            nonlocal result
            if error is None and policy.filter_kernel is not None:
                if policy.kernel_streams:
                    raise Violation("successful sed runtime retained unclosed kernel inputs")
                policy.observe("accessed", header_protocol.KERNEL_END + str(policy.kernel_sequence))
            result = {
                "ok": error is None,
                "returncode": main_status,
                "error": error,
                "consumed": sorted(policy.consumed),
                "code_consumed": sorted(policy.code_consumed),
                "accessed": sorted(policy.accessed),
                "processes": policy.total_processes,
                "live_process_peak": policy.live_process_peak,
                "syscalls": policy.calls,
                "written_bytes": policy.written,
                "created_files": policy.created,
                "memory_peak": policy.memory_peak,
                "observation_bytes": policy.observation_bytes,
                "observations": sum(map(len, policy.observation_attempts.values())),
                "metadata": encode_metadata_transport(policy.metadata),
                "events": policy.events,
            }
            if config.get("dependency"):
                result["executed"] = policy.executed
            if error is None and main_status == 0 and policy.read_trace is not None:
                result["read_trace"] = policy.read_trace.finish()
                if policy.source_effects is not None:
                    result["source_effects"] = policy.source_effects.finish(result["read_trace"])
                if policy.journal_receipts is not None:
                    if len(policy.journal_receipts) != 2 * policy.producer_completed:
                        raise Violation("native source-journal publication transcript is incomplete")
                    result["source_journal"] = {
                        "version": 1, "scope": config["producer_scope"], "mode": source_journal.MODE,
                        "receipts": policy.journal_receipts, "closed": True,
                    }
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
            reap_owned, policy.close_private_install_parents, finish_channel, write_report,
            *([] if policy.read_trace is None else [policy.read_trace.close]),
            *([] if channel is None else [channel.close]),
        ], primary=primary)
    return 0 if result["ok"] else 125
