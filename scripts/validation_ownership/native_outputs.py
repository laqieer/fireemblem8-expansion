"""Live native output objects shared by syscall effects and source-read custody."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import stat

if __package__:
    from .budget import MakeProbeError
    from .producer_channel import publication_identity
else:
    from budget import MakeProbeError
    from producer_channel import publication_identity


class NativeOutputError(MakeProbeError):
    pass


@dataclass
class OutputObject:
    owner: int
    serial: int
    path: str | None
    identity: tuple
    revision: int = 0
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

    def _identity(self, descriptor):
        self.deadline()
        info = os.fstat(descriptor)
        self.charge(128)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_mode & 0o7000
            or not 0 <= info.st_size <= self.file_limit or info.st_nlink not in {0, 1}
        ):
            raise NativeOutputError("native output is not a bounded regular object")
        return publication_identity(info)

    def _event(self, kind, item, **fields):
        self.emit(
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

    def opened(self, *, owner, pid, descriptor, pin, path, writing):
        """Bind an actual successful open to its kernel object, not pathname alone."""
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
            item = OutputObject(owner, self.serial, path, identity)
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
        self._event("output-open", item, pid=pid, fd=descriptor, identity=list(identity), writing=writing)
        return item

    def inherited(self, parent, child, descriptors):
        for descriptor in descriptors:
            original, copied = (parent, descriptor), (child, descriptor)
            item = self.descriptors.get(original)
            if item is None:
                continue
            if copied in self.descriptors:
                raise NativeOutputError("native output fork reused a live descriptor")
            self.charge(64)
            self.descriptors[copied] = item
            if original in item.writers:
                item.writers.add(copied)
            self._event("output-inherit", item, parent=parent, pid=child, fd=descriptor)

    def duplicated(self, pid, original, result):
        item = self.descriptors.get((pid, original))
        if item is None:
            raise NativeOutputError("native output duplicate has no owned descriptor")
        copied = (pid, result)
        if copied in self.descriptors:
            raise NativeOutputError("native output duplicate replaced an unretired descriptor")
        self.charge(64)
        self.descriptors[copied] = item
        if (pid, original) in item.writers:
            item.writers.add(copied)
        self._event("output-dup", item, pid=pid, fd=original, result=result)

    def before_write(self, pid, descriptor, pin):
        binding = (pid, descriptor)
        item = self.descriptors.get(binding)
        if (
            item is None or binding not in item.writers or item.retired or item.readers
            or self._identity(pin) != item.identity
        ):
            raise NativeOutputError("native output write lost its live object/descriptor")
        return item

    def written(self, pid, descriptor, pin, result):
        item = self.descriptors.get((pid, descriptor))
        if item is None or (pid, descriptor) not in item.writers:
            raise NativeOutputError("native output completion has no owned writer")
        if result < 0:
            if self._identity(pin) != item.identity:
                raise NativeOutputError("failed native output write changed its object")
            self._event("output-write-failed", item, pid=pid, fd=descriptor, result=result)
            return
        identity = self._identity(pin)
        if identity[:2] != item.identity[:2] or item.readers or item.retired:
            raise NativeOutputError("native output write completion changed its object/lifetime")
        if identity != item.identity:
            item.revision += 1
            item.identity = identity
        self._event("output-write", item, pid=pid, fd=descriptor, result=result, identity=list(identity))

    def closed(self, pid, descriptor, result):
        if result < 0:
            item = self.descriptors.get((pid, descriptor))
            if item is not None:
                self._event("output-close-failed", item, pid=pid, fd=descriptor, result=result)
            return
        binding = (pid, descriptor)
        item = self.descriptors.pop(binding, None)
        if item is not None:
            item.writers.discard(binding)
            self._event("output-close", item, pid=pid, fd=descriptor)

    def retire_process(self, pid):
        for process, descriptor in tuple(self.descriptors):
            if process == pid:
                self.closed(pid, descriptor, 0)

    def capture(self, *, owner, path, descriptor):
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
        if old is not None:
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
        self._verify_readers(item)
        del self.objects[path]
        item.identity = identity
        item.path = None
        item.retired = True
        self._event("output-retire", item, identity=list(identity), destination=path)

    def release(self, source):
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
        if self.descriptors or self.pins or any(item.readers or item.writers for item in self.versions):
            raise NativeOutputError("native output custody ended with active descriptors/pins")

    def close(self):
        """Close only model-owned source pins; tracee descriptors are borrowed."""
        for descriptor, source in tuple(self.pins.items()):
            os.close(descriptor)
            source.closed = True
            source.object.readers.discard(descriptor)
            del self.pins[descriptor]
