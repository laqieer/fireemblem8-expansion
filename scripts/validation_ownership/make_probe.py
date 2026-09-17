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
import weakref
from collections import Counter
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path, PurePosixPath
from threading import get_ident, main_thread
from types import MappingProxyType

from .authority import (
    AuthorityLoader, ENVIRONMENT, Frames, Snapshot, _command_hash, _event_command,
    _read_event_frames, _read_events, encoded, parse_json, relative_path,
)
from .budget import Limits, MakeProbeError, NAMESPACE_LAUNCHER, ProbeBudget, text
from .lifecycle import cleanup_scope, finish_cleanup
from . import metadata_transport
from . import private_install as install_protocol
from . import header_effects
from . import arm_headers
from . import header_runtime as header_protocol
from . import read_epochs
from . import source_phases
from .producer_channel import (
    ChannelError, ProducerChannel, PUBLICATION_MAGIC, PUBLICATION_POLICIES,
    publication_identity, validate_publication_confirmation,
    validate_dispatch_context, validate_job_context,
)


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
RUNTIME_TOOLCHAIN_ARCH = ("-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork")
RUNTIME_TOOLCHAIN_QUERIES = {"-print-libgcc-file-name", "-print-file-name=libc.a"}


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
    publication_policy: str = "replace"
    runtime_tool: RuntimeTool | None = None
    stdout_transform: str | None = None

    def __post_init__(self):
        if type(self.publication_policy) is not str or self.publication_policy not in PUBLICATION_POLICIES:
            raise MakeProbeError("unsupported Command publication policy")
        if self.publication_policy != "replace" and not self.outputs:
            raise MakeProbeError("content-only publication requires declared outputs")


class _PrivateInstallLaunch:
    pass


class _HeaderRuntimeLaunch:
    pass


@dataclass(frozen=True, eq=False)
class _PrivateInstallCommand:
    command: object
    binding: bytes
    snapshot: object
    tree: Path
    epoch: int
    inputs: tuple
    destinations: tuple[str, ...]


@dataclass(frozen=True, eq=False)
class _LiveDispatch:
    scope: str
    sequence: int
    arguments: tuple
    cwd: str
    environment: tuple
    rebuilding: bool
    job: tuple
    snapshot: object
    tree: Path
    epoch: int


@dataclass(frozen=True, eq=False)
class _ContextCommand:
    command: object
    snapshot: object
    tree: Path
    epoch: int
    binding: bytes


@dataclass(frozen=True)
class _FilesystemCommand(Command):
    effect: header_effects.Effect | None = None


@dataclass(eq=False)
class _HeaderPipeline:
    scope: str
    target: str
    owner: str
    stage: int
    versions: dict[str, tuple]
    source: str | None = None


@dataclass(frozen=True, eq=False)
class _HeaderStep:
    command: weakref.ReferenceType
    binding: bytes
    pipeline: _HeaderPipeline
    step: int
    snapshot: object
    tree: Path
    epoch: int


@dataclass
class _PendingHeaderEffect:
    command: str
    record: dict
    step: _HeaderStep
    effect: header_effects.Effect
    directories: tuple[str, ...]
    previous: tuple | None


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
class RuntimeTool:
    """A session-issued root-owned runtime executable and captured content identity."""

    path: str
    canonical: str
    mode: int
    digest: str


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
    runtime_receipt: tuple[tuple[str, str, str], ...] = ()
    runtime_sources: tuple[tuple[str, int, int, str], ...] = ()
    runtime_probes: tuple[dict, ...] = ()


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
    generated: tuple[GeneratedFile, ...] = ()
    file_open_attempts: tuple[tuple[str, str], ...] = ()
    read_trace: dict | None = None
    source_phases: dict | None = None


class _NamespaceUnavailable(MakeProbeError):
    """The original directory cannot supply an invariant exact leaf."""


class _OriginalNamespace:
    """Identity-only token; authority stays in its issuing session."""


@dataclass(frozen=True)
class _NamespaceImage:
    snapshot: Snapshot
    tree: Path
    members: dict
    directories: dict
    forbidden: frozenset


@dataclass
class _NamespaceCapture:
    image: _NamespaceImage
    epoch: int
    request: tuple
    stamps: dict
    mutations: list
    native_complete: bool = False
    closed: bool = False
    valid: bool = True


@dataclass(frozen=True)
class _SealedNamespace:
    image: _NamespaceImage
    epoch: int
    request: tuple
    stamps: object
    mutations: tuple


def _namespace_identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _namespace_stamp(info):
    return (*_namespace_identity(info), info.st_mtime_ns, info.st_ctime_ns)


def _star_name(pattern, name):
    if name.startswith(b".") and not pattern.startswith(b"."):
        return False
    parts = pattern.split(b"*")
    if len(parts) == 1:
        return name == pattern
    if not name.startswith(parts[0]) or not name.endswith(parts[-1]):
        return False
    offset, end = len(parts[0]), len(name) - len(parts[-1])
    if offset > end:
        return False
    for part in parts[1:-1]:
        found = name.find(part, offset, end)
        if found < 0:
            return False
        offset = found + len(part)
    return offset <= end


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
        self.runtime_tools = {}
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
        self.runtime_query_profiles = {}
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
        self._private_install_commands = {}
        self._private_install_launches = {}
        self._private_install_issued = weakref.WeakSet()
        self._private_install_launch_issued = weakref.WeakSet()
        self._live_dispatches = []
        self._issued_dispatches = weakref.WeakSet()
        self._command_dispatches = []
        self._native_context_commands = {}
        self._issued_context_commands = weakref.WeakSet()
        self._header_pipelines = {}
        self._header_commands = {}
        self._issued_header_steps = weakref.WeakSet()
        self._header_profiles = {}
        self._header_launches = {}
        self._issued_header_launches = weakref.WeakSet()
        self._read_epoch_abi = None
        self._source_phase_records = {}
        self._namespace_images = {}
        self._namespace_frames = []
        self._namespace_pending = {}
        self._namespace_publications = {}
        self._namespace_issued = {}
        self._namespace_tokens = {}
        self._namespace_epoch = 0
        self._namespace_mutation_serial = 0

    def _expire_namespaces(self):
        self._namespace_epoch += 1
        self._namespace_issued.clear()
        self._namespace_tokens.clear()

    def _namespace_directory(self, name):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME
        descriptor = os.open(self.tree, flags)
        try:
            if name != ".":
                for component in relative_path(name).split("/"):
                    following = os.open(
                        component, flags,
                        dir_fd=descriptor,
                    )
                    os.close(descriptor)
                    descriptor = following
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _capture_namespace_image(self, *, inherited=False):
        files, directories = set(), {"."}
        for values, destination in (
            (self.snapshot.files, files), (self.published_sources if inherited else (), files),
            (self.snapshot.gitlink_roots, directories),
        ):
            for name in values:
                self.budget.remaining()
                if name not in destination:
                    self.budget.charge("cache", len(encoded(name)) + 1)
                    destination.add(name)
                for parent in PurePosixPath(name).parents:
                    value = parent.as_posix()
                    if value not in directories:
                        self.budget.charge("cache", len(encoded(value)) + 1)
                        directories.add(value)
        expected = files | directories
        members, identities, found = {}, {}, set()
        pending = ["."]
        while pending:
            self.budget.remaining()
            directory = pending.pop()
            descriptor = self._namespace_directory(directory)
            try:
                before = os.fstat(descriptor)
                found.add(directory)
                identities[directory] = _namespace_identity(before)
                children = []
                with os.scandir(descriptor) as entries:
                    for entry in entries:
                        self.budget.remaining()
                        name = entry.name if directory == "." else directory + "/" + entry.name
                        relative_path(name)
                        info = entry.stat(follow_symlinks=False)
                        if name not in expected or (
                            name in files and not stat.S_ISREG(info.st_mode)
                            or name in directories and not stat.S_ISDIR(info.st_mode)
                        ):
                            raise MakeProbeError("original namespace differs from admitted materialization")
                        # Snapshot bounds source entries, not their derived directory scaffolding.
                        if len(found) >= len(expected):
                            self.budget.reject("original namespace exceeds admitted extent")
                        row = entry.name, _namespace_identity(info)
                        self.budget.charge("cache", len(encoded((directory, row))))
                        children.append(row)
                        found.add(name)
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(name)
                if _namespace_stamp(os.fstat(descriptor)) != _namespace_stamp(before):
                    raise MakeProbeError("original namespace changed during capture")
                self.budget.charge("cache", len(encoded((directory, identities[directory]))))
                members[directory] = tuple(sorted(children, key=lambda item: item[0].encode("utf-8")))
            finally:
                os.close(descriptor)
        if found != expected:
            raise MakeProbeError("original namespace materialization is incomplete")
        forbidden = frozenset(
            name for name, entry in self.loader.entries.items()
            if entry.mode not in {"100644", "100755"}
            and name not in self.snapshot.absent_paths and name not in self.snapshot.gitlink_roots
        )
        self.budget.charge("cache", len(encoded(sorted(forbidden))))
        return _NamespaceImage(
            self.snapshot, self.tree, MappingProxyType(members), MappingProxyType(identities), forbidden,
        )

    def _begin_namespace(self, target, makefile, assignments):
        if (
            set(self.published_sources) != set(self._namespace_publications)
            or set(self.published_versions) != set(self._namespace_publications)
        ):
            raise MakeProbeError("original namespace has unreceipted inherited publications")
        for name, (value, version) in self._namespace_publications.items():
            if self.published_sources[name] is not value or self.published_versions[name] != version:
                raise MakeProbeError("original namespace inherited publication identity changed")
            self._verify_effective_output(value, {"identity": version[2]})
        key = id(self.snapshot), self.tree
        if key not in self._namespace_images:
            self._namespace_images[key] = self._capture_namespace_image(inherited=bool(self.published_sources))
        image = (
            self._capture_namespace_image(inherited=True)
            if self.published_sources else self._namespace_images[key]
        )
        stamps = {}
        for name, identity in image.directories.items():
            self.budget.remaining()
            descriptor = self._namespace_directory(name)
            try:
                info = os.fstat(descriptor)
                if _namespace_identity(info) != identity:
                    raise MakeProbeError("original namespace lookup identity changed")
                stamp = _namespace_stamp(info)
                self.budget.charge("cache", len(encoded((name, stamp))))
                stamps[name] = stamp
            finally:
                os.close(descriptor)
        request = target, makefile, tuple(assignments)
        self.budget.charge("cache", len(encoded(request)))
        capture = _NamespaceCapture(image, self._namespace_epoch, request, stamps, [])
        self._namespace_pending[id(capture)] = capture, None
        self._namespace_frames.append(capture)
        return capture

    def _namespace_mutation(self, action, name, identity=()):
        if not self._namespace_frames:
            return
        if self.budget.failed or self.budget.closed:
            for frame in self._namespace_frames:
                frame.valid = False
            return
        self._namespace_mutation_serial += 1
        record = self._namespace_mutation_serial, action, name, tuple(identity)
        for frame in self._namespace_frames:
            self.budget.charge("cache", len(encoded(record)))
            frame.mutations.append(record)

    def _end_namespace(self, capture):
        if not self._namespace_frames or self._namespace_frames[-1] is not capture:
            raise MakeProbeError("original namespace capture lifetime is inconsistent")
        self._namespace_frames.pop()
        capture.closed = True

    @staticmethod
    def _namespace_observation_context(observation):
        domains = observation.semantics["domains"]
        return encoded((
            observation.target, observation.execution_digest, observation.semantic_digest,
            observation.semantics["assignments"], domains.get("MAKEFILE_LIST"), domains.get("MAKE_RESTARTS"),
            observation.semantics["published_sources"], observation.events,
        ))

    def _seal_namespace(self, capture, observation):
        self.budget.remaining()
        pending = self._namespace_pending.pop(id(capture), None)
        if (
            pending is None or pending[0] is not capture or pending[1] is not observation
            or not capture.closed or not capture.native_complete or not capture.valid
        ):
            raise MakeProbeError("incomplete original namespace capture cannot issue authority")
        capture = _SealedNamespace(
            capture.image, capture.epoch, capture.request, MappingProxyType(capture.stamps), tuple(capture.mutations),
        )
        token = _OriginalNamespace()
        key = id(observation)
        context = self._namespace_observation_context(observation)
        self.budget.charge("cache", len(context))

        def expire(reference):
            record = self._namespace_issued.get(key)
            if record is not None and record[0] is reference:
                self._namespace_tokens.pop(id(record[1]), None)
                del self._namespace_issued[key]

        reference = weakref.ref(observation, expire)
        record = reference, token, capture, context
        self._namespace_issued[key] = record
        self._namespace_tokens[id(token)] = record

    def _original_namespace(self, observation, *, target, makefile, assignments=()):
        self.budget.remaining()
        record = self._namespace_issued.get(id(observation))
        if record is None or record[0]() is not observation:
            raise MakeProbeError("original namespace requires an issued native observation")
        capture = record[2]
        if capture.request != (target, makefile, tuple(assignments)):
            raise MakeProbeError("original namespace invocation/state mismatch")
        self._require_namespace(record[1])
        return record[1]

    def _require_namespace(self, token):
        self.budget.remaining()
        if get_ident() != self.owner_thread or self.base is None or self.snapshot is None:
            raise MakeProbeError("original namespace has no active owning session")
        record = self._namespace_tokens.get(id(token))
        if record is None or record[1] is not token or record[0]() is None:
            raise MakeProbeError("original namespace token is forged or expired")
        _, _, capture, context = record
        if (
            capture.epoch != self._namespace_epoch or capture.image.snapshot is not self.snapshot
            or capture.image.tree != self.tree
            or self._namespace_observation_context(record[0]()) != context
        ):
            raise MakeProbeError("original namespace view/lifetime/observation changed")
        descriptor = self._namespace_directory(".")
        try:
            if _namespace_identity(os.fstat(descriptor)) != capture.image.directories["."]:
                raise MakeProbeError("original namespace source-root mapping changed")
        finally:
            os.close(descriptor)
        return capture

    def _retain_source_phases(self, observation, images, capture):
        if not capture.closed or not capture.valid or not capture.native_complete:
            raise MakeProbeError("original entry images lack a completed native lifetime")
        context = encoded((observation.read_trace, observation.source_phases))
        self.budget.charge("cache", len(context))
        key = id(observation)
        def expired(reference):
            record = self._source_phase_records.get(key)
            if record is not None and record[0] is reference:
                del self._source_phase_records[key]
        self._source_phase_records[key] = (
            weakref.ref(observation, expired), tuple(images), self.snapshot, self.tree,
            self._namespace_epoch, context,
        )

    def _source_phase_images(self, observation):
        self.budget.remaining()
        record = self._source_phase_records.get(id(observation))
        if (
            record is None or record[0]() is not observation
            or record[2] is not self.snapshot or record[3] != self.tree or record[4] != self._namespace_epoch
            or record[5] != encoded((observation.read_trace, observation.source_phases))
        ):
            raise MakeProbeError("original entry-image observation is forged, changed or outlived its view")
        namespace = self._namespace_issued.get(id(observation))
        if namespace is None or namespace[0]() is not observation:
            raise MakeProbeError("original entry images lost their namespace lifetime")
        self._require_namespace(namespace[1])
        return record[1]

    def _invariant_directory(self, capture, directory):
        try:
            return self._verify_invariant_directory(capture, directory)
        except OSError as error:
            raise _NamespaceUnavailable("original wildcard lookup is unavailable: " + directory) from error

    def _verify_invariant_directory(self, capture, directory):
        image = capture.image
        for forbidden in image.forbidden:
            if directory == forbidden or directory.startswith(forbidden + "/"):
                raise _NamespaceUnavailable("original wildcard enters a nonregular namespace: " + directory)
        ancestor = directory
        while ancestor not in image.directories:
            ancestor = PurePosixPath(ancestor).parent.as_posix()
        for _, action, path, _ in capture.mutations:
            if action == "retained":
                continue
            if (
                ancestor == "." or path == ancestor or path.startswith(ancestor + "/")
                or ancestor.startswith(path + "/")
            ):
                raise _NamespaceUnavailable("original wildcard namespace changed during invocation: " + directory)
        for parent in PurePosixPath(ancestor).parents:
            name = parent.as_posix()
            if name not in image.directories:
                raise _NamespaceUnavailable("original wildcard lookup ancestor is unadmitted")
            descriptor = self._namespace_directory(name)
            try:
                if _namespace_identity(os.fstat(descriptor)) != image.directories[name]:
                    raise _NamespaceUnavailable("original wildcard lookup ancestor changed: " + name)
            finally:
                os.close(descriptor)
        descriptor = self._namespace_directory(ancestor)
        try:
            if _namespace_stamp(os.fstat(descriptor)) != capture.stamps[ancestor]:
                raise _NamespaceUnavailable("original wildcard lookup identity changed: " + ancestor)
            actual = []
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    self.budget.remaining()
                    row = entry.name, _namespace_identity(entry.stat(follow_symlinks=False))
                    self.budget.charge("cache", len(encoded(row)))
                    actual.append(row)
            if tuple(sorted(actual, key=lambda item: item[0].encode("utf-8"))) != image.members[ancestor]:
                raise _NamespaceUnavailable("original wildcard directory membership changed: " + ancestor)
            if _namespace_stamp(os.fstat(descriptor)) != capture.stamps[ancestor]:
                raise _NamespaceUnavailable("original wildcard directory changed during lookup")
        finally:
            os.close(descriptor)
        if ancestor != directory:
            return ()
        return image.members[directory]

    def _original_wildcard(self, token, patterns):
        capture = self._require_namespace(token)
        if not isinstance(patterns, str) or any(character in patterns for character in "$\\?[]~\0"):
            raise _NamespaceUnavailable("unsupported original wildcard pattern grammar")
        result = []
        for match in re.finditer(r"[^ \t\r\n\v\f]+", patterns):
            self.budget.remaining()
            pattern = match[0]
            self.budget.charge("cache", len(encoded(pattern)))
            raw_basename = pattern.rsplit("/", 1)[-1].encode("utf-8")
            if _star_name(raw_basename, b".") or _star_name(raw_basename, b".."):
                # scandir's materialized members do not represent GNU's logical entries.
                raise _NamespaceUnavailable("original wildcard can match unrepresented logical dot entries")
            try:
                relative_path(pattern)
            except MakeProbeError as error:
                raise _NamespaceUnavailable("unsupported original wildcard path spelling") from error
            directory = PurePosixPath(pattern).parent.as_posix()
            basename = PurePosixPath(pattern).name
            if "*" in directory:
                raise _NamespaceUnavailable("original wildcard requires a literal directory")
            names = self._invariant_directory(capture, directory)
            for forbidden in capture.image.forbidden:
                if PurePosixPath(forbidden).parent.as_posix() == directory and _star_name(
                    basename.encode("utf-8"), PurePosixPath(forbidden).name.encode("utf-8"),
                ):
                    raise _NamespaceUnavailable("original wildcard matches an unadmitted object: " + forbidden)
            for name, identity in names:
                self.budget.remaining()
                if not _star_name(basename.encode("utf-8"), name.encode("utf-8")):
                    continue
                if (
                    any(character.isspace() or character in "$\\" for character in name)
                    or not (stat.S_ISREG(identity[2]) or stat.S_ISDIR(identity[2]))
                ):
                    raise _NamespaceUnavailable("original wildcard match has unsupported spelling/type")
                value = name if directory == "." else directory + "/" + name
                self.budget.charge("cache", len(encoded(value)) + 1)
                result.append(value)
        return " ".join(result)

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
                self._expire_namespaces()
                self._namespace_images.pop((id(self.snapshot), self.tree), None)
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
                    self._expire_namespaces()
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
            self._expire_namespaces()
            self._namespace_images.clear()
            self._namespace_frames.clear()
            self._namespace_pending.clear()
            self._namespace_publications.clear()
            self.cache.clear()
            self.mappings.clear()
            self.native_tools.clear()
            self.runtime_tools.clear()
            self.published_sources.clear()
            self.published_versions.clear()
            self.generated_paths.clear()
            self.generated_directories.clear()
            self.parked_capsules.clear()
            self.make_runtime = ()
            self.dependency_compiler = None
            self.dependency_runtime = None
            self.runtime_query_profiles.clear()
            self._private_install_commands.clear()
            self._private_install_launches.clear()
            self._private_install_issued.clear()
            self._private_install_launch_issued.clear()
            self._live_dispatches.clear()
            self._issued_dispatches.clear()
            self._command_dispatches.clear()
            self._native_context_commands.clear()
            self._issued_context_commands.clear()
            self._header_pipelines.clear()
            self._header_commands.clear()
            self._issued_header_steps.clear()
            self._header_profiles.clear()
            self._header_launches.clear()
            self._issued_header_launches.clear()
            self._read_epoch_abi = None
            self._source_phase_records.clear()
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
            stock_dispatch_alias = bool(item.aliases) and item.canonical in ALIASES
            intercepted = item.data is not None and (
                stock_dispatch_alias
                or item.canonical == "/usr/bin/env"
            )
            absent_dispatch_alias = item.data is None and stock_dispatch_alias
            if (
                any(path == other or path.startswith(other + "/") or other.startswith(path + "/")
                    for other in reserved)
                or item.canonical in reserved and not (intercepted or absent_dispatch_alias)
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

    def runtime_tool(self, path):
        """Issue one exact captured system executable for a typed confined command."""
        self.budget.remaining()
        for item in self.runtime_inputs:
            if path not in {item.path, item.canonical}:
                continue
            if item.data is None or item.mode is None or not item.mode & 0o111:
                raise MakeProbeError(f"trusted runtime tool is unavailable: {path}")
            if item.canonical != str(_trusted_runtime_path(path)):
                raise MakeProbeError(f"trusted runtime tool identity changed: {path}")
            key = (
                item.canonical, item.canonical, item.mode,
                hashlib.sha256(item.data).hexdigest(),
            )
            tool = self.runtime_tools.get(key)
            if tool is None:
                tool = RuntimeTool(*key)
                self.runtime_tools[key] = tool
                self.budget.charge("control", len(encoded(key)))
            return tool
        raise MakeProbeError(f"runtime tool was not captured by this probe session: {path}")

    def _verify_runtime_tool(self, tool):
        if not isinstance(tool, RuntimeTool) or not any(
            tool is issued for issued in self.runtime_tools.values()
        ):
            raise MakeProbeError("runtime tool is not issued by this exact probe session")
        if str(_trusted_runtime_path(tool.path)) != tool.canonical:
            raise MakeProbeError("trusted runtime tool path changed after capture")
        try:
            mode = stat.S_IMODE(Path(tool.path).lstat().st_mode)
        except OSError as error:
            raise MakeProbeError("trusted runtime tool became unavailable") from error
        data = self.budget.read_bytes(Path(tool.canonical), "control")
        if mode != tool.mode or hashlib.sha256(data).hexdigest() != tool.digest:
            raise MakeProbeError("trusted runtime tool changed after capture")

    @staticmethod
    def _compiler_runtime_aliases():
        first = Path("/usr/lib/arm-none-eabi/lib")
        try:
            first_info = first.lstat()
        except FileNotFoundError:
            return ()
        if not stat.S_ISLNK(first_info.st_mode):
            return ()
        first_target = os.readlink(first)
        second = Path(first_target)
        if not second.is_absolute():
            second = first.parent / second
        second = Path(os.path.normpath(second))
        resolved = first.resolve(strict=True)
        if not resolved.is_relative_to("/usr/lib/arm-none-eabi"):
            raise MakeProbeError("modern compiler runtime library alias escaped system newlib")
        receipt = [(str(first), first_target, str(resolved))]
        if second.is_relative_to("/usr"):
            parents = {first.parent, resolved, resolved.parent}
            for parent in parents:
                info = parent.lstat()
                if (
                    not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                ):
                    raise MakeProbeError("modern compiler runtime alias has a mutable ancestor")
            return tuple(receipt)
        if second != Path("/etc/alternatives/gcc-arm-none-eabi-lib"):
            raise MakeProbeError("modern compiler runtime has an unsupported library alias")
        try:
            second_info = second.lstat()
        except OSError as error:
            raise MakeProbeError("modern compiler runtime library alias is unavailable") from error
        second_target = os.readlink(second) if stat.S_ISLNK(second_info.st_mode) else ""
        if (
            first_info.st_uid != 0 or second_info.st_uid != 0
            or not stat.S_ISLNK(second_info.st_mode)
            or second_target != "/usr/lib/arm-none-eabi/newlib"
            or second.resolve(strict=True) != Path(second_target)
        ):
            raise MakeProbeError("modern compiler runtime library alias is mutable or unsupported")
        parents = {first.parent, second.parent, Path(second_target), Path(second_target).parent}
        for parent in parents:
            info = parent.lstat()
            if (
                not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise MakeProbeError("modern compiler runtime alias has a mutable ancestor")
        receipt.append((str(second), second_target, str(second.resolve(strict=True))))
        return tuple(receipt)

    @staticmethod
    def _runtime_tool_query(command):
        if (
            command.runtime_tool is None
            or Path(command.runtime_tool.path).name not in {
                "arm-none-eabi-gcc", "arm-none-eabi-gcc.exe",
            }
            or command.stdout_transform != "dirname"
        ):
            raise MakeProbeError("runtime tool lacks one supported typed metadata query")
        arguments = command.argv[1:]
        if arguments and arguments[0].startswith("-B"):
            if arguments[0] not in {"-B/usr/bin/", "-B/bin/"}:
                raise MakeProbeError("runtime tool metadata query escaped its binutils profile")
            arguments = arguments[1:]
        if (
            tuple(arguments[:-1]) != RUNTIME_TOOLCHAIN_ARCH
            or not arguments or arguments[-1] not in RUNTIME_TOOLCHAIN_QUERIES
        ):
            raise MakeProbeError("runtime tool metadata query escaped its exact profile")

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

    def _original_read_abi(self):
        if self._read_epoch_abi is not None:
            return self._read_epoch_abi
        data = dict(self.make_runtime)["/usr/bin/make"]
        if self.budget.read_bytes(_trusted_runtime_path("/usr/bin/make"), "control") != data:
            raise MakeProbeError("Make read-ABI image differs from its captured runtime")
        image = read_epochs.Elf(data)
        first = self.budget.run(
            ["/usr/bin/objdump", "-d", "-w", "--disassemble=read_all_makefiles", "/usr/bin/make"],
            env=ENVIRONMENT,
        )
        if first.returncode:
            raise MakeProbeError("cannot decode actual Make read entry")
        target = read_epochs.source_target(image, read_epochs.instructions(first.stdout, image))
        end = min(
            start + size for start, extent, offset, size, flags in image.loads
            if start <= target < start + size and flags & 1
        )
        second = self.budget.run(
            ["/usr/bin/objdump", "-d", "-w", "--start-address=" + hex(target),
             "--stop-address=" + hex(min(target + 65536, end)), "/usr/bin/make"],
            env=ENVIRONMENT,
        )
        if second.returncode or self.budget.read_bytes(_trusted_runtime_path("/usr/bin/make"), "control") != data:
            raise MakeProbeError("actual Make source-reader decoding failed or changed")
        self._read_epoch_abi = read_epochs.make_abi(data, first.stdout, second.stdout)
        self.budget.charge("cache", len(encoded(self._read_epoch_abi)))
        return self._read_epoch_abi

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
        for name, (owner, serial, identity) in sorted(self.published_versions.items()):
            self.budget.remaining()
            if serial > since:
                item = self.published_sources[name]
                result.append([
                    name, owner, item.mode, len(item.data), hashlib.sha256(item.data).hexdigest(),
                    list(identity),
                ])
        return result

    def _verify_effective_output(self, item, outcome):
        identity = tuple(outcome["identity"])
        directory = os.open(self.tree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = relative_path(item.path).split("/")
            for part in parts[:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = following
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "rb") as stream:
                if publication_identity(os.fstat(stream.fileno())) != identity:
                    raise MakeProbeError("effective publication metadata differs from its actual object")
                self.budget.charge("control", len(item.data))
                offset = 0
                while offset < len(item.data):
                    self.budget.remaining()
                    data = stream.read(min(65536, len(item.data) - offset))
                    if not data or data != item.data[offset:offset + len(data)]:
                        raise MakeProbeError("effective publication content differs from its produced output")
                    offset += len(data)
                if (
                    publication_identity(os.fstat(stream.fileno())) != identity
                    or publication_identity(os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)) != identity
                ):
                    raise MakeProbeError("effective publication changed during confirmation")
        except OSError as error:
            raise MakeProbeError(f"effective publication could not be confirmed: {error}") from error
        finally:
            os.close(directory)

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
            for target in sorted((set(ALIASES) | {
                item.canonical for item in self.runtime_inputs if item.path in self.runtime_dispatch
            }) - {
                item.canonical for item in self.runtime_inputs
                if item.data is None and item.aliases and item.canonical in ALIASES
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
        dependency=None, observe_recipe_dispatch=False, private_install=None,
        header_runtime=None,
        observe_read_epochs=False,
        observe_source_phases=False, source_phase_observer=None,
    ):
        self.budget.remaining()
        if (
            type(observe_source_phases) is not bool
            or observe_source_phases and (mode != "make" or producer_handler is None or source_phase_observer is None)
            or not observe_source_phases and source_phase_observer is not None
        ):
            raise MakeProbeError("original entry images require their exact Make observer")
        if type(observe_read_epochs) is not bool:
            raise MakeProbeError("original read observation requires an exact Make selection")
        observe_read_epochs = observe_read_epochs or observe_source_phases
        if type(observe_read_epochs) is not bool or observe_read_epochs and mode != "make":
            raise MakeProbeError("original read observation requires an exact Make selection")
        if observe_read_epochs:
            environment = {**environment, "VO_OBSERVE_READS": "1"}
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
            "observation_count": min(
                self.budget.limits.entries,
                self.budget.limits.observation_count - self.observations_used,
            ),
            "observation_limit": min(
                self.budget.limits.file_bytes,
                self.budget.limits.control_bytes - self.budget.bytes.get("control", 0),
            ),
        }
        if dependency is not None:
            if mode != "compile":
                raise MakeProbeError("dependency profile requires compiler confinement")
            config["dependency"] = dependency
        needs_header = dependency is not None and bool({"header_search", "filter_kernel"} & set(dependency))
        if needs_header or header_runtime is not None:
            record = self._header_launches.pop(id(header_runtime), None)
            if (
                not needs_header or type(header_runtime) is not _HeaderRuntimeLaunch
                or header_runtime not in self._issued_header_launches
                or record is None or record[0] is not header_runtime
            ):
                raise MakeProbeError("header runtime launch is unissued, forged or already consumed")
            self._issued_header_launches.discard(header_runtime)
            _, command, step, binding = record
            if (
                self._require_header_step(command, self._require_live_dispatch()) is not step
                or self._header_runtime_kind(command) is None
                or binding != header_protocol.launch_binding(config)
            ):
                raise MakeProbeError("header runtime launch differs from its issued job/view/workspace")
            config["header_runtime"] = {
                "version": 1, "scope": install_protocol.launch_scope(root), "binding": binding,
            }
            try:
                header_protocol.validate_launch(config)
            except ChannelError as error:
                raise MakeProbeError(str(error)) from error
        install_spec = None
        if private_install is not None:
            record = self._private_install_launches.pop(id(private_install), None)
            if (
                type(private_install) is not _PrivateInstallLaunch
                or record is None or record[0] is not private_install
                or private_install not in self._private_install_launch_issued
            ):
                raise MakeProbeError("private install launch is forged, expired or already consumed")
            self._private_install_launch_issued.discard(private_install)
            _, command, expected_root, output, expected_argv, expected_environment, parents = record
            permission = self._require_private_install(command)
            if (
                permission is None or root != expected_root or tuple(argv) != expected_argv
                or mode != "command" or dependency is not None or metadata_validation
                or producer_handler is not None or publication_observer is not None
                or executables is not None
                or tuple(code) != tuple(sorted(set(command.code)))
                or tuple(sources) != self.sources(command.sources)
                or tuple(directories) != self._directories(command.directories)
                or tuple(sorted(environment.items())) != expected_environment
                or [item for item in mounts if item["writable"]] != [
                    self._mount(output, "/work", writable=True),
                    self._mount(Path("/dev/null"), "/dev/null", writable=True),
                ]
            ):
                raise MakeProbeError("private install launch differs from its issued command/workspace")
            config["private_install"] = {
                "version": 1, "scope": install_protocol.launch_scope(root),
                "binding": install_protocol.launch_binding(config),
                "parents": parents, "destinations": ["/work/" + name for name in permission.destinations],
            }
            try:
                install_spec = install_protocol.validate_config(config["private_install"], config)
            except install_protocol.InstallError as error:
                raise MakeProbeError(str(error)) from error
        if observe_recipe_dispatch:
            if mode != "make":
                raise MakeProbeError("native recipe dispatch requires Make confinement")
            config["observe_recipe_dispatch"] = True
        counter_names = {
            "processes", "syscalls", "written_bytes", "created_files", "observation_bytes",
            "observations", "live_process_peak", "memory_peak",
        }
        settled = dict.fromkeys(counter_names, 0)
        sequence = 0
        barrier = 0
        completion = None
        channel = None
        channel_directory = self.base / f"producer-{self.serial}"
        if producer_handler is not None:
            if mode != "make":
                raise MakeProbeError("live producer requests require native Make")
            config["producer_scope"] = self.base.name + "/" + root.name
            if observe_read_epochs:
                config["read_epochs"] = {
                    "version": 2 if observe_source_phases else 1,
                    "scope": config["producer_scope"], "abi": self._original_read_abi(),
                }
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
            if (
                prospective["observations"] > self.budget.limits.observation_count
                or values["observations"] > config["observation_count"]
                or not failed and (
                    prospective["processes"] > self.budget.limits.descendants
                    or prospective["syscalls"] > self.budget.limits.syscalls
                    or prospective["created"] > self.budget.limits.created_files
                    or values["live_process_peak"] > config["process_limit"]
                    or values["memory_peak"] > config["memory_limit"]
                    or values["observation_bytes"] < 128*values["observations"]
                )
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
                "observation_count": (
                    settled["observations"], self.budget.limits.observation_count - self.observations_used,
                ),
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
            nonlocal sequence, completion, barrier
            request = parse_json(packet, "producer request")
            if not isinstance(request, dict) or request.get("scope") != config["producer_scope"]:
                raise MakeProbeError("foreign producer request scope")
            if request.get("kind") == "read-barrier":
                if not observe_source_phases:
                    raise MakeProbeError("unrequested original entry-image barrier")
                try:
                    source_phases.validate_barrier(
                        request, scope=config["producer_scope"], barrier=barrier + 1, sequence=sequence,
                    )
                except ChannelError as error:
                    raise MakeProbeError(str(error)) from error
                validate_confirmation(request["completed"], request["publication"])
                reserved = request["reserved"]
                if (
                    not isinstance(reserved, dict) or set(reserved) != {"live", "processes", "memory", "pending"}
                    or any(type(value) is not int for value in reserved.values())
                    or not 1 <= reserved["live"] <= reserved["processes"] <= config["process_limit"]
                    or not 1 <= reserved["memory"] <= config["memory_limit"] or reserved["pending"] != 0
                ):
                    raise MakeProbeError("original entry barrier has unsettled or invalid reservations")
                settle(request["counters"])
                if (
                    reserved["live"] > request["counters"]["live_process_peak"]
                    or reserved["live"] > request["counters"]["processes"]
                    or reserved["memory"] > request["counters"]["memory_peak"]
                ):
                    raise MakeProbeError("original entry reservations contradict measured native state")
                if publication_observer is not None:
                    publication_observer(sequence, request["publication"])
                self.parked_capsules.append(reserved)
                try:
                    image_sha256 = source_phase_observer(request)
                finally:
                    self.parked_capsules.pop()
                reply = {
                    "kind": "read-resume",
                    **{name: request[name] for name in ("scope", "barrier", "exec", "pass", "trace_seq", "input_sha256")},
                    "image_sha256": image_sha256, "limits": grants(),
                }
                bound = len(encoded(reply)) + 4
                reply["limits"] = grants(bound)
                source_phases.validate_resume(reply, request)
                data = encoded(reply)
                if len(data) + 4 > bound:
                    raise MakeProbeError("original read grant encoding exceeded its reservation")
                barrier += 1
                return data
            if request.get("kind") == "finished":
                if (
                    set(request) != {"kind", "scope", "issued", "completed", "publication"}
                    or type(request["issued"]) is not int or request["issued"] != sequence
                    or type(request["completed"]) is not int or not 0 <= request["completed"] <= sequence
                ):
                    raise MakeProbeError("invalid producer completion notification")
                validate_confirmation(request["completed"], request["publication"])
                if publication_observer is not None:
                    publication_observer(request["completed"], request["publication"])
                completion = request["issued"], request["completed"], request["publication"]
                return None
            if (
                set(request) != {
                    "kind", "scope", "sequence", "completed", "frame", "counters", "reserved", "publication",
                    "dispatch", "job",
                }
                or request["kind"] != "request" or type(request["sequence"]) is not int
                or request["sequence"] != sequence + 1 or type(request["completed"]) is not int
                or request["completed"] != sequence or not isinstance(request["frame"], str)
                or len(request["frame"]) > 2*65536 or not re.fullmatch(r"(?:[0-9a-f]{2})+", request["frame"])
            ):
                raise MakeProbeError("malformed, stale or out-of-order producer request")
            validate_confirmation(request["completed"], request["publication"])
            events = _read_events(bytes.fromhex(request["frame"]), expected_mapping_count=0)
            if len(events) != 1 or events[0]["match"] != -1:
                raise MakeProbeError("invalid producer request event")
            try:
                context = validate_dispatch_context(request["dispatch"], events[0]["arguments"])
                job = validate_job_context(request["job"], context["sequence"])
            except ChannelError as error:
                raise MakeProbeError(str(error)) from error
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
                publication_observer(sequence, request["publication"])
            sequence += 1
            self.pending_commands_peak = max(
                self.pending_commands_peak, reserved["pending"] + sum(item["pending"] for item in self.parked_capsules),
            )
            self.budget.charge("cache", len(encoded((context, job))))
            self.parked_capsules.append(reserved)
            live = _LiveDispatch(
                config["producer_scope"], context["sequence"], tuple(context["arguments"]),
                context["cwd"], tuple(sorted(context["environment"].items())),
                context["rebuilding_makefiles"], (job["kind"], job["target"], job["command_line"]),
                self.snapshot, self.tree, self._namespace_epoch,
            )
            self._issued_dispatches.add(live)
            self._live_dispatches.append(live)
            try:
                value = producer_handler(events[0], sequence)
            finally:
                self._live_dispatches.pop()
                self._issued_dispatches.discard(live)
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

        def validate_confirmation(completed, confirmation):
            if completed == 0:
                if confirmation is not None:
                    raise MakeProbeError("publication confirmation precedes any producer")
                return
            try:
                validate_publication_confirmation(
                    confirmation, count_limit=self.budget.limits.created_files,
                    file_limit=self.budget.limits.file_bytes,
                )
            except (ChannelError, UnicodeError) as error:
                raise MakeProbeError(f"invalid effective publication confirmation: {error}") from error
            if confirmation["slot"] != completed - 1:
                raise MakeProbeError("publication confirmation has the wrong completed slot")
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
            ) | (
                {"read_trace"} if observe_read_epochs and observed.get("ok") is True
                and observed.get("returncode") == 0 else set()
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
            if observe_read_epochs and observed["returncode"] == 0:
                read_epochs.validate_trace(
                    observed.get("read_trace"), config["producer_scope"],
                    count_limit=config["observation_count"], file_limit=config["file_limit"],
                    reserve=lambda size: self.budget.charge("control", size),
                )
                if observe_source_phases and barrier != len([
                    event for event in observed["read_trace"]["events"] if event["kind"] == "entry-image"
                ]):
                    raise MakeProbeError("original entry barrier transcript is incomplete")
            elif "read_trace" in observed:
                raise MakeProbeError("unrequested or failed Make supplied an original read trace")
            if channel is not None:
                final = observed["rendezvous"]
                if (
                    not isinstance(final, dict) or set(final) != {"issued", "completed", "pending_peak", "publication"}
                    or any(type(final[name]) is not int for name in ("issued", "completed", "pending_peak"))
                    or final["issued"] != sequence or final["completed"] != sequence
                    or completion != (final["issued"], final["completed"], final["publication"])
                    or not 0 <= final["pending_peak"] <= config["pending_limit"]
                ):
                    raise MakeProbeError("partial or inconsistent live producer completion")
            result.returncode = observed["returncode"]
            if metadata_validation and result.returncode not in {0, 1, 2}:
                raise MakeProbeError("invalid trusted metadata comparison status")
            if mode != "make" and not metadata_validation and result.returncode:
                raise MakeProbeError(f"registered command failed: {result.returncode}")
            try:
                installed = install_protocol.validate_records(observed["accessed"], install_spec)
            except install_protocol.InstallError as error:
                raise MakeProbeError(str(error)) from error
            if install_spec is not None:
                observed["private_install_records"] = installed
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

    @staticmethod
    def _install_command_binding(command):
        return encoded((
            command.argv, command.code, command.sources, command.directories, command.outputs,
            command.dependency_only, command.publication_policy, command.stdout_transform,
        ))

    @terminal_failure
    def _private_install_command(self, command, destinations):
        if (
            type(command) is not Command or not command.argv or command.argv[0] != "/usr/bin/python3"
            or command.native_tool is not None or command.runtime_tool is not None
            or command.dependency_only is not False or command.stdout_transform is not None
            or any(type(value) is not tuple or any(not isinstance(item, str) for item in value)
                   for value in (command.argv, command.code, command.sources, command.directories, command.outputs))
            or not isinstance(destinations, (tuple, list))
            or not 1 <= len(destinations) <= self.budget.limits.created_files
        ):
            raise MakeProbeError("private install requires an exact supported command and destinations")
        outputs = self._output_paths(command.outputs)
        names = tuple(sorted(relative_path(name) for name in destinations))
        parents = {str(PurePosixPath(name).parent) for name in outputs}
        if (
            not outputs or len(names) != len(set(names))
            or any(str(PurePosixPath(name).parent) not in parents for name in names)
            or id(command) in self._private_install_commands
        ):
            raise MakeProbeError("private install destinations are outside the command's output parents")
        inputs = tuple(self.source_owners(set(command.code) | set(self.sources(command.sources))))
        binding = self._install_command_binding(command)
        self.budget.charge("cache", len(binding) + len(encoded((inputs, names))))
        key = id(command)

        def expired(reference):
            record = self._private_install_commands.get(key)
            if type(record) is _PrivateInstallCommand and record.command is reference:
                del self._private_install_commands[key]

        record = _PrivateInstallCommand(
            weakref.ref(command, expired), binding, self.snapshot, self.tree,
            self._namespace_epoch, inputs, names,
        )
        self._private_install_commands[key] = record
        self._private_install_issued.add(record)
        return command

    def _require_private_install(self, command):
        self.budget.remaining()
        record = self._private_install_commands.get(id(command))
        if record is None:
            return None
        if (
            type(record) is not _PrivateInstallCommand or record not in self._private_install_issued
            or record.command() is not command
            or self.base is None or self.snapshot is not record.snapshot or self.tree != record.tree
            or self._namespace_epoch != record.epoch or get_ident() != self.owner_thread
            or self._install_command_binding(command) != record.binding
            or command.native_tool is not None or command.runtime_tool is not None
            or tuple(self.source_owners(set(command.code) | set(self.sources(command.sources)))) != record.inputs
        ):
            raise MakeProbeError("private install command changed or outlived its issued view")
        return record

    def _private_install_launch(self, command, output, root, argv, environment):
        permission = self._require_private_install(command)
        if permission is None:
            return None
        directories = {
            parent.as_posix() for name in permission.destinations
            for parent in PurePosixPath(name).parents
        }
        records = []
        for directory in sorted(directories, key=lambda name: (len(PurePosixPath(name).parts), name)):
            path = output if directory == "." else output / directory
            if directory != ".":
                if self.files_created >= self.budget.limits.created_files:
                    self.budget.reject("private install parent creation exceeds remaining capacity")
                self.files_created += 1
                path.mkdir()
            identity = install_protocol.directory_identity(path.stat(follow_symlinks=False))
            name = "/work" if directory == "." else "/work/" + directory
            self.budget.charge("control", len(encoded((name, identity))))
            records.append([name, *identity])
        launch = _PrivateInstallLaunch()
        self._private_install_launches[id(launch)] = (
            launch, command, root, output, tuple(argv), tuple(sorted(environment.items())), sorted(records),
        )
        self._private_install_launch_issued.add(launch)
        return launch

    def _require_live_dispatch(self, event=None):
        if not self._live_dispatches:
            raise MakeProbeError("command has no actual live dispatch context")
        context = self._live_dispatches[-1]
        if (
            type(context) is not _LiveDispatch or context not in self._issued_dispatches
            or context.snapshot is not self.snapshot or context.tree != self.tree
            or context.epoch != self._namespace_epoch
            or event is not None and context.arguments != tuple(event["arguments"])
        ):
            raise MakeProbeError("live dispatch context is forged, stale or belongs to another view")
        return context

    def _command_environment(self, command):
        record = self._native_context_commands.get(id(command))
        if record is None or not self._command_dispatches or self._command_dispatches[-1][0] is not command:
            return {**ENVIRONMENT, "SOURCE_DATE_EPOCH": "0", "TMPDIR": "/work"}
        if (
            type(record) is not _ContextCommand or record not in self._issued_context_commands
            or record.command() is not command or record.snapshot is not self.snapshot or record.tree != self.tree
            or record.epoch != self._namespace_epoch or record.binding != self._install_command_binding(command)
        ):
            raise MakeProbeError("context-aware command belongs to another binding or view")
        context = self._require_live_dispatch()
        if context is not self._command_dispatches[-1][1] or context.cwd != "/repo":
            raise MakeProbeError("registered command has an unsupported native cwd/context")
        return dict(context.environment)

    @terminal_failure
    def _native_context_command(self, command):
        if type(command) is not Command:
            raise MakeProbeError("native context requires a typed command")
        key = id(command)
        def expired(reference):
            record = self._native_context_commands.get(key)
            if type(record) is _ContextCommand and record.command is reference:
                del self._native_context_commands[key]
        binding = self._install_command_binding(command)
        self.budget.charge("cache", len(binding))
        record = _ContextCommand(
            weakref.ref(command, expired), self.snapshot, self.tree, self._namespace_epoch, binding,
        )
        self._native_context_commands[key] = record
        self._issued_context_commands.add(record)
        return command

    def _published_record(self, path):
        if path not in self.published_sources or path not in self.published_versions:
            raise MakeProbeError("header temporary has no issued publication")
        item = self.published_sources[path]
        owner, _, identity = self.published_versions[path]
        self._verify_effective_output(item, {"identity": identity})
        return (path, owner, item.mode, len(item.data), hashlib.sha256(item.data).hexdigest(), identity)

    def _header_command_binding(self, command, step, target):
        effect = command.effect if isinstance(command, _FilesystemCommand) else None
        return encoded((
            self._install_command_binding(command).decode("ascii"), step, target,
            None if command.native_tool is None else command.native_tool.digest,
            None if command.runtime_tool is None else command.runtime_tool.digest,
            None if effect is None else (effect.operation, effect.path, effect.source, effect.expected, effect.owner),
        ))

    @terminal_failure
    def _header_step_command(self, command, step):
        if not isinstance(command, Command) or type(step) is not int or step not in range(1, 6):
            raise MakeProbeError("header pipeline requires a typed command and exact step")
        context = self._require_live_dispatch()
        if context.job[0] != "recipe" or context.job[2] != step or not context.rebuilding:
            raise MakeProbeError("header step lacks its actual ordered remake job")
        target = header_effects.header_target(context.job[1])
        key = context.scope, target
        pipeline = self._header_pipelines.get(key)
        if step == 1 and (pipeline is None or pipeline.stage == 5):
            owner = hashlib.sha256(encoded(("header-dependency-v1", target))).hexdigest()
            pipeline = _HeaderPipeline(context.scope, target, owner, 0, {})
            self._header_pipelines[key] = pipeline
        if pipeline is None or pipeline.stage != step - 1:
            raise MakeProbeError("header pipeline step is missing, repeated or out of order")
        if step in {1, 4, 5}:
            path = str(PurePosixPath(target).parent) if step == 1 else target + ".tmp" if step == 4 else target
            source = target + ".tmp2" if step == 5 else None
            expected_argv = {
                1: ("mkdir", "-p", path),
                4: ("rm", "-f", path),
                5: ("mv", "-f", source, path),
            }[step]
            if command != Command(expected_argv):
                raise MakeProbeError("header filesystem command differs from its exact target operands")
            expected = None if step == 1 else self._published_record(path if step == 4 else source)
            if expected is not None and pipeline.versions.get(expected[0]) != expected:
                raise MakeProbeError("header effect refers to a foreign or stale pipeline temporary")
            effect = header_effects.Effect(
                {1: "directory", 4: "retire", 5: "transfer"}[step], path, source, expected, pipeline.owner,
            )
            header_effects.validate_effect(effect, target)
            self._output_paths((target, target + ".tmp", target + ".tmp2"))
            command = _FilesystemCommand(command.argv, effect=effect)
        elif step in {2, 3}:
            output = target + (".tmp" if step == 2 else ".tmp2")
            if command.outputs != (output,):
                raise MakeProbeError("header step output differs from its actual target")
            if step == 2 and command.dependency_only and command.runtime_tool is not None:
                if not command.argv or command.argv[-1] not in command.sources:
                    raise MakeProbeError("header scan source differs from its declared inputs")
                pipeline.source = command.argv[-1]
            if step == 3:
                source = target + ".tmp"
                if pipeline.versions.get(source) != self._published_record(source) or source not in command.sources:
                    raise MakeProbeError("header filter does not consume its exact issued scan")
        else:
            raise MakeProbeError("unknown header pipeline step")
        binding = self._header_command_binding(command, step, target)
        self.budget.charge("cache", len(binding))
        identity = id(command)
        def expired(reference):
            record = self._header_commands.get(identity)
            if type(record) is _HeaderStep and record.command is reference:
                del self._header_commands[identity]
        record = _HeaderStep(
            weakref.ref(command, expired), binding, pipeline, step,
            self.snapshot, self.tree, self._namespace_epoch,
        )
        self._header_commands[identity] = record
        self._issued_header_steps.add(record)
        return command

    def _require_header_step(self, command, context):
        record = self._header_commands.get(id(command))
        if record is None:
            if isinstance(command, _FilesystemCommand):
                raise MakeProbeError("filesystem command has no issued header authority")
            return None
        if (
            type(record) is not _HeaderStep or record not in self._issued_header_steps
            or record.command() is not command or record.snapshot is not self.snapshot
            or record.tree != self.tree or record.epoch != self._namespace_epoch
            or (record.pipeline.scope, record.pipeline.target) != (context.scope, context.job[1])
            or self._header_pipelines.get((context.scope, context.job[1])) is not record.pipeline
            or record.step != context.job[2] or record.pipeline.stage != record.step - 1
            or self._header_command_binding(command, record.step, record.pipeline.target) != record.binding
        ):
            raise MakeProbeError("header step authority is stale, forged or belongs to another job")
        return record

    def _finish_header_step(self, record, paths=()):
        if record is None:
            return
        if record not in self._issued_header_steps or record.pipeline.stage != record.step - 1:
            raise MakeProbeError("header completion is repeated or out of order")
        for path in paths:
            record.pipeline.versions[path] = self._published_record(path)
        record.pipeline.stage = record.step

    def _header_runtime_kind(self, command):
        if command.runtime_tool is None or id(command) not in self._header_commands:
            return None
        context = self._require_live_dispatch()
        record = self._require_header_step(command, context)
        if (
            record is None or record.step not in {2, 3}
            or id(command) not in self._native_context_commands
            or not self._command_dispatches or self._command_dispatches[-1][0] is not command
            or self._command_dispatches[-1][1] is not context
            or command.stdout_transform is not None
        ):
            raise MakeProbeError("header runtime execution lacks its exact primary dispatch")
        arm_headers.environment(self._command_environment(command))
        if record.step == 2:
            if not command.dependency_only:
                raise MakeProbeError("ARM header execution lacks its dependency-only profile")
            return "arm"
        source = record.pipeline.target + ".tmp"
        if (
            command.dependency_only or command.runtime_tool.path != "/usr/bin/sed"
            or len(command.argv) != 4 or command.argv[:2] != ("/usr/bin/sed", "-E")
            or command.argv[3] != "/repo/" + source or command.sources != (source,)
        ):
            raise MakeProbeError("header filter escaped its exact sed/input profile")
        arm_headers.filter_expression(command.argv[2])
        return "filter"

    @staticmethod
    def _compiler_include_aliases():
        first = Path(arm_headers.TARGET_INCLUDE)
        try:
            info = first.lstat()
        except FileNotFoundError:
            return ()
        if not stat.S_ISLNK(info.st_mode):
            return ()
        literal = os.readlink(first)
        paths = [first]
        records = [[str(first), literal, str(first.resolve(strict=True))]]
        if literal == arm_headers.INCLUDE_ALTERNATIVE:
            second = Path(literal)
            if not second.is_symlink():
                raise MakeProbeError("ARM SDK alternatives entry is not its actual symlink")
            paths.append(second)
            records.append([str(second), os.readlink(second), str(second.resolve(strict=True))])
        try:
            result = arm_headers.validate_aliases(records)
        except ChannelError as error:
            raise MakeProbeError(str(error)) from error
        paths.append(Path(arm_headers.NEWLIB))
        for path in {path for item in paths for path in (item, *item.parents)}:
            info = path.lstat()
            if info.st_uid != 0 or not stat.S_ISLNK(info.st_mode) and info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise MakeProbeError("ARM SDK include alias has a mutable ancestor")
        for path, target, canonical in result:
            if os.readlink(path) != target or str(Path(path).resolve(strict=True)) != canonical:
                raise MakeProbeError("ARM SDK include alias changed during capture")
        return result

    def _capture_header_sdk(self, executables, system):
        aliases = self._compiler_include_aliases()
        roots = arm_headers.sdk_roots(executables[1], bool(system), aliases)
        backing = self.base / ("header-sdk-" + str(len(self._header_profiles)))
        backing.mkdir()
        profile = {
            "version": 1, "roots": [], "entries": [], "excluded": [], "files": [],
            "aliases": [list(row) for row in aliases],
        }

        def creation():
            self.budget.remaining()
            if self.files_created >= self.budget.limits.created_files:
                self.budget.reject("ARM SDK capture exceeds the existing creation bound")
            self.files_created += 1

        for index, root in enumerate(roots):
            _trusted_runtime_path(root, optional=True)
            try:
                info = Path(root).lstat()
            except FileNotFoundError:
                profile["roots"].append([root, False])
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise MakeProbeError("ARM SDK root is not an ordinary directory")
            profile["roots"].append([root, True])
            pending = [(Path(root), backing / str(index))]
            while pending:
                source, destination = pending.pop()
                if (
                    not stat.S_ISDIR(source.lstat().st_mode)
                    or _trusted_runtime_path(str(source), optional=True) != source
                ):
                    raise MakeProbeError("ARM SDK directory changed or acquired an alias")
                creation()
                destination.mkdir()
                profile["entries"].append([str(source), "directory"])
                if str(source) == arm_headers.NEWLIB + "/c++":
                    profile["excluded"].append(str(source))
                    continue
                with os.scandir(source) as entries:
                    for entry in entries:
                        self.budget.remaining()
                        self.budget.charge("control", len(os.fsencode(entry.path)) + 64)
                        if len(profile["entries"]) + len(pending) >= self.budget.limits.entries:
                            self.budget.reject("ARM SDK namespace exceeds the source-entry bound")
                        path = Path(entry.path)
                        mode = entry.stat(follow_symlinks=False).st_mode
                        if stat.S_ISDIR(mode):
                            pending.append((path, destination / entry.name))
                            continue
                        if not stat.S_ISREG(mode):
                            raise MakeProbeError("ARM SDK has a symlink or special entry")
                        profile["entries"].append([str(path), "file"])
                        creation()
                        target = destination / entry.name
                        if entry.name.endswith(".h"):
                            captured = _capture_runtime_input(str(path), self.budget)
                            if captured.data is None:
                                raise MakeProbeError("ARM SDK header disappeared during capture")
                            if any(
                                item.canonical == str(path) and (item.data, item.mode) != (captured.data, captured.mode)
                                for item in self.runtime_inputs
                            ):
                                raise MakeProbeError("ARM SDK differs from the original captured runtime input")
                            self.budget.charge("control", len(captured.data))
                            target.write_bytes(captured.data)
                            target.chmod(captured.mode)
                            profile["files"].append([
                                str(path), captured.mode, len(captured.data),
                                hashlib.sha256(captured.data).hexdigest(),
                            ])
                        else:
                            target.touch(mode=0)
        profile["entries"].sort()
        profile["files"].sort()
        arm_headers.validate_search(
            profile, executables, count_limit=self.budget.limits.entries,
            file_limit=self.budget.limits.file_bytes,
        )
        self.budget.charge("control", len(encoded(profile)))
        return backing, profile

    def _header_runtime_profile(self, command, system=(), binutils=()):
        tool = command.runtime_tool
        architecture = tuple(argument for argument in command.argv[1:] if argument.startswith("-m"))
        key = tool.path, tool.canonical, tool.mode, tool.digest, tuple(system), tuple(binutils), architecture
        if key in self._header_profiles:
            return self._header_profiles[key]
        if command.runtime_tool.path == "/usr/bin/sed":
            compiler = (command.runtime_tool.path,)
            profile = self._compiler_runtime_profile(compiler, search=False)
            result = compiler, profile, None, None
        else:
            driver, programs = self._compiler_tools(
                False, ("cc1",), compiler=command.runtime_tool.path, search_arguments=binutils,
            )
            frontends = {str(_trusted_runtime_path(path, compiler=True)) for path in programs} - {driver}
            if len(frontends) != 1:
                raise MakeProbeError("ARM header profile requires exactly one real C frontend")
            compiler = (driver, frontends.pop())
            for query, wanted in (
                ("-print-file-name=include", str(Path(compiler[1]).parent / "include")),
                ("-print-file-name=include-fixed", str(Path(compiler[1]).parent / "include-fixed")),
                ("-print-sysroot", ""),
            ):
                probe = self.budget.run([driver, *binutils, query], env=ENVIRONMENT, cwd=Path("/"))
                if probe.returncode or text(probe.stdout, "ARM SDK query", "utf-8").strip() != wanted:
                    raise MakeProbeError("ARM SDK query differs from the closed frontend/sysroot profile")
            profile = self._compiler_runtime_profile(compiler, (*binutils, *architecture))
            profile["runtime_stat_probes"] = sorted({
                *profile["runtime_stat_probes"],
                *(str(Path(compiler[1]).parent / name) for name in ("collect2", "liblto_plugin.so")),
            })
            if binutils:
                prefix = Path(binutils[0][2:]).resolve(strict=True)
                candidates = (prefix, prefix / "arm-none-eabi" / Path(compiler[1]).parent.name)
                for directory in candidates:
                    if any((directory / name).exists() or (directory / name).is_symlink()
                           for name in ("include", "include-fixed")):
                        raise MakeProbeError("binutils prefix introduces an unclosed additional ARM SDK")
                profile["runtime_stat_probes"] = sorted({
                    *profile["runtime_stat_probes"],
                    *(str(directory / name) for directory in candidates
                      for name in ("include", "include-fixed", "cc1", "collect2", "liblto_plugin.so")),
                })
            backing, sdk = self._capture_header_sdk(compiler, system)
            profile["runtime_aliases"] += sdk["aliases"]
            result = compiler, profile, backing, sdk
        self._header_profiles[key] = result
        return result

    def _filter_kernel_inputs(self, root):
        profile = {
            "version": 1, "statfs": [], "reads": list(header_protocol.READ_PATHS),
            "absent": list(header_protocol.ABSENT_PATHS),
        }
        mounts = []
        for path in header_protocol.STATFS_PATHS:
            try:
                info = Path(path).lstat()
            except FileNotFoundError:
                profile["statfs"].append([path, False])
            else:
                if not stat.S_ISDIR(info.st_mode):
                    raise MakeProbeError("sed statfs input is not an ordinary directory")
                profile["statfs"].append([path, True])
                _mkdir_target(root, path, directory=True)
                mounts.append(self._mount(Path(path), path))
        for path in header_protocol.READ_PATHS:
            _mkdir_target(root, path)
            mounts.append(self._mount(Path(path), path))
        for path in header_protocol.ABSENT_PATHS:
            if Path(path).exists() or Path(path).is_symlink():
                raise MakeProbeError("present SELinux configuration is outside the closed header filter profile")
        header_protocol.validate_filter(profile, ("/usr/bin/sed",))
        self.budget.charge("control", len(encoded(profile)))
        return profile, mounts

    def _header_runtime_launch(self, command, root, *, mode, argv, environment, mounts, code, sources,
                               directories, executables, dependency):
        step = self._require_header_step(command, self._require_live_dispatch())
        if step is None or self._header_runtime_kind(command) is None:
            raise MakeProbeError("header launch lacks its issued primary command")
        token = _HeaderRuntimeLaunch()
        binding = header_protocol.launch_binding({
            "root": str(root), "mode": mode, "argv": argv, "environment": environment, "mounts": mounts,
            "code": code, "sources": sources, "enumerations": directories, "executables": executables,
            "dependency": dependency,
        })
        self._header_launches[id(token)] = token, command, step, binding
        self._issued_header_launches.add(token)
        return token

    def _confirm_header_effect(self, pending, outcome, generated_paths, generated_directories):
        effect = pending.effect
        if (
            outcome.get("kind") != "filesystem" or outcome["owner"] != effect.owner
            or outcome["operation"] != effect.operation or outcome["path"] != effect.path
            or outcome["source"] != effect.source
            or tuple(item[0] for item in outcome["directories"]) != pending.directories
        ):
            raise MakeProbeError("filesystem confirmation differs from its issued header request")
        for row in outcome["directories"]:
            descriptor = self._namespace_directory(row[0])
            try:
                if tuple(row[1:]) != install_protocol.directory_identity(os.fstat(descriptor)):
                    raise MakeProbeError("created header directory identity differs from its receipt")
                self._namespace_mutation("created-directory", row[0], tuple(row[1:]))
            finally:
                os.close(descriptor)
            generated_directories.add(row[0])
        if effect.operation != "directory":
            source = effect.path if effect.operation == "retire" else effect.source
            if source not in self.published_sources or source not in self.published_versions:
                raise MakeProbeError("header effect lost its issued input version")
            old = self.published_sources[source]
            owner, _, identity = self.published_versions[source]
            stored = (source, owner, old.mode, len(old.data), hashlib.sha256(old.data).hexdigest(), identity)
            before = header_effects.validate_record(outcome["before"])
            if before != effect.expected or stored != before:
                raise MakeProbeError("header effect confirmation has a foreign or stale source")
            try:
                (self.tree / source).lstat()
            except FileNotFoundError:
                pass
            else:
                raise MakeProbeError("header effect did not remove its original source entry")
            self._namespace_mutation("removed", source, identity)
            del self.published_sources[source]
            del self.published_versions[source]
            self._namespace_publications.pop(source, None)
            generated_paths.discard(source)
            if effect.operation == "transfer":
                after = header_effects.validate_record(outcome["after"])
                value = GeneratedFile(effect.path, old.data, old.mode)
                self._verify_effective_output(value, {"identity": after[5]})
                self.publication_serial += 1
                version = effect.owner, self.publication_serial, after[5]
                self.published_sources[effect.path] = value
                self.published_versions[effect.path] = version
                self._namespace_publications[effect.path] = value, version
                generated_paths.add(effect.path)
                self._namespace_mutation(
                    "created" if pending.previous is None else "replaced", effect.path, after[5],
                )
        self._finish_header_step(pending.step)
        semantic = dict(pending.record)
        semantic["filesystem"] = {
            "operation": effect.operation, "path": effect.path, "source": effect.source,
            "before": None if outcome["before"] is None else list(outcome["before"][:5]),
            "after": None if outcome["after"] is None else list(outcome["after"][:5]),
            "directories": list(pending.directories),
        }
        return semantic

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
        Command.__post_init__(command)
        environment = self._command_environment(command)
        header_kind = self._header_runtime_kind(command)
        if type(command.dependency_only) is not bool:
            raise MakeProbeError("dependency_only requires a boolean")
        if command.stdout_transform not in {None, "dirname"}:
            raise MakeProbeError("registered command has an unsupported stdout transform")
        if command.stdout_transform is not None and command.runtime_tool is None:
            raise MakeProbeError("stdout transform requires an issued runtime tool query")
        if command.dependency_only and (
            compiler is not None or native is not None or command.native_tool is not None
            or command.runtime_tool is not None and header_kind != "arm"
            or command.stdout_transform is not None
        ):
            raise MakeProbeError("dependency profile cannot combine native or other compiler authority")
        if command.runtime_tool is not None:
            if compiler is not None or native is not None or command.native_tool is not None:
                raise MakeProbeError("runtime tool cannot combine other execution authority")
            if header_kind is None:
                self._runtime_tool_query(command)
            self._verify_runtime_tool(command.runtime_tool)
            if not command.argv or command.argv[0] != command.runtime_tool.path:
                raise MakeProbeError("runtime tool execution requires its exact captured pathname")
            compiler = (command.runtime_tool.path,)
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
        system = binutils = ()
        if header_kind == "arm":
            include_dirs, system, binutils = arm_headers.options(command, sources, outputs)
        else:
            include_dirs = self._dependency_options(command, sources, outputs) if command.dependency_only else ()
        for path in code:
            relative_path(path)
            if path not in self.snapshot.files and path not in self.published_sources:
                raise MakeProbeError(f"unadmitted command code: {path}")
        published_inputs = tuple(self.source_owners((set(code) | set(sources)) & self.published_sources.keys()))
        if published_inputs:
            self.budget.charge("control", len(encoded(published_inputs)))
        runtime_digest = None if command.runtime_tool is None else command.runtime_tool.digest
        runtime_profile_key = runtime_profile = None
        header_compiler = sdk_backing = sdk = None
        if header_kind is not None:
            header_compiler, runtime_profile, sdk_backing, sdk = self._header_runtime_profile(command, system, binutils)
        elif command.runtime_tool is not None:
            binutils = tuple(argument for argument in command.argv[1:] if argument.startswith("-B"))
            runtime_profile_key = command.runtime_tool.digest, binutils
            if runtime_profile_key not in self.runtime_query_profiles:
                self.runtime_query_profiles[runtime_profile_key] = self._compiler_runtime_profile(
                    (command.runtime_tool.path,), binutils,
                )
            runtime_profile = self.runtime_query_profiles[runtime_profile_key]
            if tuple(self._compiler_runtime_aliases()) != tuple(runtime_profile["runtime_aliases"]):
                raise MakeProbeError("modern compiler runtime aliases changed after capture")
        key = (
            self.snapshot.digest, command, None if native is None else native.digest,
            runtime_digest, code, sources, published_inputs, tuple(sorted(environment.items())),
        )
        if key in self.cache and not outputs:
            for cached in self.cache[key]:
                if self._metadata_matches(cached.metadata):
                    return cached
        self.budget.charge("pending", len(encoded([
            command.argv, code, sources, directories, outputs, command.publication_policy, runtime_digest,
            command.stdout_transform,
        ])))
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
            runtime_mounts = []
            if command.dependency_only:
                if header_kind == "arm":
                    compiler = header_compiler
                    dependency_runtime = runtime_profile
                elif self.dependency_compiler is None:
                    driver, programs = self._compiler_tools(False, ("cc1",))
                    programs = tuple(sorted({str(_trusted_runtime_path(path, compiler=True)) for path in programs}))
                    frontends = tuple(path for path in programs if path != driver)
                    if len(frontends) != 1:
                        raise MakeProbeError("dependency profile requires one resolved C frontend")
                    self.dependency_compiler = driver, frontends[0]
                    self.budget.charge("control", len(encoded(self.dependency_compiler)))
                    self.dependency_runtime = self._dependency_runtime()
                if header_kind != "arm":
                    compiler, dependency_runtime = self.dependency_compiler, self.dependency_runtime
                argv[0] = compiler[0]
                dependency = {
                    **dependency_runtime,
                    "executables": list(compiler),
                    "include_dirs": ["/repo" if path == "." else "/repo/" + path for path in include_dirs],
                    "metadata_descendants": [],
                }
                if sdk is not None:
                    dependency["header_search"] = sdk
                    for index, (path, present) in enumerate(sdk["roots"]):
                        if present:
                            _mkdir_target(root, path, directory=True)
                            runtime_mounts.append(self._mount(sdk_backing / str(index), path))
                parent = output
                for part in PurePosixPath(outputs[0]).parts[:-1]:
                    self.budget.remaining()
                    if self.files_created >= self.budget.limits.created_files:
                        self.budget.reject("aggregate dependency directory-creation budget exhausted")
                    self.files_created += 1
                    parent /= part
                    parent.mkdir()
                argv.extend(("-MF", "/work/" + outputs[0]))
            elif command.runtime_tool is not None:
                dependency = {
                    **runtime_profile,
                    "executables": [command.runtime_tool.path],
                    "include_dirs": [],
                    "metadata_descendants": (
                        [] if header_kind else runtime_profile["compiler_search_directories"]
                    ),
                }
                if header_kind == "filter":
                    dependency["filter_kernel"], kernel_mounts = self._filter_kernel_inputs(root)
                    runtime_mounts.extend(kernel_mounts)
                    parent = output
                    for part in PurePosixPath(outputs[0]).parts[:-1]:
                        self.budget.remaining()
                        if self.files_created >= self.budget.limits.created_files:
                            self.budget.reject("header filter output creation exceeds remaining capacity")
                        self.files_created += 1
                        parent /= part
                        parent.mkdir()
                    if self.files_created >= self.budget.limits.created_files:
                        self.budget.reject("header filter output creation exceeds remaining capacity")
                    self.files_created += 1
                    descriptor = os.open(output / outputs[0], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                    os.close(descriptor)
            if command.runtime_tool is not None:
                aliases = runtime_profile["runtime_aliases"]
                for path, target, _ in aliases:
                    if path.startswith("/usr/"):
                        continue
                    destination = root / path.lstrip("/")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.symlink_to(target)
            if argv[0] == "/usr/bin/python3":
                argv[1:1] = ["-I", "-S", "-B"]
            install_launch = self._private_install_launch(command, output, root, argv, environment)
            command_mounts = [
                self._mount(self.tree, "/repo"), self._mount(Path("/usr"), "/usr", executable=True),
                *runtime_mounts, self._mount(output, "/work", writable=True),
                self._mount(Path("/dev/null"), "/dev/null", writable=True),
            ]
            header_launch = None
            if header_kind is not None:
                header_launch = self._header_runtime_launch(
                    command, root, mode="compile", argv=argv, environment=environment, mounts=command_mounts,
                    code=code, sources=sources, directories=directories, executables=compiler, dependency=dependency,
                )
            try:
                completed, observed = self._sandbox_run(
                    root, mode="command" if compiler is None else "compile", argv=argv,
                    environment=environment,
                    mounts=command_mounts,
                    code=code, sources=sources, directories=directories,
                    executables=compiler, dependency=dependency,
                    **({"private_install": install_launch} if install_launch is not None else {}),
                    **({"header_runtime": header_launch} if header_launch is not None else {}),
                )
            finally:
                if header_launch is not None:
                    self._header_launches.pop(id(header_launch), None)
                    self._issued_header_launches.discard(header_launch)
                if install_launch is not None:
                    self._private_install_launches.pop(id(install_launch), None)
                    if type(install_launch) is _PrivateInstallLaunch:
                        self._private_install_launch_issued.discard(install_launch)
            consumed = tuple(observed["consumed"])
            if consumed != sources:
                raise MakeProbeError(f"declared/consumed source mismatch: declared={sources!r}, consumed={consumed!r}")
            if command.dependency_only:
                used = set(consumed) | set(observed["code_consumed"])
                if not set(observed["code_consumed"]) <= set(code):
                    raise MakeProbeError("dependency result names undeclared header code")
                input_identities = tuple(item for item in input_identities if item[0] in used)
            stdout, stderr = completed.stdout, completed.stderr
            runtime_sources = ()
            try:
                runtime_probes = header_protocol.records(
                    observed["accessed"], None if header_kind != "filter" else dependency["filter_kernel"],
                    count_limit=self.budget.limits.entries, file_limit=self.budget.limits.file_bytes,
                )
            except ChannelError as error:
                raise MakeProbeError(str(error)) from error
            if sdk is not None:
                try:
                    runtime_sources = arm_headers.records(
                        observed["accessed"], sdk, compiler,
                        count_limit=self.budget.limits.entries, file_limit=self.budget.limits.file_bytes,
                    )
                except ChannelError as error:
                    raise MakeProbeError(str(error)) from error
            if header_kind == "filter":
                self.budget.charge("sandbox", len(stdout))
                (output / outputs[0]).write_bytes(stdout)
                stdout = b""
            if command.stdout_transform == "dirname":
                reported = text(stdout, "modern toolchain query output", "utf-8").rstrip("\n")
                stdout, stderr = ((os.path.dirname(reported) or ".") + "\n").encode(), b""
            if command.runtime_tool is not None:
                self._verify_runtime_tool(command.runtime_tool)
                aliases = self._compiler_runtime_aliases() if header_kind != "filter" else ()
                if header_kind == "arm":
                    aliases += self._compiler_include_aliases()
                if tuple(aliases) != tuple(tuple(row) for row in runtime_profile["runtime_aliases"]):
                    raise MakeProbeError("modern compiler runtime aliases changed during query")
            result = ProcessOutput(
                stdout, stderr, consumed, tuple(observed["code_consumed"]),
                None if compiler is None or command.dependency_only or command.runtime_tool is not None
                else self.budget.read_bytes(output / "tool", "control"),
                observed["metadata"],
                self._capture_outputs(output, outputs),
                input_identities,
                tuple(observed.get("executed", ())),
                () if command.runtime_tool is None else tuple(runtime_profile["runtime_aliases"]),
                runtime_sources,
                runtime_probes,
            )
            self.budget.charge(
                "cache", len(completed.stdout) + len(completed.stderr)
                + len(encoded([
                    self.snapshot.digest, command.argv, code, sources, directories,
                    published_inputs, command.publication_policy, runtime_digest, command.stdout_transform,
                    environment,
                ]))
                + (0 if result.artifact is None else len(result.artifact))
                + sum(len(item.data) + len(os.fsencode(item.path)) + 64 for item in result.generated),
            )
            self.budget.charge("cache", len(encoded(result.metadata)))
            self.budget.charge("cache", len(encoded(result.input_identities)))
            if result.executed:
                self.budget.charge("cache", len(encoded(result.executed)))
            if result.runtime_receipt:
                self.budget.charge("cache", len(encoded(result.runtime_receipt)))
            if result.runtime_sources:
                self.budget.charge("cache", len(encoded(result.runtime_sources)))
            if result.runtime_probes:
                self.budget.charge("cache", len(encoded(result.runtime_probes)))
            if not outputs:
                self.cache.setdefault(key, []).append(result)
            return result

    def _compiler_runtime_profile(self, compiler, search_arguments=(), *, search=True):
        interpreter = _make_interpreter(dict(self.make_runtime)["/usr/bin/make"])
        runtime = {interpreter, *compiler}
        for program in compiler:
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
        if not search:
            libc = {
                str(_trusted_runtime_path(path, compiler=True))
                for path in runtime if Path(path).name == "libc.so.6"
            }
            if len(libc) != 1:
                raise MakeProbeError("header runtime requires one resolved libc image")
            profile = {
                "runtime_files": sorted(runtime), "runtime_directories": [],
                "compiler_search_directories": [], "runtime_stat_probes": [],
                "runtime_interpreter": str(_trusted_runtime_path(interpreter, compiler=True)),
                "runtime_libc": libc.pop(), "runtime_aliases": [],
            }
            self.budget.charge("control", len(encoded(profile)))
            return profile
        result = self.budget.run(
            [compiler[0], *search_arguments, "-print-search-dirs"],
            env=ENVIRONMENT, cwd=Path("/"),
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
            str(Path(path) / name)
            for path in searches["libraries"] | searches["programs"]
            | {install, str(Path(install).parent)}
            for name in ("specs", "lto-wrapper")
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
            "compiler_search_directories": sorted(directories),
            "runtime_stat_probes": sorted(probes | {"/proc/self/exe"}),
            "runtime_interpreter": str(_trusted_runtime_path(interpreter, compiler=True)),
            "runtime_libc": libc.pop(),
            "runtime_aliases": list(self._compiler_runtime_aliases()) if (
                Path(compiler[0]).name in {"arm-none-eabi-gcc", "arm-none-eabi-gcc.exe"}
            ) else [],
        }
        self.budget.charge("control", len(encoded(profile)))
        return profile

    def _dependency_runtime(self):
        return self._compiler_runtime_profile(self.dependency_compiler)

    def _compiler_tools(self, cxx, names, *, compiler=None, search_arguments=()):
        compiler = str(Path(compiler or ("/usr/bin/g++" if cxx else "/usr/bin/cc")).resolve(strict=True))
        executables = [compiler]
        for name in names:
            result = self.budget.run(
                [compiler, *search_arguments, "-print-prog-name=" + name],
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
        owner_inputs=(), commands=None, observe_recipe_dispatch=False, definitions=(), observe_read_epochs=False,
        observe_source_phases=False,
    ) -> MakeObservation:
        return self._make(
            target, makefile=makefile, variables=variables, assignments=assignments,
            owner_inputs=owner_inputs, commands=commands,
            observe_recipe_dispatch=observe_recipe_dispatch, definitions=definitions,
            observe_read_epochs=observe_read_epochs,
            observe_source_phases=observe_source_phases,
        )

    @terminal_failure
    def original_make_inputs(self, target: str, names, *, assignments=()):
        """Native raw inputs before candidate source; never a graph authority."""
        if not isinstance(names, (tuple, list)) or not names or len(names) > 512:
            raise MakeProbeError("original input query exceeds its bounded name contract")
        if any(not isinstance(name, str) or len(name) > 128 or not VARIABLE.fullmatch(name) for name in names):
            raise MakeProbeError("invalid original Make input name")
        if set(names) & {
            "MAKECMDGOALS", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES",
            "MAKEFILE_LIST", "MAKE_RESTARTS", "MAKELEVEL",
        }:
            raise MakeProbeError("original input query cannot substitute invocation controls")
        empty_sources = sorted(
            path for path, data in self.snapshot.files.items()
            if not data and TARGET.fullmatch(path) and "%" not in path and path != target
            and path not in self.published_sources and path not in self.generated_paths
        )
        if not empty_sources:
            raise MakeProbeError("original input query lacks an admitted empty source witness")
        observed = self._make(
            target, makefile=empty_sources[0], definitions=tuple(names), assignments=assignments,
            _original_inputs=True,
        )
        if (
            observed.generated or observed.events or observed.semantics["dynamic_commands"]
            or observed.semantics["native_dispatches"]
            or not observed.file_open_attempts
            or len(observed.semantics["files"]) != 1
            or observed.semantics["files"][0]["target"] != target
            or observed.semantics["files"][0]["prerequisites"]
            or any(item["recipe"] not in {"", "\n"} for item in observed.semantics["files"])
            or any(resolved != "/repo/" + empty_sources[0] for resolved, _ in observed.file_open_attempts)
        ):
            raise MakeProbeError("original input query executed outside its empty source witness")
        result = observed.semantics["definitions"]["global"]
        self.budget.charge("cache", len(encoded(result)))
        return result

    def _make(
        self, target: str, *, makefile="Makefile", variables=(), assignments=(),
        owner_inputs=(), commands=None, observe_recipe_dispatch=False, definitions=(),
        _original_inputs=False,
        observe_read_epochs=False,
        observe_source_phases=False,
    ) -> MakeObservation:
        self.budget.remaining()
        if type(_original_inputs) is not bool:
            raise MakeProbeError("invalid original input observation selection")
        if type(observe_recipe_dispatch) is not bool:
            raise MakeProbeError("native recipe observation requires a boolean selection")
        if type(observe_read_epochs) is not bool or _original_inputs and observe_read_epochs:
            raise MakeProbeError("original read epochs require a normal Make invocation")
        if type(observe_source_phases) is not bool or _original_inputs and observe_source_phases:
            raise MakeProbeError("original source phases require a normal Make invocation")
        observe_read_epochs = observe_read_epochs or observe_source_phases
        if not TARGET.fullmatch(target) or target.startswith(("-", "/")) or ".." in target.split("/"):
            raise MakeProbeError("invalid requested Make target")
        if _original_inputs:
            if (
                self.snapshot.files.get(makefile) != b"" or variables or owner_inputs
                or commands is not None or observe_recipe_dispatch
            ):
                raise MakeProbeError("original input query escaped its fixed metadata-only program")
        else:
            relative_path(makefile)
            if makefile not in self.snapshot.files and makefile not in self.published_sources:
                raise MakeProbeError("Makefile is not an admitted snapshot input")
        if len(variables) + len(definitions) > 512 or len(assignments) > 512 or len(owner_inputs) > 4096:
            raise MakeProbeError("Make request count exceeds admission bound")
        if any(not isinstance(name, str) or len(name) > 128 or not VARIABLE.fullmatch(name)
               for name in (*variables, *definitions)):
            raise MakeProbeError("invalid/excessive Make observation variables")
        variables = tuple(sorted(set(variables)))
        definitions = tuple(sorted(set(definitions)))
        if set(variables) & set(definitions):
            raise MakeProbeError("Make variable cannot request two observation forms")
        observed_names = tuple(sorted((*variables, *definitions)))
        self.budget.plan(1)
        cli = []
        environment = {
            **ENVIRONMENT,
            "LD_PRELOAD": "/lib/vo-observer.so",
            "VO_OBSERVE_TARGET": target,
            "VO_OBSERVE_NAMES": " ".join(observed_names),
            "VO_OBSERVE_RAW_NAMES": " ".join(definitions),
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
        header_steps = {}
        receipt_directories = {}
        command_results = {}
        generated_paths = self.generated_paths
        generated_directories = self.generated_directories
        confirmed = 0
        last_confirmation = None
        depth = self.make_depth
        # A query without registrations or inherited outputs has no publication
        # authority. Do not copy the complete tree's unused reservation list.
        publication_allowed = commands is not None or bool(self.published_sources)
        commands = {} if commands is None else commands
        namespace_capture = None if _original_inputs else self._begin_namespace(target, makefile, assignments)
        phase_entries = []
        phase_images = []

        def capture_source_entry(request):
            self.budget.remaining()
            if (
                self.snapshot is not namespace_capture.image.snapshot or self.tree != namespace_capture.image.tree
                or self._namespace_epoch != namespace_capture.epoch or namespace_capture.closed
            ):
                raise MakeProbeError("original entry capture crossed its namespace view/lifetime")
            for path, item in self.published_sources.items():
                self._verify_effective_output(item, {"identity": self.published_versions[path][2]})
            image = self._capture_namespace_image(inherited=bool(self.published_sources))
            stamps = {}
            for path, identity in image.directories.items():
                descriptor = self._namespace_directory(path)
                try:
                    actual = os.fstat(descriptor)
                    if _namespace_identity(actual) != identity:
                        raise MakeProbeError("original entry namespace changed during its barrier")
                    stamps[path] = _namespace_stamp(actual)
                finally:
                    os.close(descriptor)
            payload = {
                "directories": dict(image.directories), "members": dict(image.members),
                "forbidden": sorted(image.forbidden), "stamps": stamps,
            }
            image_sha256 = source_phases.digest(payload)
            entry = {
                **{name: request[name] for name in ("barrier", "exec", "pass", "trace_seq", "input_sha256")},
                "image_sha256": image_sha256, "image": payload,
            }
            self.budget.charge("cache", len(encoded(entry)))
            phase_entries.append(entry)
            phase_images.append(image)
            return image_sha256

        def cleanup_generated():
            if depth:
                return

            def remove_file(name):
                self._namespace_mutation("removed", name)
                (self.tree / name).unlink(missing_ok=True)

            def remove_directory(name):
                if (self.tree / name).exists():
                    self._namespace_mutation("removed-directory", name)
                    (self.tree / name).rmdir()

            finish_cleanup([
                *(lambda name=name: remove_file(name) for name in generated_paths),
                *(lambda name=name: remove_directory(name)
                  for name in sorted(generated_directories, key=lambda value: (-value.count("/"), value))),
                generated_paths.clear, generated_directories.clear,
                self.published_sources.clear, self.published_versions.clear,
                self._namespace_publications.clear,
            ])

        def acknowledge(completed, confirmation):
            nonlocal confirmed, last_confirmation
            if not confirmed <= completed <= len(receipts):
                raise MakeProbeError("invalid producer publication acknowledgement")
            if completed == confirmed:
                if confirmation != last_confirmation:
                    raise MakeProbeError("stale or inconsistent publication confirmation")
                return
            if completed != confirmed + 1:
                raise MakeProbeError("publication confirmation skipped a producer")
            try:
                validate_publication_confirmation(
                    confirmation, count_limit=self.budget.limits.created_files,
                    file_limit=self.budget.limits.file_bytes,
                )
            except (ChannelError, UnicodeError) as error:
                raise MakeProbeError(f"invalid publication confirmation: {error}") from error
            pending = receipts[confirmed]
            if isinstance(pending, _PendingHeaderEffect):
                if confirmation["slot"] != confirmed:
                    raise MakeProbeError("header effect confirmation has the wrong slot")
                semantic = self._confirm_header_effect(
                    pending, confirmation, generated_paths, generated_directories,
                )
                command_results.setdefault(hashlib.sha256(encoded(semantic)).hexdigest(), semantic)
                confirmed += 1
                last_confirmation = confirmation
                return
            _, record, produced, producer, policy, previous = pending
            if (
                "kind" in confirmation
                or confirmation["slot"] != confirmed or confirmation["owner"] != producer
                or confirmation["policy"] != policy
                or [item["path"] for item in confirmation["outputs"]]
                != [item.path for item in produced.generated]
            ):
                raise MakeProbeError("publication confirmation differs from its producer receipt")
            effective = []
            for directory in receipt_directories[confirmed]:
                descriptor = self._namespace_directory(directory)
                try:
                    self._namespace_mutation("created-directory", directory, _namespace_identity(os.fstat(descriptor)))
                finally:
                    os.close(descriptor)
            for item, outcome in zip(produced.generated, confirmation["outputs"]):
                old = previous[item.path]
                retained = policy != "replace" and old is not None and old[0].data == item.data
                effect = "retained" if retained else "created" if old is None else "replaced"
                mode = old[0].mode if old is not None and (
                    retained or policy == "if-content-changed-preserve-mode"
                ) else item.mode
                if (
                    outcome["effect"] != effect or outcome["mode"] != mode
                    or outcome["size"] != len(item.data)
                    or outcome["sha256"] != hashlib.sha256(item.data).hexdigest()
                    or retained and tuple(outcome["identity"]) != old[1]
                ):
                    raise MakeProbeError("effective publication disagrees with its actual output contract")
                self._verify_effective_output(item, outcome)
                self._namespace_mutation(effect, item.path, outcome["identity"])
                value = GeneratedFile(item.path, item.data, mode)
                self.publication_serial += 1
                version = producer, self.publication_serial, tuple(outcome["identity"])
                self.budget.charge("cache", len(encoded([item.path, version])))
                self.published_sources[item.path] = value
                self.published_versions[item.path] = version
                self._namespace_publications[item.path] = value, version
                effective.append((item.path, f"{stat.S_IFREG | mode:06o}", outcome["sha256"]))
            self._finish_header_step(header_steps.get(confirmed), (item.path for item in produced.generated))
            semantic_record = dict(record)
            if effective:
                semantic_record["generated_outputs"] = effective
            command_results.setdefault(hashlib.sha256(encoded(semantic_record)).hexdigest(), semantic_record)
            confirmed = completed
            last_confirmation = confirmation

        def produce(event, sequence):
            publication_start = self.publication_serial
            command = _event_command(event)
            dispatch_context = self._require_live_dispatch(event)
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
                Command.__post_init__(registration)
                header_step = self._require_header_step(registration, dispatch_context)
                if header_step is not None:
                    header_steps[sequence - 1] = header_step
                if isinstance(registration, _FilesystemCommand):
                    effect = registration.effect
                    directory_names = ()
                    old = None
                    if effect.operation == "directory":
                        candidates = [
                            parent.as_posix() for parent in reversed(PurePosixPath(effect.path).parents)
                            if parent.as_posix() != "."
                        ] + [effect.path]
                        directory_names = tuple(name for name in candidates if not (self.tree / name).exists())
                        generated_directories.update(directory_names)
                    elif effect.operation == "transfer":
                        if effect.path in self.published_sources:
                            old = self._published_record(effect.path)
                            if old[1] != effect.owner:
                                raise MakeProbeError("header transfer would replace a foreign pipeline output")
                        elif (self.tree / effect.path).exists() or (self.tree / effect.path).is_symlink():
                            raise MakeProbeError("header transfer would replace an unowned source")
                        generated_paths.add(effect.path)
                    record = {
                        "command": {
                            "argv": list(registration.argv), "environment": dict(dispatch_context.environment),
                            "header_target": header_step.pipeline.target, "header_step": header_step.step,
                        },
                    }
                    key = f"{sequence - 1:016x}"
                    self.budget.charge("mapping", len(command.encode("utf-8")) + len(encoded(record)))
                    (mapping_path / (key + ".cmd")).write_bytes(command.encode("utf-8"))
                    (mapping_path / (key + ".out")).write_bytes(b"")
                    receipts[sequence - 1] = _PendingHeaderEffect(
                        command, record, header_step, effect, directory_names, old,
                    )
                    receipt_directories[sequence - 1] = ()
                    return {
                        "kind": "effect-request", "slot": sequence - 1, "owner": effect.owner,
                        "operation": effect.operation, "path": effect.path, "source": effect.source,
                        "expected": effect.expected,
                    }
                outputs = self._output_paths(registration.outputs)
                previous = {}
                new_directories = set()
                for name in outputs:
                    if any(name.startswith(other + "/") or other.startswith(name + "/") for other in generated_paths):
                        raise MakeProbeError("conflicting generated output namespaces")
                    path = self.tree / name
                    if (path.exists() or path.is_symlink()) and name not in generated_paths:
                        raise MakeProbeError("generated output would replace an unowned source object")
                    try:
                        before = path.lstat()
                    except FileNotFoundError:
                        previous[name] = None
                    else:
                        if (
                            name not in self.published_versions
                            or publication_identity(before) != self.published_versions[name][2]
                        ):
                            raise MakeProbeError("active publication identity changed before producer execution")
                        previous[name] = self.published_sources[name], publication_identity(before)
                    generated_paths.add(name)
                    new_directories.update(
                        parent.as_posix() for parent in PurePosixPath(name).parents
                        if parent.as_posix() != "." and not (self.tree / parent).exists()
                    )
                generated_directories.update(new_directories)
                self._command_dispatches.append((registration, dispatch_context))
                try:
                    environment = self._command_environment(registration)
                    result = self.command(registration)
                finally:
                    self._command_dispatches.pop()
                inputs = result.consumed
                identity = {
                    "argv": list(registration.argv), "directories": sorted(set(registration.directories)),
                    "inputs": list(result.input_identities),
                    "publication_policy": registration.publication_policy,
                }
                if id(registration) in self._native_context_commands:
                    identity["environment"] = environment
                if registration.dependency_only:
                    identity["dependency_only"] = True
                    identity["executed"] = list(result.executed)
                if registration.native_tool is not None:
                    tool = registration.native_tool
                    identity["native_tool"] = {"sha256": tool.digest, "inputs": list(tool.inputs)}
                if registration.runtime_tool is not None:
                    tool = registration.runtime_tool
                    identity["runtime_tool"] = {
                        "path": tool.path, "canonical": tool.canonical,
                        "mode": tool.mode, "sha256": tool.digest,
                        "aliases": [list(item) for item in result.runtime_receipt],
                    }
                if result.runtime_sources:
                    identity["runtime_inputs"] = [list(item) for item in result.runtime_sources]
                if result.runtime_probes:
                    identity["runtime_probes"] = list(result.runtime_probes)
                if registration.stdout_transform is not None:
                    identity["stdout_transform"] = registration.stdout_transform
                record = {
                    "command": identity, "output_sha256": hashlib.sha256(result.stdout).hexdigest(),
                }
                if result.generated:
                    record["produced_outputs"] = [
                        (item.path, f"{stat.S_IFREG | item.mode:06o}", hashlib.sha256(item.data).hexdigest())
                        for item in result.generated
                    ]
                producer = hashlib.sha256(encoded([
                    registration.argv, sorted(set(registration.code)), inputs,
                    sorted(set(registration.directories)), outputs,
                    None if registration.native_tool is None else registration.native_tool.digest,
                    registration.publication_policy,
                    None if registration.runtime_tool is None else registration.runtime_tool.digest,
                    registration.stdout_transform,
                ])).hexdigest()
                key = f"{sequence - 1:016x}"
                self.budget.charge("mapping", len(command.encode("utf-8")) + len(result.stdout) + len(encoded(record)))
                (mapping_path / (key + ".cmd")).write_bytes(command.encode("utf-8"))
                (mapping_path / (key + ".out")).write_bytes(result.stdout)
                if result.generated:
                    frame = bytearray(PUBLICATION_MAGIC + bytes.fromhex(producer))
                    frame.extend(struct.pack("<I", PUBLICATION_POLICIES.index(registration.publication_policy)))
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
                receipts[sequence - 1] = (
                    command, record, result, producer, registration.publication_policy, previous,
                )
                self.budget.charge("cache", len(encoded(sorted(new_directories))))
                receipt_directories[sequence - 1] = tuple(sorted(new_directories))
                reply = {
                    "slot": sequence - 1, "owner": producer, "outputs": list(outputs),
                    "stdout_sha256": record["output_sha256"],
                    "publication_policy": registration.publication_policy,
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
            cleanup_generated, receipts.clear, receipt_directories.clear,
            lambda: _remove_owned_tree(control), lambda: _remove_owned_tree(root),
            lambda: setattr(self, "make_depth", depth),
            *( (lambda: self._end_namespace(namespace_capture),) if namespace_capture is not None else () ),
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
            original_program = (
                ["--eval=.PHONY: " + target + "\n" + makefile + ":;\n" + target + ":;"]
                if _original_inputs else []
            )
            completed, observed = self._sandbox_run(
                root, mode="make", argv=["/usr/bin/make", "-f", makefile, *original_program, *cli, target],
                environment=environment,
                mounts=[
                    self._mount(self.tree, "/repo"),
                    self._mount(control, "/control", writable=True),
                    self._mount(self.base / "interceptor", "/control/interceptor", executable=True),
                    self._mount(Path("/dev/null"), "/dev/null", writable=True),
                ],
                producer_handler=produce, publication_observer=acknowledge,
                publication_allowed=publication_allowed,
                observe_recipe_dispatch=observe_recipe_dispatch,
                observe_read_epochs=observe_read_epochs,
                observe_source_phases=observe_source_phases,
                source_phase_observer=capture_source_entry if observe_source_phases else None,
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
                receipt_command = (
                    receipts[slot].command if slot in receipts and isinstance(receipts[slot], _PendingHeaderEffect)
                    else receipts[slot][0] if slot in receipts else None
                )
                if slot not in receipts or slot in seen or _event_command(event) != receipt_command:
                    raise MakeProbeError("unknown or repeated live producer completion")
                seen.add(slot)
                events.append(event)
            if any(native_events.values()):
                raise MakeProbeError("trusted interceptor frame differs from its native write")
            if seen != set(receipts) or confirmed != len(receipts):
                raise MakeProbeError("incomplete live producer transcript")
            if any(step.pipeline.stage != 5 for step in header_steps.values()):
                raise MakeProbeError("incomplete native header pipeline")
            if completed.returncode:
                raise MakeProbeError(f"GNU Make failed after live producers: {completed.returncode}; {completed.stderr!r}")
            file_open_attempts = []
            for entry in observed["accessed"]:
                if not entry.startswith("make-open:"):
                    continue
                record = parse_json(entry[len("make-open:"):].encode("ascii"), "Make file-open observation")
                if (
                    not isinstance(record, list) or len(record) != 2
                    or any(not isinstance(value, str) or len(value.encode("utf-8")) > 4096 for value in record)
                    or not (record[0] == "/repo" or record[0].startswith("/repo/"))
                ):
                    raise MakeProbeError("malformed Make file-open observation")
                file_open_attempts.append(tuple(record))
            semantics = _read_observation(self.budget.read_bytes(result_path, "control"), target, observed_names)
            if definitions:
                semantics["definitions"] = {
                    "global": {name: semantics["domains"].pop(name) for name in definitions},
                    "files": [
                        {"target": item["target"], "variables": {
                            name: item["variables"].pop(name) for name in definitions
                        }}
                        for item in semantics["files"]
                    ],
                }
            contexts = []
            helpers, policies, jobs = {}, {}, {}
            for value in observed["accessed"]:
                if value.startswith("make-job-context:"):
                    try:
                        job = validate_job_context(parse_json(
                            value[len("make-job-context:"):].encode("ascii"), "native job context",
                        ))
                    except ChannelError as error:
                        raise MakeProbeError(str(error)) from error
                    if job["sequence"] in jobs:
                        raise MakeProbeError("duplicate native job-context sequence")
                    jobs[job["sequence"]] = job
                    continue
                if value.startswith(("make-helper:", "make-job-policy:")):
                    kind, payload = value.split(":", 1)
                    record = parse_json(payload.encode("ascii"), "native Make job binding")
                    if (
                        not isinstance(record, list) or len(record) != 2
                        or any(type(item) is not int for item in record)
                        or not 0 < record[0] < 1 << 31
                        or kind == "make-helper" and not 0 < record[1] < 1 << 31
                        or kind == "make-job-policy" and record[1] not in {0, 1, 2, 3}
                    ):
                        raise MakeProbeError("malformed native Make job binding")
                    destination = helpers if kind == "make-helper" else policies
                    if record[0] in destination:
                        raise MakeProbeError("duplicate native Make job binding")
                    destination[record[0]] = record[1]
                    continue
                if not value.startswith("make-dispatch:"):
                    continue
                dispatch = parse_json(value[len("make-dispatch:"):].encode("ascii"), "native Make dispatch")
                if (
                    not isinstance(dispatch, dict)
                    or set(dispatch) != {
                        "sequence", "kind", "environment", "executable", "arguments", "cwd", "global_ignore_errors",
                        "rebuilding_makefiles",
                    }
                    or type(dispatch["sequence"]) is not int or dispatch["sequence"] < 1
                    or not isinstance(dispatch["kind"], str) or dispatch["kind"] not in {"recipe", "value"}
                    or type(dispatch["global_ignore_errors"]) is not bool
                    or type(dispatch["rebuilding_makefiles"]) is not bool
                    or not isinstance(dispatch["executable"], str) or not isinstance(dispatch["cwd"], str)
                    or not isinstance(dispatch["arguments"], list) or not 1 <= len(dispatch["arguments"]) <= 1024
                    or any(not isinstance(argument, str) for argument in dispatch["arguments"])
                    or not isinstance(dispatch["environment"], dict) or len(dispatch["environment"]) > 1024
                    or any(not name or "=" in name or not isinstance(content, str)
                           for name, content in dispatch["environment"].items())
                ):
                    raise MakeProbeError("malformed native Make dispatch")
                contexts.append(dispatch)
            contexts.sort(key=lambda item: item["sequence"])
            if [item["sequence"] for item in contexts] != list(range(1, len(contexts) + 1)):
                raise MakeProbeError("native Make dispatch sequence is incomplete")
            if (
                set(helpers) != {item["sequence"] for item in contexts}
                or len(set(helpers.values())) != len(helpers) or set(helpers.values()) != set(policies)
                or set(jobs) != {item["sequence"] for item in contexts}
            ):
                raise MakeProbeError("native Make job policy evidence is incomplete")
            for item in contexts:
                item["job"] = jobs[item["sequence"]]
                policy = policies[helpers[item["sequence"]]]
                if item["kind"] == "recipe" and (not policy & 2 or item["job"]["kind"] != "recipe"):
                    raise MakeProbeError("native recipe lacks its actual GNU Make job")
                item.pop("global_ignore_errors")
                item["ignore_errors"] = bool(policy & 1)
            semantics["native_dispatches"] = contexts
            if observe_recipe_dispatch:
                semantics["recipe_dispatches"] = [
                    item for item in contexts if item["kind"] == "recipe"
                ]
            semantics["assignments"] = sorted(assignments, key=lambda item: item[1])
            recipe_sources = {record["source"] for record in semantics["files"] if record["source"]}
            semantics["owner_inputs"] = self.source_owners(set(owner_inputs) | recipe_sources)
            semantics["dynamic_commands"] = sorted(command_results.values(), key=encoded)
            semantics["published_sources"] = [record[:5] for record in self._publication_records()]
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
            phases = None
            if observe_source_phases:
                phases = {
                    "version": 1, "scope": observed["read_trace"]["scope"], "entries": phase_entries, "closed": True,
                }
                try:
                    source_phases.validate_capture(phases, observed["read_trace"])
                except ChannelError as error:
                    raise MakeProbeError(str(error)) from error
            observation = MakeObservation(
                target, semantics, execution, hashlib.sha256(semantic_bytes).hexdigest(),
                completed.stdout, completed.stderr, tuple(events),
                tuple(self.published_sources[path] for path in sorted(self.published_sources)),
                tuple(file_open_attempts),
                observed.get("read_trace"),
                phases,
            )
            if namespace_capture is not None:
                namespace_capture.native_complete = True
                self._namespace_pending[id(namespace_capture)] = namespace_capture, observation
        if namespace_capture is not None:
            self._seal_namespace(namespace_capture, observation)
            if observe_source_phases:
                self._retain_source_phases(observation, phase_images, namespace_capture)
        return observation

    @terminal_failure
    def variants(self, target, states, **kwargs):
        # Materialize and bound the declared finite input before *any* variant
        # executes. There are no hidden executor queues or unbounded futures.
        planned = []
        for state in states:
            if len(planned) >= self.budget.limits.states - self.budget.states:
                self.budget.reject("variant states exceed aggregate bound before launch")
            self.budget.admit_planned_state(len(encoded(state)))
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
