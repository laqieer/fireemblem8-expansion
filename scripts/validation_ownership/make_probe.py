"""Reusable, aggregate-bounded GNU Make and generated-source authority.

PR186's graph/domain planner is intentionally not part of this foundation.
Callers share one ProbeSession across every target, variant and registry probe.
"""

from __future__ import annotations

import errno
import hashlib
import os
import platform
import re
import secrets
import shutil
import signal
import stat
import struct
import sys
from dataclasses import dataclass
from functools import wraps
from pathlib import Path, PurePosixPath
from threading import get_ident, main_thread

from .authority import AuthorityLoader, ENVIRONMENT, Snapshot, encoded, parse_json, relative_path
from .budget import Limits, MakeProbeError, NAMESPACE_LAUNCHER, ProbeBudget, text
from .lifecycle import cleanup_scope, finish_cleanup


TRUSTED_ROOT = Path(__file__).resolve().parent
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
TARGET = re.compile(r"[A-Za-z0-9_./+%-]+\Z")
MAX_DYNAMIC_PASSES = 64
ALIASES = (
    "/bin/sh", "/bin/bash",
    *("/usr/bin/" + name for name in (
        "arm-none-eabi-as", "arm-none-eabi-gcc", "cc", "find", "g++", "gcc",
        "iconv", "mkdir", "mv", "printf", "python3", "rm", "sed", "uname",
        "true", "echo",
    )),
)


def terminal_failure(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if self.base is None or self.snapshot is None:
            raise MakeProbeError("probe session is not active")
        if get_ident() != self.owner_thread:
            self.budget.failed = True
            raise MakeProbeError("a probe session has one bounded execution worker")
        try:
            return method(self, *args, **kwargs)
        except BaseException as error:
            self.budget.failed = True
            finish_cleanup([self.budget.close], primary=error)
            raise
    return guarded


@dataclass(frozen=True)
class Command:
    """A sealed argv plus its exact candidate code/data/filesystem authority."""

    argv: tuple[str, ...]
    code: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    directories: tuple[str, ...] = ()
    native_tool: NativeTool | None = None
    outputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedFile:
    path: str
    data: bytes
    mode: int


@dataclass(frozen=True)
class ProcessOutput:
    stdout: bytes
    stderr: bytes
    consumed: tuple[str, ...]
    code_consumed: tuple[str, ...]
    artifact: bytes | None = None
    generated: tuple[GeneratedFile, ...] = ()


@dataclass(frozen=True)
class NativeTool:
    """A session-issued, validated ELF; never a Make-capsule executable."""

    path: Path
    digest: str
    inputs: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class MakeObservation:
    target: str
    semantics: dict
    execution_digest: str
    semantic_digest: str
    stdout: bytes
    stderr: bytes
    events: tuple[dict, ...]


class Frames:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.offset = 0

    def take(self, size: int):
        if size > len(self.raw) - self.offset:
            raise MakeProbeError("truncated native observation/event frame")
        result = self.raw[self.offset:self.offset + size]
        self.offset += size
        return result

    def integer(self):
        return int.from_bytes(self.take(4), "little")

    def string(self, boundary):
        return text(self.take(self.integer()), boundary)

    def done(self):
        if self.offset != len(self.raw):
            raise MakeProbeError("trailing native observation/event bytes")


def _command_hash(command: str) -> str:
    result = 14695981039346656037
    for byte in command.encode("utf-8"):
        result = ((result ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return f"{result:016x}"


def _event_command(event: dict) -> str:
    arguments = event["arguments"]
    if arguments[0] in {"/bin/sh", "/bin/bash"}:
        if len(arguments) != 3 or arguments[1] not in {"-c", "-ec"}:
            raise MakeProbeError("SHELL/.SHELLFLAGS escaped the interceptor protocol")
        return arguments[2]
    program = arguments[0]
    if program.startswith("/usr/bin/") and program != "/usr/bin/make":
        program = program.removeprefix("/usr/bin/")
    def quote(value):
        if not value:
            return '""'
        if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
            return value
        return "'" + value.replace("'", "'\"'\"'") + "'"
    return " ".join(quote(value) for value in (program, *arguments[1:]))


def _read_events(raw: bytes, *, expected_mapping_count: int):
    reader = Frames(raw)
    events = []
    while reader.offset < len(raw):
        match = struct.unpack("<i", reader.take(4))[0]
        count = reader.integer()
        hash_value = int.from_bytes(reader.take(8), "little")
        argc = reader.integer()
        if match not in {-1, 0} or count != expected_mapping_count or not 1 <= argc <= 1024:
            raise MakeProbeError("invalid trusted interceptor frame")
        event = {
            "match": match, "mapping_count": count,
            "arguments": [reader.string("interceptor argv") for _ in range(argc)],
        }
        if int(_command_hash(_event_command(event)), 16) != hash_value:
            raise MakeProbeError("interceptor command/hash mismatch")
        events.append(event)
    return events


def _read_observation(raw: bytes, target: str, variables: tuple[str, ...]):
    reader = Frames(raw)
    def domains():
        count = reader.integer()
        if count != len(variables):
            raise MakeProbeError("native Make domain count mismatch")
        result = {}
        for expected in variables:
            name = reader.string("Make domain name")
            if name != expected or name in result:
                raise MakeProbeError("native Make domain identity mismatch")
            result[name] = {
                field: reader.string(f"Make domain {field}")
                for field in ("value", "origin", "flavor")
            }
        return result

    if reader.take(8) != b"VOMAKE1\0":
        raise MakeProbeError("missing authenticated native Make observation")
    count = reader.integer()
    if not 1 <= count <= 4096:
        raise MakeProbeError("native Make node count exceeds contract")
    files = []
    for _ in range(count):
        name = reader.string("Make target")
        source = reader.string("Make recipe source")
        recipe = reader.string("Make recipe")
        shell = reader.string("Make SHELL")
        flags = reader.string("Make .SHELLFLAGS")
        if shell not in {"/bin/sh", "/bin/bash"} or flags not in {"-c", "-ec"}:
            raise MakeProbeError("SHELL/.SHELLFLAGS escaped the trusted execution contract")
        prerequisites = []
        number = reader.integer()
        if number > 4096:
            raise MakeProbeError("native Make prerequisite count exceeds contract")
        for _ in range(number):
            dependency = reader.string("Make prerequisite")
            order_only = reader.integer()
            if order_only not in {0, 1}:
                raise MakeProbeError("malformed native prerequisite flag")
            prerequisites.append({"name": dependency, "order_only": bool(order_only)})
        files.append({
            "target": name, "source": source.removeprefix("/repo/"),
            "recipe": recipe, "prerequisites": prerequisites,
            "variables": domains(),
        })
    if files[0]["target"] != target:
        raise MakeProbeError("native observation did not bind the requested target")
    global_domains = domains()
    reader.done()
    return {"files": files, "domains": global_domains}


def _mkdir_target(root: Path, target: str, directory=False):
    destination = root / target.lstrip("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if directory:
        destination.mkdir(exist_ok=True)
    else:
        destination.touch()
    return destination


def _remove_owned_tree(path):
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass


def _trusted_runtime_bytes(path: str, budget: ProbeBudget):
    requested = PurePosixPath(path)
    if not requested.is_absolute() or str(requested) != path or ".." in requested.parts:
        raise MakeProbeError("noncanonical trusted runtime path")
    roots = ("/usr/bin/", "/usr/lib/", "/usr/lib64/", "/lib/", "/lib64/")
    if not path.startswith(roots):
        raise MakeProbeError(f"runtime is outside the trusted system tool/library roots: {path}")
    resolved = Path(path).resolve(strict=True)
    if not resolved.as_posix().startswith(roots):
        raise MakeProbeError(f"runtime is outside the trusted system tool/library roots: {path}")
    for entry in {Path(path), *Path(path).parents, resolved, *resolved.parents}:
        mode = entry.lstat()
        if mode.st_uid != 0 or (
            not stat.S_ISLNK(mode.st_mode) and mode.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise MakeProbeError(f"mutable/untrusted runtime input: {path}")
    return budget.read_bytes(resolved, "control")


def _make_interpreter(binary: bytes):
    if len(binary) < 64 or binary[:6] != b"\x7fELF\x02\x01" or binary[18:20] != b"\x3e\0":
        raise MakeProbeError("trusted Make is not a Linux x86-64 ELF")
    start = int.from_bytes(binary[32:40], "little")
    size = int.from_bytes(binary[54:56], "little")
    count = int.from_bytes(binary[56:58], "little")
    if size != 56 or not 1 <= count <= 64 or start + size * count > len(binary):
        raise MakeProbeError("trusted Make ELF program headers are invalid")
    interpreter = None
    for index in range(count):
        header = binary[start + index * size:start + (index + 1) * size]
        if int.from_bytes(header[:4], "little") != 3:
            continue
        offset = int.from_bytes(header[8:16], "little")
        length = int.from_bytes(header[32:40], "little")
        if interpreter is not None or not 2 <= length <= 4096 or offset + length > len(binary):
            raise MakeProbeError("trusted Make ELF interpreter is invalid")
        value = binary[offset:offset + length]
        if not value.endswith(b"\0") or b"\0" in value[:-1]:
            raise MakeProbeError("trusted Make ELF interpreter is not one pathname")
        interpreter = text(value[:-1], "trusted Make ELF interpreter", "ascii")
    if interpreter is None:
        raise MakeProbeError("trusted Make requires an ELF interpreter")
    return interpreter


def _make_runtime(budget: ProbeBudget):
    binary = _trusted_runtime_bytes("/usr/bin/make", budget)
    interpreter = _make_interpreter(binary)
    runtime = {
        "/usr/bin/make": binary,
        interpreter: _trusted_runtime_bytes(interpreter, budget),
    }
    # Only the trusted interpreter sees the trusted system Make, never a
    # candidate ELF, preload, library path, ldd script or repository cwd.
    result = budget.run([interpreter, "--list", "/usr/bin/make"], env=ENVIRONMENT, cwd=Path("/"))
    if result.returncode:
        raise MakeProbeError(f"cannot resolve trusted Make runtime: {result.stderr!r}")
    for row in text(result.stdout, "trusted Make runtime listing", "ascii").splitlines():
        row = row.strip()
        if re.fullmatch(r"linux-vdso\.so\.1 \(0x[0-9a-f]+\)", row):
            continue
        match = re.fullmatch(r"(?:[A-Za-z0-9_.+-]+ => )?(/[^ \t]+) \(0x[0-9a-f]+\)", row)
        if match is None or len(runtime) >= 64:
            raise MakeProbeError("unresolved/malformed trusted Make runtime closure")
        path = match[1]
        if path not in runtime:
            runtime[path] = _trusted_runtime_bytes(path, budget)
    if len(runtime) < 3:
        raise MakeProbeError("trusted Make runtime closure is incomplete")
    return tuple(sorted(runtime.items()))


def _scratch_directory(loader, requested):
    root = Path(os.path.abspath(requested))
    configured = loader.scratch_root
    if configured is None:
        try:
            relative = root.relative_to(loader.root)
        except ValueError as error:
            raise MakeProbeError("scratch must be below the authority root") from error
        if not relative.parts:
            raise MakeProbeError("scratch cannot be the authority root")
        base = loader.root
        parts = relative.parts
    else:
        if root != Path(os.path.abspath(configured)):
            raise MakeProbeError("scratch differs from trusted external scratch root")
        base = root
        parts = ()
    descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptors = [descriptor]
    created = []
    owned = []
    failure = None
    cursor = base
    try:
        for part in parts:
            cursor /= part
            if cursor.relative_to(loader.root).as_posix() in loader.entries:
                raise MakeProbeError("scratch traverses a tracked candidate object")
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
                created.append(cursor)
                owned.append((descriptor, part))
            except FileExistsError:
                pass
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor,
            )
            descriptors.append(following)
            descriptor = following
        name = "probe-" + secrets.token_hex(16)
        os.mkdir(name, 0o700, dir_fd=descriptor)
        owned.append((descriptor, name))
        return root / name, created
    except BaseException as error:
        failure = error
        for parent, name in reversed(owned):
            try:
                os.rmdir(name, dir_fd=parent)
            except FileNotFoundError:
                pass
            except OSError as cleanup:
                if hasattr(error, "add_note"):
                    error.add_note(f"owned scratch cleanup failed for {name!r}: {cleanup}")
        if isinstance(error, OSError):
            raise MakeProbeError("unsafe scratch directory component") from error
        raise
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError as cleanup:
                if failure is None:
                    raise
                if hasattr(failure, "add_note"):
                    failure.add_note(f"scratch descriptor cleanup failed: {cleanup}")


class ProbeSession:
    """The only execution authority; one lifetime, deadline, cache and queue."""

    def __init__(self, loader: AuthorityLoader, *, scratch_root: Path, budget: ProbeBudget):
        if not isinstance(loader, AuthorityLoader):
            raise MakeProbeError("probe requires its exact-tree authority loader")
        if (
            not isinstance(budget, ProbeBudget) or budget is not loader.budget
            or budget is not loader.entries.budget
        ):
            raise MakeProbeError("probe session requires its authority's report budget")
        budget.remaining()
        self.loader = loader
        self.scratch_root = Path(scratch_root)
        self.budget = budget
        self.base = None
        self.created = []
        self.cache = {}
        self.mappings = {}
        self.native_tools = {}
        self.handlers = {}
        self.snapshot = None
        self.make_runtime = ()
        self.serial = 0
        self.processes_used = 0
        self.syscalls_used = 0
        self.files_created = 0
        self.owner_thread = get_ident()

    def __enter__(self):
        if self.budget.session_started:
            raise MakeProbeError("report budget already owns a probe session lifetime")
        self.budget.remaining()
        self.budget.session_started = True
        try:
            if get_ident() != main_thread().ident:
                raise MakeProbeError("probe lifetime requires the main execution worker")
            self.owner_thread = get_ident()
            self.budget.remaining()
            if platform.system() != "Linux" or platform.machine() != "x86_64":
                raise MakeProbeError("ownership probe requires Linux x86-64")
            for sig in (signal.SIGINT, signal.SIGTERM):
                self.handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._interrupt)
            # Deliver interruption only after the allocated paths have an owner.
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, self.handlers)
            try:
                self.base, self.created = _scratch_directory(self.loader, self.scratch_root)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            self._tools()
            self.snapshot = Snapshot(self.loader, self.budget)
            self.tree = self.base / "tree"
            self.tree.mkdir()
            self.snapshot.materialize(self.tree, self.snapshot.files, self.budget)
            self._compile_interceptor()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def _interrupt(self, signum, frame):
        raise KeyboardInterrupt(f"ownership probe interrupted by signal {signum}")

    def __exit__(self, kind, value, traceback):
        def clear_state():
            self.cache.clear()
            self.mappings.clear()
            self.native_tools.clear()
            self.make_runtime = ()
            self.snapshot = None
            self.loader.live_modes.clear()
        def remove_base():
            if self.base is not None:
                _remove_owned_tree(self.base)
                self.base = None
        def remove_parent(path):
            try:
                path.rmdir()
            except OSError as error:
                if error.errno not in {errno.ENOENT, errno.EEXIST, errno.ENOTEMPTY}:
                    raise
        def release_parents():
            if self.base is None:
                self.created.clear()
        try:
            finish_cleanup([
                self.budget.close, clear_state, remove_base,
                *(lambda path=path: remove_parent(path) for path in reversed(self.created)),
                release_parents,
            ], primary=value, handlers=self.handlers)
        except BaseException:
            self.budget.failed = True
            raise

    def _tools(self):
        for path in ("/usr/bin/make", "/usr/bin/unshare", "/usr/bin/python3", "/usr/bin/cc"):
            if not Path(path).is_file():
                raise MakeProbeError(f"missing required ownership probe tool: {path}")
        version = self.budget.run(["/usr/bin/make", "--version"], env=ENVIRONMENT)
        if version.returncode or version.stdout.splitlines()[0] != b"GNU Make 4.3":
            raise MakeProbeError("native observation ABI requires GNU Make 4.3")
        self.make_runtime = _make_runtime(self.budget)
        python = self.budget.run(
            ["/usr/bin/python3", "-I", "-S", "-B", "-c",
             "import sys; print('%d.%d' % sys.version_info[:2])"],
            env=ENVIRONMENT,
        )
        self.python_version = text(python.stdout, "trusted Python version", "ascii").strip()
        if python.returncode or not re.fullmatch(r"3\.[0-9]+", self.python_version):
            raise MakeProbeError("cannot identify trusted Python runtime")
        self.launcher = [
            NAMESPACE_LAUNCHER[0], "--user", "--map-root-user", *NAMESPACE_LAUNCHER[1:],
        ]
        probe = self.budget.run([*self.launcher, "/usr/bin/true"], env=ENVIRONMENT)
        self.sudo_drop = False
        if probe.returncode:
            if not Path("/usr/bin/sudo").is_file() or os.getuid() == 0 or os.getgid() == 0:
                raise MakeProbeError(f"required namespaces unavailable: {probe.stderr!r}")
            self.launcher = list(NAMESPACE_LAUNCHER)
            privileged = self.budget.run(
                [*self.launcher, "/usr/bin/true"], env=ENVIRONMENT, privileged=True,
            )
            if privileged.returncode:
                raise MakeProbeError(f"required namespaces unavailable: {privileged.stderr!r}")
            self.sudo_drop = True

    def _compile_interceptor(self):
        for source, flags, output in (
            ("shell_interceptor.c", ["-static"], "interceptor"),
            ("make_observer.c", ["-shared", "-fPIC"], "observer.so"),
        ):
            destination = self.base / output
            command = [
                "/usr/bin/cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                *flags, str(TRUSTED_ROOT / source), "-o", str(destination),
                *(["-ldl"] if source == "make_observer.c" else []),
            ]
            completed = self.budget.run(
                command, env={**ENVIRONMENT, "TMPDIR": str(self.base)}, cwd=self.base,
            )
            if completed.returncode:
                raise MakeProbeError(f"trusted native authority compilation failed: {completed.stderr!r}")
            self.budget.read_bytes(destination, "control")

    def sources(self, patterns: tuple[str, ...]):
        result = set()
        for index, pattern in enumerate(patterns):
            if index >= 4096:
                self.budget.reject("source selector count exceeds bound")
            self.budget.remaining()
            relative_path(pattern)
            if any(
                path not in self.snapshot.files and PurePosixPath(path).match(pattern)
                for path in self.loader.entries
            ):
                raise MakeProbeError("source selector matches an unadmitted symlink/gitlink")
            matched = {
                name for name in self.snapshot.files
                if PurePosixPath(name).match(pattern)
            }
            if not matched:
                raise MakeProbeError(f"source declaration resolves no regular inputs: {pattern}")
            result.update(matched)
        return tuple(sorted(result))

    def _new_root(self, name, *, make=False):
        root = self.base / name
        root.mkdir()
        for directory in ("repo", "usr", "work", "dev", "control", "lib", "lib64", "bin"):
            (root / directory).mkdir()
        (root / "dev/null").touch()
        if make:
            for target, data in self.make_runtime:
                _mkdir_target(root, target).write_bytes(data)
                (root / target.lstrip("/")).chmod(0o555)
            shutil.copyfile(self.base / "observer.so", _mkdir_target(root, "/lib/vo-observer.so"))
            (root / "lib/vo-observer.so").chmod(0o555)
            for target in ALIASES:
                shutil.copyfile(self.base / "interceptor", _mkdir_target(root, target))
                (root / target.lstrip("/")).chmod(0o555)
        else:
            for directory, target in (("bin", "usr/bin"), ("lib", "usr/lib"), ("lib64", "usr/lib64")):
                (root / directory).rmdir()
                (root / directory).symlink_to(target)
        return root

    def _sandbox_run(
        self, root, *, mode, argv, environment, mounts, code=(), sources=(),
        directories=(), executables=None,
    ):
        self.budget.remaining()
        self.serial += 1
        report = self.base / f"report-{self.serial}.json"
        config_path = self.base / f"launch-{self.serial}.json"
        executable = ["/usr/bin/make", "/control/interceptor", *ALIASES] if mode == "make" else (
            [argv[0]] if executables is None else list(executables)
        )
        file_remaining = min(
            self.budget.limits.file_bytes,
            self.budget.limits.event_bytes - self.budget.bytes.get("event", 0),
            self.budget.limits.control_bytes - self.budget.bytes.get("control", 0),
        )
        if file_remaining <= 0:
            self.budget.reject("aggregate channel file budget exhausted")
        config = {
            "argv": argv, "root": str(root), "mode": mode,
            "environment": environment, "mounts": mounts, "code": list(code),
            "sources": list(sources), "enumerations": list(directories),
            "executables": executable, "report": str(report),
            "python_version": self.python_version,
            "sudo_drop": self.sudo_drop, "runner_uid": os.getuid(), "runner_gid": os.getgid(),
            "forbidden_paths": [
                "/repo/" + path for path in self.loader.entries if path not in self.snapshot.files
            ],
            "deadline": self.budget.deadline,
            "file_limit": file_remaining,
            "memory_limit": self.budget.limits.address_space_bytes,
            "process_limit": min(
                self.budget.limits.processes,
                self.budget.limits.descendants - self.processes_used,
            ),
            "syscall_limit": self.budget.limits.syscalls - self.syscalls_used,
            "write_limit": self.budget.limits.sandbox_bytes - self.budget.bytes.get("sandbox", 0),
            "creation_limit": self.budget.limits.created_files - self.files_created,
            "observation_count": self.budget.limits.entries,
            "observation_limit": min(
                self.budget.limits.file_bytes,
                self.budget.limits.control_bytes - self.budget.bytes.get("control", 0),
            ),
        }
        if config["process_limit"] < 1 or config["syscall_limit"] < 1 or config["write_limit"] < 1:
            self.budget.reject("aggregate capsule resource budget exhausted")
        payload = encoded(config)
        self.budget.charge("control", len(payload))
        with cleanup_scope([
            lambda: report.unlink(missing_ok=True), lambda: config_path.unlink(missing_ok=True),
        ]):
            config_path.write_bytes(payload)
            result = self.budget.run(
                [*self.launcher, "/usr/bin/python3", "-I", "-S", "-B",
                 str(TRUSTED_ROOT / "sandbox_exec.py"), str(config_path)],
                env=ENVIRONMENT, privileged=self.sudo_drop,
            )
            if not report.is_file():
                raise MakeProbeError(f"sandbox supervisor produced no result: {result.stderr!r}")
            observed = parse_json(self.budget.read_bytes(report, "control"), "supervisor JSON")
            if set(observed) != {
                "ok", "returncode", "error", "consumed", "code_consumed", "accessed",
                "processes", "syscalls", "written_bytes", "created_files",
                "memory_peak", "observation_bytes",
            }:
                raise MakeProbeError("malformed supervisor result")
            self.processes_used += observed["processes"]
            self.syscalls_used += observed["syscalls"]
            self.files_created += observed["created_files"]
            self.budget.charge("sandbox", observed["written_bytes"])
            self.budget.charge("control", observed["observation_bytes"])
            if result.returncode or observed["ok"] is not True:
                raise MakeProbeError(f"confined {mode} probe rejected: {observed['error']}; {result.stderr!r}")
            result.returncode = observed["returncode"]
            if mode != "make" and result.returncode:
                raise MakeProbeError(f"registered command failed: {result.returncode}")
            return result, observed

    @staticmethod
    def _mount(source, target, *, writable=False, executable=False):
        return {
            "source": str(source), "target": target,
            "writable": writable, "executable": executable,
        }

    @terminal_failure
    def command(self, command: Command):
        return self._command(command)

    def _output_paths(self, paths):
        if len(paths) > 4096:
            raise MakeProbeError("generated output count exceeds admission bound")
        names = tuple(sorted(relative_path(path) for path in paths))
        declared = set(names)
        for index, name in enumerate(names):
            self.budget.remaining()
            if index and name == names[index - 1] or any(
                parent.as_posix() in declared for parent in PurePosixPath(name).parents
            ):
                raise MakeProbeError("conflicting generated output declarations")
            if any(
                name == source or name.startswith(source + "/") or source.startswith(name + "/")
                for source in self.loader.entries
            ):
                raise MakeProbeError("generated output conflicts with immutable source")
        return names

    def _capture_outputs(self, root, names):
        if not names:
            return ()
        directories = {
            parent.as_posix() for name in names for parent in PurePosixPath(name).parents
            if parent.as_posix() != "."
        }
        pending, found = [root], {}
        count = 0
        while pending:
            self.budget.remaining()
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    self.budget.remaining()
                    count += 1
                    if count > self.budget.limits.created_files:
                        self.budget.reject("generated output tree exceeds creation bound")
                    name = Path(entry.path).relative_to(root).as_posix()
                    self.budget.charge("control", len(os.fsencode(name)) + 64)
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISDIR(mode) and name in directories:
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(mode) and name in names and not mode & 0o7000:
                        found[name] = stat.S_IMODE(mode)
                    else:
                        raise MakeProbeError("undeclared or nonregular generated output")
        if set(found) != set(names):
            raise MakeProbeError("missing declared generated output")
        return tuple(
            GeneratedFile(name, self.budget.read_bytes(root / name, "output"), found[name])
            for name in names
        )

    def _command(self, command: Command, *, compiler=None):
        self.budget.remaining()
        if not isinstance(command, Command):
            raise MakeProbeError("registered command requires a typed Command")
        native = command.native_tool
        programs = {"/usr/bin/python3", "/usr/bin/uname", "/usr/bin/printf"}
        if native is not None:
            if not isinstance(native, NativeTool) or not any(
                native is issued for issued in self.native_tools.values()
            ):
                raise MakeProbeError("native tool is not issued by this exact probe session")
            if hashlib.sha256(self.budget.read_bytes(native.path, "control")).hexdigest() != native.digest:
                raise MakeProbeError("sealed native tool changed after validation")
            if not command.argv or command.argv[0] != "/native/tool":
                raise MakeProbeError("native registration requires exact /native/tool argv")
            programs.add("/native/tool")
        if compiler is not None:
            programs.update(compiler)
        if (
            not command.argv
            or command.argv[0] not in programs
            or any(not isinstance(value, str) or "\0" in value for value in command.argv)
        ):
            raise MakeProbeError("registered command requires a supported exact trusted argv")
        if len(command.code) > 4096 or len(command.argv) > 1024 or len(command.directories) > 4096:
            raise MakeProbeError("command/code count exceeds admission bound")
        for argument in command.argv:
            try:
                if len(argument.encode("utf-8")) > 65536:
                    raise MakeProbeError("command argument exceeds byte bound")
            except UnicodeEncodeError as error:
                raise MakeProbeError("command argv is not strict UTF-8") from error
        code = tuple(sorted(set(command.code)))
        sources = self.sources(command.sources) if command.sources else ()
        directories = tuple(sorted({relative_path(path) for path in command.directories}))
        outputs = self._output_paths(command.outputs)
        key = (self.snapshot.digest, command, None if native is None else native.digest)
        if key in self.cache:
            return self.cache[key]
        self.budget.charge("pending", len(encoded([command.argv, code, sources, directories, outputs])))
        for path in code:
            relative_path(path)
            if path not in self.snapshot.files:
                raise MakeProbeError(f"unadmitted command code: {path}")
        work = self.base / f"command-{self.serial + 1}"
        root_name = f"command-root-{self.serial + 1}"
        root = self.base / root_name
        with cleanup_scope([lambda: _remove_owned_tree(work), lambda: _remove_owned_tree(root)]):
            work.mkdir()
            tree = work / "tree"
            tree.mkdir()
            self.snapshot.materialize(tree, set(code) | set(sources), self.budget)
            output = work / "output"
            output.mkdir()
            self._new_root(root_name)
            if native is not None:
                shutil.copyfile(native.path, _mkdir_target(root, "/native/tool"))
                (root / "native/tool").chmod(0o555)
            argv = list(command.argv)
            if argv[0] == "/usr/bin/python3":
                argv[1:1] = ["-I", "-S", "-B"]
            completed, observed = self._sandbox_run(
                root, mode="command" if compiler is None else "compile", argv=argv,
                environment={**ENVIRONMENT, "SOURCE_DATE_EPOCH": "0", "TMPDIR": "/work"},
                mounts=[
                    self._mount(tree, "/repo"), self._mount(Path("/usr"), "/usr", executable=True),
                    self._mount(output, "/work", writable=True),
                    self._mount(Path("/dev/null"), "/dev/null", writable=True),
                ],
                code=code, sources=sources, directories=directories,
                executables=compiler,
            )
            consumed = tuple(observed["consumed"])
            if consumed != sources:
                raise MakeProbeError(f"declared/consumed source mismatch: declared={sources!r}, consumed={consumed!r}")
            result = ProcessOutput(
                completed.stdout, completed.stderr, consumed, tuple(observed["code_consumed"]),
                None if compiler is None else self.budget.read_bytes(output / "tool", "control"),
                self._capture_outputs(output, outputs),
            )
            self.budget.charge(
                "cache", len(completed.stdout) + len(completed.stderr)
                + len(encoded([self.snapshot.digest, command.argv, code, sources, directories]))
                + (0 if result.artifact is None else len(result.artifact))
                + sum(len(item.data) + len(os.fsencode(item.path)) + 64 for item in result.generated),
            )
            self.cache[key] = result
            return result

    @terminal_failure
    def compile_native(self, sources, *, headers=(), cxx=False, libraries=(), defines=()):
        """Compile candidate tools in a channel-free capsule; seal only valid ELF."""
        if not sources or len(sources) > 32 or len(headers) > 4096 or len(libraries) > 32 or len(defines) > 32:
            raise MakeProbeError("native source count outside compilation contract")
        sources = tuple(relative_path(path) for path in sources)
        headers = tuple(relative_path(path) for path in headers)
        if any(not VARIABLE.fullmatch(name) for name in defines) or any(
            not re.fullmatch(r"[A-Za-z0-9_+.-]{1,64}", name) for name in libraries
        ):
            raise MakeProbeError("native compile options are not symbolic declarations")
        compiler = str(Path("/usr/bin/g++" if cxx else "/usr/bin/cc").resolve(strict=True))
        executables = [compiler]
        for name in ("cc1plus" if cxx else "cc1", "collect2", "as", "ld", "nm", "strip"):
            result = self.budget.run(
                [compiler, "-print-prog-name=" + name],
                env={**ENVIRONMENT, "TMPDIR": str(self.base)},
            )
            path = text(result.stdout, "trusted compiler program", "utf-8").strip()
            if not path.startswith("/"):
                path = shutil.which(path, path=ENVIRONMENT["PATH"]) or ""
            if result.returncode or not path.startswith("/usr/") or not Path(path).is_file():
                raise MakeProbeError(f"missing trusted native compilation tool: {name}")
            executables.append(str(Path(path).resolve()))
            executables.append(path)
            alias = Path("/bin") / name
            if alias.is_file() and alias.resolve() == Path(path).resolve():
                executables.append(str(alias))
        command = Command(
            (
                compiler, "-O2", "-Wall", "-Wextra", "-Werror",
                "-std=c++11" if cxx else "-std=c11",
                *("-D" + name for name in defines),
                *("/repo/" + path for path in sources), "-o", "/work/tool",
                *("-l" + name for name in libraries),
            ),
            code=tuple(sorted(set(sources) | set(headers))),
        )
        result = self._command(command, compiler=tuple(sorted(set(executables))))
        binary = result.artifact
        self._validate_native(binary)
        digest = hashlib.sha256(binary).hexdigest()
        inputs = tuple(self.snapshot.owners(command.code))
        key = hashlib.sha256(encoded([digest, inputs])).hexdigest()
        if key not in self.native_tools:
            self.budget.charge("cache", len(encoded([digest, inputs])))
            path = self.base / ("native-" + key)
            path.write_bytes(binary)
            path.chmod(0o500)
            self.native_tools[key] = NativeTool(path, digest, inputs)
        return self.native_tools[key]

    @staticmethod
    def _validate_native(binary):
        if (
            binary is None or len(binary) < 64 or binary[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(binary[16:18], "little") not in {2, 3}
            or int.from_bytes(binary[18:20], "little") != 62
        ):
            raise MakeProbeError("native compiler did not produce a Linux x86-64 ELF")
        start = int.from_bytes(binary[32:40], "little")
        size = int.from_bytes(binary[54:56], "little")
        count = int.from_bytes(binary[56:58], "little")
        if size != 56 or not 1 <= count <= 64 or start + size * count > len(binary):
            raise MakeProbeError("native ELF program headers are invalid")
        interpreter = 0
        loads = 0
        for index in range(count):
            header = binary[start + index * size:start + (index + 1) * size]
            kind, flags = struct.unpack_from("<II", header)
            offset = int.from_bytes(header[8:16], "little")
            length = int.from_bytes(header[32:40], "little")
            if offset + length > len(binary):
                raise MakeProbeError("native ELF segment escapes its sealed bytes")
            if kind == 1:
                loads += 1
                if flags & 3 == 3:
                    raise MakeProbeError("native ELF has a writable executable segment")
            if kind == 3:
                interpreter += 1
                if interpreter != 1 or binary[offset:offset + length] != b"/lib64/ld-linux-x86-64.so.2\0":
                    raise MakeProbeError("native ELF selects an unadmitted interpreter")
        if not loads:
            raise MakeProbeError("native ELF has no loadable program")

    @terminal_failure
    def native(self, tool: NativeTool, arguments=(), *, sources=(), directories=(), outputs=()):
        return self._command(
            Command(
                ("/native/tool", *arguments), sources=tuple(sources), directories=tuple(directories),
                native_tool=tool, outputs=tuple(outputs),
            ),
        )

    @terminal_failure
    def make(
        self, target: str, *, makefile="Makefile", variables=(), assignments=(),
        owner_inputs=(), commands=None,
    ) -> MakeObservation:
        self.budget.remaining()
        if not TARGET.fullmatch(target) or target.startswith(("-", "/")) or ".." in target.split("/"):
            raise MakeProbeError("invalid requested Make target")
        relative_path(makefile)
        if makefile not in self.snapshot.files:
            raise MakeProbeError("Makefile is not an admitted snapshot input")
        if len(variables) > 512 or len(assignments) > 512 or len(owner_inputs) > 4096:
            raise MakeProbeError("Make request count exceeds admission bound")
        variables = tuple(sorted(set(variables)))
        if any(not isinstance(name, str) or len(name) > 128 or not VARIABLE.fullmatch(name) for name in variables):
            raise MakeProbeError("invalid/excessive Make observation variables")
        self.budget.plan(1)
        cli = []
        environment = {
            **ENVIRONMENT,
            "LD_PRELOAD": "/lib/vo-observer.so",
            "VO_OBSERVE_TARGET": target,
            "VO_OBSERVE_NAMES": " ".join(variables),
            "VO_OBSERVE_BYTES": str(min(self.budget.limits.file_bytes, 16 * 1024 * 1024)),
        }
        names = set()
        for origin, name, value in assignments:
            if (
                origin not in {"environment", "command-line"} or not VARIABLE.fullmatch(name)
                or name in names or name.startswith(("VO_", "LD_", "GIT_"))
                or name in {"SHELL", "MAKEFLAGS", "GNUMAKEFLAGS", "MFLAGS", "MAKEFILES", "MAKELEVEL", "PATH"}
                or not isinstance(value, str) or "\0" in value or len(value) > 65536
            ):
                raise MakeProbeError("invalid or execution-authority Make assignment")
            names.add(name)
            if origin == "environment":
                environment[name] = value
            else:
                cli.append(name + "=" + value)
        root_name = f"make-root-{self.serial + 1}"
        root = self.base / root_name
        control = self.base / f"control-{self.serial + 1}"
        mappings = {}
        command_results = {}
        generated_paths = set()
        generated_directories = set()
        commands = {} if commands is None else commands
        def clear_generated():
            def remove_directory(name):
                try:
                    (self.tree / name).rmdir()
                except FileNotFoundError:
                    pass
            finish_cleanup([
                *(lambda name=name: (self.tree / name).unlink(missing_ok=True)
                  for name in sorted(generated_paths)),
                *(lambda name=name: remove_directory(name) for name in sorted(
                    generated_directories, key=lambda value: (-value.count("/"), value),
                )),
            ])
        with cleanup_scope([
            clear_generated, mappings.clear,
            lambda: _remove_owned_tree(control), lambda: _remove_owned_tree(root),
        ]):
            self._new_root(root_name, make=True)
            control.mkdir(mode=0o700)
            mapping_path = control / "map"
            mapping_path.mkdir()
            events_path, result_path = control / "events", control / "result"
            events_path.touch()
            result_path.touch()
            (control / "interceptor").touch()
            for _ in range(MAX_DYNAMIC_PASSES):
                self.budget.remaining()
                clear_generated()
                (mapping_path / "count").write_bytes(len(mappings).to_bytes(4, "little"))
                events_path.write_bytes(b"")
                result_path.write_bytes(b"")
                completed, observed = self._sandbox_run(
                    root, mode="make",
                    argv=[
                        "/usr/bin/make", "-f", makefile, *cli, target,
                    ],
                    environment=environment,
                    mounts=[
                        self._mount(self.tree, "/repo"),
                        self._mount(control, "/control", writable=True),
                        self._mount(self.base / "interceptor", "/control/interceptor", executable=True),
                        self._mount(Path("/dev/null"), "/dev/null", writable=True),
                    ],
                )
                events = _read_events(
                    self.budget.read_bytes(events_path, "event"), expected_mapping_count=len(mappings),
                )
                unknown = []
                matched = set()
                for event in events:
                    command = _event_command(event)
                    if event["match"] == 0:
                        if command not in mappings:
                            raise MakeProbeError("interceptor matched an unknown mapping")
                        matched.add(mappings[command])
                    elif command not in unknown:
                        unknown.append(command)
                if len(unknown) > self.budget.limits.pending:
                    self.budget.reject("registered-command pending count exceeds aggregate bound")
                if not unknown:
                    if completed.returncode:
                        raise MakeProbeError(
                            f"GNU Make failed after confined replay: {completed.returncode}; {completed.stderr!r}"
                        )
                    semantics = _read_observation(
                        self.budget.read_bytes(result_path, "control"), target, variables,
                    )
                    semantics["assignments"] = sorted(assignments, key=lambda item: item[1])
                    recipe_sources = {
                        record["source"] for record in semantics["files"] if record["source"]
                    }
                    generated_owners = {
                        record[0]: record for key in matched
                        for record in command_results[key].get("generated_outputs", ())
                    }
                    owners = set(owner_inputs) | recipe_sources
                    semantics["owner_inputs"] = sorted(
                        self.snapshot.owners(owners - generated_owners.keys())
                        + [generated_owners[name] for name in sorted(owners & generated_owners.keys())],
                    )
                    semantics["dynamic_commands"] = sorted(
                        (command_results[key] for key in matched), key=encoded,
                    )
                    semantic_bytes = encoded(semantics)
                    self.budget.charge("control", len(semantic_bytes))
                    return MakeObservation(
                        target, semantics, self.snapshot.digest,
                        hashlib.sha256(semantic_bytes).hexdigest(),
                        completed.stdout, completed.stderr, tuple(events),
                    )
                for command in unknown:
                    if command not in commands:
                        raise MakeProbeError(f"unregistered eager/recursive Make command: {command!r}")
                    registration = commands[command]
                    result = self.command(registration)
                    output = result.stdout
                    key = _command_hash(command)
                    if (mapping_path / (key + ".cmd")).exists():
                        raise MakeProbeError("exact-command mapping collision")
                    self.budget.charge("mapping", len(command.encode("utf-8")) + len(output) + 4)
                    (mapping_path / (key + ".cmd")).write_bytes(command.encode("utf-8"))
                    (mapping_path / (key + ".out")).write_bytes(output)
                    command_identity = {
                        "argv": list(registration.argv),
                        "directories": sorted(registration.directories),
                        "inputs": self.snapshot.owners(
                            set(registration.code) | set(self.sources(registration.sources))
                        ),
                    }
                    if registration.native_tool is not None:
                        tool = registration.native_tool
                        command_identity["native_tool"] = {
                            "sha256": tool.digest, "inputs": list(tool.inputs),
                        }
                    command_result = {
                        "command": command_identity,
                        "output_sha256": hashlib.sha256(output).hexdigest(),
                    }
                    if result.generated:
                        command_result["generated_outputs"] = [
                            (item.path, f"{stat.S_IFREG | item.mode:06o}", hashlib.sha256(item.data).hexdigest())
                            for item in result.generated
                        ]
                    if registration.native_tool is not None or result.generated:
                        self.budget.charge("mapping", len(encoded(command_result)))
                    identity = hashlib.sha256(encoded(command_result)).hexdigest()
                    if result.generated:
                        size = 44 + sum(
                            12 + len(item.path.encode("utf-8")) + len(item.data) for item in result.generated
                        )
                        if size > self.budget.limits.file_bytes:
                            self.budget.reject("generated mapping exceeds file byte bound")
                        self.budget.charge("mapping", size)
                        frame = bytearray(b"VOGEN1\0\0" + bytes.fromhex(identity))
                        frame.extend(struct.pack("<I", len(result.generated)))
                        for item in result.generated:
                            if any(
                                item.path.startswith(path + "/") or path.startswith(item.path + "/")
                                for path in generated_paths
                            ):
                                raise MakeProbeError("conflicting generated output namespaces")
                            name = item.path.encode("utf-8")
                            frame.extend(struct.pack("<III", len(name), item.mode, len(item.data)))
                            frame.extend(name)
                            frame.extend(item.data)
                            generated_paths.add(item.path)
                            generated_directories.update(
                                parent.as_posix() for parent in PurePosixPath(item.path).parents
                                if parent.as_posix() != "." and not (self.tree / parent).exists()
                            )
                        (mapping_path / (key + ".files")).write_bytes(frame)
                    command_results.setdefault(identity, command_result)
                    mappings[command] = identity
            raise MakeProbeError("Make dynamic replay exceeded the existing pass bound")

    @terminal_failure
    def variants(self, target, states, **kwargs):
        # Materialize and bound the declared finite input before *any* variant
        # executes. There are no hidden executor queues or unbounded futures.
        planned = []
        for state in states:
            if len(planned) >= self.budget.limits.states - self.budget.states:
                self.budget.reject("variant states exceed aggregate bound before launch")
            self.budget.charge("pending", len(encoded(state)))
            planned.append(tuple(tuple(item) for item in state))
        if not planned:
            raise MakeProbeError("variant plan is empty")
        return tuple(self.make(target, assignments=state, **kwargs) for state in planned)

    @terminal_failure
    def registry(self, command: Command):
        output = self.command(command)
        record = parse_json(output.stdout, "generated-registry JSON")
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "version", "source_paths", "record_count"}
            or not isinstance(record["name"], str) or not record["name"]
            or isinstance(record["version"], bool) or not isinstance(record["version"], int)
            or record["version"] < 1
            or isinstance(record["record_count"], bool) or not isinstance(record["record_count"], int)
            or record["record_count"] < 0
            or record["source_paths"] != list(output.consumed)
        ):
            raise MakeProbeError("declared/reported/consumed generated-source contract mismatch")
        return record


def probe_generated_registry(loader, *, command: Command, session: ProbeSession):
    """Use the caller's active report authority; never create a per-table owner."""
    if not isinstance(session, ProbeSession) or session.base is None or session.snapshot is None:
        raise MakeProbeError("registry requires the caller's active ProbeSession")
    if session.loader is not loader or loader.budget is not session.budget:
        raise MakeProbeError("registry loader/budget differs from the report session")
    return session.registry(command)
