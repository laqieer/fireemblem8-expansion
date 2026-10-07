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


class NativeOutputs:
    """Internal state keyed by issued producer; supervisor binds actual jobs."""

    def __init__(self, *, deadline, charge, file_limit, emit):
        self.deadline = deadline
        self.charge = charge
        self.file_limit = file_limit
        self.emit = emit
        self.serial = 0
        self.objects = {}
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
        operands = []
        try:
            for path, descriptor in pins:
                item = self.objects.get(path)
                if descriptor is None:
                    if item is not None:
                        raise NativeOutputError("native operation absence contradicts its owned object")
                    operands.append((path, None, None, None))
                    continue
                identity = self._identity(descriptor)
                if item is None or item.owner != owner or item.retired or identity != item.identity:
                    raise NativeOutputError("native operation entry differs from its owned operand")
                pin = os.dup(descriptor)
                operands.append((path, pin, identity, item))
            for label, descriptor, item in bindings:
                identity = self._identity(descriptor)
                if identity != item.identity:
                    raise NativeOutputError("native operation entry differs from its bound descriptor")
                pin = os.dup(descriptor)
                operands.append((label, pin, identity, item))
            self.charge(256 + 128 * len(operands))
            operation = NativeOperation(owner, pid, kind, source, destination, flags, tuple(operands))
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

    def _end(self, operation):
        del self.pending[operation.pid]
        finish_cleanup([
            lambda pin=pin: os.close(pin)
            for _, pin, _, _ in operation.operands if pin is not None
        ])

    def _failed(self, operation, result):
        if type(result) is not int or not -4095 <= result < 0:
            raise NativeOutputError("native failed operation has no actual kernel error")
        for _, pin, identity, item in operation.operands:
            if pin is not None:
                if self._identity(pin) != identity:
                    raise NativeOutputError("failed native operation changed its entry operand")
                if item.sha256 is not None:
                    self._verify_settled(item, identity)
        self._emit(
            "output-operation-failed", owner=operation.owner, pid=operation.pid,
            operation=operation.kind, source=operation.source,
            destination=operation.destination, result=result,
        )
        self._end(operation)

    def enter_open(self, *, owner, pid, path, flags, pin):
        self._usable()
        if (
            type(flags) is not int or flags < 0 or flags & os.O_APPEND
            or flags & os.O_TMPFILE == os.O_TMPFILE
        ):
            raise NativeOutputError("native output open has unsupported flags")
        item = self.objects.get(path)
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
            writing=bool(operation.flags & (os.O_WRONLY | os.O_RDWR | os.O_TRUNC)),
        )
        self._end(operation)
        return item

    def enter_replace(self, *, owner, pid, source, destination, source_pin, retired_pin):
        self._usable()
        if source == destination:
            raise NativeOutputError("native replacement lacks distinct declared operands")
        for path in (source, destination):
            item = self.objects.get(path)
            if item is not None and (item.writers or path == source and item.readers):
                raise NativeOutputError("native replacement overlaps an active writer/source")
        return self._begin(
            owner, pid, "replace", source, destination,
            pins=((source, source_pin), (destination, retired_pin)),
        )

    def leave_replace(self, operation, result):
        self._operation(operation, "replace")
        self._status_result(result)
        if result < 0:
            self._failed(operation, result)
            return
        if result != 0 or operation.operands[0][1] is None:
            raise NativeOutputError("native replacement success lacks its exact source object")
        self.replaced(
            owner=operation.owner, source=operation.source, destination=operation.destination,
            source_pin=operation.operands[0][1], retired_pin=operation.operands[1][1], result=result,
        )
        self._end(operation)

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
        self.removed(owner=operation.owner, path=operation.source, pin=pin, result=result)
        self._end(operation)

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
        item = self.before_write(pid, descriptor, pin)
        operation = None
        try:
            operation = self._begin(
                item.owner, pid, "write", item.path, pins=((item.path, pin),),
            )
            before = self._bytes(operation.operands[0][1], operation.operands[0][2])
            self.charge(len(data) + len(before))
            operation = NativeOperation(
                operation.owner, pid, operation.kind, operation.source,
                operation.destination, operation.flags, operation.operands,
                descriptor=descriptor, offset=offset, data=data, before=before,
            )
            self.pending[pid] = operation
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
        actual = self._bytes(pin, identity)
        expected = operation.before
        if result > 0:
            offset = operation.offset
            expected = (
                operation.before[:offset]
                + b"\0" * max(0, offset - len(operation.before))
                + operation.data[:result]
                + operation.before[offset + result:]
            )
        if actual != expected:
            raise NativeOutputError("native write return absorbed bytes outside its actual kernel effect")
        self.written(operation.pid, operation.descriptor, pin, result)
        self._end(operation)

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
            bindings.append(("target-fd:" + str(target), old.descriptor, old))
        operation = self._begin(
            item.owner, pid, "dup", bindings[0][0], flags=flags, bindings=bindings,
        )
        operation = NativeOperation(
            operation.owner, pid, operation.kind, operation.source,
            operation.destination, operation.flags, operation.operands, descriptor=descriptor,
            duplicate_kind=kind, target=target, minimum=minimum,
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
        self._event("output-settled", item, identity=list(identity), sha256=digest)
        item.sha256 = digest

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
        elif item.retired or item.owner != owner or identity != item.identity:
            raise NativeOutputError("native output open differs from its live owned object")
        if writing and item.readers:
            raise NativeOutputError("native output writer overlaps a pinned source read")
        self.charge(64)
        self.descriptors[binding] = item
        if writing:
            item.writers.add(binding)
        elif item.sha256 is None:
            self._settle(item)
        self._event("output-open", item, pid=pid, fd=descriptor, identity=list(identity), writing=writing)
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
            if writing:
                item.writers.add(copied)
            self._event("output-inherit", item, parent=parent, pid=child, fd=descriptor)

    def duplicated(self, pid, original, result):
        self._usable()
        item = self.descriptors.get((pid, original))
        if item is None:
            raise NativeOutputError("native output duplicate has no owned descriptor")
        if result == original:
            self._event("output-dup", item, pid=pid, fd=original, result=result)
            return
        copied = (pid, result)
        if copied in self.descriptors:
            self.closed(pid, result, 0)
        self.charge(64)
        self.descriptors[copied] = item
        if (pid, original) in item.writers:
            item.writers.add(copied)
        self._event("output-dup", item, pid=pid, fd=original, result=result)

    def before_write(self, pid, descriptor, pin):
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
            or self._identity(pin) != item.identity
        ):
            raise NativeOutputError("native output write lost its live object/descriptor")
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
        if result < 0:
            if self._identity(pin) != item.identity:
                raise NativeOutputError("failed native output write changed its object")
            item.pending_writer = None
            self._event("output-write-failed", item, pid=pid, fd=descriptor, result=result)
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
        self._event("output-write", item, pid=pid, fd=descriptor, result=result, identity=list(identity))

    @staticmethod
    def _status_result(result):
        if type(result) is not int or not -4095 <= result <= 0:
            raise NativeOutputError("native status return must be integer zero or a kernel error")

    def closed(self, pid, descriptor, result):
        self._usable()
        self._status_result(result)
        if result < 0:
            item = self.descriptors.get((pid, descriptor))
            if item is None or result == -errno.EBADF:
                raise NativeOutputError("failed native close contradicts its live owned descriptor")
            if result not in {-errno.EINTR, -errno.EIO, -errno.ENOSPC, -errno.EDQUOT}:
                raise NativeOutputError("native close has an unsupported Linux error outcome")
            self._event("output-close-failed", item, pid=pid, fd=descriptor, result=result)
        binding = (pid, descriptor)
        item = self.descriptors.get(binding)
        if item is not None and item.pending_writer == binding:
            raise NativeOutputError("native output close overlaps an unfinished write")
        item = self.descriptors.pop(binding, None)
        if item is not None:
            writer = binding in item.writers
            item.writers.discard(binding)
            if writer and not item.writers:
                self._settle(item)
            if result >= 0:
                self._event("output-close", item, pid=pid, fd=descriptor)

    def retire_process(self, pid):
        self._usable()
        for process, descriptor in tuple(self.descriptors):
            if process == pid:
                self.closed(pid, descriptor, 0)

    def capture(self, *, owner, path, descriptor):
        self._usable()
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
                hashlib.sha256(data).hexdigest(), item.revision,
            )
            item.readers.add(pin)
            self.pins[pin] = source
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
            or old is not None and (old.owner != owner or old.retired or old.writers)
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
            self._event("output-retire", old, identity=list(retired), destination=destination)
        del self.objects[source]
        item.path = destination
        item.identity = moved
        self.objects[destination] = item
        self._event("output-replace", item, source=source, identity=list(moved))

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
        self._event("output-source-retired", source.object, sha256=source.sha256)

    def finish(self):
        if self.pending or self.descriptors or self.pins or any(
            item.readers or item.writers or item.pending_writer is not None for item in self.versions
        ):
            raise NativeOutputError("native output custody ended with active descriptors/pins")
        if self.incomplete is not None:
            raise NativeOutputError("native output custody is incomplete: " + self.incomplete)
        if any(item.sha256 is None for item in self.versions):
            raise NativeOutputError("native output custody ended with unsettled content")

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
            os.close(descriptor)

        try:
            finish_cleanup([
                *(lambda operation=operation: self._end(operation)
                  for operation in tuple(self.pending.values())),
                *(lambda descriptor=descriptor, source=source: close_source(descriptor, source)
                  for descriptor, source in tuple(self.pins.items())),
                *(lambda item=item: close_object(item) for item in self.versions if item.descriptor >= 0),
            ])
        finally:
            self.closed_state = True
