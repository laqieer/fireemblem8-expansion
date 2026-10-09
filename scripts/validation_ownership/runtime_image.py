"""Complete immutable platform images with bounded streaming workspace."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import sys

from .budget import MakeProbeError, ProbeBudget
from .lifecycle import finish_cleanup


BLOCK_BYTES = 65536
IMAGE_SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL


@contextmanager
def _materialization_stream(destination):
    stream = destination.open("wb", buffering=0)
    try:
        yield stream
    except BaseException as error:
        finish_cleanup([stream.close, destination.unlink], primary=error)
        raise
    try:
        stream.close()
    except BaseException as error:
        finish_cleanup([stream.close, destination.unlink], primary=error)
        raise


def _write_complete(stream, part, budget=None):
    written = 0
    while written < len(part):
        if budget is not None:
            budget.remaining()
        amount = stream.write(part[written:])
        if amount is None or amount <= 0 or amount > len(part) - written:
            raise MakeProbeError("platform runtime materialization did not progress")
        written += amount
    return written


def _identity(info):
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _backing_identity(info):
    # Failed truncate attempts can change timestamps despite immutable sealed content.
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_size


class RuntimeImage:
    """A session-owned sealed body, never a mutable generated/source file."""

    def __init__(self, source: Path, budget: ProbeBudget):
        self.budget = budget
        self.descriptor = -1
        self.size = 0
        self._digest = ""
        self._identity = None
        budget.charge("control", sys.getsizeof(self) + sys.getsizeof(self.__dict__))
        descriptor = -1
        stream = None

        def close_source():
            if stream is not None:
                stream.close()
            elif descriptor >= 0:
                os.close(descriptor)

        try:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            stream = os.fdopen(descriptor, "rb", buffering=0)
            with stream:
                before = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(before.st_mode) or before.st_size <= 0
                    or before.st_uid != 0 or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH | 0o7000)
                ):
                    raise MakeProbeError("platform runtime image has untrusted backing")
                budget.charge("snapshot", before.st_size)
                self.descriptor = os.memfd_create(
                    "ownership-runtime-image", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
                )
                self.size = before.st_size
                digest = hashlib.sha256()
                work = self._buffer()
                offset = 0
                while offset < self.size:
                    budget.remaining()
                    budget.charge("control", 128)
                    count = stream.readinto(work[:min(len(work), self.size - offset)])
                    if count is None or count <= 0:
                        raise MakeProbeError("platform runtime image ended during complete capture")
                    part = work[:count]
                    digest.update(part)
                    written = 0
                    while written < count:
                        budget.remaining()
                        amount = os.write(self.descriptor, part[written:])
                        if amount <= 0:
                            raise MakeProbeError("platform runtime backing write did not progress")
                        written += amount
                    offset += count
                if (
                    stream.readinto(work[:1]) != 0
                    or _identity(before) != _identity(os.fstat(stream.fileno()))
                    or _identity(before) != _identity(source.lstat())
                ):
                    raise MakeProbeError("platform runtime image changed during complete capture")
                self._digest = digest.hexdigest()
                budget.charge("control", sys.getsizeof(self._digest))
                fcntl.fcntl(self.descriptor, fcntl.F_ADD_SEALS, IMAGE_SEALS)
                self._identity = _backing_identity(os.fstat(self.descriptor))
                budget.charge("control", sys.getsizeof(self._identity))
                self.require_sealed()
        except BaseException as error:
            finish_cleanup([close_source, self.close], primary=error)
            raise

    def _buffer(self):
        self.budget.charge("control", BLOCK_BYTES + sys.getsizeof(bytearray()) + 256)
        return memoryview(bytearray(BLOCK_BYTES))

    def require_sealed(self):
        self.budget.remaining()
        self.budget.charge("control", 128)
        if (
            self.descriptor < 0
            or fcntl.fcntl(self.descriptor, fcntl.F_GET_SEALS) != IMAGE_SEALS
            or _backing_identity(os.fstat(self.descriptor)) != self._identity
        ):
            raise MakeProbeError("platform runtime lost its owned complete sealed body")

    def __len__(self):
        return self.size

    def __getitem__(self, key):
        self.require_sealed()
        if not isinstance(key, slice) or key.step not in (None, 1):
            raise MakeProbeError("platform runtime requires a bounded ELF slice")
        first, last, _ = key.indices(self.size)
        size = max(0, last - first)
        if size > BLOCK_BYTES:
            raise MakeProbeError("platform runtime ELF slice exceeds its workspace bound")
        self.budget.charge("control", size + sys.getsizeof(b""))
        result = os.pread(self.descriptor, size, first)
        if len(result) != size:
            raise MakeProbeError("platform runtime ELF slice ended unexpectedly")
        return result

    def digest(self):
        self.require_sealed()
        return self._digest

    def materialize(self, destination: Path):
        self.require_sealed()
        self.budget.charge("snapshot", self.size)
        work = self._buffer()
        digest = hashlib.sha256()
        owner = _materialization_stream(destination)
        self.budget.charge(
            "control", sys.getsizeof(owner) + sys.getsizeof(owner.__dict__) + sys.getsizeof(owner.gen),
        )
        with owner as stream:
            offset = 0
            while offset < self.size:
                self.budget.remaining()
                self.budget.charge("control", 256)
                count = os.preadv(
                    self.descriptor, [work[:min(len(work), self.size - offset)]], offset,
                )
                if count <= 0:
                    raise MakeProbeError("platform runtime ended during materialization")
                part = work[:count]
                digest.update(part)
                _write_complete(stream, part, self.budget)
                offset += count
            self.require_sealed()
            if digest.hexdigest() != self._digest:
                raise MakeProbeError("platform runtime materialization differs from its complete captured body")
        return self.size

    def __eq__(self, other):
        if isinstance(other, RuntimeImage):
            return len(self) == len(other) and self.digest() == other.digest()
        if isinstance(other, bytes):
            self.budget.charge("total", len(other))
            return len(self) == len(other) and self.digest() == hashlib.sha256(other).hexdigest()
        return NotImplemented

    def close(self):
        descriptor, self.descriptor = self.descriptor, -1
        if descriptor >= 0:
            os.close(descriptor)


def image_digest(body: bytes | RuntimeImage):
    return body.digest() if isinstance(body, RuntimeImage) else hashlib.sha256(body).hexdigest()


def materialize_image(destination: Path, body: bytes | RuntimeImage):
    if isinstance(body, RuntimeImage):
        return body.materialize(destination)
    with _materialization_stream(destination) as stream:
        return _write_complete(stream, memoryview(body))


def close_images(images):
    finish_cleanup([
        body.close for body in images.values() if isinstance(body, RuntimeImage)
    ] + [images.clear])
