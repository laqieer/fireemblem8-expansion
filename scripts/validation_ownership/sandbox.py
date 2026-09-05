"""Supervisor-owned Linux sandbox and authenticated command dispatch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
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
    for executable in sorted({path.resolve(strict=True) for path in executables}):
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
        digest = hashlib.sha256(b"validation-ownership-execution-snapshot-v1\0")
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
            for candidate in sorted(root.rglob("*")):
                budget.remaining("snapshot enumeration")
                budget.charge_count("snapshot_ops")
                metadata = os.lstat(candidate)
                if stat.S_ISDIR(metadata.st_mode):
                    directory_paths.add(candidate.relative_to(root).as_posix())
                    continue
                candidates.append(candidate.relative_to(root).as_posix())
        else:
            selected_candidates = sorted(set(paths))
            candidates = []
            directory_paths = {
                parent.as_posix()
                for relative in selected_candidates
                for parent in Path(relative).parents
                if parent.as_posix() != "."
            }
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
    ) -> tuple[subprocess.CompletedProcess[bytes], list[dict[str, object]]]:
        executable = executable.resolve(strict=True)
        selected_read_only = list(read_only)
        selected_writable = list(writable)
        dispatcher = None if dispatcher is None else tuple(dispatcher)
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
                    on_start=(
                        None if server is None else server.set_root_pid
                    ),
                )
                return completed, [] if server is None else server.events
