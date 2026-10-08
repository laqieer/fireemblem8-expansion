"""Live native output objects shared by syscall effects and source-read custody."""

from __future__ import annotations

from dataclasses import dataclass, field
import errno
import fcntl
import hashlib
import os
import stat

if __package__:
    from .budget import MakeProbeError
    from .lifecycle import finish_cleanup
    from .producer_channel import publication_identity
else:
    from budget import MakeProbeError
    from lifecycle import finish_cleanup
    from producer_channel import publication_identity


class NativeOutputError(MakeProbeError):
    pass


class NativeOutputObserver:
    """Observe admitted native jobs and tool capsules at stopped kernel operations."""

    def __init__(self, policy, paths):
        import sys
        if __package__:
            from .authority import relative_path
        else:
            from authority import relative_path
        self.policy = policy
        self.native = sys.modules[type(policy).__module__]
        for path in paths:
            relative_path(path)
        self.sequence = 0
        self.actor = None
        self.dispatch = None
        self.prefix = "/repo/" if policy.mode == "make" else "/work/"
        self.custody = NativeOutputs(
            deadline=self.deadline, charge=policy.charge_metadata,
            file_limit=policy.config["file_limit"], emit=self.emit,
            shared_paths={"/repo/" + path for kind, path in policy.config.get("native_resources", ()) if kind == "shared-lock"},
        )

    def deadline(self):
        import time
        if time.monotonic() >= self.policy.config["deadline"]:
            raise NativeOutputError("native output observation exhausted its original deadline")

    def emit(self, kind, **fields):
        import json
        self.sequence += 1
        row = {"sequence": self.sequence, "kind": kind, **fields}
        if self.policy.mode == "make":
            if kind == "output-close-failed" and not self.policy.config.get("native_resources"):
                row.pop("description")
            if kind == "output-operation-failed":
                operation = self.custody.pending[row["pid"]]
                if self.policy.config.get("native_resources"):
                    row["preimages"] = [
                        [path, None if identity is None else list(identity),
                         None if item is None or item.entries is None else list(item.entries)]
                        for path, pin, identity, item in operation.operands
                    ]
                    if operation.kind in {"mkdir", "replace"}:
                        row["flags"] = operation.flags
                if operation.kind == "open":
                    row["flags"] = operation.flags
                elif operation.kind == "dup":
                    row.update(
                        descriptor=operation.descriptor, duplicate_kind=operation.duplicate_kind,
                        target=operation.target, minimum=operation.minimum, flags=operation.flags,
                    )
            dispatch = self.dispatch if self.policy.config.get("native_resources") else row["owner"]
            if self.policy.config.get("native_resources"):
                row.setdefault("pid", self.actor)
                if kind == "output-retire":
                    row.setdefault("operation_owner", dispatch)
            job = self.policy.native_jobs[dispatch]
            self.policy.read_trace.machine_event(
                "native-output", job["pid"], dispatch=dispatch,
                event=row, sha256=hashlib.sha256(self.native.encoded(row)).hexdigest(),
            )
        self.policy.observe(
            "accessed", "native-output:" + json.dumps(row, sort_keys=True, separators=(",", ":")),
        )

    def pin(self, pid, descriptor):
        self.deadline()
        self.policy.charge_metadata(128)
        return os.open(f"/proc/{pid}/fd/{descriptor}", os.O_RDONLY | os.O_CLOEXEC)

    def operand(self, path, *, directory=False):
        self.deadline()
        self.policy.charge_metadata(128)
        try:
            return os.open(
                self.policy.config["root"] + path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                | (os.O_DIRECTORY if directory else 0),
            )
        except FileNotFoundError:
            return None

    def buffer(self, pid, pointer, count):
        parts = []
        while count:
            self.deadline()
            size = min(count, self.native.SYSCALL_MEMORY_LIMIT)
            self.policy.charge_metadata(size)
            parts.append(self.native.memory(pid, pointer, size))
            pointer += size
            count -= size
        return b"".join(parts)

    def lock_mode(self, pid, descriptor, identity):
        import re
        with open(f"/proc/{pid}/fdinfo/{descriptor}", "rb") as stream:
            data = stream.read(4097)
        self.policy.charge_metadata(len(data))
        if len(data) > 4096:
            raise NativeOutputError("native lock descriptor information exceeds its bound")
        locks = [line for line in data.splitlines() if line.startswith(b"lock:")]
        if not locks:
            return 0
        if len(locks) != 1:
            raise NativeOutputError("native descriptor has multiple unsupported lock records")
        match = re.fullmatch(
            rb"lock:\s+\d+: FLOCK\s+ADVISORY\s+(READ|WRITE)\s+\d+\s+"
            rb"([0-9a-fA-F]+):([0-9a-fA-F]+):(\d+)\s+0 EOF",
            locks[0],
        )
        if match is None or (
            int(match[2], 16), int(match[3], 16), int(match[4])
        ) != (os.major(identity[0]), os.minor(identity[0]), identity[1]):
            raise NativeOutputError("native lock record differs from its exact file object")
        return fcntl.LOCK_SH if match[1] == b"READ" else fcntl.LOCK_EX

    def successful_exec(self, pid, state):
        self.actor, self.dispatch = pid, state.native_dispatch
        operation, state.native_output_operation = state.native_output_operation, None
        if operation is not None:
            self.custody.complete_exec(operation)
        for (process, descriptor), item in tuple(self.custody.descriptors.items()):
            if process != pid:
                continue
            self.deadline()
            self.policy.charge_metadata(128)
            if descriptor not in state.fds:
                try:
                    os.stat(f"/proc/{pid}/fd/{descriptor}")
                except FileNotFoundError:
                    pass
                else:
                    raise NativeOutputError("native exec omitted a surviving output descriptor")
                self.custody.closed(
                    pid, descriptor, 0, event_kind="output-exec-close", generation=state.native_execs,
                )
                self.policy.read_trace.fd_closed(pid, descriptor)
                continue
            actual = os.stat(f"/proc/{pid}/fd/{descriptor}")
            if publication_identity(actual) != item.identity:
                raise NativeOutputError("native exec retained a different output descriptor object")

    def finish(self):
        self.custody.finish()
        if self.policy.mode == "make":
            if __package__:
                from .native_resources import validate_terminal_resources
            else:
                from native_resources import validate_terminal_resources
            validate_terminal_resources(
                self.policy.config["native_output_paths"],
                self.policy.config.get("native_resources", ()), self.custody.objects,
            )

    def entry(self, pid, state, registers):
        self.actor, self.dispatch = pid, state.native_dispatch
        native = self.policy
        r = registers
        n, a, b, c, d, e = r.orig_rax, r.rdi, r.rsi, r.rdx, r.r10, r.r8
        descriptor = native_int(a)
        unlink_flags = native_int(c) if n == 263 else None
        if n == 263 and unlink_flags not in {0, 0x200}:
            raise NativeOutputError("native unlinkat flags escape its finite file/directory operations")
        operation = None
        owner = state.native_dispatch if self.policy.mode == "make" else 1
        if n == 59 and self.policy.mode == "make":
            closing = []
            for process, selected in self.custody.descriptors:
                if process != pid:
                    continue
                with open(f"/proc/{pid}/fdinfo/{selected}", "rb") as stream:
                    data = stream.read(4097)
                self.policy.charge_metadata(len(data))
                if len(data) > 4096:
                    raise NativeOutputError("native exec descriptor information exceeds its bound")
                flags = [line.split(b":", 1)[1].strip() for line in data.splitlines() if line.startswith(b"flags:")]
                if len(flags) != 1:
                    raise NativeOutputError("native exec descriptor lacks its actual kernel flags")
                if int(flags[0], 8) & os.O_CLOEXEC:
                    closing.append(selected)
            if closing:
                operation = self.custody.enter_exec(pid=pid, descriptors=tuple(closing))
        elif n in {2, 85, 257}:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC if n == 85 else c if n == 257 else b
            path = state.pending[1]
            if self.policy.mode == "make" and state.role == "make" and not flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC
            ):
                return
            if self.policy.mode == "make" and path not in {
                "/repo/" + name for name in self.policy.config["native_output_paths"]
            }:
                if __package__:
                    from .native_resources import resource_role
                else:
                    from native_resources import resource_role
                job = self.policy.native_jobs.get(owner)
                if job is None or resource_role(job["admission"].get("resources", ()), path, job["pid"]) not in {
                    "temporary", "pid-temporary", "atomic-temporary", "shared-lock",
                }:
                    return
            if flags & os.O_TMPFILE == os.O_TMPFILE:
                raise NativeOutputError("native anonymous temporary output transitions are not implemented")
            if path.startswith(self.prefix):
                pin = self.operand(path)
                try:
                    if pin is not None and not stat.S_ISREG(os.fstat(pin).st_mode):
                        return
                    operation = self.custody.enter_open(
                        owner=owner, pid=pid, path=path, flags=flags, pin=pin,
                    )
                finally:
                    if pin is not None:
                        os.close(pin)
        elif n in {1, 18, 20} and (pid, descriptor) in self.custody.descriptors:
            pin = self.pin(pid, descriptor)
            try:
                info = {}
                with open(f"/proc/{pid}/fdinfo/{descriptor}", encoding="ascii") as stream:
                    for line in stream:
                        key, value = line.split(":", 1)
                        info[key] = value.strip()
                native.charge_metadata(128 + sum(len(key) + len(value) for key, value in info.items()))
                if int(info["flags"], 8) & os.O_APPEND:
                    raise NativeOutputError("native output append writes are not admitted")
                if n == 20:
                    if c > 1024:
                        raise NativeOutputError("native output writev exceeds its vector bound")
                    vectors = self.buffer(pid, b, c * 16)
                    parts, size = [], 0
                    for offset in range(0, len(vectors), 16):
                        pointer = int.from_bytes(vectors[offset:offset + 8], "little")
                        count = int.from_bytes(vectors[offset + 8:offset + 16], "little")
                        size += count
                        if size > self.custody.file_limit:
                            raise NativeOutputError("native output writev exceeds its file bound")
                        parts.append(self.buffer(pid, pointer, count))
                    data = b"".join(parts)
                else:
                    if c > self.custody.file_limit:
                        raise NativeOutputError("native output write exceeds its file bound")
                    data = self.buffer(pid, b, c)
                offset = native_signed(d) if n == 18 else int(info["pos"])
                operation = self.custody.enter_write(
                    pid=pid, descriptor=descriptor, pin=pin, data=data, offset=offset,
                )
            finally:
                os.close(pin)
        elif n == 3 and (pid, descriptor) in self.custody.descriptors:
            operation = self.custody.enter_close(pid=pid, descriptor=descriptor)
            state.native_output_close = descriptor
        elif n == 73 and (pid, descriptor) in self.custody.descriptors:
            item = self.custody.descriptors[(pid, descriptor)]
            if (
                self.policy.mode == "make" or self.policy.config.get("native_resources")
            ) and item.path not in self.custody.shared_paths:
                raise NativeOutputError("native flock lacks its shared synchronization role")
            operation = self.custody.enter_lock(
                pid=pid, descriptor=descriptor, flags=native_int(b),
                observed=self.lock_mode(pid, descriptor, item.identity),
            )
        elif n == 91 and (pid, descriptor) in self.custody.descriptors:
            operation = self.custody.enter_mode(pid=pid, descriptor=descriptor, mode=b & 0xFFFF)
        elif n in {32, 33, 292, 72} and (pid, descriptor) in self.custody.descriptors:
            if n == 72 and b not in {0, 1030}:
                if b in {fcntl.F_SETLK, fcntl.F_SETLKW}:
                    raise NativeOutputError("native POSIX record lock mutation is not admitted")
                if b == fcntl.F_SETFL and c & os.O_APPEND:
                    raise NativeOutputError("native output append description change is not admitted")
            else:
                kind = {32: "dup", 33: "dup2", 292: "dup3"}.get(
                    n, "fcntl-dupfd" if b == 0 else "fcntl-dupfd-cloexec",
                )
                operation = self.custody.enter_duplicate(
                    pid=pid, descriptor=descriptor, kind=kind,
                    target=native_int(b) if n in {33, 292} else None,
                    minimum=native_int(c) if n == 72 else None,
                    flags=c if n == 292 else 0,
                )
        elif n in {33, 292} and (pid, native_int(b)) in self.custody.descriptors:
            if self.policy.mode != "make":
                raise NativeOutputError("native output replacement by an untracked descriptor is not admitted")
            source = os.stat(f"/proc/{pid}/fd/{descriptor}")
            operation = self.custody.enter_duplicate_release(
                pid=pid, descriptor=descriptor, target=native_int(b),
                identity=(source.st_dev, source.st_ino, source.st_mode),
            )
        elif n in {82, 264, 316}:
            source = native.path(pid, state, a if n == 82 else b, -100 if n == 82 else native_signed(a), follow_final=False)
            destination = native.path(pid, state, b if n == 82 else d, -100 if n == 82 else native_signed(c), follow_final=False)
            if source.startswith(self.prefix) and destination.startswith(self.prefix):
                native.check(state, source, "write")
                native.check(state, destination, "write")
                first, second = self.operand(source), None
                try:
                    second = self.operand(destination)
                    operation = self.custody.enter_replace(
                        owner=owner, pid=pid, source=source, destination=destination,
                        source_pin=first, retired_pin=second, flags=e if n == 316 else 0,
                    )
                finally:
                    if first is not None:
                        os.close(first)
                    if second is not None:
                        os.close(second)
            else:
                raise NativeOutputError("native relocation is outside its exact Command outputs")
        elif n in {83, 84, 258} or n == 263 and unlink_flags == 0x200:
            path = native.path(
                pid, state, b if n in {258, 263} else a,
                native_signed(a) if n in {258, 263} else -100, follow_final=False,
            )
            if path.startswith(self.prefix):
                pin = self.operand(path)
                try:
                    if n in {83, 258}:
                        operation = self.custody.enter_mkdir(
                            owner=owner, pid=pid, path=path, pin=pin, mode=c if n == 258 else b,
                        )
                    else:
                        operation = self.custody.enter_rmdir(owner=owner, pid=pid, path=path, pin=pin)
                finally:
                    if pin is not None:
                        os.close(pin)
        elif n == 87 or n == 263 and unlink_flags == 0:
            path = native.path(pid, state, a if n == 87 else b, -100 if n == 87 else native_signed(a), follow_final=False)
            if path.startswith(self.prefix):
                pin = self.operand(path)
                try:
                    operation = self.custody.enter_remove(owner=owner, pid=pid, path=path, pin=pin)
                finally:
                    if pin is not None:
                        os.close(pin)
        elif n in {76, 77, 90, 91, 92, 93, 94, 260, 268, 280}:
            if n in {77, 91, 93}:
                observed = (pid, descriptor) in self.custody.descriptors
            else:
                path = native.path(
                    pid, state, b if n in {260, 268, 280} else a,
                    native_signed(a) if n in {260, 268, 280} else -100,
                )
                observed = path in self.custody.objects
            if observed:
                if n == 91 and self.policy.config.get("native_resources"):
                    pin = self.pin(pid, descriptor)
                    try:
                        operation = self.custody.enter_mode(pid=pid, descriptor=descriptor, pin=pin, mode=b)
                    finally:
                        os.close(pin)
                else:
                    raise NativeOutputError("native output mode/standalone truncate transition is not implemented")
        elif n in {86, 265}:
            raise NativeOutputError("native output hardlink transitions are not implemented")
        if operation is not None:
            state.native_output_operation = operation

    def leave(self, pid, state, result):
        self.actor, self.dispatch = pid, state.native_dispatch
        if state.native_output_operation is not None and state.native_output_operation.kind == "close":
            return
        operation, state.native_output_operation = state.native_output_operation, None
        if operation is not None:
            if operation.kind == "open":
                pin = None if result < 0 else self.pin(pid, result)
                try:
                    self.custody.leave_open(operation, result=result, pin=pin)
                finally:
                    if pin is not None:
                        os.close(pin)
            elif operation.kind == "write":
                self.custody.leave_write(operation, result)
            elif operation.kind == "replace":
                self.custody.leave_replace(operation, result)
            elif operation.kind == "remove":
                self.custody.leave_remove(operation, result)
            elif operation.kind == "dup":
                pin = None if result < 0 else self.pin(pid, result)
                try:
                    self.custody.leave_duplicate(operation, result, pin=pin)
                finally:
                    if pin is not None:
                        os.close(pin)
            elif operation.kind == "duplicate-release":
                info = None if result < 0 else os.stat(f"/proc/{pid}/fd/{result}")
                self.custody.leave_duplicate_release(
                    operation, result=result,
                    identity=None if info is None else (info.st_dev, info.st_ino, info.st_mode),
                )
            elif operation.kind == "exec":
                self.custody.fail_exec(operation, result=result)
            elif operation.kind == "mkdir":
                pin = None if result < 0 else self.operand(operation.source, directory=True)
                try:
                    self.custody.leave_mkdir(operation, result=result, pin=pin)
                finally:
                    if pin is not None:
                        os.close(pin)
            elif operation.kind == "rmdir":
                self.custody.leave_rmdir(operation, result)
            elif operation.kind == "lock":
                self.custody.leave_lock(
                    operation, result=result,
                    observed=self.lock_mode(pid, operation.descriptor, operation.operands[0][2]),
                )
            elif operation.kind == "mode":
                self.custody.leave_mode(operation, result=result)
            else:
                raise NativeOutputError("native output supervisor lost its operation kind")
def native_signed(value):
    return value - (1 << 64) if value & (1 << 63) else value


def native_int(value):
    value &= (1 << 32) - 1
    return value - (1 << 32) if value & (1 << 31) else value


@dataclass
class OpenDescription:
    serial: int
    bindings: set[tuple[int, int]] = field(default_factory=set)
    lock: int = 0
    pending: int | None = None


@dataclass
class OutputObject:
    owner: int
    serial: int
    path: str | None
    identity: tuple
    descriptor: int
    sha256: str | None = None
    revision: int = 0
    pending_writer: tuple[int, int] | None = None
    writers: set[tuple[int, int]] = field(default_factory=set)
    readers: set[int] = field(default_factory=set)
    retired: bool = False
    expected: bytearray | None = None
    entries: tuple[str, ...] | None = None
    descriptions: dict[tuple[int, int], OpenDescription] = field(default_factory=dict)


@dataclass
class SourcePin:
    descriptor: int
    object: OutputObject
    identity: tuple
    data: bytes
    mode: int
    sha256: str
    revision: int
    closed: bool = False
    observed: bool = True


@dataclass(frozen=True)
class NativeOperation:
    owner: int
    pid: int
    kind: str
    source: str
    destination: str | None
    flags: int
    operands: tuple
    descriptor: int | None = None
    offset: int | None = None
    data: bytes | None = None
    before: bytes | None = None
    duplicate_kind: str | None = None
    target: int | None = None
    minimum: int | None = None
    replacement_identity: tuple | None = None
    closing_descriptions: tuple = ()
    parents: tuple[str, ...] = ()
    description: OpenDescription | None = None
    request: int | None = None


class NativeOutputs:
    """Internal state keyed by issued producer; supervisor binds actual jobs."""

    def __init__(self, *, deadline, charge, file_limit, emit, shared_paths=()):
        self.deadline = deadline
        self.charge = charge
        self.file_limit = file_limit
        self.emit = emit
        self.shared_paths = frozenset(shared_paths)
        self.serial = 0
        self.description_serial = 0
        self.write_serial = 0
        self.objects = {}
        self.directories = {}
        self.versions = []
        self.descriptors = {}
        self.pins = {}
        self.pending = {}
        self.closed_state = False
        self.incomplete = None

    def _usable(self):
        if self.closed_state or self.incomplete is not None:
            raise NativeOutputError("native output custody is terminal after cleanup or incomplete lifecycle")

    def _begin(self, owner, pid, kind, source, destination=None, flags=0, pins=(), bindings=()):
        self._usable()
        if type(owner) is not int or owner < 1 or type(pid) is not int or pid < 1:
            raise NativeOutputError("native operation lacks its issued producer/process")
        if pid in self.pending:
            raise NativeOutputError("native operation overlaps its unfinished kernel return")
        namespace_kinds = {"open", "replace", "remove", "mkdir", "rmdir"}
        if kind in namespace_kinds:
            paths = {path for path in (source, destination) if path is not None}
            paths.update(
                path.rpartition("/")[0] for path in tuple(paths)
                if path.rpartition("/")[0] in self.directories
            )
            for active in self.pending.values():
                if active.kind not in namespace_kinds:
                    continue
                if (
                    kind == active.kind == "open"
                    and not (flags | active.flags) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                ):
                    continue
                self.deadline()
                others = {
                    path for path in (active.source, active.destination, *active.parents)
                    if path is not None
                }
                self.charge(64 + sum(len(os.fsencode(path)) for path in paths | others))
                if any(
                    left == right or left.startswith(right + "/") or right.startswith(left + "/")
                    for left in paths for right in others
                ):
                    raise NativeOutputError("native namespace operation overlaps an unfinished kernel return")
        operands, directory_parents = [], []
        try:
            for path, descriptor in pins:
                directory = kind in {"mkdir", "rmdir"}
                item = (
                    self.directories.get(path) or self.objects.get(path)
                    if directory else self.objects.get(path)
                )
                if item is not None and self._pending_mode(item):
                    raise NativeOutputError("native operation overlaps an unfinished mode transition")
                if descriptor is None:
                    if item is not None:
                        raise NativeOutputError("native operation absence contradicts its owned object")
                    operands.append((path, None, None, None))
                    continue
                if directory and (
                    item is not None and item.entries is not None
                    or item is None and self._directory_operand(descriptor)
                ):
                    identity = self._directory_identity(descriptor)
                else:
                    identity = self._identity(descriptor)
                prior_version = (
                    kind == "replace" and path == destination
                    or kind == "mkdir" and item is not None and item.entries is not None
                    or kind == "open" and path in self.shared_paths
                    or kind == "open" and not flags & (
                        os.O_WRONLY | os.O_RDWR | os.O_TRUNC | os.O_CREAT
                    )
                )
                if (
                    item is None and kind != "mkdir"
                    or item is not None and (
                        item.owner != owner and not prior_version
                        or item.retired or identity != item.identity
                    )
                ):
                    raise NativeOutputError("native operation entry differs from its owned operand")
                pin = os.dup(descriptor)
                operands.append((path, pin, identity, item))
            if kind in {"open", "replace", "remove", "mkdir", "rmdir"}:
                parents = {path.rpartition("/")[0] for path in (source, destination) if path is not None}
                for parent in sorted(parents):
                    item = self.directories.get(parent)
                    if item is None:
                        continue
                    if self._directory_identity(item.descriptor) != item.identity:
                        raise NativeOutputError("native namespace operation changed its parent directory")
                    if self._directory_entries(item.descriptor) != item.entries:
                        raise NativeOutputError("native namespace operation changed its parent entries")
                    if self._directory_identity(item.descriptor) != item.identity:
                        raise NativeOutputError("native namespace parent changed during observation")
                    pin = os.dup(item.descriptor)
                    operands.append((parent, pin, item.identity, item))
                    directory_parents.append(parent)
            for label, descriptor, item in bindings:
                if self._pending_mode(item):
                    raise NativeOutputError("native operation overlaps an unfinished mode transition")
                identity = self._identity(descriptor)
                if identity != item.identity:
                    raise NativeOutputError("native operation entry differs from its bound descriptor")
                pin = os.dup(descriptor)
                operands.append((label, pin, identity, item))
            self.charge(256 + 128 * len(operands))
            operation = NativeOperation(
                owner, pid, kind, source, destination, flags, tuple(operands),
                parents=tuple(directory_parents),
            )
            self.pending[pid] = operation
            return operation
        except BaseException as error:
            finish_cleanup(
                [lambda pin=pin: os.close(pin) for _, pin, _, _ in operands if pin is not None],
                primary=error,
            )
            raise

    def _operation(self, operation, kind):
        self._usable()
        if (
            not isinstance(operation, NativeOperation)
            or self.pending.get(operation.pid) is not operation or operation.kind != kind
        ):
            raise NativeOutputError("native operation return is foreign, stale or unpaired")
        self.deadline()

    def _end(self, operation, *, success=True, parent_postimages=()):
        if success:
            if len(parent_postimages) != len(operation.parents):
                raise NativeOutputError("native namespace return lacks validated parent postimages")
            for item, identity, entries in parent_postimages:
                item.identity, item.entries = identity, entries
            for item, identity, _ in parent_postimages:
                self._event(
                    "output-directory-change", item, pid=operation.pid, operation=operation.kind,
                    identity=list(identity), source=operation.source, destination=operation.destination,
                    entries=list(item.entries),
                    before=list(next(before for path, pin, before, previous in operation.operands if previous is item)),
                )
        if operation.description is not None:
            operation.description.pending = None
        del self.pending[operation.pid]
        finish_cleanup([
            lambda pin=pin: os.close(pin)
            for _, pin, _, _ in operation.operands if pin is not None
        ])

    def _failed(self, operation, result):
        if type(result) is not int or not -4095 <= result < 0:
            raise NativeOutputError("native failed operation has no actual kernel error")
        for index, (_, pin, identity, item) in enumerate(operation.operands):
            if pin is not None:
                actual = (
                    self._directory_identity(pin)
                    if stat.S_ISDIR(identity[2])
                    or operation.parents and index >= len(operation.operands) - len(operation.parents)
                    else self._identity(pin)
                )
                if actual != identity:
                    raise NativeOutputError("failed native operation changed its entry operand")
                if item is not None and item.sha256 is not None:
                    self._verify_settled(item, identity)
        self._emit(
            "output-operation-failed", owner=operation.owner, pid=operation.pid,
            operation=operation.kind, source=operation.source,
            destination=operation.destination, result=result,
        )
        self._end(operation, success=False)

    def enter_open(self, *, owner, pid, path, flags, pin):
        self._usable()
        if (
            type(flags) is not int or flags < 0 or flags & os.O_APPEND
            or flags & os.O_TMPFILE == os.O_TMPFILE
        ):
            raise NativeOutputError("native output open has unsupported flags")
        item = self.objects.get(path)
        if path in self.shared_paths and flags & (os.O_WRONLY | os.O_TRUNC | os.O_EXCL):
            raise NativeOutputError("native shared lock open requests content or replacement authority")
        writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_TRUNC))
        if item is not None and writing and (
            item.readers or flags & os.O_TRUNC and (item.writers or item.pending_writer is not None)
        ):
            raise NativeOutputError("native output open would destroy an active source/writer version")
        return self._begin(owner, pid, "open", path, flags=flags, pins=((path, pin),))

    def leave_open(self, operation, *, result, pin=None):
        self._operation(operation, "open")
        if type(result) is not int or not -4095 <= result < 1 << 31:
            raise NativeOutputError("native open return is not a bounded kernel descriptor/status")
        if result < 0:
            if pin is not None:
                raise NativeOutputError("failed native open claims a returned descriptor pin")
            self._failed(operation, result)
            return None
        if type(result) is not int or pin is None:
            raise NativeOutputError("native open return lacks its actual descriptor pin")
        _, before_pin, before, item = operation.operands[0]
        identity = self._identity(pin)
        parents = self._directory_postimages(operation)
        if item is not None and operation.flags & os.O_TRUNC:
            if (
                identity[:3] != before[:3] or identity[3] != 0 or identity[6] != before[6]
                or self._identity(before_pin) != identity
            ):
                raise NativeOutputError("native truncate return differs from its exact entry object")
            item.identity = identity
            item.revision += 1
            item.sha256 = None
            self._event("output-truncate", item, pid=operation.pid, fd=result, identity=list(identity))
        elif item is not None and identity != before:
            raise NativeOutputError("native open absorbed an undeclared object change")
        elif item is None and not operation.flags & os.O_CREAT:
            raise NativeOutputError("native output appeared without a creating open")
        item = self.opened(
            owner=operation.owner, pid=operation.pid, descriptor=result, pin=pin,
            path=operation.source,
            writing=operation.source not in self.shared_paths and bool(operation.flags & (os.O_WRONLY | os.O_RDWR | os.O_TRUNC)),
        )
        self._end(operation, parent_postimages=parents)
        return item

    def enter_replace(self, *, owner, pid, source, destination, source_pin, retired_pin, flags=0):
        self._usable()
        if source == destination or type(flags) is not int or flags not in {0, 1}:
            raise NativeOutputError("native replacement lacks distinct declared operands")
        for path in (source, destination):
            item = self.objects.get(path)
            if item is not None and (item.writers or path == source and item.readers):
                raise NativeOutputError("native replacement overlaps an active writer/source")
        return self._begin(
            owner, pid, "replace", source, destination, flags=flags,
            pins=((source, source_pin), (destination, retired_pin)),
        )

    def leave_replace(self, operation, result):
        self._operation(operation, "replace")
        self._status_result(result)
        if result < 0:
            self._failed(operation, result)
            return
        if (
            result != 0 or operation.operands[0][1] is None
            or operation.flags == 1 and operation.operands[1][1] is not None
        ):
            raise NativeOutputError("native replacement success lacks its exact source object")
        parents = self._directory_postimages(operation)
        self.replaced(
            owner=operation.owner, source=operation.source, destination=operation.destination,
            source_pin=operation.operands[0][1], retired_pin=operation.operands[1][1], result=result,
        )
        self._end(operation, parent_postimages=parents)

    def enter_remove(self, *, owner, pid, path, pin):
        self._usable()
        item = self.objects.get(path)
        if item is not None and item.writers:
            raise NativeOutputError("native removal overlaps an active writer")
        return self._begin(owner, pid, "remove", path, pins=((path, pin),))

    def leave_remove(self, operation, result):
        self._operation(operation, "remove")
        self._status_result(result)
        if result < 0:
            self._failed(operation, result)
            return
        pin = operation.operands[0][1]
        if result != 0 or pin is None:
            raise NativeOutputError("native removal success lacks its exact owned object")
        parents = self._directory_postimages(operation)
        self.removed(owner=operation.owner, path=operation.source, pin=pin, result=result)
        self._end(operation, parent_postimages=parents)

    def _directory_operand(self, descriptor):
        self.deadline()
        self.charge(128)
        return stat.S_ISDIR(os.fstat(descriptor).st_mode)

    def _directory_identity(self, descriptor):
        self._usable()
        self.deadline()
        info = os.fstat(descriptor)
        self.charge(128)
        if (
            not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o7000
            or stat.S_IMODE(info.st_mode) & 0o700 != 0o700 or info.st_nlink < 0
        ):
            raise NativeOutputError("native output directory lacks its traversable kernel identity")
        return publication_identity(info)

    def _directory_entries(self, descriptor):
        self.deadline()
        entries = tuple(sorted(os.listdir(descriptor)))
        self.charge(128 + sum(64 + len(os.fsencode(name)) for name in entries))
        return entries

    def _directory_postimages(self, operation):
        if not operation.parents:
            return ()
        postimages = []
        for parent, pin, before, item in operation.operands[-len(operation.parents):]:
            entries = set(item.entries)
            if operation.kind in {"remove", "rmdir", "replace"}:
                if operation.source.rpartition("/")[0] == parent:
                    entries.remove(operation.source.rpartition("/")[2])
            if operation.kind == "mkdir" or (
                operation.kind == "open" and operation.operands[0][1] is None
            ):
                entries.add(operation.source.rpartition("/")[2])
            if operation.kind == "replace" and operation.destination.rpartition("/")[0] == parent:
                entries.add(operation.destination.rpartition("/")[2])
            identity = self._directory_identity(pin)
            expected = tuple(sorted(entries))
            if (
                identity[:3] != before[:3] or self._directory_entries(pin) != expected
                or self._directory_identity(pin) != identity
            ):
                raise NativeOutputError("native namespace return differs from its exact parent effect")
            postimages.append((item, identity, expected))
        return tuple(postimages)

    def enter_mkdir(self, *, owner, pid, path, pin, mode):
        if (
            type(mode) is not int or mode & ~0o777 or mode & 0o700 != 0o700
        ):
            raise NativeOutputError("native directory creation lacks its exact mode/path role")
        return self._begin(owner, pid, "mkdir", path, flags=mode, pins=((path, pin),))

    def leave_mkdir(self, operation, *, result, pin=None):
        self._operation(operation, "mkdir")
        self._status_result(result)
        if result < 0:
            if pin is not None:
                raise NativeOutputError("failed native mkdir claims a created directory pin")
            self._failed(operation, result)
            return
        if operation.operands[0][1] is not None or pin is None:
            raise NativeOutputError("successful native mkdir contradicts its absent entry operand")
        identity = self._directory_identity(pin)
        if (
            stat.S_IMODE(identity[2]) & ~operation.flags or identity[6] != 2
            or self._directory_entries(pin) or self._directory_identity(pin) != identity
        ):
            raise NativeOutputError("native mkdir returned an unexpected mode or populated directory")
        parents = self._directory_postimages(operation)
        self.charge(256)
        self.serial += 1
        item = OutputObject(
            operation.owner, self.serial, operation.source, identity, os.dup(pin), entries=(),
        )
        self.directories[operation.source] = item
        self.versions.append(item)
        self._event("output-mkdir", item, pid=operation.pid, identity=list(identity))
        self._end(operation, parent_postimages=parents)

    def enter_rmdir(self, *, owner, pid, path, pin):
        item = self.directories.get(path)
        if item is None and path in self.objects and pin is not None:
            return self._begin(owner, pid, "rmdir", path, pins=((path, pin),))
        if item is None and pin is None:
            return self._begin(owner, pid, "rmdir", path, pins=((path, pin),))
        if item is None or pin is None:
            raise NativeOutputError("native directory removal lacks its previously created owner")
        identity = self._directory_identity(pin)
        if identity != item.identity or self._directory_entries(pin) != item.entries:
            raise NativeOutputError("native directory removal changed its owned inode/mode/entries")
        return self._begin(owner, pid, "rmdir", path, pins=((path, pin),))

    def leave_rmdir(self, operation, result):
        self._operation(operation, "rmdir")
        self._status_result(result)
        if result < 0:
            self._failed(operation, result)
            return
        _, pin, before, item = operation.operands[0]
        if pin is None or item is None or item.entries is None:
            raise NativeOutputError("successful native rmdir lacks its owned entry directory")
        identity = self._directory_identity(pin)
        if identity[:3] != before[:3] or identity[6] != 0:
            raise NativeOutputError("native rmdir return differs from its retired directory")
        parents = self._directory_postimages(operation)
        item.identity, item.retired = identity, True
        del self.directories[operation.source]
        self._event("output-rmdir", item, pid=operation.pid, identity=list(identity))
        self._end(operation, parent_postimages=parents)

    def _bytes(self, pin, identity):
        self.charge(identity[3])
        data = bytearray()
        while len(data) < identity[3]:
            self.deadline()
            block = os.pread(pin, min(65536, identity[3] - len(data)), len(data))
            if not block:
                raise NativeOutputError("native operation entry content was truncated")
            data.extend(block)
        if self._identity(pin) != identity:
            raise NativeOutputError("native operation content changed during observation")
        return bytes(data)

    def enter_write(self, *, pid, descriptor, pin, data, offset):
        self._usable()
        if (
            not isinstance(data, bytes) or len(data) > self.file_limit
            or type(offset) is not int or not 0 <= offset <= self.file_limit
            or offset + len(data) > self.file_limit
        ):
            raise NativeOutputError("native write entry lacks bounded actual bytes/offset")
        if pid in self.pending:
            raise NativeOutputError("native write overlaps an unfinished kernel operation")
        item = self._claim_writer(pid, descriptor, pin, paired=True)
        operation = None
        try:
            operation = self._begin(
                item.owner, pid, "write", item.path, pins=((item.path, pin),),
            )
            initial = (
                self._bytes(operation.operands[0][1], operation.operands[0][2])
                if item.expected is None else None
            )
            self.charge(len(data) + (0 if initial is None else len(initial)))
            if initial is not None:
                item.expected = bytearray(initial)
            before = bytes(item.expected[offset:offset + len(data)])
            self.charge(len(before))
            self.write_serial += 1
            operation = NativeOperation(
                operation.owner, pid, operation.kind, operation.source,
                operation.destination, operation.flags, operation.operands,
                descriptor=descriptor, offset=offset, data=data, before=before,
                request=self.write_serial,
            )
            self.pending[pid] = operation
            self._event(
                "output-write-entry", item, pid=pid, fd=descriptor,
                request=operation.request, requested_count=len(data), offset=offset,
                description=item.descriptions[(pid, descriptor)].serial,
                identity=list(operation.operands[0][2]),
            )
            return operation
        except BaseException as error:
            item.pending_writer = None
            finish_cleanup(
                [] if operation is None else [lambda: self._end(operation)],
                primary=error,
            )
            raise

    def leave_write(self, operation, result):
        self._operation(operation, "write")
        if type(result) is not int or result < -4095 or result > len(operation.data):
            raise NativeOutputError("native write return exceeds its actual entry bytes")
        pin = operation.operands[0][1]
        identity = self._identity(pin)
        item = operation.operands[0][3]
        size = len(item.expected)
        expected = operation.before
        if result > 0:
            size = max(size, operation.offset + result)
            expected = operation.data[:result] + operation.before[result:]
        if identity[3] != size:
            raise NativeOutputError("native write return absorbed bytes outside its actual kernel effect")
        self.charge(len(expected))
        offset = 0
        while offset < len(expected):
            self.deadline()
            actual = os.pread(pin, min(65536, len(expected) - offset), operation.offset + offset)
            if not actual or actual != expected[offset:offset + len(actual)]:
                raise NativeOutputError("native write return absorbed bytes outside its actual kernel effect")
            offset += len(actual)
        if self._identity(pin) != identity:
            raise NativeOutputError("native write postimage changed during range observation")
        growth = size - len(item.expected)
        self.charge(2 * growth)
        self.written(operation.pid, operation.descriptor, pin, result)
        if result > 0:
            item.expected.extend(b"\0" * growth)
            item.expected[operation.offset:operation.offset + result] = operation.data[:result]
        self._end(operation)

    def _verify_expected(self, item, digest):
        if item.expected is None:
            return
        self.charge(len(item.expected))
        self.deadline()
        if hashlib.sha256(item.expected).hexdigest() != digest:
            raise NativeOutputError("native write return absorbed bytes outside its actual kernel effect")

    def enter_duplicate(self, *, pid, descriptor, kind="dup", target=None, minimum=None, flags=0):
        self._usable()
        item = self.descriptors.get((pid, descriptor))
        if item is None:
            raise NativeOutputError("native duplicate entry lacks its actual owned descriptor")
        if (
            kind not in {"dup", "dup2", "dup3", "fcntl-dupfd", "fcntl-dupfd-cloexec"}
            or type(flags) is not int
            or flags not in ({0, os.O_CLOEXEC} if kind == "dup3" else {0})
            or kind in {"dup2", "dup3"} and (
                type(target) is not int or not -(1 << 31) <= target < 1 << 31 or minimum is not None
            )
            or kind == "dup" and (target is not None or minimum is not None)
            or kind.startswith("fcntl-") and (
                target is not None or type(minimum) is not int or not -(1 << 31) <= minimum < 1 << 31
            )
        ):
            raise NativeOutputError("native duplicate entry lacks its exact kind/target/minimum")
        bindings = [("source-fd:" + str(descriptor), item.descriptor, item)]
        old = self.descriptors.get((pid, target)) if target is not None else None
        if old is not None and target != descriptor:
            if old.pending_writer is not None:
                raise NativeOutputError("native duplicate target overlaps an unfinished writer")
            self._close_available(old, (pid, target))
            bindings.append(("target-fd:" + str(target), old.descriptor, old))
        operation = self._begin(
            item.owner, pid, "dup", bindings[0][0], flags=flags, bindings=bindings,
        )
        operation = NativeOperation(
            operation.owner, pid, operation.kind, operation.source,
            operation.destination, operation.flags, operation.operands, descriptor=descriptor,
            duplicate_kind=kind, target=target, minimum=minimum,
            description=old.descriptions[(pid, target)] if old is not None and target != descriptor else None,
        )
        self.pending[pid] = operation
        return operation

    def leave_duplicate(self, operation, result, *, pin=None):
        self._operation(operation, "dup")
        if type(result) is not int:
            raise NativeOutputError("native duplicate return is not a kernel descriptor/status")
        if result < 0:
            if pin is not None:
                raise NativeOutputError("failed native duplicate claims a returned descriptor pin")
            self._failed(operation, result)
            return
        if (
            result >= 1 << 31 or pin is None
            or operation.duplicate_kind in {"dup2", "dup3"} and result != operation.target
            or operation.duplicate_kind == "dup3" and result == operation.descriptor
            or operation.duplicate_kind not in {"dup2", "dup3"} and (
                result == operation.descriptor or (operation.pid, result) in self.descriptors
                or operation.minimum is not None and (operation.minimum < 0 or result < operation.minimum)
            )
        ):
            raise NativeOutputError("native duplicate return differs from its requested FD operation")
        for _, operand, identity, _ in operation.operands:
            if self._identity(operand) != identity:
                raise NativeOutputError("native duplicate return changed its exact entry object")
        if self._identity(pin) != operation.operands[0][2]:
            raise NativeOutputError("native duplicate return pin differs from its exact source object")
        self.duplicated(operation.pid, operation.descriptor, result)
        self._end(operation)

    def _identity(self, descriptor):
        self._usable()
        self.deadline()
        info = os.fstat(descriptor)
        self.charge(128)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_mode & 0o7000
            or not 0 <= info.st_size <= self.file_limit or info.st_nlink not in {0, 1}
        ):
            raise NativeOutputError("native output is not a bounded regular object")
        return publication_identity(info)

    def enter_lock(self, *, pid, descriptor, flags, observed):
        self._usable()
        item = self.descriptors.get((pid, descriptor))
        if (
            item is None or type(flags) is not int
            or flags not in {
                fcntl.LOCK_SH, fcntl.LOCK_EX, fcntl.LOCK_UN,
                fcntl.LOCK_SH | fcntl.LOCK_NB, fcntl.LOCK_EX | fcntl.LOCK_NB,
            }
            or type(observed) is not int or observed not in {0, fcntl.LOCK_SH, fcntl.LOCK_EX}
        ):
            raise NativeOutputError("native flock lacks its finite bound descriptor operation")
        description = item.descriptions[(pid, descriptor)]
        if self._description_closing(description):
            raise NativeOutputError("native flock overlaps an unfinished close on its description")
        if description.pending is not None or item.pending_writer is not None or observed != description.lock:
            raise NativeOutputError("native flock differs from its live open-file description")
        operation = self._begin(
            item.owner, pid, "lock", item.path, flags=flags,
            bindings=(("lock-fd:" + str(descriptor), item.descriptor, item),),
        )
        try:
            before = self._digest(operation.operands[0][1], operation.operands[0][2]).encode("ascii")
            operation = NativeOperation(
                operation.owner, pid, operation.kind, operation.source, None,
                flags, operation.operands, descriptor=descriptor, before=before,
                description=description,
            )
            self.pending[pid] = operation
            description.pending = pid
            return operation
        except BaseException as error:
            finish_cleanup([lambda: self._end(operation, success=False)], primary=error)
            raise

    def enter_mode(self, *, pid, descriptor, mode):
        self._usable()
        binding = (pid, descriptor)
        item = self.descriptors.get(binding)
        if (
            item is None or binding not in item.writers or item.retired or item.readers
            or type(mode) is not int or mode < 0 or mode & ~0o777
            or item.pending_writer is not None
            or any(description.pending is not None for description in item.descriptions.values())
            or any(
                operand[3] is item for active in self.pending.values() for operand in active.operands
            )
        ):
            raise NativeOutputError("native mode transition lacks its exclusive owned writable object")
        operation = self._begin(
            item.owner, pid, "mode", item.path, flags=mode,
            bindings=(("mode-fd:" + str(descriptor), item.descriptor, item),),
        )
        try:
            digest = self._digest(operation.operands[0][1], operation.operands[0][2])
            if item.expected is not None:
                self._verify_expected(item, digest)
            operation = NativeOperation(
                operation.owner, pid, operation.kind, operation.source, None,
                mode, operation.operands, descriptor=descriptor, before=digest.encode("ascii"),
            )
            self.pending[pid] = operation
            return operation
        except BaseException as error:
            finish_cleanup([lambda: self._end(operation, success=False)], primary=error)
            raise

    def _pending_mode(self, item):
        return any(
            active.kind == "mode" and any(operand[3] is item for operand in active.operands)
            for active in self.pending.values()
        )

    def _description_closing(self, description):
        return any(
            active.kind in {"close", "duplicate-release", "dup"} and active.description is description
            or any(selected is description for selected in active.closing_descriptions)
            for active in self.pending.values()
        )

    def enter_exec(self, *, pid, descriptors):
        bindings, descriptions, owner = [], [], None
        for descriptor in descriptors:
            binding = (pid, descriptor)
            item = self.descriptors.get(binding)
            if item is None:
                raise NativeOutputError("native exec closure lost its actual output descriptor")
            self._close_available(item, binding)
            description = item.descriptions[binding]
            if self._description_closing(description) or owner is not None and item.owner != owner:
                raise NativeOutputError("native exec closure overlaps another description transition or owner")
            owner = item.owner
            bindings.append(("exec-fd:" + str(descriptor), item.descriptor, item))
            descriptions.append(description)
        if not bindings:
            raise NativeOutputError("native exec closure has no issued descriptors")
        operation = self._begin(owner, pid, "exec", bindings[0][2].path, bindings=tuple(bindings))
        operation = NativeOperation(
            operation.owner, pid, operation.kind, operation.source, None, 0, operation.operands,
            closing_descriptions=tuple(descriptions),
        )
        self.pending[pid] = operation
        return operation

    def complete_exec(self, operation):
        self._operation(operation, "exec")
        self._end(operation)

    def fail_exec(self, operation, *, result):
        self._operation(operation, "exec")
        self._failed(operation, result)

    def enter_close(self, *, pid, descriptor):
        self._usable()
        binding = (pid, descriptor)
        item = self.descriptors.get(binding)
        if item is None:
            raise NativeOutputError("native close entry lacks its exact owned descriptor")
        self._close_available(item, binding)
        operation = self._begin(
            item.owner, pid, "close", item.path,
            bindings=(("close-fd:" + str(descriptor), item.descriptor, item),),
        )
        operation = NativeOperation(
            operation.owner, pid, operation.kind, operation.source, None, 0,
            operation.operands, descriptor=descriptor, description=item.descriptions[binding],
        )
        self.pending[pid] = operation
        return operation

    def enter_duplicate_release(self, *, pid, descriptor, target, identity):
        binding = (pid, target)
        item = self.descriptors.get(binding)
        if item is None or (pid, descriptor) in self.descriptors:
            raise NativeOutputError("native duplicate release lacks its foreign source and owned target")
        self._close_available(item, binding)
        operation = self._begin(
            item.owner, pid, "duplicate-release", item.path,
            bindings=(("duplicate-target:" + str(target), item.descriptor, item),),
        )
        operation = NativeOperation(
            operation.owner, pid, operation.kind, operation.source, None, 0,
            operation.operands, descriptor=descriptor, target=target,
            description=item.descriptions[binding], replacement_identity=identity,
        )
        self.pending[pid] = operation
        return operation

    def leave_duplicate_release(self, operation, *, result, identity):
        self._operation(operation, "duplicate-release")
        if result < 0:
            self._failed(operation, result)
            return
        if result != operation.target or identity != operation.replacement_identity:
            raise NativeOutputError("native duplicate release differs from its actual source descriptor")
        self.closed(operation.pid, operation.target, 0, event_kind="output-duplicate-release")
        self._end(operation)

    def leave_close(self, operation, *, result):
        self._operation(operation, "close")
        self.closed(operation.pid, operation.descriptor, result)
        self._end(operation)

    def _close_available(self, item, binding):
        if item.pending_writer == binding:
            raise NativeOutputError("native output close overlaps an unfinished write")
        if item.descriptions[binding].pending is not None:
            raise NativeOutputError("native output close overlaps an unfinished flock")
        if any(
            any(selected is item.descriptions[binding] for selected in active.closing_descriptions)
            for active in self.pending.values()
        ):
            raise NativeOutputError("native output close overlaps an unfinished exec closure")
        if self._pending_mode(item):
            raise NativeOutputError("native output close overlaps an unfinished mode transition")

    def leave_mode(self, operation, *, result):
        self._operation(operation, "mode")
        self._status_result(result)
        pin, before, item = operation.operands[0][1:]
        if self.descriptors.get((operation.pid, operation.descriptor)) is not item:
            raise NativeOutputError("native mode return lost its actual descriptor")
        identity = self._identity(pin)
        if (
            result < 0 and identity != before
            or result == 0 and (
                identity[:2] != before[:2] or identity[3:5] != before[3:5]
                or identity[6] != before[6] or stat.S_IMODE(identity[2]) != operation.flags
            )
            or self._digest(pin, identity).encode("ascii") != operation.before
        ):
            raise NativeOutputError("native mode return changed content or unrelated identity")
        if result < 0:
            self._failed(operation, result)
            return
        item.identity = identity
        self._event(
            "output-mode", item, pid=operation.pid, fd=operation.descriptor,
            mode=operation.flags, identity=list(identity),
        )
        self._end(operation)

    def leave_lock(self, operation, *, result, observed):
        self._operation(operation, "lock")
        self._status_result(result)
        if type(observed) is not int or observed not in {0, fcntl.LOCK_SH, fcntl.LOCK_EX}:
            raise NativeOutputError("native flock return lacks its observed kernel lock mode")
        description = operation.description
        item = self.descriptors.get((operation.pid, operation.descriptor))
        if (
            item is None or item.descriptions.get((operation.pid, operation.descriptor)) is not description
            or description.pending != operation.pid
        ):
            raise NativeOutputError("native flock return lost its exact open-file description")
        pin, identity = operation.operands[0][1:3]
        if self._identity(pin) != identity or self._digest(pin, identity).encode("ascii") != operation.before:
            raise NativeOutputError("native flock changed its file content or identity")
        requested = operation.flags & ~fcntl.LOCK_NB
        target = 0 if requested == fcntl.LOCK_UN else requested
        if result < 0:
            if result not in {-errno.EAGAIN, -errno.EINTR} or requested == fcntl.LOCK_UN:
                raise NativeOutputError("native flock has an unsupported kernel failure")
            if result == -errno.EAGAIN and not operation.flags & fcntl.LOCK_NB:
                raise NativeOutputError("native blocking flock claims a nonblocking failure")
            # Linux lock conversion releases the prior lock before retrying.
            target = description.lock if description.lock == requested else 0
        if observed != target:
            raise NativeOutputError("native flock return differs from its observed kernel lock")
        description.lock = observed
        self._event(
            "output-lock", item, pid=operation.pid, fd=operation.descriptor,
            description=description.serial, flags=operation.flags, result=result, mode=observed,
        )
        self._end(operation)

    def _emit(self, kind, **fields):
        try:
            self.emit(kind, **fields)
        except BaseException:
            self.incomplete = "unpublished " + kind
            raise

    def _event(self, kind, item, **fields):
        self._emit(
            kind, owner=item.owner, serial=item.serial, revision=item.revision, path=item.path,
            **fields,
        )

    def _verify_readers(self, item):
        for descriptor in item.readers:
            source = self.pins[descriptor]
            before = self._identity(descriptor)
            if before[:5] != source.identity[:5] or item.revision != source.revision:
                raise NativeOutputError("native retirement changed pinned source content or mode")
            self.charge(before[3])
            digest, offset = hashlib.sha256(), 0
            while offset < before[3]:
                self.deadline()
                block = os.pread(descriptor, min(65536, before[3] - offset), offset)
                if not block:
                    raise NativeOutputError("native retirement truncated pinned source content")
                digest.update(block)
                offset += len(block)
            if self._identity(descriptor) != before or digest.hexdigest() != source.sha256:
                raise NativeOutputError("native retirement changed pinned source content")

    def _digest(self, descriptor, identity):
        self.charge(identity[3])
        digest, offset = hashlib.sha256(), 0
        while offset < identity[3]:
            self.deadline()
            block = os.pread(descriptor, min(65536, identity[3] - offset), offset)
            if not block:
                raise NativeOutputError("native output content was truncated during settlement")
            digest.update(block)
            offset += len(block)
        if self._identity(descriptor) != identity:
            raise NativeOutputError("native output changed during content settlement")
        return digest.hexdigest()

    def _settle(self, item):
        if item.writers or item.pending_writer is not None or item.retired:
            raise NativeOutputError("native output settlement overlaps an active writer")
        item.sha256 = None
        identity = self._identity(item.descriptor)
        if identity != item.identity:
            raise NativeOutputError("native output settlement absorbed an unobserved change")
        digest = self._digest(item.descriptor, identity)
        self._verify_expected(item, digest)
        self._event("output-settled", item, identity=list(identity), sha256=digest)
        item.sha256 = digest
        item.expected = None

    def _verify_settled(self, item, identity):
        if item.sha256 is None or self._digest(item.descriptor, identity) != item.sha256:
            raise NativeOutputError("native output retirement changed settled content")

    def opened(self, *, owner, pid, descriptor, pin, path, writing):
        """Bind an actual successful open to its kernel object, not pathname alone."""
        self._usable()
        if type(owner) is not int or owner < 1 or type(writing) is not bool:
            raise NativeOutputError("native output open lacks its issued producer")
        identity = self._identity(pin)
        binding = (pid, descriptor)
        if binding in self.descriptors:
            raise NativeOutputError("native output descriptor was reused without retirement")
        item = self.objects.get(path)
        if item is None:
            if identity[6] != 1:
                raise NativeOutputError("native output open names a retired object")
            self.charge(256)
            self.serial += 1
            item = OutputObject(owner, self.serial, path, identity, os.dup(pin))
            self.objects[path] = item
            self.versions.append(item)
        elif item.retired or writing and item.owner != owner or identity != item.identity:
            raise NativeOutputError("native output open differs from its live owned object")
        if writing and item.readers:
            raise NativeOutputError("native output writer overlaps a pinned source read")
        self.charge(192)
        self.descriptors[binding] = item
        self.description_serial += 1
        description = OpenDescription(self.description_serial, {binding})
        item.descriptions[binding] = description
        if writing:
            item.writers.add(binding)
        self._event(
            "output-open", item, pid=pid, fd=descriptor, operation_owner=owner,
            identity=list(identity), writing=writing, description=description.serial,
        )
        if not writing and item.sha256 is None and not item.writers:
            self._settle(item)
        return item

    def inherited(self, parent, child, descriptors):
        self._usable()
        copies = {}
        for descriptor in descriptors:
            self.deadline()
            original, copied = (parent, descriptor), (child, descriptor)
            item = self.descriptors.get(original)
            if item is None:
                continue
            if copied in self.descriptors or copied in copies:
                raise NativeOutputError("native output fork reused a live descriptor")
            copies[copied] = (item, original in item.writers)
        self.charge(64 * len(copies))
        for copied, (item, writing) in copies.items():
            descriptor = copied[1]
            self.descriptors[copied] = item
            description = item.descriptions[(parent, descriptor)]
            description.bindings.add(copied)
            item.descriptions[copied] = description
            if writing:
                item.writers.add(copied)
            self._event(
                "output-inherit", item, parent=parent, pid=child, fd=descriptor,
                description=description.serial,
            )

    def duplicated(self, pid, original, result):
        self._usable()
        item = self.descriptors.get((pid, original))
        if item is None:
            raise NativeOutputError("native output duplicate has no owned descriptor")
        if result == original:
            self._event(
                "output-dup", item, pid=pid, fd=original, result=result,
                description=item.descriptions[(pid, original)].serial,
            )
            return
        copied = (pid, result)
        if copied in self.descriptors:
            self.closed(pid, result, 0)
        self.charge(64)
        self.descriptors[copied] = item
        description = item.descriptions[(pid, original)]
        description.bindings.add(copied)
        item.descriptions[copied] = description
        if (pid, original) in item.writers:
            item.writers.add(copied)
        self._event(
            "output-dup", item, pid=pid, fd=original, result=result, description=description.serial,
        )

    def before_write(self, pid, descriptor, pin):
        return self._claim_writer(pid, descriptor, pin, paired=False)

    def _claim_writer(self, pid, descriptor, pin, *, paired):
        self._usable()
        self.deadline()
        self.charge(64)
        if fcntl.fcntl(pin, fcntl.F_GETFL) & os.O_APPEND:
            raise NativeOutputError("native fixed-offset write does not support append descriptors")
        binding = (pid, descriptor)
        item = self.descriptors.get(binding)
        if (
            item is None or binding not in item.writers or item.retired or item.readers
            or item.pending_writer is not None
            or self._pending_mode(item)
            or self._identity(pin) != item.identity
        ):
            raise NativeOutputError("native output write lost its live object/descriptor")
        if not paired and item.expected is not None:
            raise NativeOutputError("native paired writer cannot switch to payload-less observation")
        item.pending_writer = binding
        return item

    def written(self, pid, descriptor, pin, result):
        self._usable()
        if type(result) is not int or not -4095 <= result <= self.file_limit:
            raise NativeOutputError("native write return is not a bounded kernel byte count/status")
        item = self.descriptors.get((pid, descriptor))
        if (
            item is None or (pid, descriptor) not in item.writers
            or item.pending_writer != (pid, descriptor)
        ):
            raise NativeOutputError("native output completion has no owned writer")
        operation = self.pending.get(pid)
        request = {"request": operation.request} if operation is not None and operation.kind == "write" else {}
        if result < 0:
            if self._identity(pin) != item.identity:
                raise NativeOutputError("failed native output write changed its object")
            item.pending_writer = None
            self._event("output-write-failed", item, pid=pid, fd=descriptor, result=result, **request)
            return
        identity = self._identity(pin)
        if result == 0 and identity != item.identity:
            raise NativeOutputError("zero-byte native output write changed its object")
        if identity[:2] != item.identity[:2] or item.readers or item.retired:
            raise NativeOutputError("native output write completion changed its object/lifetime")
        if identity[2] != item.identity[2] or identity[6] != item.identity[6]:
            raise NativeOutputError("native output write absorbed an unrelated mode or link change")
        if identity != item.identity:
            item.revision += 1
            item.identity = identity
            item.sha256 = None
        item.pending_writer = None
        self._event("output-write", item, pid=pid, fd=descriptor, result=result, identity=list(identity), **request)

    @staticmethod
    def _status_result(result):
        if type(result) is not int or not -4095 <= result <= 0:
            raise NativeOutputError("native status return must be integer zero or a kernel error")

    def closed(self, pid, descriptor, result, *, event_kind="output-close", generation=None):
        self._usable()
        self._status_result(result)
        if result < 0:
            item = self.descriptors.get((pid, descriptor))
            if item is None or result == -errno.EBADF:
                raise NativeOutputError("failed native close contradicts its live owned descriptor")
            if result not in {-errno.EINTR, -errno.EIO, -errno.ENOSPC, -errno.EDQUOT}:
                raise NativeOutputError("native close has an unsupported Linux error outcome")
        binding = (pid, descriptor)
        item = self.descriptors.get(binding)
        if item is not None:
            self._close_available(item, binding)
        item = self.descriptors.pop(binding, None)
        if item is not None:
            description = item.descriptions.pop(binding)
            description.bindings.remove(binding)
            if not description.bindings:
                mode, description.lock = description.lock, 0
                if mode:
                    self._event(
                        "output-lock-release", item, pid=pid, fd=descriptor,
                        description=description.serial, mode=mode,
                    )
            writer = binding in item.writers
            item.writers.discard(binding)
            if writer and not item.writers:
                self._settle(item)
            if result < 0:
                self._event(
                    "output-close-failed", item, pid=pid, fd=descriptor,
                    description=description.serial, result=result,
                )
            if result >= 0:
                self._event(
                    event_kind, item, pid=pid, fd=descriptor, description=description.serial,
                    **({"generation": generation} if generation is not None else {}),
                )

    def retire_process(self, pid):
        self._usable()
        for process, descriptor in tuple(self.descriptors):
            if process == pid:
                self.closed(pid, descriptor, 0)

    def capture(self, *, owner, path, descriptor, observed=True):
        self._usable()
        if type(observed) is not bool:
            raise NativeOutputError("native source capture has an invalid observation mode")
        item = self.objects.get(path)
        if item is None or item.owner != owner or item.retired or item.writers:
            raise NativeOutputError("native generated source has no settled owned version")
        identity = self._identity(descriptor)
        if identity != item.identity:
            raise NativeOutputError("native generated source differs from its current object")
        pin = os.dup(descriptor)
        try:
            self.charge(identity[3] + 256)
            data = bytearray()
            while len(data) < identity[3]:
                self.deadline()
                block = os.pread(pin, min(65536, identity[3] - len(data)), len(data))
                if not block:
                    raise NativeOutputError("native generated source truncated during capture")
                data.extend(block)
            if self._identity(pin) != identity:
                raise NativeOutputError("native generated source changed during capture")
            if item.sha256 is None or hashlib.sha256(data).hexdigest() != item.sha256:
                raise NativeOutputError("native generated source differs from its settled content")
            source = SourcePin(
                pin, item, identity, bytes(data), stat.S_IMODE(identity[2]),
                hashlib.sha256(data).hexdigest(), item.revision, observed=observed,
            )
            item.readers.add(pin)
            self.pins[pin] = source
            if observed:
                self._event("output-source", item, identity=list(identity), sha256=source.sha256)
            return source
        except BaseException:
            item.readers.discard(pin)
            self.pins.pop(pin, None)
            os.close(pin)
            raise

    def replaced(self, *, owner, source, destination, source_pin, retired_pin, result):
        """Record a stopped successful rename and its exact replaced inode."""
        self._usable()
        self._status_result(result)
        item = self.objects.get(source)
        old = self.objects.get(destination)
        if (
            item is None or item.owner != owner or item.retired or item.writers
            or item.readers or source == destination
            or old is not None and (old.retired or old.writers)
            or (old is None) != (retired_pin is None)
        ):
            raise NativeOutputError("native output replacement lacks exact owned operands")
        moved = self._identity(source_pin)
        retired = self._identity(retired_pin) if retired_pin is not None else None
        if result < 0:
            if moved != item.identity or old is not None and retired != old.identity:
                raise NativeOutputError("failed native replacement changed its objects")
            self._event("output-replace-failed", item, destination=destination, result=result)
            return
        if (
            moved[:5] != item.identity[:5] or moved[6] != item.identity[6]
            or old is not None and (
                retired[:5] != old.identity[:5] or retired[6] != old.identity[6] - 1
            )
        ):
            raise NativeOutputError("native replacement changed object content or mode")
        self._verify_settled(item, moved)
        if old is not None:
            self._verify_settled(old, retired)
            self._verify_readers(old)
            old.identity = retired
            old.path = None
            old.retired = True
            self._event(
                "output-retire", old, operation_owner=owner,
                identity=list(retired), destination=destination,
            )
        del self.objects[source]
        item.path = destination
        item.identity = moved
        self.objects[destination] = item
        self._event("output-replace", item, source=source, identity=list(moved))
        self._event("output-settled", item, identity=list(moved), sha256=item.sha256)

    def removed(self, *, owner, path, pin, result):
        self._usable()
        self._status_result(result)
        item = self.objects.get(path)
        if item is None or item.owner != owner or item.retired or item.writers:
            raise NativeOutputError("native output removal lacks its settled owned object")
        identity = self._identity(pin)
        if result < 0:
            if identity != item.identity:
                raise NativeOutputError("failed native output removal changed its object")
            self._event("output-remove-failed", item, result=result)
            return
        if identity[:5] != item.identity[:5] or identity[6] != item.identity[6] - 1:
            raise NativeOutputError("native output removal changed object content or mode")
        self._verify_settled(item, identity)
        self._verify_readers(item)
        del self.objects[path]
        item.identity = identity
        item.path = None
        item.retired = True
        self._event("output-retire", item, identity=list(identity), destination=path)

    def release(self, source):
        self._usable()
        if (
            source.closed or self.pins.get(source.descriptor) is not source
            or source.descriptor not in source.object.readers
        ):
            raise NativeOutputError("native source pin is foreign or already retired")
        current = self._identity(source.descriptor)
        if current != source.object.identity:
            raise NativeOutputError("native source pin changed outside observed retirement")
        if current[:5] != source.identity[:5] or source.object.revision != source.revision:
            raise NativeOutputError("native source pin bytes or mode changed")
        source.object.readers.remove(source.descriptor)
        del self.pins[source.descriptor]
        source.closed = True
        os.close(source.descriptor)
        if source.observed:
            self._event("output-source-retired", source.object, sha256=source.sha256)

    def finish(self):
        if self.pending or self.descriptors or self.pins or any(
            item.readers or item.writers or item.pending_writer is not None for item in self.versions
        ):
            raise NativeOutputError("native output custody ended with active descriptors/pins")
        if self.incomplete is not None:
            raise NativeOutputError("native output custody is incomplete: " + self.incomplete)
        if any(item.sha256 is None for item in self.versions if item.entries is None):
            raise NativeOutputError("native output custody ended with unsettled content")
        for item in self.versions:
            if item.entries is not None:
                if self._directory_identity(item.descriptor) != item.identity or (
                    not item.retired and self._directory_entries(item.descriptor) != item.entries
                ):
                    raise NativeOutputError("native output custody ended with changed directory state")

    def close(self):
        """Close owned object/source pins, never borrowed tracee FD integers."""
        if self.closed_state:
            return
        if self.pending or self.descriptors or self.pins or any(
            item.readers or item.writers or item.pending_writer is not None for item in self.versions
        ):
            self.incomplete = self.incomplete or "terminal cleanup preceded observed lifecycle returns"
        def close_source(descriptor, source):
            source.closed = True
            source.object.readers.discard(descriptor)
            del self.pins[descriptor]
            os.close(descriptor)

        def close_object(item):
            descriptor, item.descriptor = item.descriptor, -1
            item.expected = None
            for description in item.descriptions.values():
                description.bindings.clear()
                description.lock = 0
                description.pending = None
            item.descriptions.clear()
            os.close(descriptor)

        try:
            finish_cleanup([
                *(lambda operation=operation: self._end(operation, success=False)
                  for operation in tuple(self.pending.values())),
                *(lambda descriptor=descriptor, source=source: close_source(descriptor, source)
                  for descriptor, source in tuple(self.pins.items())),
                *(lambda item=item: close_object(item) for item in self.versions if item.descriptor >= 0),
            ])
        finally:
            self.closed_state = True
