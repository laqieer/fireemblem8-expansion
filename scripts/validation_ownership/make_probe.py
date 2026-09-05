#!/usr/bin/env python3
"""Authoritative, nonexecuting GNU Make authority probe."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import selectors
import signal
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Iterable


MAKE = Path("/usr/bin/make")
UNSHARE = Path("/usr/bin/unshare")
SUDO = Path("/usr/bin/sudo")
PYTHON = Path("/usr/bin/python3")
CC = Path("/usr/bin/cc")
LIBC = Path("/lib/x86_64-linux-gnu/libc.so.6")
LOADER = Path("/lib64/ld-linux-x86-64.so.2")
PYTHON_STDLIB = Path(
    f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}"
)
PLATFORM_LIB = Path("/usr/lib/x86_64-linux-gnu")
LOADER_LIB = Path("/usr/lib64")
SANDBOX_EXEC = Path("scripts/validation_ownership/sandbox_exec.py")
INTERCEPTOR_SOURCE = Path(
    "scripts/validation_ownership/shell_interceptor.c"
)
GENERATED_REGISTRY_PROBE = Path(
    "scripts/validation_ownership/generated_registry_probe.py"
)
MAX_SANDBOX_RUNS = 4096
MAX_DYNAMIC_PASSES = 64
MAX_VARIANT_STATES = 4096
MAX_CONTEXT_STATES = 512
MAX_CONTEXT_DEPTH = 8
MAX_DISCOVERED_SOURCES = 4096
MAX_DISCOVERED_DOMAINS = 512
MAX_PROBE_SECONDS = 3600
MAX_SANDBOX_OUTPUT_BYTES = 1024 * 1024
MAX_MAKE_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_EVENT_RECORDS = 4096
MAX_EVENT_ARGUMENTS = 4096
MAX_DOMAIN_BYTES = 1024 * 1024
MAX_DOMAIN_RECORDS = 1
MAX_PENDING_COMMANDS = MAX_SANDBOX_RUNS
MAX_MAPPING_COUNT = MAX_SANDBOX_RUNS
MAX_MAPPING_BYTES = 16 * 1024 * 1024
MAX_REGISTRY_READ_EVENTS = 16384
MAX_REGISTRY_WATCHES = 8192
REGISTERED_COMMAND_CACHE_LIMIT = 8192
MAX_PARALLEL_REGISTERED_COMMANDS = 32
IN_ACCESS = 0x00000001
IN_OPEN = 0x00000020
IN_Q_OVERFLOW = 0x00004000
IN_ISDIR = 0x40000000
TRACE_RE = re.compile(
    r"^(?P<source>.+?):[0-9]+: "
    r"(?:(?:(?:update )?target) '(?P<target>[^']+)'"
    r"(?: due to: (?P<due>.*))?"
    r"|target '(?P<missing_target>[^']+)' does not exist)$"
)
CONSIDER_RE = re.compile(
    r"^(?P<indent> *)Considering target file '(?P<target>[^']+)'\.$"
)
READING_RE = re.compile(
    r"^Reading makefile '(?P<path>[^']+)'(?P<details>.*)$"
)
EXTERNAL_DEFAULT_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<modifiers>(?:(?:export|private|override)\s+)*)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\?="
)
UNDEFINED_VARIABLE_RE = re.compile(
    r"(?:^|\n)(?:[^\n:]+:[0-9]+:\s+)?"
    r"warning: undefined variable '(?P<name>[^']+)'"
)
DIRECT_VARIABLE_RE = re.compile(
    r"(?<!\$)\$(?:\((?P<paren>[A-Za-z_][A-Za-z0-9_]*)(?=[:)])"
    r"|\{(?P<brace>[A-Za-z_][A-Za-z0-9_]*)(?=[:}])"
    r"|(?P<short>[A-Za-z]))"
)
SCOPED_VARIABLE_RE = re.compile(
    r"(?<!\$)\$(?:"
    r"\((?P<paren>[@%*+<?^|](?:D|F)?|[0-9])\)"
    r"|\{(?P<brace>[@%*+<?^|](?:D|F)?|[0-9])\}"
    r"|(?P<short>[@%*+<?^|0-9]))"
)
SECOND_EXPANSION_VARIABLE_RE = re.compile(
    r"\$\$(?:\((?P<paren>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\{(?P<brace>[A-Za-z_][A-Za-z0-9_]*)\})"
)
INTROSPECTION_VARIABLE_RE = re.compile(
    r"\$\((?:flavor|origin|value)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\)"
)
CONDITIONAL_VARIABLE_RE = re.compile(
    r"^\s*(?:ifdef|ifndef)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
VARIABLE_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:(?:export|override|private)\s+)*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:\?=|:=|::=|\+=|!=|=)(?P<value>.*)$"
)
TARGET_VARIABLE_ASSIGNMENT_RE = re.compile(
    r"^(?P<target>.*?)\s*:\s*"
    r"(?:(?:export|override|private)\s+)*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:\?=|:=|::=|\+=|!=|=)(?P<value>.*)$"
)
DEFINE_RE = re.compile(
    r"^\s*(?:(?:export|override|private)\s+)*"
    r"define\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)


class MakeProbeError(RuntimeError):
    """Raised when GNU Make authority cannot be observed safely and exactly."""


def _strict_utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MakeProbeError(f"{label} is not valid UTF-8") from error


_REGISTERED_COMMAND_CACHE: dict[tuple[Any, ...], bytes] = {}
_REGISTERED_COMMAND_CACHE_LOCK = Lock()
_SCANINC_COMPILE_LOCK = Lock()
_COMMAND_ROOT_LOCK = Lock()


def _prepare_confined_scratch(loader: Any, scratch_root: Path) -> Path:
    if not hasattr(os, "O_NOFOLLOW"):
        raise MakeProbeError("probe scratch requires O_NOFOLLOW")
    configured_scratch = getattr(loader, "scratch_root", None)
    if configured_scratch is not None:
        configured = Path(os.path.abspath(configured_scratch))
        scratch = Path(os.path.abspath(scratch_root))
        if scratch != configured:
            raise MakeProbeError("probe scratch differs from trusted external root")
        try:
            entry_stat = os.lstat(configured)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(configured, flags)
        except OSError as error:
            raise MakeProbeError(
                f"cannot open trusted external probe scratch: {error}"
            ) from error
        try:
            opened_stat = os.fstat(descriptor)
            if (
                stat.S_ISLNK(entry_stat.st_mode)
                or not stat.S_ISDIR(entry_stat.st_mode)
                or opened_stat.st_dev != entry_stat.st_dev
                or opened_stat.st_ino != entry_stat.st_ino
            ):
                raise MakeProbeError(
                    "trusted external probe scratch is not a stable directory"
                )
        finally:
            os.close(descriptor)
        return configured

    root = Path(os.path.abspath(loader.root))
    scratch = Path(os.path.abspath(scratch_root))
    try:
        relative = scratch.relative_to(root)
        root_lstat = os.lstat(root)
        resolved_root = root.resolve(strict=True)
    except (OSError, ValueError) as error:
        raise MakeProbeError(f"cannot inspect probe scratch root: {error}") from error
    if (
        not relative.parts
        or stat.S_ISLNK(root_lstat.st_mode)
        or not stat.S_ISDIR(root_lstat.st_mode)
    ):
        raise MakeProbeError(
            "probe scratch must be below a non-symlink authority root"
        )
    component_path = Path()
    for part in relative.parts:
        component_path /= part
        if component_path.as_posix() in loader.entries:
            raise MakeProbeError(
                f"probe scratch component {component_path} is a tracked Git object"
            )

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    created: list[Path] = []
    try:
        current_fd = os.open(root, flags)
    except OSError as error:
        raise MakeProbeError(f"cannot open probe authority root safely: {error}") from error
    current_path = root
    success = False
    try:
        for part in relative.parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=current_fd)
                created.append(current_path / part)
            except FileExistsError:
                pass
            try:
                entry_stat = os.stat(
                    part,
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise MakeProbeError(
                    f"cannot lstat probe scratch component {current_path / part}: "
                    f"{error}"
                ) from error
            if (
                stat.S_ISLNK(entry_stat.st_mode)
                or not stat.S_ISDIR(entry_stat.st_mode)
            ):
                raise MakeProbeError(
                    f"probe scratch component {current_path / part} must be "
                    "a non-symlink directory"
                )
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except OSError as error:
                raise MakeProbeError(
                    f"cannot open probe scratch component {current_path / part} "
                    f"safely: {error}"
                ) from error
            opened_stat = os.fstat(next_fd)
            if (
                opened_stat.st_dev != entry_stat.st_dev
                or opened_stat.st_ino != entry_stat.st_ino
            ):
                os.close(next_fd)
                raise MakeProbeError(
                    f"probe scratch component {current_path / part} was replaced"
                )
            os.close(current_fd)
            current_fd = next_fd
            current_path /= part
        resolved_scratch = current_path.resolve(strict=True)
        if resolved_root not in resolved_scratch.parents:
            raise MakeProbeError("probe scratch escaped the authority root")
        success = True
        return current_path
    finally:
        os.close(current_fd)
        if not success:
            for path in reversed(created):
                try:
                    path.rmdir()
                except OSError:
                    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identities(path: Path) -> tuple[str, str]:
    def signature(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    sha1 = hashlib.sha1(usedforsecurity=False)
    sha256 = hashlib.sha256()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = None
    try:
        before = os.lstat(path)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or signature(before) != signature(opened)
        ):
            raise MakeProbeError(
                f"copied snapshot path {path} is not a stable regular file"
            )
        sha1.update(f"blob {opened.st_size}\0".encode("ascii"))
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            sha1.update(chunk)
            sha256.update(chunk)
        after_read = os.fstat(descriptor)
    except OSError as error:
        raise MakeProbeError(
            f"cannot hash copied snapshot path {path} stably: {error}"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after_path = os.lstat(path)
    except OSError as error:
        raise MakeProbeError(
            f"copied snapshot path {path} changed while hashing: {error}"
        ) from error
    if not (
        signature(opened) == signature(after_read) == signature(after_path)
    ):
        raise MakeProbeError(
            f"copied snapshot path {path} changed while hashing"
        )
    return sha1.hexdigest(), sha256.hexdigest()


_NAMESPACE_LAUNCHER: dict[str, Any] | None = None


def _namespace_probe_command(*, sudo: bool) -> list[str]:
    command = [
        str(UNSHARE),
        "--mount",
        "--net",
        "--pid",
        "--fork",
        "--kill-child",
        "--propagation",
        "private",
        "/usr/bin/true",
    ]
    if sudo:
        return [str(SUDO), "-n", *command]
    return [
        str(UNSHARE),
        "--user",
        "--map-root-user",
        *command[1:],
    ]


def _tool_version(path: Path) -> str:
    completed = subprocess.run(
        [str(path), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    output = completed.stdout or completed.stderr
    if completed.returncode != 0 or not output.strip():
        raise MakeProbeError(f"cannot identify trusted tool {path}")
    return output.splitlines()[0]


def _select_namespace_launcher(*, refresh: bool = False) -> dict[str, Any]:
    global _NAMESPACE_LAUNCHER
    if _NAMESPACE_LAUNCHER is not None and not refresh:
        return _NAMESPACE_LAUNCHER
    probe_environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    user_probe = subprocess.run(
        _namespace_probe_command(sudo=False),
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=probe_environment,
    )
    if user_probe.returncode == 0:
        mode = "user-namespace"
        prefix = _namespace_probe_command(sudo=False)[:-1]
    else:
        if not SUDO.is_file():
            raise MakeProbeError(
                "unprivileged user namespaces are unavailable and "
                "/usr/bin/sudo is absent"
            )
        sudo_probe = subprocess.run(
            _namespace_probe_command(sudo=True),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=probe_environment,
        )
        if sudo_probe.returncode != 0:
            raise MakeProbeError(
                "neither unprivileged nor passwordless-sudo namespace "
                "confinement is available"
            )
        mode = "sudo-drop"
        prefix = _namespace_probe_command(sudo=True)[:-1]
    result = {
        "argv_prefix": prefix,
        "mode": mode,
        "runner_gid": os.getgid(),
        "runner_uid": os.getuid(),
        "unshare": str(UNSHARE),
        "unshare_sha256": _sha256_file(UNSHARE),
        "unshare_version": _tool_version(UNSHARE),
    }
    if mode == "sudo-drop":
        result.update(
            {
                "sudo": str(SUDO),
                "sudo_sha256": _sha256_file(SUDO),
                "sudo_version": _tool_version(SUDO),
            }
        )
    _NAMESPACE_LAUNCHER = result
    return result


def _ensure_tools() -> dict[str, Any]:
    required = (MAKE, UNSHARE, PYTHON, CC, LIBC, LOADER)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise MakeProbeError(
            "authoritative Make sandbox tools are unavailable: "
            + ", ".join(missing)
        )
    completed = subprocess.run(
        [str(MAKE), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    if completed.returncode != 0 or not completed.stdout.startswith("GNU Make "):
        raise MakeProbeError("trusted /usr/bin/make version probe failed")
    return {
        "make": str(MAKE),
        "make_sha256": _sha256_file(MAKE),
        "make_version": completed.stdout.splitlines()[0],
        "namespace_launcher": _select_namespace_launcher(),
    }


def _mkdir_target(root: Path, target: str, *, directory: bool = False) -> Path:
    path = root / target.lstrip("/")
    if directory:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return path


class _EventStreamParser:
    def __init__(self, expected_mapping_count: int):
        self.expected_mapping_count = expected_mapping_count
        self.buffer = bytearray()
        self.total_bytes = 0
        self.records: list[dict[str, Any]] = []

    def feed(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        if self.total_bytes > MAX_EVENT_BYTES:
            raise MakeProbeError("shell interceptor event stream exceeds byte bound")
        self.buffer.extend(chunk)
        self._parse()

    def _parse(self) -> None:
        offset = 0
        while len(self.buffer) - offset >= 20:
            start = offset
            match, mapping_count, low_hash, high_hash, argc = struct.unpack_from(
                "<IIIII",
                self.buffer,
                offset,
            )
            offset += 20
            if argc > MAX_EVENT_ARGUMENTS:
                raise MakeProbeError("shell interceptor emitted excessive arguments")
            arguments = []
            complete = True
            for _ in range(argc):
                if len(self.buffer) - offset < 4:
                    complete = False
                    break
                size = struct.unpack_from("<I", self.buffer, offset)[0]
                offset += 4
                if size > MAX_EVENT_BYTES:
                    raise MakeProbeError(
                        "shell interceptor argument exceeds byte bound"
                    )
                if len(self.buffer) - offset < size:
                    complete = False
                    break
                arguments.append(bytes(self.buffer[offset:offset + size]))
                offset += size
            if not complete:
                offset = start
                break
            if len(self.records) >= MAX_EVENT_RECORDS:
                raise MakeProbeError(
                    "shell interceptor event stream exceeds record bound"
                )
            if mapping_count != self.expected_mapping_count:
                raise MakeProbeError(
                    "shell interceptor mapping count differs from supervisor state"
                )
            if match not in {0, 0xFFFFFFFF}:
                raise MakeProbeError(
                    "shell interceptor emitted an invalid match identity"
                )
            event = {
                "arguments": [
                    _strict_utf8(argument, "shell interceptor argument")
                    for argument in arguments
                ],
                "command_hash": f"{low_hash | (high_hash << 32):016x}",
                "match": -1 if match == 0xFFFFFFFF else match,
                "mapping_count": mapping_count,
            }
            command = _event_command(event)
            if command is None or event["command_hash"] != _command_hash(command):
                raise MakeProbeError(
                    "shell interceptor event command identity is invalid"
                )
            self.records.append(event)
        if offset:
            del self.buffer[:offset]

    def finish(self) -> None:
        self._parse()
        if self.buffer:
            raise MakeProbeError("shell interceptor emitted a truncated event")


class _DomainStreamParser:
    def __init__(self, names: list[str]):
        self.names = names
        self.buffer = bytearray()
        self.total_bytes = 0
        self.records: list[dict[str, str]] = []

    def feed(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        if self.total_bytes > MAX_DOMAIN_BYTES:
            raise MakeProbeError("GNU Make domain stream exceeds byte bound")
        self.buffer.extend(chunk)
        self._parse()

    def _parse(self) -> None:
        offset = 0
        while len(self.buffer) - offset >= 4:
            start = offset
            count = struct.unpack_from("<I", self.buffer, offset)[0]
            offset += 4
            if count != len(self.names):
                raise MakeProbeError(
                    "GNU Make domain count differs from supervisor state"
                )
            values = []
            complete = True
            for _ in range(count):
                if len(self.buffer) - offset < 4:
                    complete = False
                    break
                size = struct.unpack_from("<I", self.buffer, offset)[0]
                offset += 4
                if size > MAX_DOMAIN_BYTES:
                    raise MakeProbeError("GNU Make domain value exceeds byte bound")
                if len(self.buffer) - offset < size:
                    complete = False
                    break
                values.append(bytes(self.buffer[offset:offset + size]))
                offset += size
            if not complete:
                offset = start
                break
            if len(self.records) >= MAX_DOMAIN_RECORDS:
                raise MakeProbeError("GNU Make domain stream exceeds record bound")
            self.records.append(
                {
                    name: _strict_utf8(value, f"GNU Make domain {name!r}")
                    for name, value in zip(self.names, values)
                }
            )
        if offset:
            del self.buffer[:offset]

    def finish(self, *, require_record: bool = True) -> None:
        self._parse()
        if self.buffer:
            raise MakeProbeError("GNU Make emitted a truncated domain record")
        if require_record and len(self.records) != 1:
            raise MakeProbeError(
                "GNU Make domain observer did not emit exactly one record"
            )


class _ReadObserver:
    def __init__(
        self,
        root: Path,
        watch_roots: Iterable[Path] | None = None,
    ):
        self.root = root.resolve(strict=True)
        self.paths: set[str] = set()
        self.directories: set[str] = set()
        self.event_count = 0
        self.error: MakeProbeError | None = None
        self.stop_event = Event()
        self.watches: dict[int, Path] = {}
        libc = ctypes.CDLL(None, use_errno=True)
        descriptor = libc.inotify_init1(
            os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        )
        if descriptor < 0:
            error = ctypes.get_errno()
            raise MakeProbeError(
                f"cannot create generated-data read observer: {os.strerror(error)}"
            )
        self.descriptor = descriptor
        selected_roots = (
            [self.root]
            if watch_roots is None
            else sorted(
                {
                    path.resolve(strict=True)
                    for path in watch_roots
                }
            )
        )
        directories = []
        for selected in selected_roots:
            if selected != self.root and self.root not in selected.parents:
                os.close(self.descriptor)
                raise MakeProbeError(
                    "generated-data read observer root escapes the repository"
                )
            if selected.is_symlink() or not selected.is_dir():
                os.close(self.descriptor)
                raise MakeProbeError(
                    "generated-data read observer root is not a regular directory"
                )
            directories.append(selected)
            directories.extend(
                path
                for path in sorted(selected.rglob("*"))
                if path.is_dir() and not path.is_symlink()
            )
        directories = sorted(set(directories))
        if len(directories) > MAX_REGISTRY_WATCHES:
            os.close(self.descriptor)
            raise MakeProbeError(
                "generated-data read observer exceeds watch bound"
            )
        for directory in directories:
            watch = libc.inotify_add_watch(
                self.descriptor,
                os.fsencode(directory),
                IN_ACCESS | IN_OPEN,
            )
            if watch < 0:
                error = ctypes.get_errno()
                os.close(self.descriptor)
                raise MakeProbeError(
                    "cannot watch generated-data source reads: "
                    + os.strerror(error)
                )
            self.watches[watch] = directory
        self.thread = Thread(target=self._run, daemon=True)

    def __enter__(self) -> "_ReadObserver":
        self.thread.start()
        return self

    def _run(self) -> None:
        try:
            while not self.stop_event.wait(0.01):
                self._drain()
            self._drain()
        except MakeProbeError as error:
            self.error = error
        except OSError as error:
            self.error = MakeProbeError(
                f"generated-data read observer failed: {error}"
            )

    def _drain(self) -> None:
        while True:
            try:
                data = os.read(self.descriptor, 64 * 1024)
            except BlockingIOError:
                return
            if not data:
                return
            offset = 0
            while offset < len(data):
                if len(data) - offset < 16:
                    raise MakeProbeError(
                        "generated-data read observer emitted a truncated event"
                    )
                watch, mask, _, length = struct.unpack_from(
                    "iIII",
                    data,
                    offset,
                )
                offset += 16
                if len(data) - offset < length:
                    raise MakeProbeError(
                        "generated-data read observer emitted a truncated name"
                    )
                raw_name = data[offset:offset + length].split(b"\0", 1)[0]
                offset += length
                if mask & IN_Q_OVERFLOW:
                    raise MakeProbeError(
                        "generated-data read observer queue overflowed"
                    )
                self.event_count += 1
                if self.event_count > MAX_REGISTRY_READ_EVENTS:
                    raise MakeProbeError(
                        "generated-data read observer exceeds event bound"
                    )
                directory = self.watches.get(watch)
                if directory is None:
                    raise MakeProbeError(
                        "generated-data read observer used an unknown watch"
                    )
                try:
                    name = raw_name.decode("utf-8")
                    opened = directory if not name else directory / name
                    relative = opened.relative_to(self.root)
                except (UnicodeDecodeError, ValueError) as error:
                    raise MakeProbeError(
                        "generated-data read observer path is invalid"
                    ) from error
                relative_path = relative.as_posix()
                if mask & IN_ISDIR and mask & (IN_ACCESS | IN_OPEN):
                    self.directories.add(relative_path)
                elif raw_name and mask & IN_ACCESS:
                    self.paths.add(relative_path)

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            os.close(self.descriptor)
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise MakeProbeError("generated-data read observer did not stop")
        else:
            os.close(self.descriptor)
        if self.error is not None and exc is None:
            raise self.error


def _registry_program_path(path: str) -> bool:
    return (
        path in {
            GENERATED_REGISTRY_PROBE.as_posix(),
            SANDBOX_EXEC.as_posix(),
            "scripts/__init__.py",
            "scripts/assets/__init__.py",
            "scripts/assets/tmx.py",
        }
        or (
            path.startswith("scripts/generated_data/")
            and path.endswith(".py")
        )
    )


def _registry_observed_programs(
    tree: Path,
    observed: set[str],
    *,
    label: str,
) -> set[str]:
    unexpected = sorted(
        path for path in observed if not _registry_program_path(path)
    )
    if unexpected:
        raise MakeProbeError(
            f"{label} read undeclared candidate paths: {unexpected}"
        )
    for path in observed:
        candidate = tree / path
        metadata = os.lstat(candidate)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MakeProbeError(
                f"{label} program path {path!r} is not a regular file"
            )
    return set(observed)


def _registry_program_directory(path: str) -> bool:
    return (
        path in {
            ".",
            "scripts",
            "scripts/assets",
            "scripts/generated_data",
            "scripts/validation_ownership",
        }
        or path.startswith("scripts/generated_data/")
    )


def _registry_observed_program_directories(
    observed: set[str],
    *,
    label: str,
) -> set[str]:
    unexpected = sorted(
        path for path in observed if not _registry_program_directory(path)
    )
    if unexpected:
        raise MakeProbeError(
            f"{label} opened undeclared candidate directories: {unexpected}"
        )
    return set(observed)


def _sandbox_run(
    root: Path,
    work: Path,
    *,
    argv: list[str],
    config_name: str | None = None,
    domain_names: list[str] | None = None,
    domain_path: Path | None = None,
    event_path: Path | None = None,
    environment: dict[str, str],
    mapping_path: Path | None = None,
    read_only: list[tuple[Path, str]],
    writable: list[tuple[Path, str]] | None = None,
    timeout: int = 120,
    max_output_bytes: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    writable_mounts = (
        [(work, "/work")]
        + ([] if writable is None else writable)
    )
    mounted_sources = [
        source.resolve(strict=True)
        for source, _ in [*read_only, *writable_mounts]
    ]
    for control_path in (domain_path, event_path, mapping_path):
        if control_path is None:
            continue
        control = control_path.resolve(strict=True)
        if any(
            control == source
            or source in control.parents
            or control in source.parents
            for source in mounted_sources
        ):
            raise MakeProbeError(
                "sandbox control state overlaps a candidate-visible mount"
            )
    config = {
        "argv": argv,
        "cwd": "/repo",
        "domain_path": (
            None
            if domain_path is None
            else str(domain_path.resolve(strict=True))
        ),
        "environment": environment,
        "event_path": (
            None
            if event_path is None
            else str(event_path.resolve(strict=True))
        ),
        "mapping_path": (
            None
            if mapping_path is None
            else str(mapping_path.resolve(strict=True))
        ),
        "read_only": [
            [str(source.resolve(strict=True)), target]
            for source, target in read_only
        ],
        "root": str(root.resolve(strict=True)),
        "sudo_drop": _select_namespace_launcher()["mode"] == "sudo-drop",
        "runner_gid": _select_namespace_launcher()["runner_gid"],
        "runner_uid": _select_namespace_launcher()["runner_uid"],
        "writable": [
            [str(source.resolve(strict=True)), target]
            for source, target in writable_mounts
        ],
    }
    config_path = work.parent / (
        "sandbox.json" if config_name is None else f"{config_name}.json"
    )
    config_path.write_text(
        json.dumps(config, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    command = [
        *_select_namespace_launcher()["argv_prefix"],
        str(PYTHON),
        "-I",
        str((read_only[0][0] / SANDBOX_EXEC).resolve(strict=True)),
        str(config_path),
    ]
    channels = {}
    dummy_writers = []
    opened_streams = []
    try:
        for name, path, parser in (
            (
                "domains",
                domain_path,
                _DomainStreamParser([] if domain_names is None else domain_names),
            ),
            (
                "events",
                event_path,
                _EventStreamParser(int(environment.get("VO_COMMAND_COUNT", "0"))),
            ),
        ):
            if path is None:
                continue
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISFIFO(metadata.st_mode):
                raise MakeProbeError(
                    f"sandbox {name} control must be a supervisor FIFO"
                )
            reader_fd = os.open(
                path,
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            dummy_fd = os.open(
                path,
                os.O_WRONLY
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            stream = os.fdopen(reader_fd, "rb", buffering=0)
            channels[name] = (stream, parser)
            opened_streams.append(stream)
            dummy_writers.append(dummy_fd)
        completed = _run_bounded_process(
            command,
            timeout=timeout,
            max_output_bytes=(
                MAX_SANDBOX_OUTPUT_BYTES
                if max_output_bytes is None
                else max_output_bytes
            ),
            channels=channels,
            dummy_writers=dummy_writers,
            env={
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TZ": "UTC",
            },
        )
        completed.events = (
            channels["events"][1].records
            if "events" in channels
            else []
        )
        completed.domain_values = (
            channels["domains"][1].records[0]
            if (
                "domains" in channels
                and channels["domains"][1].records
            )
            else {}
        )
        return completed
    finally:
        for descriptor in dummy_writers:
            try:
                os.close(descriptor)
            except OSError:
                pass
        for stream in opened_streams:
            try:
                stream.close()
            except OSError:
                pass


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    else:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if process.poll() is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _run_bounded_process(
    command: list[str],
    *,
    timeout: int,
    max_output_bytes: int,
    channels: dict[str, tuple[Any, Any]] | None = None,
    dummy_writers: list[int] | None = None,
    env: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    channels = {} if channels is None else channels
    dummy_writers = [] if dummy_writers is None else dummy_writers
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    if process.stdout is None or process.stderr is None:
        _terminate_process_group(process)
        raise MakeProbeError("sandbox output pipes are unavailable")
    streams = {
        process.stdout: bytearray(),
        process.stderr: bytearray(),
    }
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    closed_dummy_writers = False
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, ("output", stream))
        for name, (stream, parser) in channels.items():
            selector.register(
                stream,
                selectors.EVENT_READ,
                ("channel", name, parser),
            )
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    command,
                    timeout,
                    output=bytes(streams[process.stdout]),
                    stderr=bytes(streams[process.stderr]),
                )
            events = selector.select(min(remaining, 0.1))
            for key, _ in events:
                stream = key.fileobj
                chunk = os.read(stream.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(stream)
                    continue
                if key.data[0] == "output":
                    total = sum(len(buffer) for buffer in streams.values())
                    if total + len(chunk) > max_output_bytes:
                        raise MakeProbeError(
                            "sandbox command output exceeds "
                            f"{max_output_bytes}-byte bound"
                        )
                    streams[stream].extend(chunk)
                else:
                    key.data[2].feed(chunk)
            if process.poll() is not None and not closed_dummy_writers:
                for descriptor in dummy_writers:
                    os.close(descriptor)
                dummy_writers.clear()
                closed_dummy_writers = True
        if process.poll() is None:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        for _, parser in channels.values():
            if isinstance(parser, _DomainStreamParser):
                parser.finish(require_record=process.returncode == 0)
            else:
                parser.finish()
    except BaseException:
        _terminate_process_group(process)
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=bytes(streams[process.stdout]),
        stderr=bytes(streams[process.stderr]),
    )


def _compile_interceptor(repository: Path, output: Path) -> dict[str, str]:
    source = repository / INTERCEPTOR_SOURCE
    completed = subprocess.run(
        [
            str(CC),
            "-std=c11",
            "-O2",
            "-static",
            str(source),
            "-o",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    if completed.returncode != 0:
        raise MakeProbeError(
            "cannot compile the trusted Make shell interceptor: "
            + completed.stderr.strip()
        )
    output.chmod(0o755)
    version = subprocess.run(
        [str(CC), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    ).stdout.splitlines()[0]
    return {
        "compiler": str(CC),
        "compiler_version": version,
        "interceptor_sha256": _sha256_file(output),
        "source_sha256": _sha256_file(source),
    }


def _copy_tree(loader: Any, destination: Path) -> None:
    for path, entry in sorted(loader.entries.items()):
        if entry.mode == "160000" and entry.object_type == "commit":
            _copy_gitlink(
                loader.root,
                path,
                entry.object_id,
                destination / path,
            )
            continue
        if entry.object_type != "blob" or entry.mode not in {"100644", "100755"}:
            continue
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot_blob = getattr(loader, "snapshot_blob", None)
        if callable(snapshot_blob):
            content, mode = snapshot_blob(path, "Make probe input")
        else:
            content = loader.read_blob(path, "Make probe input")
            mode = entry.mode
        target.write_bytes(content)
        target.chmod(0o755 if mode == "100755" else 0o644)


def _snapshot_tree_state(
    root: Path,
    entries: dict[str, Any],
    revision: str | None,
    source_root: Path,
) -> str:
    digest = hashlib.sha256(b"validation-ownership-make-snapshot-v1\0")
    for relative, entry in sorted(entries.items()):
        path = root / relative
        if entry.mode == "160000" and entry.object_type == "commit":
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise MakeProbeError(
                    f"copied Make snapshot gitlink {relative!r} is invalid"
                )
            _verify_copied_gitlink(
                source_root,
                relative,
                entry.object_id,
                path,
            )
            values = (relative, "160000", f"gitlink:{entry.object_id}")
        elif entry.object_type == "blob" and entry.mode in {"100644", "100755"}:
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise MakeProbeError(
                    f"copied Make snapshot path {relative!r} is not regular"
                )
            mode = "100755" if metadata.st_mode & 0o111 else "100644"
            git_identity, content_identity = _file_identities(path)
            if revision is not None and (
                git_identity != entry.object_id or mode != entry.mode
            ):
                raise MakeProbeError(
                    f"copied Make snapshot path {relative!r} "
                    "differs from the selected revision"
                )
            values = (
                relative,
                mode,
                content_identity,
            )
        else:
            continue
        for value in values:
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "little"))
            digest.update(encoded)
    return digest.hexdigest()


def _copy_gitlink(
    repository: Path,
    gitlink_path: str,
    commit: str,
    destination: Path,
) -> None:
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    common = subprocess.run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    git_dir = Path(common) / "modules" / gitlink_path
    listing = subprocess.run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            f"--git-dir={git_dir}",
            "ls-tree",
            "-rz",
            "--full-tree",
            "-r",
            commit,
        ],
        check=False,
        capture_output=True,
        env=environment,
    )
    if listing.returncode != 0:
        raise MakeProbeError(
            f"cannot materialize exact gitlink {repository.name}@{commit}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = header.decode("ascii").split()
        if object_type != "blob" or mode not in {"100644", "100755"}:
            continue
        path = raw_path.decode("utf-8")
        content = subprocess.run(
            [
                "/usr/bin/git",
                "--no-replace-objects",
                f"--git-dir={git_dir}",
                "cat-file",
                "blob",
                object_id,
            ],
            check=True,
            capture_output=True,
            env=environment,
        ).stdout
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if mode == "100755" else 0o644)


def _verify_copied_gitlink(
    repository: Path,
    gitlink_path: str,
    commit: str,
    destination: Path,
) -> None:
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    common = subprocess.run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    git_dir = Path(common) / "modules" / gitlink_path
    listing = subprocess.run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            f"--git-dir={git_dir}",
            "ls-tree",
            "-rz",
            "--full-tree",
            "-r",
            commit,
        ],
        check=False,
        capture_output=True,
        env=environment,
    )
    if listing.returncode != 0:
        raise MakeProbeError(
            f"cannot verify exact gitlink {repository.name}@{commit}"
        )
    expected = {}
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = header.decode("ascii").split()
        if object_type == "blob" and mode in {"100644", "100755"}:
            expected[raw_path.decode("utf-8")] = (mode, object_id)
    actual = {}
    for path in sorted(destination.rglob("*")):
        relative = path.relative_to(destination).as_posix()
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode):
            raise MakeProbeError(
                f"copied Make snapshot gitlink path {relative!r} is a symlink"
            )
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise MakeProbeError(
                f"copied Make snapshot gitlink path {relative!r} is not regular"
            )
        mode = "100755" if metadata.st_mode & 0o111 else "100644"
        git_identity, _ = _file_identities(path)
        actual[relative] = (mode, git_identity)
    if actual != expected:
        raise MakeProbeError(
            f"copied Make snapshot gitlink {gitlink_path!r} differs "
            "from the selected commit"
        )


def _read_events(
    path: Path,
    *,
    expected_mapping_count: int,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    parser = _EventStreamParser(expected_mapping_count)
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            parser.feed(chunk)
    parser.finish()
    return parser.records


def _event_command(event: dict[str, Any]) -> str | None:
    arguments = event["arguments"]
    if (
        len(arguments) == 3
        and arguments[1] == "-c"
    ):
        return arguments[2]
    if (
        len(arguments) == 4
        and arguments[1:3] == ["-eu", "-c"]
    ):
        return arguments[3]
    if not arguments:
        return None
    aliases = {
        "/usr/bin/find": "find",
        "/usr/bin/printf": "printf",
        "/usr/bin/python3": "python3",
        "/usr/bin/uname": "uname",
        "/bin/vo-make": "/usr/bin/make",
    }
    return " ".join(
        [
            aliases.get(arguments[0], arguments[0]),
            *(
                '""' if not argument else argument
                for argument in arguments[1:]
            ),
        ]
    )


def _event_direct_arguments(event: dict[str, Any]) -> list[str] | None:
    arguments = event["arguments"]
    if (
        len(arguments) >= 3
        and arguments[-2] == "-c"
        and arguments[0] in {"/bin/sh", "/bin/bash"}
    ):
        return None
    return arguments or None


def _command_hash(command: str) -> str:
    value = 0xCBF29CE484222325
    for byte in command.encode("utf-8"):
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def _write_mapping(
    directory: Path,
    mappings: list[dict[str, Any]],
    *,
    materialized_names: set[str] | None = None,
) -> set[str]:
    if len(mappings) > MAX_MAPPING_COUNT:
        raise MakeProbeError("Make command mapping exceeds count bound")
    payloads: list[tuple[str, bytes, bytes]] = []
    seen = set()
    total_bytes = 0
    for mapping in mappings:
        name = _command_hash(mapping["command"])
        if name in seen:
            raise MakeProbeError("Make command mapping hash collision")
        seen.add(name)
        command_bytes = mapping["command"].encode("utf-8")
        output_bytes = mapping["output"]
        if (
            len(command_bytes) > MAX_SANDBOX_OUTPUT_BYTES
            or len(output_bytes) > MAX_SANDBOX_OUTPUT_BYTES
        ):
            raise MakeProbeError(
                "Make command mapping entry exceeds individual byte bound"
            )
        total_bytes += len(command_bytes) + len(output_bytes)
        if total_bytes > MAX_MAPPING_BYTES:
            raise MakeProbeError("Make command mapping exceeds byte bound")
        payloads.append((name, command_bytes, output_bytes))
    if materialized_names is None:
        materialized_names = set()
        if directory.exists():
            directory.chmod(0o700)
            shutil.rmtree(directory)
        directory.mkdir(mode=0o700)
    elif not directory.exists():
        directory.mkdir(mode=0o700)
    else:
        directory.chmod(0o700)
    for name, command_bytes, output_bytes in payloads:
        if name in materialized_names:
            continue
        command_path = directory / f"{name}.cmd"
        output_path = directory / f"{name}.out"
        if command_path.exists() or output_path.exists():
            if (
                not command_path.is_file()
                or command_path.is_symlink()
                or not output_path.is_file()
                or output_path.is_symlink()
                or command_path.read_bytes() != command_bytes
                or output_path.read_bytes() != output_bytes
            ):
                raise MakeProbeError(
                    "Make command mapping materialization differs from the "
                    "existing supervisor state"
                )
        else:
            command_path.write_bytes(command_bytes)
            output_path.write_bytes(output_bytes)
            command_path.chmod(0o400)
            output_path.chmod(0o400)
        materialized_names.add(name)
    directory.chmod(0o500)
    return materialized_names


def _make_environment(
    mapping_count: int,
    extra: dict[str, str],
) -> dict[str, str]:
    return {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "MAKEFLAGS": "",
        "MAKEOVERRIDES": "",
        "MFLAGS": "",
        "GNUMAKEFLAGS": "",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "SOURCE_DATE_EPOCH": "0",
        "TMPDIR": "/work",
        "TZ": "UTC",
        "VO_COMMAND_COUNT": str(mapping_count),
        **extra,
    }


def _strip_make_comment(line: str) -> str:
    escaped = False
    result = []
    for character in line:
        if character == "#" and not escaped:
            break
        result.append(character)
        if character == "\\":
            escaped = not escaped
        else:
            escaped = False
    return "".join(result)


def _external_default_names(
    loader: Any,
    loaded_sources: set[str],
) -> set[str]:
    names = set()
    for path, entry in sorted(loader.entries.items()):
        if (
            path != "Makefile"
            and not path.endswith(".mk")
        ) or (
            path not in loaded_sources
            or entry.object_type != "blob"
            or entry.mode not in {
                "100644",
                "100755",
            }
        ):
            continue
        try:
            text = loader.read_blob(
                path,
                "Make external-default census",
            ).decode("utf-8")
        except UnicodeDecodeError as error:
            raise MakeProbeError(
                f"Make input {path!r} is not UTF-8"
            ) from error
        in_define = False
        for line_number, raw_line in enumerate(text.splitlines(), 1):
            stripped = raw_line.lstrip()
            if stripped.startswith("define "):
                in_define = True
                continue
            if stripped == "endef":
                in_define = False
                continue
            if raw_line.startswith("\t") and not in_define and "$(" not in raw_line:
                continue
            line = _strip_make_comment(raw_line)
            matches = list(EXTERNAL_DEFAULT_RE.finditer(line))
            for match in matches:
                if "override" not in match.group("modifiers").split():
                    names.add(match.group("name"))
            if "?=" in line and not matches:
                raise MakeProbeError(
                    "Make external-default declaration has a dynamic name "
                    f"at {path}:{line_number}"
                )
    return names


def _make_reference_names(line: str) -> set[str]:
    names = {
        next(
            value
            for value in match.group(
                "paren",
                "brace",
                "short",
            )
            if value is not None
        )
        for match in DIRECT_VARIABLE_RE.finditer(line)
    }
    names.update(
        match.group("name")
        for match in INTROSPECTION_VARIABLE_RE.finditer(line)
    )
    names.update(
        next(
            value
            for value in match.group("paren", "brace", "short")
            if value is not None
        )
        for match in SCOPED_VARIABLE_RE.finditer(line)
    )
    conditional = CONDITIONAL_VARIABLE_RE.match(line)
    if conditional is not None:
        names.add(conditional.group("name"))
    return names


def _second_expansion_reference_names(line: str) -> set[str]:
    return {
        match.group("paren") or match.group("brace")
        for match in SECOND_EXPANSION_VARIABLE_RE.finditer(line)
    }


def _make_reference_census(
    loader: Any,
) -> tuple[
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, dict[str, set[str]]],
]:
    all_by_path: dict[str, set[str]] = {}
    graph_by_path: dict[str, set[str]] = {}
    recipe_by_path: dict[str, set[str]] = {}
    introspection_by_path: dict[str, set[str]] = {}
    dependencies_by_path: dict[str, dict[str, set[str]]] = {}
    for path, entry in sorted(loader.entries.items()):
        if (
            path != "Makefile"
            and not path.endswith(".mk")
        ) or entry.object_type != "blob" or entry.mode not in {
            "100644",
            "100755",
        }:
            continue
        try:
            text = loader.read_blob(
                path,
                "Make reference census",
            ).decode("utf-8")
        except UnicodeDecodeError as error:
            raise MakeProbeError(
                f"Make input {path!r} is not UTF-8"
            ) from error
        references: set[str] = set()
        graph_references: set[str] = set()
        recipe_references: set[str] = set()
        introspection_references: set[str] = set()
        path_dependencies: dict[str, set[str]] = {}
        defaults = set()
        current_define: str | None = None
        computed_graph = False
        computed_recipe = False
        computed_variables: set[str] = set()
        for raw_line in text.splitlines():
            line = _strip_make_comment(raw_line)
            define = DEFINE_RE.match(line)
            if define is not None and current_define is None:
                current_define = define.group("name")
                path_dependencies.setdefault(current_define, set())
                continue
            if current_define is not None:
                if line.strip() == "endef":
                    current_define = None
                    continue
                define_references = _make_reference_names(line)
                references.update(define_references)
                path_dependencies[current_define].update(define_references)
                introspection_references.update(
                    match.group("name")
                    for match in INTROSPECTION_VARIABLE_RE.finditer(line)
                )
                if "$($" in line or "${$" in line:
                    computed_variables.add(current_define)
                if "$(eval" in line or "${eval" in line:
                    graph_references.add(current_define)
                continue

            line_references = _make_reference_names(line)
            references.update(line_references)
            introspection_references.update(
                match.group("name")
                for match in INTROSPECTION_VARIABLE_RE.finditer(line)
            )
            is_recipe = raw_line.startswith("\t")
            assignment = (
                None if is_recipe else VARIABLE_ASSIGNMENT_RE.match(line)
            )
            target_assignment = (
                None
                if is_recipe
                else TARGET_VARIABLE_ASSIGNMENT_RE.match(line)
            )
            if assignment is not None:
                path_dependencies.setdefault(
                    assignment.group("name"),
                    set(),
                ).update(_make_reference_names(assignment.group("value")))
                if line.lstrip().startswith("export "):
                    recipe_references.update(line_references)
                if "$(eval" in assignment.group("value") or (
                    "${eval" in assignment.group("value")
                ):
                    graph_references.update(line_references)
            elif target_assignment is not None:
                path_dependencies.setdefault(
                    target_assignment.group("name"),
                    set(),
                ).update(
                    _make_reference_names(target_assignment.group("value"))
                )
                graph_references.update(
                    _make_reference_names(target_assignment.group("target"))
                )
                if "$(eval" in target_assignment.group("value") or (
                    "${eval" in target_assignment.group("value")
                ):
                    graph_references.update(line_references)
            elif raw_line.startswith("\t"):
                if "$(eval" in line or "${eval" in line:
                    graph_references.update(line_references)
                else:
                    recipe_references.update(line_references)
            else:
                graph_references.update(line_references)
                second_expansion = _second_expansion_reference_names(line)
                references.update(second_expansion)
                graph_references.update(second_expansion)

            default_matches = list(EXTERNAL_DEFAULT_RE.finditer(line))
            defaults.update(
                match.group("name")
                for match in default_matches
                if "override" not in match.group("modifiers").split()
            )
            if "$($" in line or "${$" in line:
                if assignment is not None:
                    computed_variables.add(assignment.group("name"))
                elif target_assignment is not None:
                    computed_variables.add(target_assignment.group("name"))
                elif raw_line.startswith("\t"):
                    computed_recipe = True
                else:
                    computed_graph = True
        for name in computed_variables:
            path_dependencies[name].update(defaults)
        if computed_graph:
            graph_references.update(defaults)
        if computed_recipe:
            recipe_references.update(defaults)
        all_by_path[path] = references
        graph_by_path[path] = graph_references
        recipe_by_path[path] = recipe_references
        introspection_by_path[path] = introspection_references
        dependencies_by_path[path] = path_dependencies
    return (
        all_by_path,
        graph_by_path,
        recipe_by_path,
        introspection_by_path,
        dependencies_by_path,
    )


def _expand_make_references(
    pending: set[str],
    dependencies: dict[str, set[str]],
) -> set[str]:
    reached = set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.update(dependencies.get(name, ()))
    return reached


def _target_variable_usage(
    baseline: dict[str, Any],
    reference_census: tuple[
        dict[str, set[str]],
        dict[str, set[str]],
        dict[str, set[str]],
        dict[str, set[str]],
        dict[str, dict[str, set[str]]],
    ],
) -> dict[str, set[str]]:
    sources = {"Makefile"}
    sources.update(item["path"] for item in baseline["includes"])
    for trace in baseline["traces"].values():
        sources.update(trace["sources"])
    return _source_variable_usage(sources, reference_census)


def _source_variable_usage(
    sources: set[str],
    reference_census: tuple[
        dict[str, set[str]],
        dict[str, set[str]],
        dict[str, set[str]],
        dict[str, set[str]],
        dict[str, dict[str, set[str]]],
    ],
) -> dict[str, set[str]]:
    (
        all_by_path,
        graph_by_path,
        recipe_by_path,
        introspection_by_path,
        dependencies_by_path,
    ) = reference_census
    dependencies: dict[str, set[str]] = {}
    for source in sources:
        for name, referenced_names in dependencies_by_path.get(
            source,
            {},
        ).items():
            dependencies.setdefault(name, set()).update(referenced_names)
    observed = _expand_make_references(
        {
            name
            for source in sources
            for name in all_by_path.get(source, ())
        },
        dependencies,
    )
    graph = _expand_make_references(
        {
            name
            for source in sources
            for name in graph_by_path.get(source, ())
        },
        dependencies,
    )
    recipe = _expand_make_references(
        {
            name
            for source in sources
            for name in recipe_by_path.get(source, ())
        },
        dependencies,
    )
    introspection = _expand_make_references(
        {
            name
            for source in sources
            for name in introspection_by_path.get(source, ())
        },
        dependencies,
    )
    observed.update(graph)
    observed.update(recipe)
    return {
        "all": observed,
        "graph": graph,
        "introspection": introspection,
        "recipe": recipe,
        "recipe_only": recipe - graph,
    }


def _environment_sensitive_names(
    *,
    defaults: set[str],
    usage: dict[str, set[str]],
    undefined_names: Iterable[str],
    ambient_undefined_names: set[str],
    environment_names: set[str],
) -> set[str]:
    sensitive = (defaults & usage["all"]) | (
        set(undefined_names) & ambient_undefined_names
    )
    return sensitive & environment_names


def _variant_sources(variant: dict[str, Any]) -> set[str]:
    if "sources" in variant:
        return set(variant["sources"])
    sources = {"Makefile"}
    sources.update(item["path"] for item in variant["includes"])
    for trace in variant["traces"].values():
        sources.update(trace["sources"])
    return sources


def _variant_discovery_signature(
    variant: dict[str, Any],
) -> tuple[
    tuple[str, ...],
    tuple[tuple[str, tuple[str, ...]], ...],
]:
    return (
        tuple(sorted(_variant_sources(variant))),
        tuple(
            (
                target,
                tuple(closure),
            )
            for target, closure in sorted(variant["closures"].items())
        ),
    )


def _undefined_variable_names(output: str) -> set[str]:
    return {
        match.group("name")
        for match in UNDEFINED_VARIABLE_RE.finditer(output)
    }


def _prepare_make_root(
    base: Path,
    interceptor: Path,
) -> tuple[Path, list[tuple[Path, str]]]:
    root = base / "make-root"
    for directory in (
        "bin",
        "dev",
        "lib/x86_64-linux-gnu",
        "lib64",
        "probe",
        "repo",
        "usr/bin",
        "work",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(interceptor, root / "bin/sh")
    shutil.copy2(interceptor, root / "bin/bash")
    shutil.copy2(interceptor, root / "bin/vo-make")
    shutil.copy2(interceptor, root / "bin/vo-shell")
    shutil.copy2(interceptor, root / "usr/bin/vo-domain-observer")
    for name in (
        "arm-none-eabi-as",
        "arm-none-eabi-gcc",
        "cc",
        "find",
        "g++",
        "gcc",
        "iconv",
        "mkdir",
        "mv",
        "printf",
        "python3",
        "rm",
        "sed",
        "uname",
    ):
        shutil.copy2(interceptor, root / "usr/bin" / name)
    shutil.copy2(MAKE, root / "usr/bin/make")
    shutil.copy2(LIBC, root / "lib/x86_64-linux-gnu/libc.so.6")
    shutil.copy2(LOADER, root / "lib64/ld-linux-x86-64.so.2")
    (root / "dev/null").touch()
    return root, []


def _prepare_command_root(base: Path) -> Path:
    root = base / "command-root"
    with _COMMAND_ROOT_LOCK:
        for directory in (
            "dev",
            "probe",
            "repo",
            "usr/bin",
            PYTHON_STDLIB.as_posix().lstrip("/"),
            PLATFORM_LIB.as_posix().lstrip("/"),
            LOADER_LIB.as_posix().lstrip("/"),
            "work",
        ):
            (root / directory).mkdir(parents=True, exist_ok=True)
        for file_path in ("dev/null", "usr/bin/python3"):
            path = root / file_path
            if not path.exists():
                path.touch()
        for name, target in (
            ("bin", "usr/bin"),
            ("lib", "usr/lib"),
            ("lib64", "usr/lib64"),
        ):
            link = root / name
            if not link.exists() and not link.is_symlink():
                link.symlink_to(target)
        (root / "dev/null").touch()
        return root


def probe_generated_registry(
    loader: Any,
    *,
    scratch_root: Path,
) -> tuple[bytes, dict[str, Any]]:
    """Run candidate generated registry code in a credential-free sandbox."""
    scratch_root = _prepare_confined_scratch(loader, scratch_root)
    tools = _ensure_tools()
    with tempfile.TemporaryDirectory(
        prefix="generated-registry-probe-",
        dir=scratch_root,
    ) as directory:
        base = Path(directory)
        tree = base / "tree"
        work = base / "command"
        tree.mkdir()
        work.mkdir()
        (work / "build").mkdir()
        _copy_tree(loader, tree)
        (tree / "build").mkdir()
        root = _prepare_command_root(base)
        common = {
            "environment": {
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "SOURCE_DATE_EPOCH": "0",
                "TZ": "UTC",
            },
            "read_only": [
                (tree, "/repo"),
                (PYTHON.resolve(strict=True), "/usr/bin/python3"),
                (PYTHON_STDLIB.resolve(strict=True), PYTHON_STDLIB.as_posix()),
                (PLATFORM_LIB.resolve(strict=True), PLATFORM_LIB.as_posix()),
                (LOADER_LIB.resolve(strict=True), LOADER_LIB.as_posix()),
            ],
            "writable": [(Path("/dev/null"), "/dev/null")],
            "timeout": 60,
        }

        def execute(
            arguments: list[str],
            name: str,
        ) -> tuple[
            subprocess.CompletedProcess[bytes],
            set[str],
            set[str],
        ]:
            with _ReadObserver(tree) as observer:
                completed = _sandbox_run(
                    root,
                    work,
                    argv=[
                        "/usr/bin/python3",
                        "-I",
                        "-B",
                        f"/repo/{GENERATED_REGISTRY_PROBE.as_posix()}",
                        *arguments,
                    ],
                    config_name=name,
                    **common,
                )
            if completed.returncode != 0:
                raise MakeProbeError(
                    "candidate generated-data registry probe failed: "
                    + _normalize(
                        _strict_utf8(
                            completed.stderr,
                            "candidate generated-data registry stderr",
                        )
                    )
                )
            return completed, observer.paths, observer.directories

        (
            names_completed,
            names_observed,
            names_directories,
        ) = execute(["list"], "registry-list")
        program_paths = _registry_observed_programs(
            tree,
            names_observed,
            label="candidate generated-data registry list",
        )
        program_directories = _registry_observed_program_directories(
            names_directories,
            label="candidate generated-data registry list",
        )
        names_output = names_completed.stdout
        try:
            names = json.loads(
                _strict_utf8(
                    names_output,
                    "candidate generated-data registry list",
                )
            )
        except json.JSONDecodeError as error:
            raise MakeProbeError(
                "candidate generated-data registry list is invalid JSON"
            ) from error
        if (
            not isinstance(names, list)
            or not names
            or names != sorted(set(names))
            or not all(isinstance(name, str) and name for name in names)
        ):
            raise MakeProbeError(
                "candidate generated-data registry list is invalid"
            )

        records = []
        for index, name in enumerate(names):
            (
                record_completed,
                record_observed,
                record_directories,
            ) = execute(
                ["metadata", name],
                f"registry-metadata-{index}",
            )
            program_paths.update(
                _registry_observed_programs(
                    tree,
                    record_observed,
                    label=(
                        "candidate generated-data registry metadata "
                        f"{name!r}"
                    ),
                )
            )
            program_directories.update(
                _registry_observed_program_directories(
                    record_directories,
                    label=(
                        "candidate generated-data registry metadata "
                        f"{name!r}"
                    ),
                )
            )
            record_output = record_completed.stdout
            try:
                record = json.loads(
                    _strict_utf8(
                        record_output,
                        f"candidate generated-data registry record {name!r}",
                    )
                )
            except json.JSONDecodeError as error:
                raise MakeProbeError(
                    f"candidate generated-data registry record {name!r} "
                    "is invalid JSON"
                ) from error
            if not isinstance(record, dict) or record.get("name") != name:
                raise MakeProbeError(
                    f"candidate generated-data registry record {name!r} "
                    "has invalid identity"
                )
            authorized = _registry_authorized_sources(tree, record)
            (
                load_completed,
                load_observed,
                load_directories,
            ) = execute(
                ["load", name],
                f"registry-load-{index}",
            )
            observed_programs = {
                path
                for path in load_observed
                if _registry_program_path(path)
            }
            unknown_programs = observed_programs - program_paths
            if unknown_programs:
                raise MakeProbeError(
                    f"candidate generated-data registry record {name!r} "
                    "loaded undeclared program paths: "
                    f"{sorted(unknown_programs)}"
                )
            source_directories = set()
            source = record["default_source"]
            if source is not None and (tree / source).is_dir():
                source_directories.add(source)
            unknown_directories = (
                load_directories
                - program_directories
                - source_directories
            )
            if unknown_directories:
                raise MakeProbeError(
                    f"candidate generated-data registry record {name!r} "
                    "opened undeclared candidate directories: "
                    f"{sorted(unknown_directories)}"
                )
            reported_output = load_completed.stdout
            try:
                reported = json.loads(
                    _strict_utf8(
                        reported_output,
                        f"candidate generated-data source report {name!r}",
                    )
                )
            except json.JSONDecodeError as error:
                raise MakeProbeError(
                    f"candidate generated-data source report {name!r} "
                    "is invalid JSON"
                ) from error
            if (
                not isinstance(reported, list)
                or reported != sorted(reported)
                or len(reported) != len(set(reported))
                or not all(isinstance(path, str) and path for path in reported)
            ):
                raise MakeProbeError(
                    f"candidate generated-data registry record {name!r} "
                    "reports invalid sources"
                )
            observed = _registry_observed_sources(
                record,
                authorized,
                load_observed - observed_programs,
            )
            if set(reported) != authorized or observed != authorized:
                raise MakeProbeError(
                    f"candidate generated-data registry record {name!r} "
                    "source authority differs "
                    f"(authorized={sorted(authorized)}, "
                    f"reported={reported}, observed={sorted(observed)})"
                )
            record["resolved_sources"] = sorted(authorized)
            records.append(record)
        output = json.dumps(
            records,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
        return output, {
            "launcher": tools["namespace_launcher"],
            "observed_program_directories": sorted(program_directories),
            "observed_programs": [
                {
                    "mode": (
                        "100755"
                        if os.lstat(tree / path).st_mode & 0o111
                        else "100644"
                    ),
                    "path": path,
                    "sha256": _sha256_file(tree / path),
                }
                for path in sorted(program_paths)
            ],
            "probe_path": GENERATED_REGISTRY_PROBE.as_posix(),
            "python": str(PYTHON),
            "python_version": _tool_version(PYTHON),
        }


def _registry_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MakeProbeError(f"{label} is invalid")
    path = Path(value)
    if path.is_absolute() or path.as_posix() != value or ".." in path.parts:
        raise MakeProbeError(f"{label} must be a canonical relative path")
    return value


def _registry_authorized_sources(
    tree: Path,
    record: dict[str, Any],
) -> set[str]:
    name = record.get("name", "<unknown>")
    source = record.get("default_source")
    pattern = record.get("default_source_pattern")
    additional = record.get("default_additional_sources")
    if (
        not isinstance(additional, list)
        or additional != sorted(set(additional))
    ):
        raise MakeProbeError(
            f"generated-data registry {name!r} additional sources are invalid"
        )
    authorized = {
        _registry_relative_path(
            path,
            f"generated-data registry {name!r} additional source",
        )
        for path in additional
    }
    if source is None:
        if pattern is not None or authorized:
            raise MakeProbeError(
                f"generated-data registry {name!r} has sources without a default"
            )
    else:
        source = _registry_relative_path(
            source,
            f"generated-data registry {name!r} default source",
        )
        source_path = tree / source
        if source_path.is_symlink():
            raise MakeProbeError(
                f"generated-data registry {name!r} default source is a symlink"
            )
        if source_path.is_file():
            if pattern is not None:
                raise MakeProbeError(
                    f"generated-data registry {name!r} file source has a pattern"
                )
            authorized.add(source)
        elif source_path.is_dir():
            if (
                not isinstance(pattern, str)
                or not pattern
                or "/" in pattern
                or "\\" in pattern
                or "**" in pattern
                or re.fullmatch(r"[A-Za-z0-9_.?*\-\[\]]+", pattern) is None
                or not any(character in pattern for character in "*?[")
            ):
                raise MakeProbeError(
                    f"generated-data registry {name!r} directory pattern is invalid"
                )
            matches = []
            for path in sorted(source_path.glob(pattern)):
                if path.is_symlink() or not path.is_file():
                    raise MakeProbeError(
                        f"generated-data registry {name!r} pattern matched "
                        "a non-regular source"
                    )
                matches.append(path.relative_to(tree).as_posix())
            if not matches:
                raise MakeProbeError(
                    f"generated-data registry {name!r} pattern has no sources"
                )
            authorized.update(matches)
        else:
            raise MakeProbeError(
                f"generated-data registry {name!r} default source is stale"
            )
    for path in authorized:
        target = tree / path
        if target.is_symlink() or not target.is_file():
            raise MakeProbeError(
                f"generated-data registry {name!r} source {path!r} "
                "is not a regular file"
            )
    return authorized


def _registry_observed_sources(
    record: dict[str, Any],
    authorized: set[str],
    observed: set[str],
) -> set[str]:
    del record, authorized
    return set(observed)


def _compile_scaninc(tree: Path, work: Path) -> Path:
    output = work / "scaninc"
    with _SCANINC_COMPILE_LOCK:
        if output.exists():
            return output
        sources = [
            tree / "tools/scaninc" / name
            for name in (
                "scaninc.cpp",
                "c_file.cpp",
                "asm_file.cpp",
                "source_file.cpp",
            )
        ]
        completed = subprocess.run(
            [
                "/usr/bin/g++",
                "-Wall",
                "-Werror",
                "-std=c++11",
                "-O2",
                *(str(path) for path in sources),
                "-o",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "TZ": "UTC",
            },
        )
        if completed.returncode != 0:
            raise MakeProbeError(
                "cannot compile registered scaninc authority: "
                + completed.stderr.strip()
            )
        output.chmod(0o755)
        return output


def _compile_gbagfx(tree: Path, output: Path) -> dict[str, str]:
    source_root = tree / "tools/gbagfx"
    sources = [
        source_root / name
        for name in (
            "main.c",
            "convert_png.c",
            "gfx.c",
            "jasc_pal.c",
            "lz.c",
            "rl.c",
            "util.c",
            "font.c",
        )
    ]
    completed = subprocess.run(
        [
            "/usr/bin/gcc",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-std=c11",
            "-O2",
            "-s",
            "-DPNG_SKIP_SETJMP_CHECK",
            *(str(path) for path in sources),
            "-o",
            str(output),
            "-lpng",
            "-lz",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    if completed.returncode != 0:
        raise MakeProbeError(
            "cannot compile registered gbagfx authority: "
            + completed.stderr.strip()
        )
    output.chmod(0o755)
    return {
        "compiler": "/usr/bin/gcc",
        "output_sha256": _sha256_file(output),
    }


def _registered_command_cache_key(
    *,
    cache_namespace: tuple[Any, ...],
    command: str,
    contract: dict[str, Any],
    environment: dict[str, str],
) -> tuple[Any, ...] | None:
    if (
        contract["resolved_value"] is not None
        or "build/" in command
        or "/usr/bin/make -j" in command
        or "/bin/vo-make -j" in command
        or "--out-dir" in command
        or "--lock-output" in command
    ):
        return None
    return (
        cache_namespace,
        contract["id"],
        command,
        tuple(sorted(environment.items())),
    )


def _is_parallel_registered_command(
    *,
    cache_namespace: tuple[Any, ...],
    command: str,
    contract: dict[str, Any],
    environment: dict[str, str],
) -> bool:
    return (
        _registered_command_cache_key(
            cache_namespace=cache_namespace,
            command=command,
            contract=contract,
            environment=environment,
        )
        is not None
    )


def _execute_registered_command(
    command: str,
    contract: dict[str, Any],
    *,
    base: Path,
    build_output: Path,
    cache_namespace: tuple[Any, ...],
    command_work: Path,
    direct_arguments: list[str] | None,
    tree: Path,
    environment: dict[str, str],
) -> bytes:
    if contract["resolved_value"] is not None:
        if not contract["resolved_value"]:
            return b""
        return (contract["resolved_value"] + "\n").encode("utf-8")
    cache_key = _registered_command_cache_key(
        cache_namespace=cache_namespace,
        command=command,
        contract=contract,
        environment=environment,
    )
    if cache_key is not None:
        with _REGISTERED_COMMAND_CACHE_LOCK:
            cached = _REGISTERED_COMMAND_CACHE.pop(cache_key, None)
        if cached is not None:
            with _REGISTERED_COMMAND_CACHE_LOCK:
                _REGISTERED_COMMAND_CACHE[cache_key] = cached
            return cached
    executed = command
    if contract["id"] == "banim-scaninc-inputs":
        _compile_scaninc(tree, command_work)
        executed = re.sub(
            r"^tools/scaninc/scaninc\b",
            "/work/scaninc",
            executed,
        )
    root = base / "command-root"
    if not root.exists():
        root = _prepare_command_root(base)
    if direct_arguments is None:
        argv = ["/usr/bin/bash", "-c", executed]
    else:
        executable_aliases = {
            "arm-none-eabi-as": "/usr/bin/arm-none-eabi-as",
            "arm-none-eabi-gcc": "/usr/bin/arm-none-eabi-gcc",
            "cc": "/usr/bin/cc",
            "find": "/usr/bin/find",
            "g++": "/usr/bin/g++",
            "gcc": "/usr/bin/gcc",
            "iconv": "/usr/bin/iconv",
            "mkdir": "/usr/bin/mkdir",
            "mv": "/usr/bin/mv",
            "printf": "/usr/bin/printf",
            "python3": "/usr/bin/python3",
            "rm": "/usr/bin/rm",
            "sed": "/usr/bin/sed",
            "uname": "/usr/bin/uname",
        }
        argv = [
            executable_aliases.get(
                direct_arguments[0],
                direct_arguments[0],
            ),
            *direct_arguments[1:],
        ]
    completed = _sandbox_run(
        root,
        command_work,
        argv=argv,
        config_name=f"sandbox-{_command_hash(command)}",
        environment={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOURCE_DATE_EPOCH": "0",
            "TMPDIR": "/work",
            "TZ": "UTC",
            **environment,
        },
        read_only=[
            (tree, "/repo"),
            (Path("/usr"), "/usr"),
        ],
        writable=[
            (build_output, "/repo/build"),
            (Path("/dev/null"), "/dev/null"),
        ],
    )
    if completed.returncode != 0:
        raise MakeProbeError(
            f"registered Make command {contract['id']!r} failed in confinement: "
            + _normalize(
                _strict_utf8(
                    completed.stderr,
                    f"registered Make command {contract['id']!r} stderr",
                )
            )
        )
    output = completed.stdout
    if cache_key is not None:
        with _REGISTERED_COMMAND_CACHE_LOCK:
            if len(_REGISTERED_COMMAND_CACHE) >= REGISTERED_COMMAND_CACHE_LIMIT:
                _REGISTERED_COMMAND_CACHE.pop(next(iter(_REGISTERED_COMMAND_CACHE)))
            _REGISTERED_COMMAND_CACHE[cache_key] = output
    return output


def _normalize(text: str) -> str:
    text = text.replace("/bin/vo-make", "/usr/bin/make")
    text = text.replace("/repo/", "")
    text = text.replace("/repo", ".")
    text = text.replace("/work", "<WORK>")
    return "\n".join(line.rstrip() for line in text.splitlines())


def _trace_records(output: str) -> dict[str, dict[str, Any]]:
    lines = _normalize(output).splitlines()
    records: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for line in lines:
        match = TRACE_RE.match(line)
        if match:
            target = match.group("target") or match.group("missing_target")
            current = records.setdefault(
                target,
                {
                    "commands": [],
                    "reasons": [],
                    "sources": [],
                },
            )
            current["sources"].append(
                re.sub(r":[0-9]+$", "", match.group("source"))
            )
            if match.group("due"):
                current["reasons"].extend(match.group("due").split())
            continue
        stripped = line.lstrip()
        if current is not None and line and not stripped.startswith(
            (
                "Considering target file ",
                "Finished prerequisites of target file ",
                "File ",
                "Making ",
                "Must remake target ",
                "No need to remake target ",
                "Successfully remade target file ",
                "Pruning file ",
                "Reading makefile ",
                "Trying pattern rule ",
                "Updating makefiles",
                "Updating goal targets",
            )
        ):
            current["commands"].append(line)
    return {
        target: {
            "commands": record["commands"],
            "reasons": sorted(set(record["reasons"])),
            "sources": sorted(set(record["sources"])),
        }
        for target, record in records.items()
    }


def _target_graph(
    output: str,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    graph: dict[str, set[str]] = {}
    stack: list[tuple[int, str]] = []
    includes: dict[str, bool] = {}
    goals_started = False
    for raw_line in output.splitlines():
        reading = READING_RE.match(raw_line.rstrip())
        if reading:
            path = reading.group("path")
            includes[path] = (
                includes.get(path, False)
                or "(don't care)" in reading.group("details")
            )
        line = _normalize(raw_line)
        if line.startswith("Updating goal targets"):
            goals_started = True
            stack = []
            continue
        if not goals_started:
            continue
        match = CONSIDER_RE.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        target = match.group("target")
        graph.setdefault(target, set())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            graph.setdefault(stack[-1][1], set()).add(target)
        stack.append((indent, target))
    return (
        {
            target: sorted(children)
            for target, children in sorted(graph.items())
        },
        [
            {"optional": optional, "path": path}
            for path, optional in sorted(includes.items())
        ],
    )


def _database_semantics(output: str) -> str:
    normalized = _normalize(output)
    marker = "\n# Files\n"
    if marker not in normalized:
        raise MakeProbeError("GNU Make database output lacks the Files section")
    result = normalized.split(marker, 1)[1]
    for end_marker in (
        "\n# files hash-table stats:",
        "\n# Finished Make data base",
    ):
        if end_marker in result:
            result = result.split(end_marker, 1)[0]
    result = "\n".join(
        line
        for line in result.splitlines()
        if not line.startswith("#  Last modified ")
    )
    return re.sub(r":[0-9]+", ":<LINE>", result)


def _closures(
    targets: Iterable[str],
    graph: dict[str, list[str]],
) -> dict[str, list[str]]:
    result = {}
    for target in targets:
        seen = set()
        pending = [target]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(graph.get(current, ()))
        result[target] = sorted(seen)
    return result


def _validate_includes(
    includes: Iterable[dict[str, Any]],
    loader: Any,
    build_output: Path,
) -> None:
    for include in includes:
        if set(include) != {"optional", "path"}:
            raise MakeProbeError("GNU Make include record is malformed")
        raw_path = include["path"]
        optional = include["optional"]
        if not isinstance(raw_path, str) or not isinstance(optional, bool):
            raise MakeProbeError("GNU Make include record is malformed")
        if raw_path == "/probe/probe.mk":
            continue
        if (
            "\\" in raw_path
            or "//" in raw_path
            or re.search(r"%(?:2e|2f|5c)", raw_path, re.IGNORECASE)
        ):
            raise MakeProbeError(
                f"GNU Make read a noncanonical include {raw_path!r}"
            )
        if raw_path.startswith("/repo/"):
            path = raw_path.removeprefix("/repo/")
        elif raw_path.startswith("/"):
            raise MakeProbeError(
                f"GNU Make read an absolute or dynamic include {raw_path!r}"
            )
        else:
            path = raw_path.removeprefix("./")
        parts = path.split("/")
        if (
            not path
            or any(part in {"", ".", ".."} for part in parts)
            or path.startswith("<WORK>")
        ):
            raise MakeProbeError(
                f"GNU Make read a noncanonical include {raw_path!r}"
            )
        if path in loader.entries:
            entry = loader.entries[path]
            if entry.object_type != "blob" or entry.mode not in {
                "100644",
                "100755",
            }:
                raise MakeProbeError(
                    f"GNU Make include {path!r} is not a tracked regular file"
                )
            continue
        if path.startswith("build/"):
            build_root = build_output.resolve(strict=True)
            candidate = build_output.joinpath(*parts[1:])
            current = build_output
            try:
                for part in parts[1:]:
                    current /= part
                    entry_stat = os.lstat(current)
                    if stat.S_ISLNK(entry_stat.st_mode):
                        raise MakeProbeError(
                            f"GNU Make build include traverses a symlink {raw_path!r}"
                        )
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError:
                # GNU Make itself distinguishes optional from required missing
                # includes in the invocation status checked immediately below.
                continue
            except OSError as error:
                raise MakeProbeError(
                    f"cannot inspect GNU Make build include {raw_path!r}: {error}"
                ) from error
            if (
                build_root not in resolved.parents
                or not resolved.is_file()
            ):
                raise MakeProbeError(
                    f"GNU Make build include escapes its canonical root {raw_path!r}"
                )
            continue
        if any(
            entry.mode == "160000"
            and path.startswith(gitlink.rstrip("/") + "/")
            for gitlink, entry in loader.entries.items()
        ):
            continue
        if optional:
            continue
        raise MakeProbeError(
            f"GNU Make read an untracked include {path!r}"
        )


def _makefile_modes(loader: Any) -> list[dict[str, str]]:
    return [
        {"mode": entry.mode, "path": path}
        for path, entry in sorted(loader.entries.items())
        if path == "Makefile" or path.endswith(".mk")
    ]


def run_probe(
    loader: Any,
    targets: set[str],
    prerequisite_domains: dict[str, dict[str, Any]],
    dynamic_contracts: dict[str, dict[str, Any]],
    *,
    declared_external_names: set[str] | None = None,
    environment_names: set[str] | None = None,
    generated_path_names: set[str] | None = None,
    scratch_root: Path,
    symbolic_recipe_names: set[str] | None = None,
    ambient_undefined_names: set[str] | None = None,
    escaped_literal_names: set[str] | None = None,
    scoped_variable_names: set[str] | None = None,
    trusted_builtin_names: set[str] | None = None,
    trusted_reference_names: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run actual GNU Make for every sealed domain value/origin pair."""
    if not targets:
        return {}
    declared_external_names = (
        set()
        if declared_external_names is None
        else declared_external_names
    )
    environment_names = set() if environment_names is None else environment_names
    generated_path_names = (
        set() if generated_path_names is None else set(generated_path_names)
    )
    symbolic_recipe_names = (
        set()
        if symbolic_recipe_names is None
        else symbolic_recipe_names
    )
    ambient_undefined_names = (
        set()
        if ambient_undefined_names is None
        else set(ambient_undefined_names)
    )
    escaped_literal_names = (
        set()
        if escaped_literal_names is None
        else set(escaped_literal_names)
    )
    scoped_variable_names = (
        set()
        if scoped_variable_names is None
        else set(scoped_variable_names)
    )
    trusted_builtin_names = (
        set()
        if trusted_builtin_names is None
        else set(trusted_builtin_names)
    )
    trusted_reference_names = (
        set()
        if trusted_reference_names is None
        else trusted_reference_names
    )
    trusted_reference_names |= (
        ambient_undefined_names
        | escaped_literal_names
        | scoped_variable_names
        | trusted_builtin_names
    )
    sealed_external_names = (
        declared_external_names | set(prerequisite_domains)
    )
    reference_census = _make_reference_census(loader)
    _external_default_names(loader, set(reference_census[0]))
    unclassified = (
        declared_external_names
        - set(prerequisite_domains)
        - symbolic_recipe_names
    )
    stale_symbolic = symbolic_recipe_names - declared_external_names
    if unclassified or stale_symbolic:
        raise MakeProbeError(
            "external Make variables lack finite domains or symbolic recipe "
            f"authority (unclassified={sorted(unclassified)}, "
            f"stale_symbolic={sorted(stale_symbolic)})"
        )
    scratch_root = _prepare_confined_scratch(loader, scratch_root)
    tools = _ensure_tools()
    with tempfile.TemporaryDirectory(
        prefix="gnu-make-probe-",
        dir=scratch_root,
    ) as directory:
        base = Path(directory)
        tree = base / "tree"
        make_work = base / "make-work"
        command_work = base / "command-work"
        build_output = base / "build-output"
        probe_control = base / "probe-control"
        control = base / "supervisor-control"
        mapping_path = control / "mapping"
        domain_path = control / "domains.pipe"
        event_path = control / "events.pipe"
        tree.mkdir()
        make_work.mkdir()
        command_work.mkdir()
        build_output.mkdir()
        probe_control.mkdir()
        control.mkdir(mode=0o700)
        os.mkfifo(domain_path, mode=0o600)
        os.mkfifo(event_path, mode=0o600)
        _copy_tree(loader, tree)
        cache_namespace = (
            str(Path(loader.root).resolve(strict=True)),
            loader.revision,
            _snapshot_tree_state(
                tree,
                loader.entries,
                loader.revision,
                Path(loader.root),
            ),
        )
        (tree / "build").mkdir()
        interceptor = base / "shell-interceptor"
        interceptor_authority = _compile_interceptor(tree, interceptor)
        root, read_only = _prepare_make_root(base, interceptor)
        read_only.append((tree, "/repo"))
        read_only.append((probe_control, "/probe"))
        if (tree / "tools/scaninc").is_dir():
            scaninc_target = tree / "tools/scaninc/scaninc"
            scaninc_target.touch()
            scaninc_target.chmod(0o755)
            read_only.append((interceptor, "/repo/tools/scaninc/scaninc"))
        if (tree / "scripts/arm_compressing_linker.py").is_file():
            read_only.append(
                (
                    interceptor,
                    "/repo/scripts/arm_compressing_linker.py",
                )
            )
        gbagfx_authority = None
        if (tree / "tools/gbagfx/main.c").is_file():
            gbagfx = base / "gbagfx"
            gbagfx_authority = _compile_gbagfx(tree, gbagfx)
            gbagfx_target = tree / "tools/gbagfx/gbagfx"
            gbagfx_target.touch()
            gbagfx_target.chmod(0o755)
            read_only.append((gbagfx, "/repo/tools/gbagfx/gbagfx"))
        probe_file = probe_control / "probe.mk"
        tracked_inputs = [
            path
            for path, entry in sorted(loader.entries.items())
            if entry.object_type == "blob"
            and entry.mode in {"100644", "100755"}
        ]
        tracked_input_rules = "\n".join(
            " ".join(tracked_inputs[index:index + 100]) + ": ;"
            for index in range(0, len(tracked_inputs), 100)
        )
        tracked_directories = sorted(
            {
                parent.as_posix()
                for path in tracked_inputs
                for parent in Path(path).parents
                if parent.as_posix() != "."
            }
        )
        tracked_directory_rules = "\n".join(
            " ".join(tracked_directories[index:index + 100]) + ": ;"
            for index in range(0, len(tracked_directories), 100)
        )
        probe_file.write_text(
            "/probe/probe.mk: ;\n"
            + tracked_input_rules
            + "\n"
            + tracked_directory_rules
            + "\n"
            + f"override export VO_DOMAIN_COUNT := {len(prerequisite_domains)}\n"
            + "\n".join(
                f"override export VO_DOMAIN_{index} := $({name})"
                for index, name in enumerate(sorted(prerequisite_domains))
            )
            + "\n$(shell /usr/bin/vo-domain-observer)\n",
            encoding="ascii",
        )
        probe_file.chmod(0o400)
        mappings: list[dict[str, Any]] = []
        materialized_mapping_names = _write_mapping(mapping_path, mappings)
        normal_targets = sorted(
            target for target in targets if target != "validation-ownership-check"
        )
        target_groups = [[target] for target in normal_targets]
        if "validation-ownership-check" in targets:
            target_groups.append(["validation-ownership-check"])
        variant_results = []
        run_count = 0
        fallback_values: dict[str, str] | None = None
        probe_budgets: dict[
            tuple[tuple[str, ...], tuple[tuple[str, str, str], ...]],
            float,
        ] = {}

        def check_probe_budget(
            selected_targets: Iterable[str],
            assignments: tuple[tuple[str, str, str], ...] = (),
        ) -> None:
            key = (tuple(selected_targets), assignments)
            now = time.monotonic()
            started = probe_budgets.setdefault(key, now)
            if now - started > MAX_PROBE_SECONDS:
                raise MakeProbeError("GNU Make probe exceeds time bound")

        def invoke(
            selected_targets: list[str],
            *,
            assignments: tuple[tuple[str, str, str], ...] = (),
            cli: tuple[str, str] | None = None,
            database_only: bool = False,
            environment_value: tuple[str, str] | None = None,
        ) -> dict[str, Any]:
            nonlocal run_count, materialized_mapping_names
            run_count += 1
            if run_count > MAX_SANDBOX_RUNS:
                raise MakeProbeError("GNU Make probe exceeds run bound")
            state_assignments = list(assignments)
            if cli is not None:
                state_assignments.append(
                    ("command-line", cli[0], cli[1])
                )
            if environment_value is not None:
                state_assignments.append(
                    (
                        "environment",
                        environment_value[0],
                        environment_value[1],
                    )
                )
            if len({item[1] for item in state_assignments}) != len(
                state_assignments
            ):
                raise MakeProbeError(
                    "GNU Make variant assigns one variable more than once"
                )
            state_assignments.sort(key=lambda item: (item[1], item[0], item[2]))
            canonical_assignments = tuple(state_assignments)
            check_probe_budget(selected_targets, canonical_assignments)
            extra_environment = {
                name: value
                for origin, name, value in canonical_assignments
                if origin == "environment"
            }
            public_gate = selected_targets == ["validation-ownership-check"]
            if public_gate:
                argv = [
                    "/usr/bin/make",
                    "--no-print-directory",
                    "validation-ownership-check",
                ]
            else:
                argv = [
                    "/usr/bin/make",
                    "--no-print-directory",
                    "--debug=v",
                    "--warn-undefined-variables",
                    "--eval",
                    "export VO_COMMAND_COUNT",
                    *(
                        ["--print-data-base", "--question"]
                        if database_only
                        else ["--trace", "-n", "-B"]
                    ),
                    "-f",
                    "Makefile",
                    "-f",
                        "/probe/probe.mk",
                    "MAKE=/bin/vo-make",
                ]
            argv.extend(
                f"{name}={value}"
                for origin, name, value in canonical_assignments
                if origin == "command-line"
            )
            if not public_gate:
                argv.extend(selected_targets)
            for _ in range(MAX_DYNAMIC_PASSES):
                materialized_mapping_names = _write_mapping(
                    mapping_path,
                    mappings,
                    materialized_names=materialized_mapping_names,
                )
                completed = _sandbox_run(
                    root,
                    make_work,
                    argv=argv,
                    domain_names=(
                        sorted(prerequisite_domains)
                        if not public_gate
                        else None
                    ),
                    domain_path=(
                        domain_path
                        if not public_gate
                        else None
                    ),
                    event_path=event_path,
                    environment=_make_environment(
                        len(mappings),
                        extra_environment,
                    ),
                    mapping_path=mapping_path,
                    max_output_bytes=MAX_MAKE_OUTPUT_BYTES,
                    read_only=read_only,
                    writable=[(build_output, "/repo/build")],
                )
                events = completed.events
                mapped_commands = {
                    mapping["command"]
                    for mapping in mappings
                }
                if any(
                    event["match"] >= 0
                    and _event_command(event) not in mapped_commands
                    for event in events
                ):
                    raise MakeProbeError(
                        "shell interceptor matched an unknown supervisor mapping"
                    )
                unknown = [
                    event
                    for event in events
                    if event["match"] < 0
                ]
                if public_gate:
                    public_commands = [
                        _event_command(event)
                        for event in events
                        if _event_command(event) is not None
                    ]
                    if (
                        len(public_commands) != 1
                        or re.fullmatch(
                            r"/usr/bin/python3 -I "
                            r"scripts/validation_ownership/isolated_launcher\.py "
                            r"\\\n\tcheck --repository-root \"/repo\" "
                            r"> /dev/null",
                            public_commands[0],
                        )
                        is None
                    ):
                        raise MakeProbeError(
                            "public validation-ownership-check recipe is not "
                            "the exact isolated checker"
                        )
                if not unknown or public_gate:
                    break
                replay = False
                pending = []
                pending_commands = set()
                for event in unknown:
                    command = _event_command(event)
                    if command is None:
                        raise MakeProbeError(
                            "GNU Make used an unsupported shell invocation: "
                            + repr(event)
                        )
                    matches = [
                        contract
                        for contract in dynamic_contracts.values()
                        if re.fullmatch(
                            contract.get("command_regex", r"(?!x)x"),
                            command,
                            re.DOTALL,
                        )
                    ]
                    if len(matches) != 1:
                        raise MakeProbeError(
                            "GNU Make attempted command execution without "
                            "exactly one sealed contract: " + repr(event)
                        )
                    if (
                        any(item["command"] == command for item in mappings)
                        or command in pending_commands
                    ):
                        continue
                    pending_commands.add(command)
                    pending.append(
                        (
                            command,
                            matches[0],
                            _event_direct_arguments(event),
                        )
                    )
                    if len(pending) > MAX_PENDING_COMMANDS:
                        raise MakeProbeError(
                            "GNU Make pending command fanout exceeds count bound"
                        )
                if any(
                    contract["id"] == "banim-scaninc-inputs"
                    for _, contract, _ in pending
                ):
                    _compile_scaninc(tree, command_work)
                parallel: list[tuple[str, dict[str, Any], list[str] | None]] = []
                sequential: list[tuple[str, dict[str, Any], list[str] | None]] = []
                for item in pending:
                    command, contract, _ = item
                    if _is_parallel_registered_command(
                        cache_namespace=cache_namespace,
                        command=command,
                        contract=contract,
                        environment=extra_environment,
                    ):
                        parallel.append(item)
                    else:
                        sequential.append(item)
                outputs: dict[str, bytes] = {}
                output_bytes = sum(
                    len(mapping["command"].encode("utf-8"))
                    + len(mapping["output"])
                    for mapping in mappings
                )

                def retain_output(command: str, output: bytes) -> None:
                    nonlocal output_bytes
                    output_bytes += len(command.encode("utf-8")) + len(output)
                    if output_bytes > MAX_MAPPING_BYTES:
                        raise MakeProbeError(
                            "GNU Make mapped command output exceeds byte bound"
                        )
                    outputs[command] = output

                for start in range(
                    0,
                    len(parallel),
                    MAX_PARALLEL_REGISTERED_COMMANDS,
                ):
                    batch = parallel[
                        start:start + MAX_PARALLEL_REGISTERED_COMMANDS
                    ]
                    with ThreadPoolExecutor(
                        max_workers=len(batch),
                    ) as executor:
                        futures = [
                            executor.submit(
                                _execute_registered_command,
                                command,
                                contract,
                                base=base,
                                build_output=build_output,
                                cache_namespace=cache_namespace,
                                command_work=command_work,
                                direct_arguments=direct_arguments,
                                tree=tree,
                                environment=extra_environment,
                            )
                            for command, contract, direct_arguments in batch
                        ]
                        for (command, _, _), future in zip(batch, futures):
                            retain_output(command, future.result())
                for command, contract, direct_arguments in sequential:
                    retain_output(
                        command,
                        _execute_registered_command(
                            command,
                            contract,
                            base=base,
                            build_output=build_output,
                            cache_namespace=cache_namespace,
                            command_work=command_work,
                            direct_arguments=direct_arguments,
                            tree=tree,
                            environment=extra_environment,
                        ),
                    )
                for command, contract, _ in pending:
                    output = outputs[command]
                    mappings.append(
                        {
                            "command": command,
                            "contract": contract,
                            "output": output,
                            "suppressed_recipe": False,
                        }
                    )
                    if len(mappings) > MAX_MAPPING_COUNT:
                        raise MakeProbeError(
                            "Make command mapping exceeds count bound"
                        )
                    replay |= (
                        contract["resolved_value"] is None
                        or bool(output)
                    )
                if not replay:
                    break
            else:
                raise MakeProbeError(
                    "GNU Make dynamic command expansion exceeds pass bound"
                )
            combined = (
                _strict_utf8(completed.stdout, "GNU Make stdout")
                + "\n"
                + _strict_utf8(completed.stderr, "GNU Make stderr")
            )
            undefined_names = _undefined_variable_names(combined)
            unknown_undefined = undefined_names - (
                sealed_external_names | trusted_reference_names
            )
            if unknown_undefined:
                raise MakeProbeError(
                    "GNU Make evaluated undefined variables without sealed "
                    f"authority: {sorted(unknown_undefined)}"
                )
            if public_gate:
                graph = {"validation-ownership-check": []}
                includes = [{"optional": False, "path": "Makefile"}]
                traces = {
                    "validation-ownership-check": {
                        "commands": [
                            event["arguments"][2]
                            for event in events
                            if len(event["arguments"]) == 3
                            and event["arguments"][1] == "-c"
                        ],
                        "reasons": [],
                        "sources": ["Makefile"],
                    }
                }
            else:
                graph, includes = _target_graph(combined)
                traces = _trace_records(combined)
                _validate_includes(includes, loader, build_output)
            if completed.returncode != 0 and not (
                database_only and completed.returncode == 1
            ):
                raise MakeProbeError(
                    "GNU Make authority probe failed: " + _normalize(combined)
                )
            closures = _closures(selected_targets, graph)
            retained_targets = {
                item
                for closure in closures.values()
                for item in closure
            }
            traces = {
                item: traces[item]
                for item in retained_targets
                if item in traces
            }
            sources = {"Makefile"}
            sources.update(item["path"] for item in includes)
            for trace in traces.values():
                sources.update(trace["sources"])
            return {
                "assignments": canonical_assignments,
                "argv": argv,
                "closures": closures,
                "domain_values": (
                    completed.domain_values
                    if not public_gate
                    else {}
                ),
                "environment_assignment": environment_value,
                "database_sha256": (
                    hashlib.sha256(
                        _database_semantics(combined).encode("utf-8")
                    ).hexdigest()
                    if database_only
                    else None
                ),
                "includes": includes,
                "sources": tuple(sorted(sources)),
                "undefined_names": sorted(undefined_names),
                "origin": (
                    "command-line"
                    if cli is not None
                    else "environment"
                    if environment_value is not None
                    else "fallback"
                ),
                "requested_targets": list(selected_targets),
                "traces": traces,
                "variable": None if cli is None else cli[0],
                "value": (
                    cli[1]
                    if cli is not None
                    else environment_value[1]
                    if environment_value is not None
                    else None
                ),
            }

        baseline_by_group = []
        for group in target_groups:
            baseline_by_group.append(invoke(group))
        loaded_sources = {"Makefile"}
        for baseline in baseline_by_group:
            loaded_sources.update(
                item["path"] for item in baseline["includes"]
            )
            for trace in baseline["traces"].values():
                loaded_sources.update(trace["sources"])
        external_default_names = _external_default_names(
            loader,
            loaded_sources,
        )
        undeclared_defaults = external_default_names - sealed_external_names
        if undeclared_defaults:
            raise MakeProbeError(
                "Make external defaults lack sealed ambient authority: "
                f"{sorted(undeclared_defaults)}"
            )
        target_variable_usage = {
            group[0]: _target_variable_usage(
                baseline,
                reference_census,
            )
            for group, baseline in zip(target_groups, baseline_by_group)
            if group != ["validation-ownership-check"]
        }
        baseline_by_target = {
            group[0]: baseline
            for group, baseline in zip(target_groups, baseline_by_group)
            if group != ["validation-ownership-check"]
        }
        baseline_default_names = {
            target: _external_default_names(loader, _variant_sources(baseline))
            for target, baseline in baseline_by_target.items()
        }
        graph_shaping_symbolic = {
            name
            for usage in target_variable_usage.values()
            for name in usage["graph"] & symbolic_recipe_names
        }
        if graph_shaping_symbolic:
            raise MakeProbeError(
                "symbolic Make variables can shape targets, prerequisites, "
                "includes, conditionals, or eval output and require finite "
                f"domains: {sorted(graph_shaping_symbolic)}"
            )
        target_domain_names = {
            target: usage["graph"] & set(prerequisite_domains)
            for target, usage in target_variable_usage.items()
        }
        target_environment_sensitive_names = {
            target: _environment_sensitive_names(
                defaults=baseline_default_names[target],
                usage=target_variable_usage[target],
                undefined_names=baseline_by_target[target]["undefined_names"],
                ambient_undefined_names=ambient_undefined_names,
                environment_names=environment_names,
            )
            for target in baseline_by_target
        }

        def target_semantics(
            variant: dict[str, Any],
            target: str,
        ) -> dict[str, Any] | None:
            closure = variant["closures"].get(target)
            if closure is None:
                return None
            return {
                "closure": closure,
                "includes": variant["includes"],
                "recipes": {
                    item: variant["traces"][item]
                    for item in closure
                    if item in variant["traces"]
                },
            }

        def target_goal_semantics(
            variant: dict[str, Any],
            target: str,
        ) -> dict[str, Any] | None:
            semantics = target_semantics(variant, target)
            if semantics is None:
                return None
            return {
                "closure": semantics["closure"],
                "recipes": semantics["recipes"],
            }

        combined_orders = [normal_targets]
        combined_baselines = [
            invoke(order)
            for order in combined_orders
        ] if normal_targets else []
        target_goal_sensitive = {
            target: any(
                target_goal_semantics(combined, target)
                != target_goal_semantics(
                    baseline_by_group[target_groups.index([target])],
                    target,
                )
                for combined in combined_baselines
            )
            for target in normal_targets
        }

        def reuse_baseline(
            baseline: dict[str, Any],
            database_sha256: str | None,
            origin: str,
            name: str,
            value: str,
        ) -> dict[str, Any]:
            candidate = dict(baseline)
            assignment = (origin, name, value)
            domain_values = dict(baseline["domain_values"])
            domain_values[name] = value
            candidate.update(
                {
                    "assignments": (assignment,),
                    "database_sha256": database_sha256,
                    "domain_values": domain_values,
                    "environment_assignment": (
                        [name, value]
                        if origin == "environment"
                        else None
                    ),
                    "origin": origin,
                    "same_as_fallback": True,
                    "variable": name,
                    "value": value,
                }
            )
            return candidate

        for group, baseline in zip(
            target_groups,
            baseline_by_group,
        ):
            variant_results.append(baseline)
            if group == ["validation-ownership-check"]:
                continue
            for name in sorted(target_domain_names[group[0]]):
                target_usage = target_variable_usage[group[0]]
                domain = prerequisite_domains[name]
                values = (
                    domain["values"]
                    if domain["kind"] == "explicit"
                    else [baseline["domain_values"][name]]
                )
                for value in values:
                    if (
                        baseline["domain_values"][name] == value
                        and name not in target_usage["introspection"]
                    ):
                        candidate = reuse_baseline(
                            baseline,
                            None,
                            "command-line",
                            name,
                            value,
                        )
                    else:
                        candidate = invoke(
                            group,
                            cli=(name, value),
                            database_only=True,
                        )
                        signature_matches = (
                            _variant_discovery_signature(candidate)
                            == _variant_discovery_signature(baseline)
                        )
                        safe_reuse = signature_matches and (
                            name not in target_usage["recipe"]
                            or (
                                baseline["domain_values"][name] == value
                                and name not in target_usage["introspection"]
                            )
                        )
                        if safe_reuse:
                            candidate = reuse_baseline(
                                baseline,
                                candidate["database_sha256"],
                                "command-line",
                                name,
                                value,
                            )
                        else:
                            candidate = invoke(group, cli=(name, value))
                    variant_results.append(candidate)
                    if name not in target_environment_sensitive_names[group[0]]:
                        continue
                    if (
                        baseline["domain_values"][name] == value
                        and name not in target_usage["introspection"]
                    ):
                        candidate = reuse_baseline(
                            baseline,
                            None,
                            "environment",
                            name,
                            value,
                        )
                    else:
                        candidate = invoke(
                            group,
                            environment_value=(name, value),
                            database_only=True,
                        )
                        signature_matches = (
                            _variant_discovery_signature(candidate)
                            == _variant_discovery_signature(baseline)
                        )
                        safe_reuse = signature_matches and (
                            name not in target_usage["recipe"]
                            or (
                                baseline["domain_values"][name] == value
                                and name not in target_usage["introspection"]
                            )
                        )
                        if safe_reuse:
                            candidate = reuse_baseline(
                                baseline,
                                candidate["database_sha256"],
                                "environment",
                                name,
                                value,
                            )
                        else:
                            candidate = invoke(
                                group,
                                environment_value=(name, value),
                            )
                    variant_results.append(candidate)

        variants_by_target: dict[
            str,
            dict[tuple[tuple[str, str, str], ...], dict[str, Any]],
        ] = {
            target: {} for target in normal_targets
        }
        state_parents: dict[
            tuple[str, tuple[tuple[str, str, str], ...]],
            tuple[tuple[str, str, str], ...],
        ] = {}
        for target in normal_targets:
            for variant in variant_results:
                if variant["requested_targets"] != [target]:
                    continue
                state = tuple(variant["assignments"])
                variants_by_target[target][state] = variant
                if state:
                    state_parents[(target, state)] = ()
        state_count = sum(
            len(variants) for variants in variants_by_target.values()
        )
        if state_count > MAX_VARIANT_STATES:
            raise MakeProbeError(
                "GNU Make variant fixed point exceeds state bound"
            )
        worklist = sorted(
            (
                target,
                state,
            )
            for target, variants in variants_by_target.items()
            for state in variants
        )
        work_index = 0
        context_state_count = 0
        discovered_sources: set[str] = set()
        discovered_domains: set[str] = set()
        target_external_default_names = {
            target: set() for target in normal_targets
        }
        target_variable_usage = {
            target: {
                "all": set(),
                "graph": set(),
                "introspection": set(),
                "recipe": set(),
                "recipe_only": set(),
            }
            for target in normal_targets
        }

        while work_index < len(worklist):
            target, state = worklist[work_index]
            work_index += 1
            check_probe_budget([target], state)
            variant = variants_by_target[target][state]
            sources = _variant_sources(variant)
            usage = _target_variable_usage(variant, reference_census)
            defaults = _external_default_names(loader, sources)
            undeclared_defaults = defaults - sealed_external_names
            if undeclared_defaults:
                raise MakeProbeError(
                    "Make external defaults lack sealed ambient authority: "
                    f"{sorted(undeclared_defaults)}"
                )
            graph_shaping_symbolic = (
                usage["graph"] & symbolic_recipe_names
            )
            if graph_shaping_symbolic:
                raise MakeProbeError(
                    "symbolic Make variables can shape targets, "
                    "prerequisites, includes, conditionals, or eval output "
                    "and require finite domains: "
                    f"{sorted(graph_shaping_symbolic)}"
                )
            for field in target_variable_usage[target]:
                target_variable_usage[target][field].update(usage[field])
            target_external_default_names[target].update(
                defaults & usage["all"]
            )
            target_environment_sensitive_names[target].update(
                _environment_sensitive_names(
                    defaults=defaults,
                    usage=usage,
                    undefined_names=variant["undefined_names"],
                    ambient_undefined_names=ambient_undefined_names,
                    environment_names=environment_names,
                )
            )
            observed_domains = usage["all"] & set(prerequisite_domains)
            graph_domains = usage["graph"] & set(prerequisite_domains)
            discovered_sources.update(sources)
            discovered_domains.update(graph_domains)
            if len(discovered_sources) > MAX_DISCOVERED_SOURCES:
                raise MakeProbeError(
                    "GNU Make variant fixed point exceeds source bound"
                )
            if len(discovered_domains) > MAX_DISCOVERED_DOMAINS:
                raise MakeProbeError(
                    "GNU Make variant fixed point exceeds domain bound"
                )

            if not state:
                expansion_domains = graph_domains
            else:
                parent_state = state_parents[(target, state)]
                parent = variants_by_target[target][parent_state]
                parent_sources = _variant_sources(parent)
                parent_usage = _target_variable_usage(
                    parent,
                    reference_census,
                )
                new_source_usage = _source_variable_usage(
                    sources - parent_sources,
                    reference_census,
                )
                expansion_domains = (
                    new_source_usage["graph"] & set(prerequisite_domains)
                )
                expansion_domains.update(
                    graph_domains
                    - (parent_usage["graph"] & set(prerequisite_domains))
                )

            assigned_names = {item[1] for item in state}
            for name in sorted(expansion_domains - assigned_names):
                domain = prerequisite_domains[name]
                values = (
                    domain["values"]
                    if domain["kind"] == "explicit"
                    else [variant["domain_values"][name]]
                )
                origins = ["command-line"]
                if name in target_environment_sensitive_names[target]:
                    origins.append("environment")
                for value in values:
                    for origin in origins:
                        assignment = (origin, name, value)
                        child_state = tuple(
                            sorted(
                                (*state, assignment),
                                key=lambda item: (
                                    item[1],
                                    item[0],
                                    item[2],
                                ),
                            )
                        )
                        if child_state in variants_by_target[target]:
                            continue
                        if len(child_state) > MAX_CONTEXT_DEPTH:
                            raise MakeProbeError(
                                "GNU Make variant fixed point exceeds "
                                "context depth bound"
                            )
                        context_state_count += 1
                        if context_state_count > MAX_CONTEXT_STATES:
                            raise MakeProbeError(
                                "GNU Make variant fixed point exceeds "
                                "combination bound"
                            )
                        state_count += 1
                        if state_count > MAX_VARIANT_STATES:
                            raise MakeProbeError(
                                "GNU Make variant fixed point exceeds "
                                "state bound"
                            )
                        candidate = invoke(
                            [target],
                            assignments=state,
                            cli=(
                                (name, value)
                                if origin == "command-line"
                                else None
                            ),
                            environment_value=(
                                (name, value)
                                if origin == "environment"
                                else None
                            ),
                        )
                        variants_by_target[target][child_state] = candidate
                        state_parents[(target, child_state)] = state
                        variant_results.append(candidate)
                        worklist.append((target, child_state))

        make_inputs = _makefile_modes(loader)
        result = {}
        for target in sorted(targets):
            target_variants = []
            baseline_semantics = None
            all_closure_items = set()
            target_undefined_names = set()
            for variant in variant_results:
                if variant["requested_targets"] != [target]:
                    continue
                if target not in variant["closures"]:
                    continue
                closure = variant["closures"][target]
                all_closure_items.update(closure)
                target_undefined_names.update(variant["undefined_names"])
                semantics = (
                    baseline_semantics
                    if variant.get("same_as_fallback")
                    else {
                        "closure": closure,
                        "includes": variant["includes"],
                        "recipes": {
                            item: variant["traces"][item]
                            for item in closure
                            if item in variant["traces"]
                        },
                    }
                )
                if semantics is None:
                    raise MakeProbeError(
                        f"GNU Make target {target!r} lacks fallback authority"
                    )
                semantics_bytes = json.dumps(
                    semantics,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                semantic_hash = hashlib.sha256(semantics_bytes).hexdigest()
                record = {
                    "assignments": [
                        list(item) for item in variant["assignments"]
                    ],
                    "environment_assignment": (
                        list(variant["environment_assignment"])
                        if variant["environment_assignment"] is not None
                        else None
                    ),
                    "database_sha256": variant["database_sha256"],
                    "origin": variant["origin"],
                    "semantic_sha256": semantic_hash,
                    "value": variant["value"],
                    "variable": variant["variable"],
                }
                if variant["origin"] == "fallback":
                    baseline_semantics = semantics
                    record["semantics"] = semantics
                elif semantics != baseline_semantics:
                    record["semantics"] = semantics
                target_variants.append(record)
            usage = target_variable_usage.get(
                target,
                {
                    "all": set(),
                    "graph": set(),
                    "recipe": set(),
                    "recipe_only": set(),
                },
            )
            observed_generated_paths = {
                path
                for path in generated_path_names
                if path in all_closure_items
            }
            observed_symbolic = (
                usage["recipe_only"] & symbolic_recipe_names
            )
            target_observed_undefined = (
                target_undefined_names & ambient_undefined_names
            )
            target_observed_names = usage["all"] | target_undefined_names
            handled_names = (
                target_observed_undefined
                | (target_observed_names & trusted_builtin_names)
                | (target_observed_names & scoped_variable_names)
                | (target_observed_names & escaped_literal_names)
            )
            result[target] = {
                "cycles": [],
                "dynamic_dependencies": [],
                "effective_exported_environment": {},
                "global_exported_environment": {},
                "prerequisite_domain_census": {
                    "generated_paths": sorted(observed_generated_paths),
                    "unconstrained": [],
                    "used": sorted(
                        usage["all"] & set(prerequisite_domains)
                    ),
                },
                "record": {
                    "interceptor": interceptor_authority,
                    "generated_gbagfx": gbagfx_authority,
                    "dynamic_commands": [
                        {
                            "authority_id": mapping["contract"]["id"],
                            "command": _normalize(mapping["command"]),
                            "output_sha256": hashlib.sha256(
                                mapping["output"]
                            ).hexdigest(),
                        }
                        for mapping in mappings
                        if mapping["contract"] is not None
                    ],
                    "make_inputs": make_inputs,
                    "probe_tools": tools,
                    "snapshot_sha256": cache_namespace[2],
                    "sanitized_environment": _make_environment(
                        len(mappings),
                        {},
                    ),
                    "standalone_goal_sensitive": target_goal_sensitive.get(
                        target,
                        False,
                    ),
                    "symbolic_recipe_names": sorted(observed_symbolic),
                    "variants": target_variants,
                },
                "target": target,
                "transitive": sorted(
                    all_closure_items - {target}
                ),
                "unknown_dynamic_prerequisites": [],
                "variable_census": {
                    "ambient_undefined": sorted(
                        target_observed_undefined
                    ),
                    "escaped_literals": sorted(
                        target_observed_names & escaped_literal_names
                    ),
                    "external_defaults": sorted(
                        target_external_default_names.get(target, set())
                    ),
                    "handled_names": sorted(handled_names),
                    "observed_undefined": sorted(target_undefined_names),
                    "scoped_variables": sorted(
                        target_observed_names & scoped_variable_names
                    ),
                    "trusted_builtins": sorted(
                        target_observed_names & trusted_builtin_names
                    ),
                    "unbound": [],
                },
            }
        return result
