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
    from .producer_channel import ProducerChannel
else:
    from authority import _event_command, _read_events, encoded, parse_json
    from lifecycle import finish_cleanup
    from producer_channel import ProducerChannel


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

    def clone(self):
        return Process(
            role=self.role, cwd=self.cwd, fds=dict(self.fds), entering=False,
            observer_ranges=self.observer_ranges, bootstrap=self.bootstrap,
            break_end=self.break_end, dispatch=self.dispatch, observer_ready=self.observer_ready,
            memory_group=self.memory_group, memory_limit=self.memory_limit,
        )

    def close(self):
        if self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1


class Policy:
    def __init__(self, config):
        self.config = config
        self.mode = config["mode"]
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
        self.producer_requests = deque()
        self.producer_issued = 0
        self.producer_completed = 0
        self.producer_pending_peak = 0
        self.published = {}
        self.memory_peak = 0
        self.processes = {}
        self.newborn_stops = {}
        self.total_processes = 0
        self.live_process_peak = 0
        self.make_pid = 0
        self.make_restarts = 0
        self.executable = set(config["executables"])
        self.executable.update(self.resolve(path) for path in config["executables"])
        self.runtime_closure = set(config.get("runtime_closure", ()))
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

    def publish(self, key, *, owner, outputs):
        mapping = Path(self.config["root"]) / "control/map"
        path = mapping / f"{key:016x}.files"
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if outputs:
                raise Violation("declared generated result is missing")
            return ()
        names = []
        with os.fdopen(descriptor, "rb") as source:
            status = os.fstat(source.fileno())
            if not stat.S_ISREG(status.st_mode) or not 44 <= status.st_size <= self.config["file_limit"]:
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
            if take(8) != b"VOGEN1\0\0":
                raise Violation("invalid generated mapping protocol")
            producer = take(32)
            if producer.hex() != owner:
                raise Violation("generated result has a foreign producer identity")
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
                if (
                    name.startswith("/") or "\\" in name
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                ):
                    raise Violation("generated output path escapes the readonly view")
                if name not in outputs or name in names:
                    raise Violation("generated result differs from its exact declared outputs")
                if any(
                    name == path or name.startswith(path + "/") or path.startswith(name + "/")
                    for path in self.config.get("reserved_paths", ())
                ):
                    raise Violation("generated result conflicts with admitted source authority")
                self.written += size
                if self.written > self.config["write_limit"]:
                    raise Violation("aggregate generated publication byte budget exhausted")
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
                    if name in self.published:
                        if self.published[name] != producer:
                            raise Violation("conflicting generated output producers")
                        os.unlink(parts[-1], dir_fd=directory)
                    self.reserve_creation()
                    try:
                        output = os.open(
                            parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=directory,
                        )
                    except FileExistsError as error:
                        raise Violation("generated output conflicts with immutable source") from error
                    with os.fdopen(output, "wb") as destination:
                        if self.config["sudo_drop"]:
                            os.fchown(destination.fileno(), self.config["runner_uid"], self.config["runner_gid"])
                        left = size
                        while left:
                            data = take(min(left, SYSCALL_MEMORY_LIMIT))
                            destination.write(data)
                            left -= len(data)
                        os.fchmod(destination.fileno(), mode)
                    self.published[name] = producer
                    names.append(name)
                finally:
                    os.close(directory)
            if remaining:
                raise Violation("trailing generated mapping bytes")
        return tuple(names)

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
            if dirfd == -100:
                return state.cwd
            return self.fd(state, dirfd)
        if not name.startswith("/"):
            base = state.cwd if dirfd == -100 else self.fd(state, dirfd)
            if base.startswith("<"):
                raise Violation("relative path through a non-directory descriptor")
            name = base.rstrip("/") + "/" + name
        return self.resolve(name, follow_final=follow_final)

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
        if (
            not (path == "/repo" or path.startswith("/repo/"))
            or self.mode == "make" and state.role != "helper"
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
        elif self.mode != "make":
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
        state.observations.clear()
        state.observation_needs_bytes = False
        trusted = self.observer(state, r)
        if n == 39 and a in {VO_READY, VO_DISPATCH, VO_QUERY_KIND, VO_METADATA, VO_PRODUCE}:
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
                elif b:
                    path = self.path(pid, state, b)
                    if path not in self.executable or path == "/control/interceptor" or c not in {0, 1}:
                        raise Violation(f"untrusted executable dispatch: {path}")
                    state.dispatch = (path, c)
                else:
                    if c:
                        raise Violation("invalid Make dispatch completion")
                    state.dispatch = None
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
            state.pending = ("close", a)
        elif n in {8, 74, 75, 73}:
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
                # Even MAP_PRIVATE + O_RDONLY can observe another process's
                # writes to the backing inode until COW. Closing/duplicating/
                # hardlinking the FD does not make /work immutable.
                if path.startswith("<") or path == "/dev/null" or path == "/work" or path.startswith("/work/"):
                    raise Violation("mutable backing-file mappings/argument races are forbidden")
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
            # Moving a cwd/dirfd ancestor changes the kernel's '..' meaning
            # without changing its recorded path. No supported tool needs it.
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
        result = signed(r.rax)
        self.finish_metadata(pid, state, result)
        state.memory_reservation = 0
        state.process_reservation = False
        observations = state.observations
        state.observations = []
        pending, state.pending = state.pending, None
        if r.orig_rax == 12 and result > 0:
            state.break_end = result
        if result < 0:
            return
        operation, value = pending if pending is not None else (None, None)
        if operation in {"open", "dup"}:
            state.fds[result] = value
        elif operation == "close":
            state.fds.pop(value, None)
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
        if parking or state.producer_ready:
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
            policy.account_processes()
            if already_stopped:
                resume(child.value)
        elif sig == signal.SIGTRAP and event == 4:
            if state.pending is None or state.pending[0] != "exec":
                raise Violation("unapproved executable transition")
            if state.bootstrap:
                descriptors = {entry.name for entry in Path(f"/proc/{stopped}/fd").iterdir()}
                policy.charge_metadata(sum(len(name) + 16 for name in descriptors))
                if descriptors != {"0", "1", "2"}:
                    raise Violation("initial guest exec inherited a nonstandard descriptor")
            state.role = state.pending[1]
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
                state.kernel_call = None
            else:
                raise Violation("kernel did not identify syscall entry/exit")
        elif sig not in {signal.SIGSTOP, signal.SIGCHLD, signal.SIGTRAP}:
            raise Violation(f"sandbox signal {sig}")
        resume(stopped)

    def unsettled(state):
        if state.parked or state.kernel_call is None:
            return False
        if state.metadata_pending is not None or state.observations:
            return True
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
        }
        raw = channel.exchange(
            encoded(request),
            watch=[record.pidfd for record in processes.values()] + list(newborn_stops.values()),
        )
        reply = parse_json(raw, "producer reply")
        if (
            not isinstance(reply, dict)
            or set(reply) != {"kind", "scope", "sequence", "slot", "owner", "outputs", "stdout_sha256", "limits"}
            or reply["kind"] != "result" or reply["scope"] != config["producer_scope"]
            or type(reply["sequence"]) is not int or reply["sequence"] != sequence
            or type(reply["slot"]) is not int or reply["slot"] != sequence - 1
            or not isinstance(reply["owner"], str) or not re.fullmatch(r"[0-9a-f]{64}", reply["owner"])
            or not isinstance(reply["stdout_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", reply["stdout_sha256"])
            or not isinstance(reply["outputs"], list)
            or len(reply["outputs"]) > config["creation_limit"]
            or any(not isinstance(name, str) for name in reply["outputs"])
            or len(set(reply["outputs"])) != len(reply["outputs"])
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
        channel.require_live([record.pidfd for record in processes.values()] + list(newborn_stops.values()))
        channel.ensure_idle()
        policy.publish(sequence - 1, owner=reply["owner"], outputs=reply["outputs"])
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
        def write_report():
            nonlocal result
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
                "metadata": policy.metadata,
                "events": policy.events,
            }
            if channel is not None:
                result["rendezvous"] = {
                    "issued": policy.producer_issued, "completed": policy.producer_completed,
                    "pending_peak": policy.producer_pending_peak,
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
                    }))
                except BaseException as failure:
                    if error is None:
                        error = str(failure)
                    raise
        finish_cleanup([
            reap_owned, finish_channel, write_report,
            *([] if channel is None else [channel.close]),
        ], primary=primary)
    return 0 if result["ok"] else 125
