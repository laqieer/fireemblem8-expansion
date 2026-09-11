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
from collections import Counter
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path, PurePosixPath
from threading import get_ident, main_thread

from .authority import (
    AuthorityLoader, ENVIRONMENT, Frames, Snapshot, _command_hash, _event_command,
    _read_event_frames, _read_events, encoded, parse_json, relative_path,
)
from .budget import Limits, MakeProbeError, NAMESPACE_LAUNCHER, ProbeBudget, text
from .lifecycle import cleanup_scope, finish_cleanup
from . import metadata_transport
from .producer_channel import ChannelError, ProducerChannel


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
STOCK_RUNTIME_ALIASES = {"/bin": "/usr/bin"}
METADATA_CALLS = metadata_transport.METADATA_CALLS
METADATA_HEADER = metadata_transport.METADATA_HEADER


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
    dependency_only: bool = False


@dataclass(frozen=True)
class GeneratedFile:
    path: str
    data: bytes
    mode: int



@dataclass(frozen=True)
class RuntimeInput:
    """One explicitly captured system input, not candidate execution authority."""

    path: str
    data: bytes | None
    mode: int | None
    parents: tuple[tuple[str, bool], ...]
    canonical: str = ""
    aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProcessOutput:
    stdout: bytes
    stderr: bytes
    consumed: tuple[str, ...]
    code_consumed: tuple[str, ...]
    artifact: bytes | None = None
    metadata: tuple[tuple, ...] = ()
    generated: tuple[GeneratedFile, ...] = ()
    input_identities: tuple[tuple[str, str, str], ...] = ()
    executed: tuple[str, ...] = ()


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


def _metadata_records(value, limit, *, runtime_paths=(), runtime_absent=()):
    return metadata_transport.validate_legacy_metadata_records(
        value, limit, runtime_paths=runtime_paths, runtime_absent=runtime_absent,
    )


def _metadata_frame(records):
    return metadata_transport.metadata_frame(records)


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
    def identity(info):
        return info.st_dev, info.st_ino

    def remove():
        parts = Path(path).absolute().parts
        if len(parts) < 2 or ".." in parts:
            raise OSError(errno.EINVAL, "cleanup requires a named owned tree")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        current = following = -1
        try:
            current = os.open(parts[0], flags)
            try:
                for name in parts[1:-1]:
                    following = os.open(name, flags, dir_fd=current)
                    os.close(current)
                    current, following = following, -1
                parent = identity(os.fstat(current))
                before = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
                following = os.open(parts[-1], flags, dir_fd=current)
            except FileNotFoundError:
                return
            if identity(before) != identity(os.fstat(following)):
                raise OSError(errno.ESTALE, "owned cleanup root changed")
            os.close(current)
            current, following = following, -1
            stack = [(parts[-1], identity(before), parent, iter(os.listdir(current)))]
            while stack:
                name, expected, parent, names = stack[-1]
                entry = next(names, None)
                if entry is None:
                    following = os.open("..", flags, dir_fd=current)
                    if identity(os.fstat(following)) != parent:
                        raise OSError(errno.ESTALE, "owned cleanup parent changed")
                    try:
                        present = os.stat(name, dir_fd=following, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        if identity(present) != expected:
                            raise OSError(errno.ESTALE, "owned cleanup entry changed")
                        os.rmdir(name, dir_fd=following)
                    os.close(current)
                    current, following = following, -1
                    stack.pop()
                    continue
                try:
                    before = os.stat(entry, dir_fd=current, follow_symlinks=False)
                    if not stat.S_ISDIR(before.st_mode):
                        os.unlink(entry, dir_fd=current)
                        continue
                    following = os.open(entry, flags, dir_fd=current)
                except FileNotFoundError:
                    continue
                if identity(before) != identity(os.fstat(following)):
                    raise OSError(errno.ESTALE, "owned cleanup child changed")
                names = iter(os.listdir(following))
                stack.append((entry, identity(before), identity(os.fstat(current)), names))
                os.close(current)
                current, following = following, -1
        finally:
            finish_cleanup([
                lambda descriptor=descriptor: os.close(descriptor)
                for descriptor in (following, current) if descriptor >= 0
            ], primary=sys.exc_info()[1])

    finish_cleanup([remove])


def _trusted_runtime_path(path: str, *, optional=False, compiler=False):
    requested = PurePosixPath(path)
    if not requested.is_absolute() or str(requested) != path or ".." in requested.parts:
        raise MakeProbeError("noncanonical trusted runtime path")
    roots = (
        "/usr/bin/", "/usr/lib/", "/usr/lib64/", "/lib/", "/lib64/",
        *(("/usr/libexec/",) if compiler else ()),
        *(("/usr/include/", "/bin/") if optional else ()),
    )
    if not path.startswith(roots):
        raise MakeProbeError(f"runtime is outside the trusted system tool/library roots: {path}")
    resolved = Path(path).resolve(strict=not optional)
    if not resolved.as_posix().startswith(roots):
        raise MakeProbeError(f"runtime is outside the trusted system tool/library roots: {path}")
    for entry in {Path(path), *Path(path).parents, resolved, *resolved.parents}:
        try:
            mode = entry.lstat()
        except FileNotFoundError:
            if optional:
                continue
            raise
        if mode.st_uid != 0 or (
            not stat.S_ISLNK(mode.st_mode) and mode.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise MakeProbeError(f"mutable/untrusted runtime input: {path}")
    return resolved


def _trusted_runtime_bytes(path: str, budget: ProbeBudget):
    return budget.read_bytes(_trusted_runtime_path(path), "control")


def _capture_runtime_input(path, budget):
    if (
        not isinstance(path, str) or not path.startswith("/")
        or any(character in path for character in "*?[")
    ):
        raise MakeProbeError("runtime input must be a bounded exact pathname")
    relative_path(path[1:])
    resolved = _trusted_runtime_path(path, optional=True)
    parents, aliases, states = [], [], {}
    for parent in Path(path).parents:
        budget.remaining()
        try:
            info = parent.lstat()
        except FileNotFoundError:
            parents.append((str(parent), False))
            states[parent] = None
        else:
            if stat.S_ISLNK(info.st_mode):
                expected = STOCK_RUNTIME_ALIASES.get(str(parent))
                if expected is None or parent.resolve().as_posix() != expected:
                    raise MakeProbeError("runtime input has a nonstock/escaping ancestor alias")
                aliases.append((str(parent), os.path.relpath(expected, str(parent.parent))))
            elif not stat.S_ISDIR(info.st_mode):
                raise MakeProbeError("runtime input has a non-directory/symlink ancestor")
            parents.append((str(parent), True))
            states[parent] = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    try:
        before = Path(path).lstat()
    except FileNotFoundError:
        data, mode = None, None
        before = None
    else:
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o7000:
            raise MakeProbeError("runtime input is not an ordinary regular file")
        data = budget.read_bytes(resolved, "control")
        mode = stat.S_IMODE(before.st_mode)
    try:
        after = Path(path).lstat()
    except FileNotFoundError:
        after = None
    def identity(info):
        return None if info is None else (
            info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        )
    if identity(before) != identity(after) or _trusted_runtime_path(path, optional=True) != resolved:
        raise MakeProbeError("runtime input changed during capture")
    for parent, expected in states.items():
        try:
            info = parent.lstat()
        except FileNotFoundError:
            actual = None
        else:
            actual = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
        if actual != expected:
            raise MakeProbeError("runtime ancestor changed during capture")
    budget.charge("control", len(encoded([path, mode, parents, str(resolved), aliases])))
    return RuntimeInput(path, data, mode, tuple(parents), str(resolved), tuple(aliases))


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


def _runtime_library_paths(raw, existing):
    known = set(existing)
    result = []
    for row in text(raw, "trusted runtime listing", "ascii").splitlines():
        row = row.strip()
        if re.fullmatch(r"linux-vdso\.so\.1 \(0x[0-9a-f]+\)", row):
            continue
        match = re.fullmatch(r"(?:[A-Za-z0-9_.+-]+ => )?(/[^ \t]+) \(0x[0-9a-f]+\)", row)
        if match is None or len(known) >= 64:
            raise MakeProbeError("unresolved/malformed trusted runtime closure")
        path = match[1]
        if path not in known:
            result.append(path)
            known.add(path)
    return tuple(result)


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
    for path in _runtime_library_paths(result.stdout, runtime):
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


class _OwnedViewContext(AbstractContextManager):
    def __init__(self, session, context):
        self._session = session
        self._context = context

    def _require_owner(self):
        if get_ident() != self._session.owner_thread:
            self._session.budget.failed = True
            raise MakeProbeError("a probe session has one bounded execution worker")

    def __enter__(self):
        self._require_owner()
        return self._context.__enter__()

    def __exit__(self, kind, value, traceback):
        # A check inside the generator would still let its finally blocks run.
        self._require_owner()
        return self._context.__exit__(kind, value, traceback)


class ProbeSession:
    """The only execution authority; one lifetime with explicitly selected views."""

    def __init__(
        self, loader: AuthorityLoader, *, scratch_root: Path, budget: ProbeBudget,
        runtime_files: tuple[str, ...] = (),
    ):
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
        self.published_sources = {}
        self.published_versions = {}
        self.publication_serial = 0
        self.generated_paths = set()
        self.generated_directories = set()
        self.parked_capsules = []
        self.memory_peak = 0
        self.make_depth = 0
        self._views = []
        self.handlers = {}
        self.snapshot = None
        self.make_runtime = ()
        self.dependency_compiler = None
        self.dependency_runtime = None
        if (
            not isinstance(runtime_files, (tuple, list))
            or len(runtime_files) > budget.limits.pending
            or any(not isinstance(path, str) for path in runtime_files)
            or len(set(runtime_files)) != len(runtime_files)
        ):
            raise MakeProbeError("runtime input count/duplicates exceed admission contract")
        self.runtime_paths = tuple(runtime_files)
        self.runtime_inputs = ()
        self.runtime_dispatch = ()
        self.runtime_root = None
        self.serial = 0
        self.processes_used = 0
        self.live_process_peak = 0
        self.syscalls_used = 0
        self.observations_used = 0
        self.files_created = 0
        self.pending_commands = 0
        self.pending_commands_peak = 0
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
            for name in sorted(self.snapshot.gitlink_roots):
                self.budget.remaining()
                (self.tree / name).mkdir(parents=True, exist_ok=True)
            self._compile_interceptor()
            if self.runtime_inputs:
                self.runtime_root = self._new_root("runtime", make=True)
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def _interrupt(self, signum, frame):
        raise KeyboardInterrupt(f"ownership probe interrupted by signal {signum}")

    def select_view(self, loader: AuthorityLoader) -> AbstractContextManager[ProbeSession]:
        """Temporarily select an immutable capture on this report's authority."""
        return _OwnedViewContext(self, self._select_view(loader))

    @contextmanager
    def _select_view(self, loader: AuthorityLoader) -> Iterator[ProbeSession]:
        if self.base is None or self.snapshot is None:
            raise MakeProbeError("probe session is not active")
        self.budget.remaining()
        if (
            not isinstance(loader, AuthorityLoader) or loader.budget is not self.budget
            or loader.entries.budget is not self.budget or loader.root != self.loader.root
            or loader.revision is None or loader.entries.capture != (loader.root, loader.revision)
        ):
            raise MakeProbeError("view requires a same-report, same-repository immutable capture")
        if self.budget.children or self.pending_commands or self.make_depth:
            raise MakeProbeError("cannot select a view during active report execution")
        previous = (
            self.loader, self.snapshot, self.tree, self.cache, self.mappings, self.native_tools,
        )
        cache, mappings, tools = {}, {}, {}
        selected = False

        def restore():
            if selected and self.base is not None and self.snapshot is not None:
                if not self._views or self._views[-1] is not previous:
                    error = MakeProbeError("view contexts must exit in nesting order")
                    self.__exit__(type(error), error, None)
                    raise error
                self._views.pop()
                (self.loader, self.snapshot, self.tree,
                 self.cache, self.mappings, self.native_tools) = previous

        try:
            self.budget.plan(1)
            self.serial += 1
            root = self.base / f"view-{self.serial}"
            tree = root / "tree"
            with cleanup_scope([
                cache.clear, mappings.clear, tools.clear, lambda: _remove_owned_tree(root), restore,
            ]):
                root.mkdir()
                tree.mkdir()
                snapshot = Snapshot(loader, self.budget, reuse=previous[1])
                for name in sorted(snapshot.reused_paths):
                    self.budget.remaining()
                    target = tree / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(previous[2] / name, target, follow_symlinks=False)
                copied = snapshot.files.keys() - snapshot.reused_paths
                self.budget.charge("snapshot", sum(len(snapshot.files[name]) for name in copied))
                snapshot.materialize(tree, copied, self.budget)
                for name in sorted(snapshot.gitlink_roots):
                    self.budget.remaining()
                    (tree / name).mkdir(parents=True, exist_ok=True)
                # No caller observes a partially prepared source/native/cache view.
                mask = signal.pthread_sigmask(signal.SIG_BLOCK, self.handlers)
                try:
                    self._views.append(previous)
                    (self.loader, self.snapshot, self.tree,
                     self.cache, self.mappings, self.native_tools) = (
                        loader, snapshot, tree, cache, mappings, tools,
                    )
                    selected = True
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                yield self
        except BaseException as error:
            self.budget.failed = True
            finish_cleanup([self.budget.close], primary=error)
            raise

    def __exit__(self, kind, value, traceback):
        def clear_state():
            self.cache.clear()
            self.mappings.clear()
            self.native_tools.clear()
            self.published_sources.clear()
            self.published_versions.clear()
            self.generated_paths.clear()
            self.generated_directories.clear()
            self.parked_capsules.clear()
            self.make_runtime = ()
            self.dependency_compiler = None
            self.dependency_runtime = None
            self.runtime_inputs = ()
            self.runtime_dispatch = ()
            self.runtime_root = None
            self.snapshot = None
            self.loader.live_modes.clear()
            for loader, snapshot, tree, cache, mappings, tools in self._views:
                loader.live_modes.clear()
                cache.clear()
                mappings.clear()
                tools.clear()
            if self._views:
                self.loader = self._views[0][0]
            self._views.clear()
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
        reserved = {path for path, _ in self.make_runtime} | set(ALIASES) | {"/lib/vo-observer.so"}
        if self.runtime_paths:
            reserved.update(str(_trusted_runtime_path(path)) for path, _ in self.make_runtime)
            reserved.update(
                target + path.removeprefix(alias)
                for path in ALIASES for alias, target in STOCK_RUNTIME_ALIASES.items()
                if path.startswith(alias + "/")
            )
        captured, dispatch = [], []
        for path in self.runtime_paths:
            item = _capture_runtime_input(path, self.budget)
            intercepted = item.data is not None and (
                bool(item.aliases) and item.canonical in ALIASES
                or item.canonical == "/usr/bin/env"
            )
            if (
                any(path == other or path.startswith(other + "/") or other.startswith(path + "/")
                    for other in reserved)
                or item.canonical in reserved and not intercepted
            ):
                raise MakeProbeError("runtime input conflicts with trusted execution image")
            for other in captured:
                overlap = (
                    item.canonical == other.canonical
                    or item.canonical.startswith(other.canonical + "/")
                    or other.canonical.startswith(item.canonical + "/")
                )
                env_spellings = (
                    item.canonical == other.canonical == "/usr/bin/env"
                    and {item.path, other.path} == {"/bin/env", "/usr/bin/env"}
                    and (item.data, item.mode) == (other.data, other.mode)
                )
                if overlap and not env_spellings:
                    raise MakeProbeError("duplicate/overlapping optional runtime inputs")
            captured.append(item)
            if intercepted:
                dispatch.append(path)
        self.runtime_inputs = tuple(captured)
        self.runtime_dispatch = tuple(dispatch)
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
            is_glob = any(character in pattern for character in "*?[")

            def matches(name):
                return PurePosixPath(name).match(pattern) if is_glob else name == pattern

            if any(
                path not in self.snapshot.files and path not in self.snapshot.absent_paths and matches(path)
                for path in self.loader.entries
            ):
                raise MakeProbeError("source selector matches an unadmitted symlink/gitlink")
            matched = {
                name for name in self.snapshot.files.keys() | self.published_sources.keys()
                if matches(name)
            }
            if not matched:
                raise MakeProbeError(f"source declaration resolves no regular inputs: {pattern}")
            result.update(matched)
        return tuple(sorted(result))

    def source_owners(self, paths):
        selected = set(paths)
        result = self.snapshot.owners(selected - self.published_sources.keys())
        for name in sorted(selected & self.published_sources.keys()):
            item = self.published_sources[name]
            result.append((name, f"{stat.S_IFREG | item.mode:06o}", hashlib.sha256(item.data).hexdigest()))
        return sorted(result)

    def _publication_records(self, since=0):
        result = []
        for name, (owner, serial) in sorted(self.published_versions.items()):
            self.budget.remaining()
            if serial > since:
                item = self.published_sources[name]
                result.append([name, owner, item.mode, len(item.data), hashlib.sha256(item.data).hexdigest()])
        return result

    def _new_root(self, name, *, make=False):
        root = self.base / name
        root.mkdir()
        for directory in ("repo", "usr", "work", "dev", "control", "lib", "lib64", "bin"):
            (root / directory).mkdir()
        (root / "dev/null").touch()
        if make:
            if self.runtime_root is not None:
                return root
            for alias, target in sorted({pair for item in self.runtime_inputs for pair in item.aliases}):
                destination = root / alias.lstrip("/")
                _mkdir_target(root, "/" + target, directory=True)
                destination.rmdir()
                destination.symlink_to(target)
            for target, data in self.make_runtime:
                _mkdir_target(root, target).write_bytes(data)
                (root / target.lstrip("/")).chmod(0o555)
            shutil.copyfile(self.base / "observer.so", _mkdir_target(root, "/lib/vo-observer.so"))
            (root / "lib/vo-observer.so").chmod(0o555)
            for target in sorted(set(ALIASES) | {
                item.canonical for item in self.runtime_inputs if item.path in self.runtime_dispatch
            }):
                shutil.copyfile(self.base / "interceptor", _mkdir_target(root, target))
                (root / target.lstrip("/")).chmod(0o555)
            for item in self.runtime_inputs:
                for parent, present in reversed(item.parents):
                    if present and parent != "/":
                        _mkdir_target(root, parent, directory=True)
                if item.data is not None and item.path not in self.runtime_dispatch:
                    self.budget.charge("control", len(item.data))
                    target = _mkdir_target(root, item.canonical)
                    target.write_bytes(item.data)
                    target.chmod(item.mode)
        else:
            for directory, target in (("bin", "usr/bin"), ("lib", "usr/lib"), ("lib64", "usr/lib64")):
                (root / directory).rmdir()
                (root / directory).symlink_to(target)
        return root

    def _sandbox_run(
        self, root, *, mode, argv, environment, mounts, code=(), sources=(),
        directories=(), executables=None, mapping_entries=(), metadata_validation=False,
        producer_handler=None, publication_observer=None, publication_allowed=True,
        dependency=None,
    ):
        self.budget.remaining()
        if [item for item in mounts if item["target"] == "/repo"] != [self._mount(self.tree, "/repo")]:
            raise MakeProbeError("incomplete/non-readonly source backing is not admitted")
        if self.runtime_root is not None and (mode == "make" or metadata_validation):
            mounts = [
                self._mount(self.runtime_root, "/", executable=True),
                *mounts,
            ]
        self.serial += 1
        report = self.base / f"report-{self.serial}.json"
        config_path = self.base / f"launch-{self.serial}.json"
        executable = ["/usr/bin/make", "/control/interceptor", *ALIASES, *self.runtime_dispatch] if mode == "make" else (
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
                "/repo/" + path for path in self.loader.entries
                if path not in self.snapshot.files and path not in self.snapshot.gitlink_roots
                and path not in self.snapshot.absent_paths
            ],
            "runtime_closure": [name for name, _ in self.make_runtime] if mode == "make" else [],
            "runtime_files": sorted({name for item in self.runtime_inputs for name in (item.path, item.canonical)}),
            "runtime_aliases": sorted({alias for item in self.runtime_inputs for alias, _ in item.aliases}),
            "intercepted_runtime": sorted({
                item.canonical for item in self.runtime_inputs if item.path in self.runtime_dispatch
            }),
            "runtime_absent": sorted({
                name for item in self.runtime_inputs if item.data is None for name in (item.path, item.canonical)
            }),
            "runtime_parents": sorted({
                parent for item in self.runtime_inputs for parent in (
                    *(name for name, _ in item.parents), *(str(name) for name in Path(item.canonical).parents),
                )
            }),
            "mapping_entries": mapping_entries,
            "metadata_validation": metadata_validation,
            "deadline": self.budget.deadline,
            "file_limit": file_remaining,
            "memory_limit": self.budget.limits.address_space_bytes - sum(item["memory"] for item in self.parked_capsules),
            "process_limit": self.budget.limits.processes - sum(item["processes"] for item in self.parked_capsules),
            "descendant_limit": self.budget.limits.descendants - self.processes_used,
            "syscall_limit": self.budget.limits.syscalls - self.syscalls_used,
            "write_limit": self.budget.limits.sandbox_bytes - self.budget.bytes.get("sandbox", 0),
            "creation_limit": self.budget.limits.created_files - self.files_created,
            "observation_count": self.budget.limits.entries - self.observations_used,
            "observation_limit": min(
                self.budget.limits.file_bytes,
                self.budget.limits.control_bytes - self.budget.bytes.get("control", 0),
            ),
        }
        if dependency is not None:
            if mode != "compile":
                raise MakeProbeError("dependency profile requires compiler confinement")
            config["dependency"] = dependency
        counter_names = {
            "processes", "syscalls", "written_bytes", "created_files", "observation_bytes",
            "observations", "live_process_peak", "memory_peak",
        }
        settled = dict.fromkeys(counter_names, 0)
        sequence = 0
        completion = None
        channel = None
        channel_directory = self.base / f"producer-{self.serial}"
        if producer_handler is not None:
            if mode != "make":
                raise MakeProbeError("live producer requests require native Make")
            config["producer_scope"] = self.base.name + "/" + root.name
            config["reserved_paths"] = list(self.loader.entries) if publication_allowed else None
            config["publication_limit"] = self.budget.limits.created_files
            if self.published_sources:
                config["published"] = self._publication_records()
            config["pending_limit"] = self.budget.limits.pending - sum(
                item["pending"] for item in self.parked_capsules
            )
            if config["pending_limit"] < 1:
                self.budget.reject("pending producer capacity exhausted before launch")

        def settle(values, *, failed=False):
            if not isinstance(values, dict) or set(values) != counter_names or any(
                type(values[name]) is not int or values[name] < settled[name] for name in counter_names
            ):
                raise MakeProbeError("nonmonotonic or malformed supervisor resource checkpoint")
            prospective = {
                "processes": self.processes_used + values["processes"] - settled["processes"],
                "syscalls": self.syscalls_used + values["syscalls"] - settled["syscalls"],
                "observations": self.observations_used + values["observations"] - settled["observations"],
                "created": self.files_created + values["created_files"] - settled["created_files"],
            }
            if not failed and (
                prospective["processes"] > self.budget.limits.descendants
                or prospective["syscalls"] > self.budget.limits.syscalls
                or prospective["observations"] > self.budget.limits.entries
                or prospective["created"] > self.budget.limits.created_files
                or values["live_process_peak"] > config["process_limit"]
                or values["memory_peak"] > config["memory_limit"]
                or values["observation_bytes"] < 128*values["observations"]
            ):
                self.budget.reject("supervisor checkpoint exceeds aggregate resource authority")
            self.observations_used += values["observations"] - settled["observations"]
            self.processes_used += values["processes"] - settled["processes"]
            self.syscalls_used += values["syscalls"] - settled["syscalls"]
            self.files_created += values["created_files"] - settled["created_files"]
            self.live_process_peak = max(
                self.live_process_peak, values["live_process_peak"] + sum(item["live"] for item in self.parked_capsules),
            )
            self.memory_peak = max(
                self.memory_peak, values["memory_peak"] + sum(item["memory"] for item in self.parked_capsules),
            )
            self.budget.charge("sandbox", values["written_bytes"] - settled["written_bytes"])
            self.budget.charge("control", values["observation_bytes"] - settled["observation_bytes"])
            settled.update(values)

        def grants(extra_control=0):
            available = {
                "descendant_limit": (settled["processes"], self.budget.limits.descendants - self.processes_used),
                "syscall_limit": (settled["syscalls"], self.budget.limits.syscalls - self.syscalls_used),
                "write_limit": (settled["written_bytes"], self.budget.limits.sandbox_bytes - self.budget.bytes.get("sandbox", 0)),
                "creation_limit": (settled["created_files"], self.budget.limits.created_files - self.files_created),
                "observation_count": (settled["observations"], self.budget.limits.entries - self.observations_used),
                "observation_limit": (
                    settled["observation_bytes"],
                    self.budget.limits.control_bytes - self.budget.bytes.get("control", 0) - extra_control,
                ),
            }
            if any(left < 0 for _, left in available.values()):
                self.budget.reject("producer work exhausted an outer remaining allowance")
            return {
                **{name: min(config[name], used + left) for name, (used, left) in available.items()},
                "process_limit": config["process_limit"], "memory_limit": config["memory_limit"],
            }

        def dispatch(packet):
            nonlocal sequence, completion
            request = parse_json(packet, "producer request")
            if not isinstance(request, dict) or request.get("scope") != config["producer_scope"]:
                raise MakeProbeError("foreign producer request scope")
            if request.get("kind") == "finished":
                if (
                    set(request) != {"kind", "scope", "issued", "completed"}
                    or type(request["issued"]) is not int or request["issued"] != sequence
                    or type(request["completed"]) is not int or not 0 <= request["completed"] <= sequence
                ):
                    raise MakeProbeError("invalid producer completion notification")
                completion = request["issued"], request["completed"]
                return None
            if (
                set(request) != {"kind", "scope", "sequence", "completed", "frame", "counters", "reserved"}
                or request["kind"] != "request" or type(request["sequence"]) is not int
                or request["sequence"] != sequence + 1 or type(request["completed"]) is not int
                or request["completed"] != sequence or not isinstance(request["frame"], str)
                or len(request["frame"]) > 2*65536 or not re.fullmatch(r"(?:[0-9a-f]{2})+", request["frame"])
            ):
                raise MakeProbeError("malformed, stale or out-of-order producer request")
            events = _read_events(bytes.fromhex(request["frame"]), expected_mapping_count=0)
            if len(events) != 1 or events[0]["match"] != -1:
                raise MakeProbeError("invalid producer request event")
            reserved = request["reserved"]
            if (
                not isinstance(reserved, dict) or set(reserved) != {"live", "processes", "memory", "pending"}
                or any(type(value) is not int for value in reserved.values())
                or not 1 <= reserved["live"] <= reserved["processes"] <= config["process_limit"]
                or not 1 <= reserved["memory"] <= config["memory_limit"]
                or not 1 <= reserved["pending"] <= config["pending_limit"]
            ):
                raise MakeProbeError("invalid parked producer-context reservations")
            settle(request["counters"])
            if (
                reserved["live"] > request["counters"]["live_process_peak"]
                or reserved["live"] > request["counters"]["processes"]
                or reserved["memory"] > request["counters"]["memory_peak"]
            ):
                raise MakeProbeError("parked reservations contradict measured supervisor state")
            if publication_observer is not None:
                publication_observer(sequence)
            sequence += 1
            self.pending_commands_peak = max(
                self.pending_commands_peak, reserved["pending"] + sum(item["pending"] for item in self.parked_capsules),
            )
            self.parked_capsules.append(reserved)
            try:
                value = producer_handler(events[0], sequence)
            finally:
                self.parked_capsules.pop()
            reply = {
                "kind": "result", "scope": config["producer_scope"], "sequence": sequence,
                **value, "limits": grants(),
            }
            bound = len(encoded(reply)) + 4
            reply["limits"] = grants(bound)
            data = encoded(reply)
            if len(data) + 4 > bound:
                raise MakeProbeError("producer resumption grant encoding exceeded its reservation")
            return data
        if (
            config["process_limit"] < 1 or config["descendant_limit"] < 1
            or config["syscall_limit"] < 1 or config["write_limit"] < 1 or config["memory_limit"] < 1
        ):
            self.budget.reject("aggregate capsule resource budget exhausted")
        if config["observation_count"] < 1:
            self.budget.reject("aggregate filesystem-observation budget exhausted before launch")
        def close_channel():
            if channel is not None:
                channel.close()
        with cleanup_scope([
            lambda: report.unlink(missing_ok=True), lambda: config_path.unlink(missing_ok=True),
            close_channel, lambda: _remove_owned_tree(channel_directory),
        ]):
            if producer_handler is not None:
                mask = signal.pthread_sigmask(signal.SIG_BLOCK, (signal.SIGINT, signal.SIGTERM))
                try:
                    channel_directory.mkdir(mode=0o700)
                    channel = ProducerChannel.listen(
                        channel_directory, deadline=self.budget.deadline, limit=file_remaining,
                        charge=lambda size: self.budget.charge("control", size),
                    )
                    config["producer_endpoint"] = channel.endpoint
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            payload = encoded(config)
            self.budget.charge("control", len(payload))
            config_path.write_bytes(payload)
            try:
                result = self.budget.run(
                    [*self.launcher, "/usr/bin/python3", "-I", "-S", "-B",
                     str(TRUSTED_ROOT / "sandbox_exec.py"), str(config_path)],
                    env=ENVIRONMENT, privileged=self.sudo_drop,
                    producer_channel=channel, producer_handler=dispatch if channel is not None else None,
                )
            except ChannelError as error:
                raise MakeProbeError(f"producer rendezvous failed: {error}") from error
            if not report.is_file():
                raise MakeProbeError(f"sandbox supervisor produced no result: {result.stderr!r}")
            observed = parse_json(self.budget.read_bytes(report, "control"), "supervisor JSON")
            if not isinstance(observed, dict) or set(observed) != {
                "ok", "returncode", "error", "consumed", "code_consumed", "accessed",
                "processes", "syscalls", "written_bytes", "created_files",
                "memory_peak", "observation_bytes", "live_process_peak", "observations",
                "metadata", "events",
            } | ({"rendezvous"} if channel is not None else set()) | (
                {"executed"} if dependency is not None else set()
            ):
                raise MakeProbeError("malformed supervisor result")
            observations = observed["observations"]
            collections = [observed[name] for name in ("consumed", "code_consumed", "accessed")]
            if (
                type(observations) is not int or not 0 <= observations <= config["observation_count"]
                or any(
                    not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)
                    or len(set(paths)) != len(paths) for paths in collections
                )
                or observations < sum(map(len, collections))
                or type(observed["observation_bytes"]) is not int
                or observed["observation_bytes"] < 128 * observations
            ):
                raise MakeProbeError("malformed supervisor observation accounting")
            observed["metadata"] = metadata_transport.decode_metadata_transport(
                observed["metadata"], config["observation_count"],
                decoded_limit=min(
                    self.budget.limits.file_bytes,
                    self.budget.limits.control_bytes - self.budget.bytes.get("control", 0),
                ),
                runtime_paths=set(config["runtime_files"]) | set(config["runtime_parents"]),
                runtime_absent=config["runtime_absent"],
                reserve=lambda size: self.budget.charge("control", size),
            )
            if len(observed["metadata"]) > observations:
                raise MakeProbeError("unaccounted guest metadata records")
            if (
                not isinstance(observed["events"], list)
                or any(not isinstance(item, str) or len(item) > 2*65536
                       or not re.fullmatch(r"(?:[0-9a-f]{2})+", item) for item in observed["events"])
                or mode != "make" and observed["events"]
            ):
                raise MakeProbeError("malformed supervisor native event writes")
            settle({name: observed[name] for name in counter_names}, failed=observed["ok"] is not True)
            if result.returncode or observed["ok"] is not True:
                raise MakeProbeError(f"confined {mode} probe rejected: {observed['error']}; {result.stderr!r}")
            if dependency is not None and observed["executed"] != dependency["executables"]:
                raise MakeProbeError("dependency result lacks its actual driver/cc1 execution")
            if channel is not None:
                final = observed["rendezvous"]
                if (
                    not isinstance(final, dict) or set(final) != {"issued", "completed", "pending_peak"}
                    or any(type(value) is not int for value in final.values())
                    or final["issued"] != sequence or final["completed"] != sequence
                    or completion != (final["issued"], final["completed"])
                    or not 0 <= final["pending_peak"] <= config["pending_limit"]
                ):
                    raise MakeProbeError("partial or inconsistent live producer completion")
                if publication_observer is not None:
                    publication_observer(sequence)
            result.returncode = observed["returncode"]
            if metadata_validation and result.returncode not in {0, 1, 2}:
                raise MakeProbeError("invalid trusted metadata comparison status")
            if mode != "make" and not metadata_validation and result.returncode:
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

    def _metadata_matches(self, records):
        if not records:
            return True
        frame = _metadata_frame(records)
        if len(frame) > self.budget.limits.file_bytes:
            self.budget.reject("metadata revalidation exceeds file byte bound")
        self.budget.charge("control", len(frame))
        name = f"validation-root-{self.serial + 1}"
        root = self.base / name
        control = self.base / f"validation-control-{self.serial + 1}"
        with cleanup_scope([lambda: _remove_owned_tree(root), lambda: _remove_owned_tree(control)]):
            self._new_root(name)
            (control / "map").mkdir(parents=True)
            (control / "interceptor").touch()
            (control / "map/validate.meta").write_bytes(frame)
            result, _ = self._sandbox_run(
                root, mode="command", argv=["/control/interceptor"], environment=ENVIRONMENT,
                mounts=[
                    self._mount(self.tree, "/repo"),
                    self._mount(control, "/control"),
                    self._mount(self.base / "interceptor", "/control/interceptor", executable=True),
                    self._mount(Path("/dev/null"), "/dev/null", writable=True),
                ],
                mapping_entries=[{"key": "0000000000000000", "metadata": records}],
                metadata_validation=True,
            )
            return result.returncode == 0

    def _directories(self, declared):
        result = tuple(sorted({path if path == "." else relative_path(path) for path in declared}))
        for path in result:
            if any(
                name not in self.snapshot.files and name not in self.snapshot.gitlink_roots
                and (path == name or path.startswith(name + "/"))
                for name in self.loader.entries
            ):
                raise MakeProbeError("directory declaration enters a nonregular source namespace")
            self.budget.charge("control", len(path.encode("utf-8")) + 64)
            try:
                mode = (self.tree / path).lstat().st_mode
            except FileNotFoundError as error:
                raise MakeProbeError(f"directory declaration is absent: {path}") from error
            if not stat.S_ISDIR(mode):
                raise MakeProbeError(f"directory declaration is not an active directory: {path}")
        return result

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

    @staticmethod
    def _dependency_options(command, sources, outputs):
        if (
            command.argv[0] != "/usr/bin/cc" or command.native_tool is not None
            or len(outputs) != 1 or not outputs[0].endswith(".d")
        ):
            raise MakeProbeError("dependency profile requires host cc and one declared .d output")
        modes = set()
        includes = []
        translation_unit = target = None
        arguments = iter(command.argv[1:])
        for argument in arguments:
            if argument in {"-E", "-MM", "-MG", "-nostdinc", "-undef"}:
                if argument in modes:
                    raise MakeProbeError("duplicate dependency mode")
                modes.add(argument)
                continue
            option = next(
                (name for name in ("-iquote", "-MT", "-I", "-D", "-U") if argument.startswith(name)),
                None,
            )
            if option is not None:
                value = argument[len(option):] if argument != option else next(arguments, "")
                if not value or value.startswith("@") or "\n" in value or "\r" in value:
                    raise MakeProbeError("invalid or missing dependency option value")
                if option in {"-I", "-iquote"}:
                    if value.startswith(("=", "$SYSROOT")):
                        raise MakeProbeError("sysroot-special dependency include operand is unsupported")
                    if value.startswith("-"):
                        raise MakeProbeError("dependency include path is not repository-relative")
                    includes.append(value if value == "." else relative_path(value))
                elif option in {"-D", "-U"}:
                    name, separator, _ = value.partition("=")
                    if not VARIABLE.fullmatch(name) or option == "-U" and separator:
                        raise MakeProbeError("dependency macro requires a symbolic name")
                else:
                    if target is not None or not TARGET.fullmatch(value) or value.startswith("-"):
                        raise MakeProbeError("invalid or duplicate dependency target")
                    target = relative_path(value)
                continue
            if argument.startswith(("-", "@")) or translation_unit is not None or not argument.endswith(".c"):
                raise MakeProbeError("unsupported dependency compiler option or source")
            translation_unit = relative_path(argument)
        if (
            not {"-E", "-MM", "-nostdinc", "-undef"} <= modes
            or target is None or translation_unit not in sources
        ):
            raise MakeProbeError("dependency profile requires exact modes, target and declared C source")
        return tuple(dict.fromkeys(includes))


    def _command(self, command: Command, *, compiler=None, native=None):
        self.budget.remaining()
        if not isinstance(command, Command):
            raise MakeProbeError("registered command requires a typed Command")
        if type(command.dependency_only) is not bool:
            raise MakeProbeError("dependency_only requires a boolean")
        if command.dependency_only and (
            compiler is not None or native is not None or command.native_tool is not None
        ):
            raise MakeProbeError("dependency profile cannot combine native or other compiler authority")
        if command.native_tool is not None:
            if native is not None and native is not command.native_tool:
                raise MakeProbeError("conflicting native execution authority")
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
                raise MakeProbeError("native execution requires exact /native/tool argv")
            programs.add("/native/tool")
        if compiler is not None:
            programs.update(compiler)
        if command.dependency_only:
            programs.add("/usr/bin/cc")
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
        directories = self._directories(command.directories)
        outputs = self._output_paths(command.outputs)
        include_dirs = self._dependency_options(command, sources, outputs) if command.dependency_only else ()
        for path in code:
            relative_path(path)
            if path not in self.snapshot.files and path not in self.published_sources:
                raise MakeProbeError(f"unadmitted command code: {path}")
        published_inputs = tuple(self.source_owners((set(code) | set(sources)) & self.published_sources.keys()))
        if published_inputs:
            self.budget.charge("control", len(encoded(published_inputs)))
        key = (self.snapshot.digest, command, None if native is None else native.digest, code, sources, published_inputs)
        if key in self.cache and not outputs:
            for cached in self.cache[key]:
                if self._metadata_matches(cached.metadata):
                    return cached
        self.budget.charge("pending", len(encoded([command.argv, code, sources, directories, outputs])))
        input_identities = tuple(self.source_owners(set(code) | set(sources)))
        work = self.base / f"command-{self.serial + 1}"
        root_name = f"command-root-{self.serial + 1}"
        root = self.base / root_name
        with cleanup_scope([lambda: _remove_owned_tree(work), lambda: _remove_owned_tree(root)]):
            work.mkdir()
            output = work / "output"
            output.mkdir()
            self._new_root(root_name)
            if native is not None:
                shutil.copyfile(native.path, _mkdir_target(root, "/native/tool"))
                (root / "native/tool").chmod(0o555)
            argv = list(command.argv)
            dependency = None
            if command.dependency_only:
                if self.dependency_compiler is None:
                    driver, programs = self._compiler_tools(False, ("cc1",))
                    programs = tuple(sorted({str(_trusted_runtime_path(path, compiler=True)) for path in programs}))
                    frontends = tuple(path for path in programs if path != driver)
                    if len(frontends) != 1:
                        raise MakeProbeError("dependency profile requires one resolved C frontend")
                    self.dependency_compiler = driver, frontends[0]
                    self.budget.charge("control", len(encoded(self.dependency_compiler)))
                    self.dependency_runtime = self._dependency_runtime()
                argv[0] = self.dependency_compiler[0]
                compiler = self.dependency_compiler
                dependency = {
                    **self.dependency_runtime,
                    "executables": list(compiler),
                    "include_dirs": ["/repo" if path == "." else "/repo/" + path for path in include_dirs],
                }
                parent = output
                for part in PurePosixPath(outputs[0]).parts[:-1]:
                    self.budget.remaining()
                    if self.files_created >= self.budget.limits.created_files:
                        self.budget.reject("aggregate dependency directory-creation budget exhausted")
                    self.files_created += 1
                    parent /= part
                    parent.mkdir()
                argv.extend(("-MF", "/work/" + outputs[0]))
            if argv[0] == "/usr/bin/python3":
                argv[1:1] = ["-I", "-S", "-B"]
            completed, observed = self._sandbox_run(
                root, mode="command" if compiler is None else "compile", argv=argv,
                environment={**ENVIRONMENT, "SOURCE_DATE_EPOCH": "0", "TMPDIR": "/work"},
                mounts=[
                    self._mount(self.tree, "/repo"),
                    self._mount(Path("/usr"), "/usr", executable=True),
                    self._mount(output, "/work", writable=True),
                    self._mount(Path("/dev/null"), "/dev/null", writable=True),
                ],
                code=code, sources=sources, directories=directories,
                executables=compiler, dependency=dependency,
            )
            consumed = tuple(observed["consumed"])
            if consumed != sources:
                raise MakeProbeError(f"declared/consumed source mismatch: declared={sources!r}, consumed={consumed!r}")
            if command.dependency_only:
                used = set(consumed) | set(observed["code_consumed"])
                if not set(observed["code_consumed"]) <= set(code):
                    raise MakeProbeError("dependency result names undeclared header code")
                input_identities = tuple(item for item in input_identities if item[0] in used)
            result = ProcessOutput(
                completed.stdout, completed.stderr, consumed, tuple(observed["code_consumed"]),
                None if compiler is None or command.dependency_only else self.budget.read_bytes(output / "tool", "control"),
                observed["metadata"],
                self._capture_outputs(output, outputs),
                input_identities,
                tuple(observed.get("executed", ())),
            )
            self.budget.charge(
                "cache", len(completed.stdout) + len(completed.stderr)
                + len(encoded([self.snapshot.digest, command.argv, code, sources, directories, published_inputs]))
                + (0 if result.artifact is None else len(result.artifact))
                + sum(len(item.data) + len(os.fsencode(item.path)) + 64 for item in result.generated),
            )
            self.budget.charge("cache", len(encoded(result.metadata)))
            self.budget.charge("cache", len(encoded(result.input_identities)))
            if result.executed:
                self.budget.charge("cache", len(encoded(result.executed)))
            if not outputs:
                self.cache.setdefault(key, []).append(result)
            return result

    def _dependency_runtime(self):
        interpreter = _make_interpreter(dict(self.make_runtime)["/usr/bin/make"])
        runtime = {interpreter, *self.dependency_compiler}
        for program in self.dependency_compiler:
            result = self.budget.run(
                [interpreter, "--inhibit-cache", "--list", program], env=ENVIRONMENT, cwd=Path("/"),
            )
            if result.returncode:
                raise MakeProbeError(f"cannot resolve dependency compiler runtime: {result.stderr!r}")
            runtime.update(_runtime_library_paths(result.stdout, runtime))
        runtime.update(
            str(_trusted_runtime_path(path, compiler=True)) for path in tuple(runtime)
        )
        if len(runtime) > 64:
            raise MakeProbeError("dependency runtime closure exceeds the existing path bound")
        result = self.budget.run(
            [self.dependency_compiler[0], "-print-search-dirs"], env=ENVIRONMENT, cwd=Path("/"),
        )
        rows = text(result.stdout, "trusted compiler search directories", "utf-8").splitlines()
        if result.returncode or len(rows) != 3:
            raise MakeProbeError("unresolved dependency compiler search directories")
        searches = {}
        for expected, row in zip(("install", "programs", "libraries"), rows):
            name, separator, value = row.partition(": ")
            if name != expected or not separator:
                raise MakeProbeError("malformed dependency compiler search directories")
            paths = value.removeprefix("=").split(":")
            if not paths or len(paths) > 64:
                raise MakeProbeError("dependency compiler search count exceeds bound")
            normalized = set()
            for path in paths:
                if not path.startswith(("/usr/", "/lib/", "/lib64/")):
                    raise MakeProbeError("dependency compiler search escapes system roots")
                canonical = os.path.normpath(path)
                if canonical not in {"/usr", "/lib", "/lib64"} and not canonical.startswith(("/usr/", "/lib/", "/lib64/")):
                    raise MakeProbeError("dependency compiler search escapes system roots")
                relative_path(canonical[1:])
                normalized.add(canonical)
            searches[name] = normalized
        if len(searches["install"]) != 1:
            raise MakeProbeError("dependency compiler requires one installation directory")
        install, = searches["install"]
        directories = set().union(*searches.values())
        if len(directories) > 64:
            raise MakeProbeError("dependency compiler search count exceeds bound")
        probes = {
            str(Path(path) / "specs")
            for path in searches["libraries"] | {install, str(Path(install).parent)}
        }
        libc = {
            str(_trusted_runtime_path(path, compiler=True))
            for path in runtime if Path(path).name == "libc.so.6"
        }
        if len(libc) != 1:
            raise MakeProbeError("dependency runtime requires one resolved libc image")
        profile = {
            "runtime_files": sorted(runtime),
            "runtime_directories": sorted(directories),
            "runtime_stat_probes": sorted(probes | {"/proc/self/exe"}),
            "runtime_interpreter": str(_trusted_runtime_path(interpreter, compiler=True)),
            "runtime_libc": libc.pop(),
        }
        self.budget.charge("control", len(encoded(profile)))
        return profile

    def _compiler_tools(self, cxx, names):
        compiler = str(Path("/usr/bin/g++" if cxx else "/usr/bin/cc").resolve(strict=True))
        executables = [compiler]
        for name in names:
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
        return compiler, tuple(sorted(set(executables)))

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
        compiler, executables = self._compiler_tools(
            cxx, ("cc1plus" if cxx else "cc1", "collect2", "as", "ld", "nm", "strip"),
        )
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
        inputs = result.input_identities
        key = hashlib.sha256(encoded([digest, inputs])).hexdigest()
        if key not in self.native_tools:
            self.budget.charge("cache", len(encoded([digest, inputs])))
            path = self.tree.parent / ("native-" + key)
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
                outputs=tuple(outputs),
            ),
            native=tool,
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
        if makefile not in self.snapshot.files and makefile not in self.published_sources:
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
        receipts = {}
        command_results = {}
        generated_paths = self.generated_paths
        generated_directories = self.generated_directories
        confirmed = 0
        depth = self.make_depth
        # A query without registrations or inherited outputs has no publication
        # authority. Do not copy the complete tree's unused reservation list.
        publication_allowed = commands is not None or bool(self.published_sources)
        commands = {} if commands is None else commands

        def cleanup_generated():
            if depth:
                return
            finish_cleanup([
                *(lambda name=name: (self.tree / name).unlink(missing_ok=True) for name in generated_paths),
                *(lambda name=name: (self.tree / name).rmdir() if (self.tree / name).exists() else None
                  for name in sorted(generated_directories, key=lambda value: (-value.count("/"), value))),
                generated_paths.clear, generated_directories.clear,
                self.published_sources.clear, self.published_versions.clear,
            ])

        def acknowledge(completed):
            nonlocal confirmed
            if not confirmed <= completed <= len(receipts):
                raise MakeProbeError("invalid producer publication acknowledgement")
            for index in range(confirmed, completed):
                for item in receipts[index][2].generated:
                    self.publication_serial += 1
                    version = receipts[index][3], self.publication_serial
                    self.budget.charge("cache", len(encoded([item.path, version])))
                    self.published_sources[item.path] = item
                    self.published_versions[item.path] = version
            confirmed = completed

        def produce(event, sequence):
            publication_start = self.publication_serial
            command = _event_command(event)
            if sequence != len(receipts) + 1:
                raise MakeProbeError("producer request slot is stale or duplicated")
            if command not in commands:
                raise MakeProbeError(f"unregistered eager/recursive Make command: {command!r}")
            pending = self.pending_commands
            if pending >= self.budget.limits.pending:
                self.budget.reject("registered-command pending count exceeds aggregate bound")
            with cleanup_scope([lambda: setattr(self, "pending_commands", pending)]):
                self.pending_commands = pending + 1
                registration = commands[command]
                if not isinstance(registration, Command):
                    raise MakeProbeError("producer registration requires a typed Command")
                outputs = self._output_paths(registration.outputs)
                for name in outputs:
                    if any(name.startswith(other + "/") or other.startswith(name + "/") for other in generated_paths):
                        raise MakeProbeError("conflicting generated output namespaces")
                    path = self.tree / name
                    if (path.exists() or path.is_symlink()) and name not in generated_paths:
                        raise MakeProbeError("generated output would replace an unowned source object")
                    generated_paths.add(name)
                    generated_directories.update(
                        parent.as_posix() for parent in PurePosixPath(name).parents
                        if parent.as_posix() != "." and not (self.tree / parent).exists()
                    )
                result = self.command(registration)
                inputs = result.consumed
                identity = {
                    "argv": list(registration.argv), "directories": sorted(set(registration.directories)),
                    "inputs": list(result.input_identities),
                }
                if registration.dependency_only:
                    identity["dependency_only"] = True
                    identity["executed"] = list(result.executed)
                if registration.native_tool is not None:
                    tool = registration.native_tool
                    identity["native_tool"] = {"sha256": tool.digest, "inputs": list(tool.inputs)}
                record = {
                    "command": identity, "output_sha256": hashlib.sha256(result.stdout).hexdigest(),
                }
                if result.generated:
                    record["generated_outputs"] = [
                        (item.path, f"{stat.S_IFREG | item.mode:06o}", hashlib.sha256(item.data).hexdigest())
                        for item in result.generated
                    ]
                producer = hashlib.sha256(encoded([
                    registration.argv, sorted(set(registration.code)), inputs,
                    sorted(set(registration.directories)), outputs,
                    None if registration.native_tool is None else registration.native_tool.digest,
                ])).hexdigest()
                key = f"{sequence - 1:016x}"
                self.budget.charge("mapping", len(command.encode("utf-8")) + len(result.stdout) + len(encoded(record)))
                (mapping_path / (key + ".cmd")).write_bytes(command.encode("utf-8"))
                (mapping_path / (key + ".out")).write_bytes(result.stdout)
                if result.generated:
                    frame = bytearray(b"VOGEN1\0\0" + bytes.fromhex(producer))
                    frame.extend(struct.pack("<I", len(result.generated)))
                    for item in result.generated:
                        name = item.path.encode("utf-8")
                        frame.extend(struct.pack("<III", len(name), item.mode, len(item.data)))
                        frame.extend(name)
                        frame.extend(item.data)
                    if len(frame) > self.budget.limits.file_bytes:
                        self.budget.reject("generated result mapping exceeds file byte bound")
                    self.budget.charge("mapping", len(frame))
                    (mapping_path / (key + ".files")).write_bytes(frame)
                result_identity = hashlib.sha256(encoded(record)).hexdigest()
                command_results.setdefault(result_identity, record)
                receipts[sequence - 1] = (command, result_identity, result, producer)
                reply = {
                    "slot": sequence - 1, "owner": producer, "outputs": list(outputs),
                    "stdout_sha256": record["output_sha256"],
                }
                adopted = self._publication_records(publication_start)
                if adopted:
                    data = encoded(adopted)
                    if len(data) > self.budget.limits.file_bytes:
                        self.budget.reject("nested publication mapping exceeds file byte bound")
                    self.budget.charge("mapping", len(data))
                    (mapping_path / (key + ".adopt")).write_bytes(data)
                    reply["adopt_sha256"] = hashlib.sha256(data).hexdigest()
                return reply

        with cleanup_scope([
            cleanup_generated, receipts.clear,
            lambda: _remove_owned_tree(control), lambda: _remove_owned_tree(root),
            lambda: setattr(self, "make_depth", depth),
        ]):
            self.make_depth = depth + 1
            self._new_root(root_name, make=True)
            control.mkdir(mode=0o700)
            mapping_path = control / "map"
            mapping_path.mkdir()
            events_path, result_path = control / "events", control / "result"
            events_path.touch()
            result_path.touch()
            (control / "interceptor").touch()
            (mapping_path / "count").write_bytes((0).to_bytes(4, "little"))
            completed, observed = self._sandbox_run(
                root, mode="make", argv=["/usr/bin/make", "-f", makefile, *cli, target],
                environment=environment,
                mounts=[
                    self._mount(self.tree, "/repo"),
                    self._mount(control, "/control", writable=True),
                    self._mount(self.base / "interceptor", "/control/interceptor", executable=True),
                    self._mount(Path("/dev/null"), "/dev/null", writable=True),
                ],
                producer_handler=produce, publication_observer=acknowledge,
                publication_allowed=publication_allowed,
            )
            raw_events = self.budget.read_bytes(events_path, "event")
            native_events = Counter(bytes.fromhex(item) for item in observed["events"])
            self.budget.charge(
                "control", sum(len(frame) * count for frame, count in native_events.items()),
            )
            events = []
            seen = set()
            for data, event in _read_event_frames(raw_events, expected_mapping_count=None):
                if native_events[data] == 0:
                    raise MakeProbeError("trusted interceptor frame differs from its native write")
                native_events[data] -= 1
                if len(data) < 20:
                    raise MakeProbeError("truncated live producer completion")
                slot = int.from_bytes(data[:4], "little", signed=True)
                if slot not in receipts or slot in seen or _event_command(event) != receipts[slot][0]:
                    raise MakeProbeError("unknown or repeated live producer completion")
                seen.add(slot)
                events.append(event)
            if any(native_events.values()):
                raise MakeProbeError("trusted interceptor frame differs from its native write")
            if seen != set(receipts) or confirmed != len(receipts):
                raise MakeProbeError("incomplete live producer transcript")
            if completed.returncode:
                raise MakeProbeError(f"GNU Make failed after live producers: {completed.returncode}; {completed.stderr!r}")
            semantics = _read_observation(self.budget.read_bytes(result_path, "control"), target, variables)
            semantics["assignments"] = sorted(assignments, key=lambda item: item[1])
            recipe_sources = {record["source"] for record in semantics["files"] if record["source"]}
            semantics["owner_inputs"] = self.source_owners(set(owner_inputs) | recipe_sources)
            semantics["dynamic_commands"] = sorted(command_results.values(), key=encoded)
            semantic_bytes = encoded(semantics)
            self.budget.charge("control", len(semantic_bytes))
            execution = self.snapshot.digest
            if self.runtime_inputs:
                runtime = [
                    [item.path, item.canonical, item.mode, item.parents, item.aliases,
                     None if item.data is None else hashlib.sha256(item.data).hexdigest()]
                    for item in self.runtime_inputs
                ]
                execution = hashlib.sha256(encoded([execution, runtime])).hexdigest()
            return MakeObservation(
                target, semantics, execution, hashlib.sha256(semantic_bytes).hexdigest(),
                completed.stdout, completed.stderr, tuple(events),
            )

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
