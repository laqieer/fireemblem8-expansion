"""Fail-closed Linux x86-64 syscall authority, outside the candidate chroot.

The sparse, noexec mount is the filesystem boundary. Ptrace adds violation
reporting (including caught failures), FD/channel separation, exec authority
and complete metadata/mmap/directory observations. No candidate code runs in
this Python process. Mutable shared memory and namespace aliases reject.
"""

from __future__ import annotations

import ctypes
import errno
import json
import math
import os
import posixpath
import re
import resource
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.ptrace.restype = ctypes.c_long
WALL = 0x40000000
TRACEME, PEEKDATA, SYSCALL, GETREGS, SETOPTIONS = 0, 2, 24, 12, 0x4200
OPTIONS = 1 | 2 | 4 | 8 | 16 | 0x100000  # syscall/fork/vfork/clone/exec/exitkill
PROT_READ, PROT_WRITE, PROT_EXEC = 1, 2, 4
MAP_SHARED, MAP_PRIVATE, MAP_SHARED_VALIDATE = 1, 2, 3
MAP_ANONYMOUS = 0x20
# Fixed placement, loader hints and stacks do not alias pages or change their
# size. Growing/huge-page and unknown flags cannot bypass 4 KiB reservations.
MMAP_FLAGS = 3 | 0x10 | MAP_ANONYMOUS | 0x800 | 0x1000 | 0x20000 | 0x100000


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
        raise OSError(error, os.strerror(error))
    return result


def memory(pid, address, count):
    if count < 0 or count > 65536 or not address:
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
    for offset in range(0, 4096, 8):
        word = memory(pid, address + offset, 8)
        if b"\0" in word:
            result.extend(word.split(b"\0", 1)[0])
            try:
                return result.decode("utf-8", "strict")
            except UnicodeDecodeError as error:
                raise Violation("pathname is not strict UTF-8") from error
        result.extend(word)
    raise Violation("pathname exceeds bound")


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

    def clone(self):
        return Process(
            self.role, self.cwd, dict(self.fds), False, None,
            self.observer_ranges, self.bootstrap, 0, self.break_end,
        )


class Policy:
    def __init__(self, config):
        self.config = config
        self.mode = config["mode"]
        self.code = {"/repo/" + path for path in config["code"]}
        self.sources = {"/repo/" + path for path in config["sources"]}
        self.enumerations = {"/repo/" + path for path in config["enumerations"]}
        self.consumed = set()
        self.code_consumed = set()
        self.accessed = set()
        self.written = 0
        self.calls = 0
        self.created = 0
        self.observation_bytes = 0
        self.memory_peak = 0
        self.processes = {}
        self.executable = set(config["executables"])
        self.executable.update(self.resolve(path) for path in config["executables"])
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

    def observe(self, collection, value):
        if value not in collection:
            self.observation_bytes += len(value.encode("utf-8")) + 128
            if (
                self.observation_bytes > self.config["observation_limit"]
                or len(collection) >= self.config["observation_count"]
            ):
                raise Violation("aggregate filesystem-observation budget exhausted")
            collection.add(value)

    @staticmethod
    def virtual_memory(pid):
        try:
            fields = Path(f"/proc/{pid}/statm").read_bytes().split()
        except FileNotFoundError:
            return 0
        return int(fields[0]) * os.sysconf("SC_PAGE_SIZE")

    def reserve_memory(self, pid, state, additional):
        additional = (additional + 4095) & ~4095
        used = sum(
            self.virtual_memory(child) + record.memory_reservation
            for child, record in self.processes.items()
        )
        self.memory_peak = max(self.memory_peak, used + additional)
        if used + additional > self.config["memory_limit"]:
            raise Violation("aggregate address-space budget exhausted before allocation/fork")
        state.memory_reservation = additional

    def reserve_creation(self):
        self.created += 1
        if self.created > self.config["creation_limit"]:
            raise Violation("aggregate file-creation budget exhausted")

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

    def check(self, state, path, operation, *, observer=False):
        if path.startswith("<"):
            return
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
            raise Violation(f"interceptor attempted nonprotocol filesystem access: {path}")
        # These trusted runtime configuration probes are deliberately absent in
        # the chroot. No proc/etc mount or candidate-writable ancestor exists.
        if path in {
            "/proc/sys/crypto/fips_enabled", "/proc/self/stat",
            "/etc/ssl/openssl.cnf", "/usr/lib/ssl/openssl.cnf",
        }:
            if operation in {"read", "metadata"}:
                return
        if path.startswith(("/proc", "/sys", "/dev/")):
            raise Violation(f"descriptor/device namespace denied: {path}")
        if self.mode == "make" and operation == "metadata" and path in {
            "/usr/gnu/include", "/usr/local/include", "/usr/include",
        }:
            return
        if self.mode == "compile" and operation == "metadata" and path == "/proc/self/exe":
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
        runtime = (
            path.startswith(("/usr/lib/python3.", "/usr/lib/x86_64-linux-gnu/",
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
        if self.mode == "make":
            if path == "/repo" or path.startswith("/repo/"):
                for forbidden in self.config["forbidden_paths"]:
                    if path == forbidden or path.startswith(forbidden + "/"):
                        raise Violation(f"nonregular candidate source denied: {path}")
                self.observe(self.accessed, path)
                return
        elif path in self.sources:
            self.observe(self.consumed, path.removeprefix("/repo/"))
            return
        elif path in self.code:
            self.observe(self.code_consumed, path.removeprefix("/repo/"))
            return
        elif self.mode == "compile" and operation == "metadata" and path.endswith(".gch") and path[:-4] in self.code:
            return
        elif self.mode == "compile" and operation in {"metadata", "read"} and path in self.link_option_probes:
            return
        elif path in self.code_dirs | self.source_dirs:
            if operation != "directory" or path in self.code_dirs | self.enumerations:
                if operation == "directory":
                    for name in self.sources:
                        if posixpath.dirname(name) == path:
                            self.observe(self.consumed, name.removeprefix("/repo/"))
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
                return
        raise Violation(f"undeclared source {operation}: {path}")

    def check_fd(self, state, descriptor, operation, registers):
        path = self.fd(state, descriptor)
        self.check(state, path, operation, observer=self.observer(state, registers))
        return path

    def entry(self, pid, state, r):
        self.calls += 1
        if self.calls > self.config["syscall_limit"]:
            raise Violation("aggregate syscall budget exhausted")
        n = r.orig_rax
        a, b, c, d, e = r.rdi, r.rsi, r.rdx, r.r10, r.r8
        state.pending = None
        trusted = self.observer(state, r)
        if n in {2, 85, 257}:  # open, creat, openat
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
            self.check(state, path, "write" if writing else "read", observer=trusted)
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
        elif n in {5, 138}:  # fstat, fstatfs
            self.check_fd(state, a, "metadata", r)
        elif n in {0, 17, 19}:  # read/pread/readv
            self.check_fd(state, a, "read", r)
        elif n in {1, 18, 20}:  # write/pwrite/writev
            self.check_fd(state, a, "write", r)
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
        elif n == 3:
            state.pending = ("close", a)
        elif n in {8, 74, 75, 73}:
            self.check_fd(state, a, "read", r)
        elif n in {78, 217}:
            self.check_fd(state, a, "directory", r)
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
            path = self.path(pid, state, a)
            if path not in self.executable:
                raise Violation(f"untrusted executable dispatch: {path}")
            if self.mode == "make":
                role = "make" if state.bootstrap and path == "/usr/bin/make" else "helper"
                if path == "/usr/bin/make" and not state.bootstrap:
                    raise Violation("recursive native Make/SHELL dispatch denied")
            else:
                role = "compiler" if self.mode == "compile" else "command"
            state.pending = ("exec", role)
        elif n in {56, 57, 58}:
            if n == 56:
                allowed = 0x100 | 0x4000 | 0x100000 | 0x200000 | 0x1000000 | 0xFF
                if a & ~allowed or (a & 0xFF) != signal.SIGCHLD:
                    raise Violation("untraced/reparented/shared-state clone denied")
                if a & 0x100 and not a & 0x4000:
                    raise Violation("shared-memory candidate threads denied")
            self.reserve_memory(pid, state, self.virtual_memory(pid))
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
            self.reserve_memory(pid, state, self.virtual_memory(pid))
        elif n in {22, 293}:
            state.pending = ("pipe", a)
        elif n in {32, 33, 292}:
            path = self.fd(state, a)
            self.check(state, path, "read", observer=trusted)
            state.pending = ("dup", path)
        elif n == 72:
            path = self.fd(state, a)
            if b in {0, 1030}:
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
        elif n in {76, 83, 84, 87, 90, 92, 94}:
            path = self.path(pid, state, a, follow_final=n in {76, 90, 92})
            self.check(state, path, "write")
            if n == 76:
                self.written += b
            if n == 83:
                self.reserve_creation()
        elif n in {77, 91, 93}:
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
        elif n in {258, 260, 263, 268, 280}:
            follow = n == 268 or n == 260 and not e & 0x100 or n == 280 and not d & 0x100
            self.check(state, self.path(pid, state, b, signed(a), follow_final=follow), "write")
            if n == 258:
                self.reserve_creation()
        elif n == 265:
            self.check(state, self.path(pid, state, b, signed(a), follow_final=bool(e & 0x400)), "write")
            self.check(state, self.path(pid, state, d, signed(c), follow_final=False), "write")
            self.reserve_creation()
        elif n == 62:
            if signed(a) != pid:
                raise Violation("cross-process signal denied")
        elif n == 234:
            if a != pid or b != pid:
                raise Violation("cross-process thread signal denied")
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
                if self.mode != "compile" or b != resource.RLIMIT_STACK or not soft <= hard <= 16 * 1024 * 1024:
                    raise Violation(f"resource limit changes denied: {b}")
        elif n == 157:
            if a not in {15, 16}:  # PR_SET_NAME/GET_NAME only
                raise Violation("process privilege/control operation denied")
        elif n == 158:
            if a not in {0x1001, 0x1002, 0x1003, 0x1004}:
                raise Violation("unknown arch_prctl")
        elif n not in {
            7, 11, 13, 14, 15, 23, 24, 26, 27, 28, 35, 36, 37, 38,
            39, 60, 61, 63, 79, 95, 96, 97, 98, 99, 100, 102, 104, 107, 108,
            110, 111, 115, 118, 120, 121, 124, 127, 128, 129, 130, 131, 186, 202, 204,
            218, 219, 228, 229, 230, 231, 232, 233, 247, 270, 271,
            273, 281, 291, 292, 309, 318, 324, 334,
        }:
            raise Violation(f"unadmitted syscall {n}")
        if self.written > self.config["write_limit"]:
            raise Violation("aggregate capsule storage budget exhausted")

    def leave(self, pid, state, r):
        result = signed(r.rax)
        state.memory_reservation = 0
        if r.orig_rax == 12 and result > 0:
            state.break_end = result
        if result < 0 or state.pending is None:
            return
        operation, value = state.pending
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


def observer_ranges(pid):
    result = []
    for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[-1].endswith("/lib/vo-observer.so") and "x" in fields[1]:
            first, last = fields[0].split("-")
            result.append((int(first, 16), int(last, 16)))
    return tuple(result)


def supervise(config, drop_privileges):
    policy = Policy(config)
    processes = {}
    policy.processes = processes
    newborn_stops = set()
    error = None
    main_status = None
    total_processes = 0
    pid = os.fork()
    if pid == 0:
        try:
            os.chroot(config["root"])
            os.chdir("/repo")
            os.closerange(3, 65536)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
            resource.setrlimit(resource.RLIMIT_FSIZE, (config["file_limit"], config["file_limit"]))
            resource.setrlimit(resource.RLIMIT_AS, (config["memory_limit"], config["memory_limit"]))
            resource.setrlimit(resource.RLIMIT_STACK, (16 * 1024 * 1024, 16 * 1024 * 1024))
            cpu = max(1, math.ceil(config["deadline"] - time.monotonic()))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            drop_privileges()
            ptrace(TRACEME, 0)
            os.kill(os.getpid(), signal.SIGSTOP)
            os.execve(config["argv"][0], config["argv"], config["environment"])
        except BaseException as failure:
            os.write(2, ("capsule exec failed: " + repr(failure)).encode("utf-8")[:4096])
            os._exit(125)
    processes[pid] = Process(
        "make" if config["mode"] == "make" else "compiler" if config["mode"] == "compile" else "command"
    )
    try:
        waited, status = os.waitpid(pid, 0)
        if waited != pid or not os.WIFSTOPPED(status):
            raise Violation("sandbox child did not enter traced confinement")
        for mapping in Path(f"/proc/{pid}/maps").read_text().splitlines():
            if mapping.endswith("[heap]"):
                processes[pid].break_end = int(mapping.split()[0].split("-")[1], 16)
        ptrace(SETOPTIONS, pid, 0, OPTIONS)
        ptrace(SYSCALL, pid)
        total_processes = 1
        while processes:
            if time.monotonic() >= config["deadline"]:
                raise Violation("aggregate probe deadline exhausted in syscall supervisor")
            stopped, status = os.waitpid(-1, os.WNOHANG | WALL)
            if stopped == 0:
                time.sleep(0.0001)
                continue
            state = processes.get(stopped)
            if state is None:
                if os.WIFSTOPPED(status) and os.WSTOPSIG(status) == signal.SIGSTOP:
                    # Linux can report the child's initial stop before the
                    # parent's fork event. Keep it stopped until that event
                    # authenticates the relationship; never guess its role.
                    newborn_stops.add(stopped)
                    if len(newborn_stops) > config["process_limit"]:
                        raise Violation("unresolved descendant count exceeds bound")
                    continue
                raise Violation("unrecorded sandbox descendant")
            if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                code = os.waitstatus_to_exitcode(status)
                del processes[stopped]
                if stopped == pid:
                    main_status = code
                if code != 0 and not (stopped == pid and config["mode"] == "make"):
                    raise Violation(f"sandbox process exited unsuccessfully: {code}")
                continue
            sig = os.WSTOPSIG(status)
            event = status >> 16
            if sig == signal.SIGTRAP and event in {1, 2, 3}:
                child = ctypes.c_ulong()
                ptrace(0x4201, stopped, 0, ctypes.byref(child))
                total_processes += 1
                processes[child.value] = state.clone()
                state.memory_reservation = 0
                if total_processes > config["process_limit"]:
                    raise Violation("aggregate descendant-process budget exhausted")
                if child.value in newborn_stops:
                    newborn_stops.remove(child.value)
                    ptrace(SYSCALL, child.value)
            elif sig == signal.SIGTRAP and event == 4:
                if state.pending is None or state.pending[0] != "exec":
                    raise Violation("unapproved executable transition")
                state.role = state.pending[1]
                state.bootstrap = False
                state.fds = {0: "<stdin>", 1: "<stdout>", 2: "<stderr>"}
                state.observer_ranges = ()
                state.memory_reservation = 0
                state.break_end = 0
            elif sig == (signal.SIGTRAP | 0x80):
                registers = Registers()
                ptrace(GETREGS, stopped, 0, ctypes.byref(registers))
                information = (ctypes.c_ubyte * 128)()
                ptrace(0x420E, stopped, len(information), ctypes.byref(information))
                operation = information[0]
                if int.from_bytes(bytes(information[4:8]), "little") != 0xC000003E:
                    raise Violation("unadmitted syscall architecture")
                if operation == 1:
                    # The dynamic loader maps the observer after the exec event.
                    if state.role == "make" and not state.observer_ranges:
                        state.observer_ranges = observer_ranges(stopped)
                    policy.entry(stopped, state, registers)
                elif operation == 2:
                    policy.leave(stopped, state, registers)
                else:
                    raise Violation("kernel did not identify syscall entry/exit")
            elif sig not in {signal.SIGSTOP, signal.SIGCHLD, signal.SIGTRAP}:
                raise Violation(f"sandbox signal {sig}")
            ptrace(SYSCALL, stopped)
        if newborn_stops:
            raise Violation("unresolved descendant at completion")
    except BaseException as failure:
        error = str(failure)
    finally:
        for child in newborn_stops:
            processes.setdefault(child, Process("unresolved"))
        for child in tuple(processes):
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while processes:
            try:
                child, status = os.waitpid(-1, WALL)
                if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                    processes.pop(child, None)
                else:
                    try:
                        ptrace(SYSCALL, child, 0, signal.SIGKILL)
                    except OSError:
                        pass
            except ChildProcessError:
                processes.clear()
        result = {
            "ok": error is None,
            "returncode": main_status,
            "error": error,
            "consumed": sorted(policy.consumed),
            "code_consumed": sorted(policy.code_consumed),
            "accessed": sorted(policy.accessed),
            "processes": total_processes,
            "syscalls": policy.calls,
            "written_bytes": policy.written,
            "created_files": policy.created,
            "memory_peak": policy.memory_peak,
            "observation_bytes": policy.observation_bytes,
        }
        Path(config["report"]).write_text(
            json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="ascii",
        )
    return 0 if result["ok"] else 125
