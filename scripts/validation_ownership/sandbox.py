"""Supervisor-owned Linux sandbox and authenticated command dispatch."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import select
import selectors
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from scripts.validation_ownership.budget import (
    ProbeBudget,
    ProbeBudgetError,
    ProbeCache,
    bounded_collect,
)


ROOT = Path(__file__).resolve().parents[2]
SANDBOX_EXEC = ROOT / "scripts/validation_ownership/sandbox_exec.py"
INTERCEPTOR_SOURCE = ROOT / "scripts/validation_ownership/shell_interceptor.c"
PYTHON = Path("/usr/bin/python3")
MAKE = Path("/usr/bin/make")
CC = Path("/usr/bin/cc")
UNSHARE = Path("/usr/bin/unshare")
SUDO = Path("/usr/bin/sudo")
REQUEST_MAGIC = b"VOREQ001"
RESPONSE_MAGIC = b"VORES001"
MAX_ARGUMENTS = 4096
MAX_ARGUMENT_BYTES = 1024 * 1024
MAX_SOCKET_OUTPUT = 16 * 1024 * 1024
SECCOMP_USER_NOTIF_FLAG_CONTINUE = 1
AT_FDCWD = -100
PROT_EXEC = 0x4
SECCOMP_CONTROL_MAGIC = b"VO-SECCOMP-LISTENER-v1"


def _ioctl(direction: int, type_value: int, number: int, size: int) -> int:
    return (
        (direction << 30)
        | (size << 16)
        | (type_value << 8)
        | number
    )


SECCOMP_IOCTL_NOTIF_RECV = _ioctl(3, ord("!"), 0, 80)
SECCOMP_IOCTL_NOTIF_SEND = _ioctl(3, ord("!"), 1, 24)


class _Iovec(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("length", ctypes.c_size_t)]


@dataclass(frozen=True)
class SyscallPolicy:
    """Supervisor-only syscall/path authority for candidate Python."""

    repository_root: Path
    allowed_paths: frozenset[str]
    metadata: dict[str, dict[str, object]]
    omitted_path: str | None = None


class _SyscallSupervisor:
    PATH_SYSCALLS = {
        2: ("open", 0, None),
        4: ("stat", 0, 1),
        6: ("lstat", 0, 1),
        21: ("access", 0, None),
        89: ("readlink", 0, None),
        257: ("openat", 1, None),
        262: ("newfstatat", 1, 2),
        267: ("readlinkat", 1, None),
        269: ("faccessat", 1, None),
        332: ("statx", 1, 4),
        437: ("openat2", 1, None),
        439: ("faccessat2", 1, None),
    }

    def __init__(
        self,
        channel: socket.socket,
        policy: SyscallPolicy,
        budget: ProbeBudget,
    ):
        self.channel = channel
        self.policy = policy
        self.budget = budget
        self.events: list[dict[str, object]] = []
        self.error: BaseException | None = None
        self.stop_event = threading.Event()
        self.listener_fd: int | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self._directory_offsets: dict[tuple[int, int], int] = {}
        self._finished = False

    def __enter__(self) -> "_SyscallSupervisor":
        self.channel.settimeout(0.05)
        self.thread.start()
        return self

    @staticmethod
    def _read_memory(pid: int, address: int, size: int) -> bytes:
        if address == 0 or size <= 0 or size > 1024 * 1024:
            raise ProbeSandboxError("candidate syscall memory range is invalid")
        buffer = ctypes.create_string_buffer(size)
        local = _Iovec(ctypes.cast(buffer, ctypes.c_void_p), size)
        remote = _Iovec(ctypes.c_void_p(address), size)
        libc = ctypes.CDLL(None, use_errno=True)
        count = libc.process_vm_readv(
            pid,
            ctypes.byref(local),
            1,
            ctypes.byref(remote),
            1,
            0,
        )
        if count < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), "process_vm_readv")
        return bytes(buffer.raw[:count])

    @staticmethod
    def _write_memory(pid: int, address: int, data: bytes) -> None:
        buffer = ctypes.create_string_buffer(data)
        local = _Iovec(ctypes.cast(buffer, ctypes.c_void_p), len(data))
        remote = _Iovec(ctypes.c_void_p(address), len(data))
        libc = ctypes.CDLL(None, use_errno=True)
        count = libc.process_vm_writev(
            pid,
            ctypes.byref(local),
            1,
            ctypes.byref(remote),
            1,
            0,
        )
        if count != len(data):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), "process_vm_writev")

    @classmethod
    def _read_c_string(cls, pid: int, address: int) -> str:
        data = cls._read_memory(pid, address, 4096)
        end = data.find(b"\0")
        if end < 0:
            raise ProbeSandboxError("candidate syscall path is not terminated")
        try:
            return data[:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProbeSandboxError(
                "candidate syscall path is not valid UTF-8"
            ) from error

    def _translate_host_path(self, pid: int, value: str) -> str:
        repository = self.policy.repository_root.resolve(strict=True)
        path = Path(value)
        candidates = [repository]
        try:
            process_root = Path(f"/proc/{pid}/root").resolve(strict=True)
            candidates.append(process_root / "repo")
        except OSError:
            pass
        resolved = path.resolve(strict=False)
        for candidate_root in candidates:
            try:
                relative = resolved.relative_to(
                    candidate_root.resolve(strict=False)
                )
            except ValueError:
                continue
            return (
                "/repo"
                if not relative.parts
                else f"/repo/{relative.as_posix()}"
            )
        return os.path.normpath(value)

    def _canonical_path(
        self,
        pid: int,
        directory_fd: int,
        raw_path: str,
    ) -> str:
        if raw_path.startswith("/"):
            candidate = os.path.normpath(raw_path)
        else:
            descriptor_path = (
                f"/proc/{pid}/cwd"
                if directory_fd == AT_FDCWD
                else f"/proc/{pid}/fd/{directory_fd}"
            )
            base = self._translate_host_path(pid, os.readlink(descriptor_path))
            candidate = os.path.normpath(f"{base}/{raw_path}")
        if candidate != "/repo" and not candidate.startswith("/repo/"):
            raise PermissionError(
                f"candidate path {candidate!r} is outside the admitted repository"
            )
        return candidate

    @staticmethod
    def _stat_bytes(record: dict[str, object]) -> bytes:
        data = bytearray(144)
        mode = int(record["mode"])
        uid = int(record["uid"])
        gid = int(record["gid"])
        size = int(record["size"])
        mtime_ns = int(record["mtime_ns"])
        struct.pack_into("<I", data, 24, mode)
        struct.pack_into("<I", data, 28, uid)
        struct.pack_into("<I", data, 32, gid)
        struct.pack_into("<q", data, 48, size)
        struct.pack_into("<q", data, 88, mtime_ns // 1_000_000_000)
        struct.pack_into("<q", data, 96, mtime_ns % 1_000_000_000)
        return bytes(data)

    @staticmethod
    def _statx_bytes(record: dict[str, object]) -> bytes:
        data = bytearray(256)
        mask = 0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0040 | 0x0200
        mtime_ns = int(record["mtime_ns"])
        struct.pack_into("<I", data, 0, mask)
        struct.pack_into("<I", data, 20, int(record["uid"]))
        struct.pack_into("<I", data, 24, int(record["gid"]))
        struct.pack_into("<H", data, 28, int(record["mode"]))
        struct.pack_into("<Q", data, 40, int(record["size"]))
        struct.pack_into("<qI", data, 112, mtime_ns // 1_000_000_000, mtime_ns % 1_000_000_000)
        return bytes(data)

    def _path_for_fd(self, pid: int, descriptor: int) -> str:
        return self._translate_host_path(
            pid,
            os.readlink(f"/proc/{pid}/fd/{descriptor}")
        )

    def _record(self, operation: str, path: str, *, denied: bool) -> None:
        encoded = f"{operation}\0{path}\0{int(denied)}".encode("utf-8")
        self.budget.charge_count("events")
        self.budget.charge_bytes("events", len(encoded))
        self.events.append(
            {"denied": denied, "operation": operation, "path": path}
        )

    def _directory_bytes(
        self,
        pid: int,
        descriptor: int,
        path: str,
        maximum: int,
    ) -> bytes:
        if maximum < 24:
            raise ProbeSandboxError("candidate directory buffer is too small")
        maximum = min(maximum, 1024 * 1024)
        prefix = path.rstrip("/") + "/"
        children = []
        for candidate, record in self.policy.metadata.items():
            self.budget.remaining("directory observation")
            if not candidate.startswith(prefix):
                continue
            suffix = candidate[len(prefix):]
            if not suffix or "/" in suffix:
                continue
            if len(children) >= self.budget.limits.counts["snapshot_files"]:
                raise ProbeBudgetError(
                    "directory observation exceeds aggregate count bound"
                )
            children.append((suffix, record))
        children.sort(key=lambda item: item[0])
        key = (pid, descriptor)
        index = self._directory_offsets.get(key, 0)
        output = bytearray()
        while index < len(children):
            name, record = children[index]
            encoded = name.encode("utf-8") + b"\0"
            record_length = (19 + len(encoded) + 7) & ~7
            if len(output) + record_length > maximum:
                break
            chunk = bytearray(record_length)
            entry_type = 8 if record["kind"] == "file" else 4
            struct.pack_into(
                "<QqHB",
                chunk,
                0,
                0,
                index + 1,
                record_length,
                entry_type,
            )
            chunk[19:19 + len(encoded)] = encoded
            output.extend(chunk)
            index += 1
        if not output and index < len(children):
            raise ProbeSandboxError("candidate directory buffer is too small")
        self._directory_offsets[key] = index
        return bytes(output)

    def _respond(
        self,
        notification_id: int,
        *,
        error: int = 0,
        value: int = 0,
        continue_syscall: bool = False,
    ) -> None:
        assert self.listener_fd is not None
        response = struct.pack(
            "<QqiI",
            notification_id,
            value,
            -error if error else 0,
            SECCOMP_USER_NOTIF_FLAG_CONTINUE if continue_syscall else 0,
        )
        fcntl.ioctl(
            self.listener_fd,
            SECCOMP_IOCTL_NOTIF_SEND,
            response,
        )

    def _handle(self, data: bytes) -> None:
        notification_id, pid, _, number, arch, _, *arguments = struct.unpack(
            "<QIIiIQQQQQQQ",
            data,
        )
        if arch != 0xC000003E:
            self._respond(notification_id, error=errno.EPERM)
            return
        if number == 3:
            self._directory_offsets.pop((pid, arguments[0]), None)
            self._respond(notification_id, continue_syscall=True)
            return
        if number in {9, 10, 329}:
            protection = arguments[2]
            if protection & PROT_EXEC:
                self._record("exec-memory", "<memory>", denied=True)
                self._respond(notification_id, error=errno.EPERM)
                return
            if number == 9 and arguments[4] != 0xFFFFFFFFFFFFFFFF:
                try:
                    path = self._path_for_fd(pid, arguments[4])
                except OSError:
                    path = "<unknown-fd>"
                if (
                    path == self.policy.omitted_path
                    or path not in self.policy.allowed_paths
                ):
                    self._record("mmap", path, denied=True)
                    self._respond(notification_id, error=errno.EACCES)
                    return
                self._record("mmap", path, denied=False)
            self._respond(notification_id, continue_syscall=True)
            return
        if number in {5, 217}:
            try:
                path = self._path_for_fd(pid, arguments[0])
            except OSError:
                self._respond(notification_id, error=errno.EACCES)
                return
            operation = "fstat" if number == 5 else "scandir"
            if path == self.policy.omitted_path:
                self._record(operation, path, denied=True)
                self._respond(notification_id, error=errno.ENOENT)
                return
            if path not in self.policy.allowed_paths:
                self._record(operation, path, denied=True)
                self._respond(notification_id, error=errno.EACCES)
                return
            self._record(operation, path, denied=False)
            if number == 5:
                payload = self._stat_bytes(self.policy.metadata[path])
                self.budget.charge_bytes("outputs", len(payload))
                self._write_memory(
                    pid,
                    arguments[1],
                    payload,
                )
                self._respond(notification_id)
            else:
                directory_data = self._directory_bytes(
                    pid,
                    arguments[0],
                    path,
                    arguments[2],
                )
                if directory_data:
                    self.budget.charge_bytes("outputs", len(directory_data))
                    self._write_memory(pid, arguments[1], directory_data)
                self._respond(notification_id, value=len(directory_data))
            return
        specification = self.PATH_SYSCALLS.get(number)
        if specification is None:
            self._respond(notification_id, error=errno.EPERM)
            return
        operation, path_index, output_index = specification
        directory_fd = (
            ctypes.c_int(arguments[0]).value
            if number in {257, 262, 267, 269, 332, 437, 439}
            else AT_FDCWD
        )
        try:
            raw_path = self._read_c_string(pid, arguments[path_index])
            path = self._canonical_path(pid, directory_fd, raw_path)
        except (OSError, PermissionError, ProbeSandboxError):
            self._respond(notification_id, error=errno.EACCES)
            return
        omitted = self.policy.omitted_path
        if path == omitted:
            self._record(operation, path, denied=True)
            self._respond(notification_id, error=errno.ENOENT)
            return
        if path not in self.policy.allowed_paths:
            self._record(operation, path, denied=True)
            self._respond(notification_id, error=errno.EACCES)
            return
        if operation in {"open", "openat", "openat2"}:
            if operation == "open":
                flags = arguments[1]
            elif operation == "openat":
                flags = arguments[2]
            else:
                flags = struct.unpack(
                    "<Q",
                    self._read_memory(pid, arguments[2], 8),
                )[0]
            if flags & (
                os.O_WRONLY
                | os.O_RDWR
                | os.O_CREAT
                | os.O_TRUNC
                | os.O_APPEND
            ):
                self._record(operation, path, denied=True)
                self._respond(notification_id, error=errno.EACCES)
                return
        self._record(operation, path, denied=False)
        if output_index is None:
            self._respond(notification_id, continue_syscall=True)
        elif operation == "statx":
            payload = self._statx_bytes(self.policy.metadata[path])
            self.budget.charge_bytes("outputs", len(payload))
            self._write_memory(
                pid,
                arguments[output_index],
                payload,
            )
            self._respond(notification_id)
        else:
            payload = self._stat_bytes(self.policy.metadata[path])
            self.budget.charge_bytes("outputs", len(payload))
            self._write_memory(
                pid,
                arguments[output_index],
                payload,
            )
            self._respond(notification_id)

    def _run(self) -> None:
        try:
            while not self.stop_event.is_set() and self.listener_fd is None:
                try:
                    message, ancillary, _, _ = self.channel.recvmsg(
                        32,
                        socket.CMSG_SPACE(struct.calcsize("i")),
                    )
                except socket.timeout:
                    continue
                if message != SECCOMP_CONTROL_MAGIC:
                    raise ProbeSandboxError(
                        "candidate seccomp listener message is invalid"
                    )
                for level, kind, payload in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        self.listener_fd = struct.unpack("i", payload[:4])[0]
                        os.set_blocking(self.listener_fd, False)
                        break
                if self.listener_fd is None:
                    raise ProbeSandboxError(
                        "candidate did not transfer a seccomp listener"
                    )
            while not self.stop_event.is_set() and self.listener_fd is not None:
                readable, _, _ = select.select([self.listener_fd], [], [], 0.05)
                if not readable:
                    continue
                buffer = bytearray(80)
                try:
                    fcntl.ioctl(
                        self.listener_fd,
                        SECCOMP_IOCTL_NOTIF_RECV,
                        buffer,
                        True,
                    )
                except BlockingIOError:
                    continue
                except OSError as error:
                    if error.errno in {errno.ENOENT, errno.EBADF}:
                        continue
                    raise
                try:
                    self._handle(bytes(buffer))
                except BaseException as error:
                    self.error = error
                    notification_id = struct.unpack_from("<Q", buffer, 0)[0]
                    try:
                        self._respond(
                            notification_id,
                            error=errno.EOVERFLOW,
                        )
                    except OSError:
                        pass
                    self.stop_event.set()
                    return
        except BaseException as error:
            if not self.stop_event.is_set():
                self.error = error

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.stop_event.set()
        if self.listener_fd is not None:
            os.close(self.listener_fd)
            self.listener_fd = None
        self.channel.close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise ProbeSandboxError("seccomp supervisor did not stop")
        if self.error is not None:
            raise ProbeSandboxError(
                f"seccomp supervisor failed: {self.error}"
            ) from self.error

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.finish()


class ProbeSandboxError(RuntimeError):
    """Raised when the trusted sandbox cannot prove a closed execution."""


def strict_utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProbeSandboxError(f"{label} is not valid UTF-8") from error


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.02)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_bounded_process(
    command: list[str],
    budget: ProbeBudget,
    *,
    environment: dict[str, str],
    cwd: Path | None = None,
    on_start: Callable[[int], None] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[bytes]:
    """Run one child under the aggregate deadline and output allowance."""
    budget.reserve_subprocess()
    process = subprocess.Popen(
        command,
        cwd=None if cwd is None else str(cwd),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
        pass_fds=pass_fds,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    assert process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    try:
        if on_start is not None:
            on_start(process.pid)
        while selector.get_map():
            timeout = min(0.1, budget.remaining("subprocess"))
            for key, _ in selector.select(timeout):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                budget.charge_bytes("outputs", len(chunk))
                chunks[key.data].append(chunk)
            if process.poll() is not None:
                for key in list(selector.get_map().values()):
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if chunk:
                        budget.charge_bytes("outputs", len(chunk))
                        chunks[key.data].append(chunk)
                    else:
                        selector.unregister(key.fileobj)
        process.wait(timeout=budget.remaining("subprocess completion"))
    except BaseException:
        _terminate_process_group(process)
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        b"".join(chunks["stdout"]),
        b"".join(chunks["stderr"]),
    )


def _clean_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "SOURCE_DATE_EPOCH": "0",
        "TZ": "UTC",
    }
    if extra:
        for name, value in extra.items():
            if (
                not re.fullmatch(r"[A-Z][A-Z0-9_]*", name)
                or "\0" in value
                or name in {"LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH"}
            ):
                raise ProbeSandboxError(
                    f"sandbox environment entry {name!r} is not admitted"
                )
            environment[name] = value
    return environment


def _launcher_prefix(budget: ProbeBudget) -> tuple[list[str], dict[str, str]]:
    probe = [
        str(UNSHARE),
        "--user",
        "--map-root-user",
        "--mount",
        "--ipc",
        "--net",
        "--pid",
        "--fork",
        "--kill-child",
        "--propagation",
        "private",
        "/usr/bin/true",
    ]
    completed = run_bounded_process(
        probe,
        budget,
        environment=_clean_environment(),
    )
    if completed.returncode == 0:
        return (
            [
                str(UNSHARE),
                "--user",
                "--map-root-user",
                "--mount",
                "--ipc",
                "--net",
                "--pid",
                "--fork",
                "--kill-child",
                "--propagation",
                "private",
            ],
            {
                "mode": "user-namespace",
                "runner_gid": os.getgid(),
                "runner_uid": os.getuid(),
                "sudo_drop": False,
                "unshare_sha256": sha256_file(UNSHARE),
            },
        )
    sudo_probe = [
        str(SUDO),
        "-n",
        str(UNSHARE),
        "--mount",
        "--ipc",
        "--net",
        "--pid",
        "--fork",
        "--kill-child",
        "--propagation",
        "private",
        "/usr/bin/true",
    ]
    completed = run_bounded_process(
        sudo_probe,
        budget,
        environment=_clean_environment(),
    )
    if completed.returncode != 0:
        raise ProbeSandboxError(
            "neither unprivileged namespaces nor exact passwordless "
            "sudo unshare is available"
        )
    return (
        [
            str(SUDO),
            "-n",
            str(UNSHARE),
            "--mount",
            "--ipc",
            "--net",
            "--pid",
            "--fork",
            "--kill-child",
            "--propagation",
            "private",
        ],
        {
            "mode": "sudo-namespace",
            "runner_gid": os.getgid(),
            "runner_uid": os.getuid(),
            "sudo_drop": True,
            "sudo_sha256": sha256_file(SUDO),
            "unshare_sha256": sha256_file(UNSHARE),
        },
    )


def _runtime_dependencies(
    executables: Iterable[Path],
    budget: ProbeBudget,
) -> list[tuple[Path, str]]:
    dependencies: dict[str, Path] = {}
    selected_executables = bounded_collect(
        budget,
        (path.resolve(strict=True) for path in executables),
        limit=budget.limits.counts["snapshot_files"],
        label="runtime dependency inputs",
        unique=True,
    )
    for executable in sorted(selected_executables):
        completed = run_bounded_process(
            ["/usr/bin/ldd", str(executable)],
            budget,
            environment=_clean_environment(),
        )
        if completed.returncode != 0:
            raise ProbeSandboxError(
                f"cannot identify runtime libraries for {executable}"
            )
        output = strict_utf8(
            completed.stdout + completed.stderr,
            f"ldd output for {executable}",
        )
        for line in output.splitlines():
            match = re.search(r"=>\s+(/[^\s]+)\s+\(", line)
            if match is None:
                match = re.match(r"\s*(/[^\s]+)\s+\(", line)
            if match is not None:
                target = Path(match.group(1)).as_posix()
                if (
                    target not in dependencies
                    and len(dependencies)
                    >= budget.limits.counts["snapshot_files"]
                ):
                    raise ProbeBudgetError(
                        "runtime dependencies exceed aggregate count bound"
                    )
                dependencies[target] = Path(target).resolve(strict=True)
    return [
        (source, target)
        for target, source in sorted(dependencies.items())
    ]


def runtime_dependency_mounts(
    executables: Iterable[Path],
    budget: ProbeBudget,
) -> tuple[list["Mount"], list[dict[str, str]]]:
    """Resolve exact ELF dependency paths and identities for trusted modules."""
    mounts = []
    authority = []
    for source, target in _runtime_dependencies(executables, budget):
        mounts.append(Mount(source, target, noexec=False))
        authority.append(
            {
                "path": target,
                "resolved_path": str(source),
                "sha256": sha256_file(source),
            }
        )
    return mounts, authority


def compile_interceptor(output: Path, budget: ProbeBudget) -> dict[str, str]:
    completed = run_bounded_process(
        [
            str(CC),
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            '-DVO_SOCKET_PATH="/control/dispatch.sock"',
            str(INTERCEPTOR_SOURCE),
            "-o",
            str(output),
        ],
        budget,
        environment=_clean_environment(),
    )
    if completed.returncode != 0:
        raise ProbeSandboxError(
            "cannot compile trusted shell interceptor: "
            + strict_utf8(completed.stderr, "interceptor compiler stderr")
        )
    output.chmod(0o500)
    return {
        "compiler": str(CC),
        "compiler_sha256": sha256_file(CC.resolve(strict=True)),
        "interceptor_sha256": sha256_file(output),
        "source_sha256": sha256_file(INTERCEPTOR_SOURCE),
    }


@dataclass(frozen=True)
class SnapshotEntry:
    path: str
    kind: str
    mode: int
    size: int
    mtime_ns: int
    uid: int
    gid: int
    data: bytes


class ExecutionSnapshot:
    """Exact regular-file snapshot; symlinks and special files reject."""

    def __init__(self, entries: Iterable[SnapshotEntry]):
        selected = sorted(entries, key=lambda entry: entry.path)
        if not selected or len({entry.path for entry in selected}) != len(selected):
            raise ProbeSandboxError("execution snapshot paths are empty or duplicate")
        self.entries = tuple(selected)
        digest = hashlib.sha256(
            b"validation-ownership-execution-snapshot-v2\0"
            b"stat-unsupported-fields-zero-v1\0"
        )
        for entry in self.entries:
            encoded = entry.path.encode("utf-8")
            digest.update(struct.pack("<I", len(encoded)))
            digest.update(encoded)
            digest.update(entry.kind.encode("ascii") + b"\0")
            digest.update(struct.pack("<I", entry.mode))
            digest.update(struct.pack("<QqII", entry.size, entry.mtime_ns, entry.uid, entry.gid))
            digest.update(entry.data)
        self.digest = digest.hexdigest()
        self._by_path = {entry.path: entry for entry in self.entries}

    @classmethod
    def capture(
        cls,
        root: Path,
        budget: ProbeBudget,
        paths: Iterable[str] | None = None,
    ) -> "ExecutionSnapshot":
        if root.is_symlink():
            raise ProbeSandboxError("execution snapshot root is a symlink")
        root = root.resolve(strict=True)
        root_metadata = os.lstat(root)
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise ProbeSandboxError("execution snapshot root is not a directory")
        directory_paths: set[str] = set()
        if paths is None:
            candidates = []
            pending_directories = [root]
            while pending_directories:
                directory = pending_directories.pop()
                with os.scandir(directory) as iterator:
                    for item in iterator:
                        budget.remaining("snapshot enumeration")
                        budget.charge_count("snapshot_ops")
                        if (
                            len(candidates) + len(directory_paths)
                            >= budget.limits.counts["snapshot_files"]
                        ):
                            raise ProbeBudgetError(
                                "snapshot tree exceeds aggregate file bound"
                            )
                        candidate = Path(item.path)
                        relative = candidate.relative_to(root).as_posix()
                        if item.is_symlink():
                            raise ProbeSandboxError(
                                f"snapshot path {relative!r} uses a symlink"
                            )
                        if item.is_dir(follow_symlinks=False):
                            directory_paths.add(relative)
                            pending_directories.append(candidate)
                        else:
                            candidates.append(relative)
            candidates.sort()
        else:
            selected_candidates = sorted(
                bounded_collect(
                    budget,
                    paths,
                    limit=budget.limits.counts["snapshot_files"],
                    label="snapshot input paths",
                    unique=True,
                )
            )
            candidates = []
            for relative in selected_candidates:
                for parent in Path(relative).parents:
                    if parent.as_posix() == ".":
                        continue
                    if (
                        parent.as_posix() not in directory_paths
                        and len(directory_paths) + len(selected_candidates)
                        >= budget.limits.counts["snapshot_files"]
                    ):
                        raise ProbeBudgetError(
                            "snapshot parent paths exceed aggregate file bound"
                        )
                    directory_paths.add(parent.as_posix())
            for relative in selected_candidates:
                candidate = root / relative
                metadata = os.lstat(candidate)
                if stat.S_ISLNK(metadata.st_mode):
                    raise ProbeSandboxError(
                        f"snapshot path {relative!r} uses a symlink"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    directory_paths.add(relative)
                else:
                    candidates.append(relative)
        budget.charge_count("snapshot_files")
        entries = [
            SnapshotEntry(
                ".",
                "directory",
                stat.S_IMODE(root_metadata.st_mode),
                root_metadata.st_size,
                root_metadata.st_mtime_ns,
                root_metadata.st_uid,
                root_metadata.st_gid,
                b"",
            )
        ]
        for relative in sorted(directory_paths, key=lambda item: (item.count("/"), item)):
            budget.remaining("snapshot directory")
            budget.charge_count("snapshot_files")
            budget.charge_count("snapshot_ops")
            path = root / relative
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                raise ProbeSandboxError(
                    f"snapshot directory {relative!r} is a symlink"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise ProbeSandboxError(
                    f"snapshot directory {relative!r} is not a stable directory"
                )
            entries.append(
                SnapshotEntry(
                    relative,
                    "directory",
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_uid,
                    metadata.st_gid,
                    b"",
                )
            )
        for relative in candidates:
            budget.remaining("snapshot file")
            budget.charge_count("snapshot_files")
            path = Path(relative)
            if (
                path.is_absolute()
                or path.as_posix() != relative
                or ".." in path.parts
            ):
                raise ProbeSandboxError(
                    f"snapshot path {relative!r} is not canonical"
                )
            source = root / path
            current = root
            for index, part in enumerate(path.parts):
                current /= part
                budget.charge_count("snapshot_ops")
                metadata = os.lstat(current)
                if stat.S_ISLNK(metadata.st_mode):
                    raise ProbeSandboxError(
                        f"snapshot path {relative!r} uses a symlink"
                    )
                if index + 1 < len(path.parts):
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise ProbeSandboxError(
                            f"snapshot parent for {relative!r} is not a directory"
                        )
                elif not stat.S_ISREG(metadata.st_mode):
                    raise ProbeSandboxError(
                        f"snapshot path {relative!r} is not a regular file"
                    )
            before = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
            budget.charge_bytes("snapshot", metadata.st_size)
            descriptor = os.open(
                source,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                budget.charge_count("snapshot_ops")
                opened = os.fstat(descriptor)
                opened_identity = (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                    opened.st_ctime_ns,
                )
                if opened_identity != before or not stat.S_ISREG(opened.st_mode):
                    raise ProbeSandboxError(
                        f"snapshot path {relative!r} changed before being read"
                    )
                chunks = []
                while True:
                    budget.remaining("snapshot read")
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    budget.charge_count("snapshot_ops")
                    chunks.append(chunk)
                data = b"".join(chunks)
                budget.charge_count("snapshot_ops")
                after_read = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            after = os.lstat(source)
            budget.charge_count("snapshot_ops")
            if before != (
                after_read.st_dev,
                after_read.st_ino,
                after_read.st_size,
                after_read.st_mtime_ns,
                after_read.st_ctime_ns,
            ) or before != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ProbeSandboxError(
                    f"snapshot path {relative!r} changed while being read"
                )
            entries.append(
                SnapshotEntry(
                    relative,
                    "file",
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_uid,
                    metadata.st_gid,
                    data,
                )
            )
        return cls(entries)

    def paths(self) -> set[str]:
        return set(self._by_path)

    def entry(self, path: str) -> SnapshotEntry:
        try:
            return self._by_path[path]
        except KeyError as error:
            raise ProbeSandboxError(
                f"snapshot does not admit required path {path!r}"
            ) from error

    def materialize(
        self,
        destination: Path,
        budget: ProbeBudget,
        *,
        omit: set[str] | None = None,
    ) -> None:
        omitted = set() if omit is None else omit
        budget.charge_count("snapshot_ops")
        destination.mkdir(parents=True, exist_ok=False)
        directories = [
            entry for entry in self.entries if entry.kind == "directory"
        ]
        files = [entry for entry in self.entries if entry.kind == "file"]
        budget.charge_bytes(
            "snapshot",
            sum(
                entry.size
                for entry in files
                if entry.path not in omitted
            ),
        )
        for entry in directories:
            budget.remaining("snapshot materialization")
            if entry.path in omitted:
                continue
            target = destination / entry.path
            budget.charge_count("snapshot_ops")
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(entry.mode)
        for entry in files:
            if entry.path in omitted:
                continue
            budget.remaining("snapshot materialization")
            target = destination / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            budget.charge_count("snapshot_ops", 2)
            with target.open("wb") as stream:
                for index in range(0, len(entry.data), 1024 * 1024):
                    budget.remaining("snapshot write")
                    chunk = entry.data[index:index + 1024 * 1024]
                    budget.charge_count("snapshot_ops")
                    stream.write(chunk)
            target.chmod(entry.mode)
            os.utime(target, ns=(entry.mtime_ns, entry.mtime_ns))
            metadata = os.lstat(target)
            if (
                stat.S_IMODE(metadata.st_mode) != entry.mode
                or metadata.st_size != entry.size
                or metadata.st_mtime_ns != entry.mtime_ns
                or metadata.st_uid != entry.uid
                or metadata.st_gid != entry.gid
            ):
                raise ProbeSandboxError(
                    f"materialized snapshot metadata differs for {entry.path!r}"
                )
        for entry in reversed(directories):
            target = destination / entry.path
            if target.exists():
                os.utime(target, ns=(entry.mtime_ns, entry.mtime_ns))


CommandHandler = Callable[[str, ProbeBudget], bytes]


@dataclass(frozen=True)
class RegisteredCommand:
    id: str
    pattern: str
    handler: CommandHandler
    programs: tuple[str, ...] = ()
    authority: tuple[tuple[str, str], ...] = ()

    @classmethod
    def fixed(
        cls,
        identifier: str,
        command: str,
        output: bytes,
    ) -> "RegisteredCommand":
        arguments = shlex.split(command)
        if not arguments:
            raise ProbeSandboxError("registered fixed command is empty")
        program = arguments[0]
        if (
            Path(program).name != program
            or re.fullmatch(r"[A-Za-z0-9_.+-]+", program) is None
        ):
            raise ProbeSandboxError(
                "registered fixed command program must be a safe basename"
            )
        return cls(
            identifier,
            re.escape(command),
            lambda _command, _budget: bytes(output),
            (program,),
        )

    @classmethod
    def native(
        cls,
        identifier: str,
        command: str,
        executable: Path,
        *,
        scratch_root: Path,
        read_only: Iterable["Mount"] = (),
    ) -> "RegisteredCommand":
        arguments = shlex.split(command)
        if not arguments:
            raise ProbeSandboxError("registered native command is empty")
        program = arguments[0]
        if (
            Path(program).name != program
            or re.fullmatch(r"[A-Za-z0-9_.+-]+", program) is None
        ):
            raise ProbeSandboxError(
                "registered native command program must be a safe basename"
            )
        executable = executable.resolve(strict=True)
        metadata = os.lstat(executable)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or not metadata.st_mode & 0o111
        ):
            raise ProbeSandboxError(
                "registered native command is not a regular executable"
            )
        executable_sha256 = sha256_file(executable)
        selected_mounts = tuple(read_only)
        selected_scratch = scratch_root.resolve(strict=True)

        def handler(actual: str, budget: ProbeBudget) -> bytes:
            if actual != command or sha256_file(executable) != executable_sha256:
                raise ProbeSandboxError(
                    "registered native executable authority changed"
                )
            runner = SandboxRunner(selected_scratch, budget)
            completed, _ = runner.run(
                executable,
                [program, *arguments[1:]],
                read_only=selected_mounts,
            )
            if completed.returncode != 0 or completed.stderr:
                raise ProbeSandboxError(
                    f"registered native command {identifier!r} failed safely"
                )
            return completed.stdout

        return cls(
            identifier,
            re.escape(command),
            handler,
            (program,),
            (("executable_sha256", executable_sha256),),
        )


class _DispatchServer:
    def __init__(
        self,
        path: Path,
        commands: Iterable[RegisteredCommand],
        budget: ProbeBudget,
        cache: ProbeCache,
        cache_namespace: tuple[object, ...],
    ):
        self.path = path
        self.commands = tuple(commands)
        if len({command.id for command in self.commands}) != len(self.commands):
            raise ProbeSandboxError("registered command IDs are not unique")
        for command in self.commands:
            try:
                re.compile(command.pattern)
            except re.error as error:
                raise ProbeSandboxError(
                    f"registered command {command.id!r} has invalid regex"
                ) from error
            for program in command.programs:
                if (
                    Path(program).name != program
                    or re.fullmatch(r"[A-Za-z0-9_.+-]+", program) is None
                ):
                    raise ProbeSandboxError(
                        f"registered command {command.id!r} has invalid program"
                    )
        self.budget = budget
        self.cache = cache
        self.cache_namespace = cache_namespace
        self.stop_event = threading.Event()
        self.root_pid: int | None = None
        self.root_pid_condition = threading.Condition()
        self.error: BaseException | None = None
        self.events: list[dict[str, object]] = []
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.listener.bind(str(path))
        except BaseException:
            self.listener.close()
            raise
        os.chmod(path, 0o600)
        self.listener.listen(8)
        self.listener.settimeout(0.05)
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_DispatchServer":
        self.thread.start()
        return self

    def set_root_pid(self, pid: int) -> None:
        if pid <= 1:
            raise ProbeSandboxError("sandbox root PID is invalid")
        with self.root_pid_condition:
            if self.root_pid is not None:
                raise ProbeSandboxError("sandbox root PID was already bound")
            self.root_pid = pid
            self.root_pid_condition.notify_all()

    def _peer_is_descendant(self, connection: socket.socket) -> bool:
        credentials = connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        peer_pid, peer_uid, _ = struct.unpack("3i", credentials)
        if peer_uid != os.getuid():
            return False
        with self.root_pid_condition:
            while self.root_pid is None:
                self.root_pid_condition.wait(
                    timeout=min(0.05, self.budget.remaining("peer binding"))
                )
            root_pid = self.root_pid
        current = peer_pid
        for _ in range(64):
            if current == root_pid:
                return True
            if current <= 1:
                return False
            try:
                stat_text = Path(f"/proc/{current}/stat").read_text(
                    encoding="ascii"
                )
            except OSError:
                return False
            close = stat_text.rfind(")")
            fields = stat_text[close + 2 :].split()
            if close < 0 or len(fields) < 2:
                return False
            try:
                current = int(fields[1])
            except ValueError:
                return False
        return False

    @staticmethod
    def _read_exact(connection: socket.socket, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining:
            chunk = connection.recv(remaining)
            if not chunk:
                raise ProbeSandboxError("interceptor request is truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _request(self, connection: socket.socket) -> tuple[list[bytes], int]:
        magic = self._read_exact(connection, len(REQUEST_MAGIC))
        if magic != REQUEST_MAGIC:
            raise ProbeSandboxError("interceptor request magic is invalid")
        argc = struct.unpack("<I", self._read_exact(connection, 4))[0]
        if not 0 < argc <= MAX_ARGUMENTS:
            raise ProbeSandboxError("interceptor argument count is invalid")
        arguments = []
        byte_count = len(REQUEST_MAGIC) + 4
        for _ in range(argc):
            size = struct.unpack("<I", self._read_exact(connection, 4))[0]
            if size > MAX_ARGUMENT_BYTES:
                raise ProbeSandboxError("interceptor argument exceeds byte bound")
            value = self._read_exact(connection, size)
            arguments.append(value)
            byte_count += 4 + size
        self.budget.charge_count("events")
        self.budget.charge_bytes("events", byte_count)
        return arguments, byte_count

    def _dispatch(self, arguments: list[bytes]) -> tuple[int, bytes]:
        decoded = [
            strict_utf8(argument, "shell interceptor argument")
            for argument in arguments
        ]
        if (
            len(decoded) == 3
            and decoded[0] in {"/bin/sh", "/bin/bash", "/bin/vo-shell"}
            and decoded[1] == "-c"
        ):
            command_text = decoded[2]
        else:
            programs = {
                program
                for command in self.commands
                for program in command.programs
            }
            if not decoded or Path(decoded[0]).name not in programs:
                raise ProbeSandboxError(
                    "GNU Make changed the trusted SHELL/.SHELLFLAGS invocation"
                )
            decoded[0] = Path(decoded[0]).name
            command_text = shlex.join(decoded)
        matches = [
            command
            for command in self.commands
            if re.fullmatch(command.pattern, command_text, re.DOTALL)
        ]
        if len(matches) != 1:
            raise ProbeSandboxError(
                "GNU Make attempted command execution without exactly one "
                f"registered dispatch: {command_text!r}"
            )
        selected = matches[0]
        key = (*self.cache_namespace, selected.id, command_text)
        output = self.cache.get(key)
        if output is None:
            with self.budget.lease("pending"):
                output = selected.handler(command_text, self.budget)
            if not isinstance(output, bytes):
                raise ProbeSandboxError(
                    f"registered command {selected.id!r} did not return raw bytes"
                )
            self.cache.put(key, output)
        if len(output) > MAX_SOCKET_OUTPUT:
            raise ProbeSandboxError(
                f"registered command {selected.id!r} output is too large"
            )
        mapping_bytes = len(command_text.encode("utf-8")) + len(output)
        self.budget.charge_count("mappings")
        self.budget.charge_bytes("mappings", mapping_bytes)
        self.events.append(
            {
                "argv": decoded,
                "authority": dict(selected.authority),
                "command": command_text,
                "id": selected.id,
                "output_sha256": sha256_bytes(output),
            }
        )
        return 0, output

    def _serve_connection(self, connection: socket.socket) -> None:
        status = 125
        output = b""
        try:
            if not self._peer_is_descendant(connection):
                raise ProbeSandboxError(
                    "shell interceptor peer is outside the sandbox process tree"
                )
            arguments, _ = self._request(connection)
            status, output = self._dispatch(arguments)
        except BaseException as error:
            if self.error is None:
                self.error = error
        response = (
            RESPONSE_MAGIC
            + struct.pack("<iQ", status, len(output))
            + output
        )
        try:
            connection.sendall(response)
        except OSError as error:
            if self.error is None:
                self.error = ProbeSandboxError(
                    f"cannot answer shell interceptor: {error}"
                )

    def _serve(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    connection, _ = self.listener.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(
                        min(1.0, self.budget.remaining("interceptor"))
                    )
                    self._serve_connection(connection)
        except BaseException as error:
            if self.error is None and not self.stop_event.is_set():
                self.error = error

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop_event.set()
        self.listener.close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise ProbeSandboxError("shell interceptor server did not stop")
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        if self.error is not None and exc is None:
            if isinstance(self.error, ProbeSandboxError):
                raise self.error
            if isinstance(self.error, ProbeBudgetError):
                raise self.error
            raise ProbeSandboxError(
                f"shell interceptor server failed: {self.error}"
            ) from self.error


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    noexec: bool = True


class SandboxRunner:
    """Create one fresh root with no inherited supervisor descriptors."""

    def __init__(self, scratch_root: Path, budget: ProbeBudget):
        self.scratch_root = scratch_root.resolve(strict=True)
        self.budget = budget
        self.launcher_prefix, self.launcher_authority = _launcher_prefix(budget)
        self._dependency_cache: dict[
            tuple[Path, ...],
            list[tuple[Path, str]],
        ] = {}

    def _socket_path(self) -> Path:
        root = self.scratch_root
        while len(os.fsencode(root)) + 12 >= 100:
            if root.parent == root:
                raise ProbeSandboxError(
                    "cannot derive a bounded supervisor socket path"
                )
            root = root.parent
        return root / f".v{secrets.token_hex(4)}"

    def _dependencies(self, executables: Iterable[Path]) -> list[tuple[Path, str]]:
        key = tuple(sorted(path.resolve(strict=True) for path in executables))
        if key not in self._dependency_cache:
            self._dependency_cache[key] = _runtime_dependencies(
                key,
                self.budget,
            )
        return self._dependency_cache[key]

    @staticmethod
    def _mount_target(root: Path, mount: Mount) -> None:
        target = root / mount.target.lstrip("/")
        if mount.source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif mount.source.is_file() or stat.S_ISSOCK(
            os.lstat(mount.source).st_mode
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        else:
            raise ProbeSandboxError(
                f"sandbox mount source {mount.source} is not regular"
            )

    def run(
        self,
        executable: Path,
        argv: list[str],
        *,
        read_only: Iterable[Mount] = (),
        writable: Iterable[Mount] = (),
        environment: dict[str, str] | None = None,
        dispatcher: Iterable[RegisteredCommand] | None = None,
        interceptor: Path | None = None,
        cache: ProbeCache | None = None,
        cache_namespace: tuple[object, ...] = (),
        cwd: str = "/repo",
        bootstrap_config: Path | None = None,
        syscall_policy: SyscallPolicy | None = None,
    ) -> tuple[subprocess.CompletedProcess[bytes], list[dict[str, object]]]:
        executable = executable.resolve(strict=True)
        selected_read_only = bounded_collect(
            self.budget,
            read_only,
            limit=self.budget.limits.counts["snapshot_files"],
            label="sandbox read-only mounts",
        )
        selected_writable = bounded_collect(
            self.budget,
            writable,
            limit=self.budget.limits.counts["snapshot_files"],
            label="sandbox writable mounts",
        )
        dispatcher = (
            None
            if dispatcher is None
            else tuple(
                bounded_collect(
                    self.budget,
                    dispatcher,
                    limit=self.budget.limits.counts["mappings"],
                    label="sandbox dispatch registry",
                )
            )
        )
        with tempfile.TemporaryDirectory(
            prefix="probe-sandbox-",
            dir=self.scratch_root,
        ) as temporary:
            with ExitStack() as stack:
                base = Path(temporary)
                root = base / "root"
                for directory in (
                    "bin",
                    "control",
                    "dev",
                    "probe",
                    "repo",
                    "trusted",
                    "usr/bin",
                    "work",
                ):
                    (root / directory).mkdir(parents=True, exist_ok=True)
                (root / "dev/null").touch()
                executable_mount = Mount(
                    executable,
                    "/trusted/exec",
                    noexec=False,
                )
                runtime_executables = [executable]
                server = None
                syscall_supervisor = None
                syscall_child = None
                if dispatcher is not None:
                    if interceptor is None:
                        raise ProbeSandboxError(
                            "command dispatch requires a trusted interceptor"
                        )
                    interceptor = interceptor.resolve(strict=True)
                    runtime_executables.append(interceptor)
                    for target in ("/bin/sh", "/bin/bash", "/bin/vo-shell"):
                        selected_read_only.append(
                            Mount(interceptor, target, noexec=False)
                        )
                    programs = {
                        program
                        for command in dispatcher
                        for program in command.programs
                    }
                    for program in sorted(programs):
                        selected_read_only.append(
                            Mount(
                                interceptor,
                                f"/usr/bin/{program}",
                                noexec=False,
                            )
                        )
                    dispatch_cache = (
                        ProbeCache(self.budget) if cache is None else cache
                    )
                    socket_path = self._socket_path()
                    server = stack.enter_context(
                        _DispatchServer(
                            socket_path,
                            dispatcher,
                            self.budget,
                            dispatch_cache,
                            cache_namespace,
                        )
                    )
                    selected_read_only.append(
                        Mount(
                            socket_path,
                            "/control/dispatch.sock",
                            noexec=True,
                        )
                    )
                if syscall_policy is not None:
                    syscall_parent, syscall_child = socket.socketpair(
                        socket.AF_UNIX,
                        socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC,
                    )
                    syscall_child.set_inheritable(True)
                    stack.callback(syscall_child.close)
                    syscall_supervisor = stack.enter_context(
                        _SyscallSupervisor(
                            syscall_parent,
                            syscall_policy,
                            self.budget,
                        )
                    )
                for dependency, target in self._dependencies(runtime_executables):
                    selected_read_only.append(
                        Mount(dependency, target, noexec=False)
                    )
                selected_read_only.append(executable_mount)
                for mount in [*selected_read_only, *selected_writable]:
                    self._mount_target(root, mount)

                launcher = base / "sandbox_exec.py"
                shutil.copy2(SANDBOX_EXEC, launcher)
                launcher.chmod(0o500)
                config = {
                    "argv": argv,
                    "bootstrap_config": (
                        None
                        if bootstrap_config is None
                        else str(bootstrap_config.resolve(strict=True))
                    ),
                    "cwd": cwd,
                    "environment": _clean_environment(environment),
                    "executable": executable_mount.target,
                    "read_only": [
                        {
                            "noexec": mount.noexec,
                            "source": str(mount.source.resolve(strict=True)),
                            "target": mount.target,
                        }
                        for mount in selected_read_only
                    ],
                    "root": str(root),
                    "runner_gid": self.launcher_authority["runner_gid"],
                    "runner_uid": self.launcher_authority["runner_uid"],
                    "sudo_drop": self.launcher_authority["sudo_drop"],
                    "supervisor_fd": (
                        None
                        if syscall_child is None
                        else syscall_child.fileno()
                    ),
                    "writable": [
                        {
                            "source": str(mount.source.resolve(strict=True)),
                            "target": mount.target,
                        }
                        for mount in selected_writable
                    ],
                }
                config_path = base / "sandbox.json"
                config_path.write_text(
                    json.dumps(
                        config,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                command = [
                    *self.launcher_prefix,
                    str(PYTHON),
                    "-I",
                    str(launcher),
                    str(config_path),
                ]
                completed = run_bounded_process(
                    command,
                    self.budget,
                    environment=_clean_environment(),
                    on_start=lambda pid: (
                        server.set_root_pid(pid) if server is not None else None,
                        syscall_child.close()
                        if syscall_child is not None
                        else None,
                    ),
                    pass_fds=(
                        ()
                        if syscall_child is None
                        else (syscall_child.fileno(),)
                    ),
                )
                if syscall_supervisor is not None:
                    syscall_supervisor.finish()
                    completed.syscall_events = syscall_supervisor.events
                else:
                    completed.syscall_events = []
                return completed, [] if server is None else server.events
