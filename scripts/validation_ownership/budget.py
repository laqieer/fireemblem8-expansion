"""One monotonic, byte-accounted lifetime for an entire ownership probe."""

from __future__ import annotations

import math
import os
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, field, fields
from pathlib import Path


class MakeProbeError(RuntimeError):
    """Authority could not be measured safely and exactly."""


MAX_PROBE_SECONDS = 3600


@dataclass(frozen=True)
class Limits:
    seconds: float = MAX_PROBE_SECONDS
    runs: int = 4096
    states: int = 4096
    processes: int = 32
    descendants: int = 16384
    pending: int = 32
    total_bytes: int = 768 * 1024 * 1024
    snapshot_bytes: int = 384 * 1024 * 1024
    output_bytes: int = 64 * 1024 * 1024
    event_bytes: int = 16 * 1024 * 1024
    mapping_bytes: int = 32 * 1024 * 1024
    cache_bytes: int = 32 * 1024 * 1024
    pending_bytes: int = 1024 * 1024
    control_bytes: int = 32 * 1024 * 1024
    sandbox_bytes: int = 64 * 1024 * 1024
    created_files: int = 4096
    entries: int = 32768
    file_bytes: int = 16 * 1024 * 1024
    process_output_bytes: int = 1024 * 1024
    address_space_bytes: int = 512 * 1024 * 1024
    syscalls: int = 2_000_000

    def __post_init__(self):
        for definition in fields(self):
            name, value = definition.name, getattr(self, definition.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MakeProbeError(f"invalid {name} budget")
            if value <= 0 or value > definition.default or not math.isfinite(value):
                raise MakeProbeError(f"invalid {name} budget")
            if name != "seconds" and not isinstance(value, int):
                raise MakeProbeError(f"nonintegral {name} budget")


@dataclass
class ProbeBudget:
    limits: Limits = field(default_factory=Limits)
    started: float = field(default_factory=time.monotonic, init=False)
    bytes: dict[str, int] = field(default_factory=dict, init=False)
    runs: int = field(default=0, init=False)
    states: int = field(default=0, init=False)
    children: set[subprocess.Popen] = field(default_factory=set, init=False)
    failed: bool = field(default=False, init=False)

    @property
    def deadline(self) -> float:
        return self.started + self.limits.seconds

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if self.failed or remaining <= 0:
            self.failed = True
            raise MakeProbeError("aggregate probe deadline/budget exhausted")
        return remaining

    def reject(self, reason: str):
        self.failed = True
        raise MakeProbeError(reason)

    def charge(self, category: str, size: int):
        self.remaining()
        cap = getattr(self.limits, f"{category}_bytes", None)
        if cap is None or isinstance(size, bool) or not isinstance(size, int) or size < 0:
            self.reject("invalid byte-accounting request")
        used = self.bytes.get(category, 0) + size
        if used > cap or sum(self.bytes.values()) + size > self.limits.total_bytes:
            self.reject(f"aggregate {category} byte budget exhausted")
        self.bytes[category] = used

    def plan(self, states: int, pending: int = 1):
        self.remaining()
        if (
            isinstance(states, bool)
            or not isinstance(states, int)
            or states < 1
            or self.states + states > self.limits.states
            or pending < 1
            or pending > self.limits.pending
        ):
            self.reject("aggregate variant/pending-state budget exhausted before launch")
        self.states += states

    def read_bytes(self, path: Path, category: str) -> bytes:
        self.remaining()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > self.limits.file_bytes:
                self.reject(f"{category} file is nonregular or exceeds byte bound")
            self.charge(category, before.st_size)
            data = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
            if len(data) != before.st_size or (
                before.st_size, before.st_mtime_ns, before.st_ctime_ns
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                self.reject(f"{category} file changed during bounded read")
            return data

    @staticmethod
    def _terminate(child: subprocess.Popen):
        # Every child is launched in its own session; never address another group.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()

    def close(self):
        for child in tuple(self.children):
            self._terminate(child)
            self.children.discard(child)

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path | None = None,
        output_limit: int | None = None,
        input_data: bytes | None = None,
        category: str = "output",
    ) -> subprocess.CompletedProcess[bytes]:
        self.remaining()
        self.runs += 1
        if self.runs > self.limits.runs:
            self.reject("aggregate process-launch budget exhausted")
        self.charge("pending", sum(len(os.fsencode(arg)) + 1 for arg in argv))
        limit = self.limits.process_output_bytes if output_limit is None else output_limit
        if limit <= 0 or limit > getattr(self.limits, f"{category}_bytes", 0):
            self.reject("invalid process stream budget")
        if input_data is not None:
            self.charge("pending", len(input_data))
        child = subprocess.Popen(
            argv, cwd=cwd, env=env,
            stdin=subprocess.DEVNULL if input_data is None else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, start_new_session=True,
        )
        self.children.add(child)
        output = [bytearray(), bytearray()]
        try:
            with selectors.DefaultSelector() as selector:
                for index, stream in enumerate((child.stdout, child.stderr)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, index)
                supplied = 0
                if input_data is not None:
                    os.set_blocking(child.stdin.fileno(), False)
                    selector.register(child.stdin, selectors.EVENT_WRITE, 2)
                count = 0
                while selector.get_map():
                    for key, _ in selector.select(min(self.remaining(), 0.05)):
                        if key.data == 2:
                            if supplied < len(input_data):
                                supplied += os.write(key.fd, input_data[supplied:supplied + 65536])
                            if supplied == len(input_data):
                                selector.unregister(key.fileobj)
                                child.stdin.close()
                            continue
                        chunk = os.read(key.fd, min(65536, limit - count + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        count += len(chunk)
                        self.charge(category, len(chunk))
                        if count > limit:
                            self.reject("process output exceeds streaming byte bound")
                        output[key.data].extend(chunk)
                child.wait(timeout=self.remaining())
                self.remaining()
            return subprocess.CompletedProcess(
                argv, child.returncode, bytes(output[0]), bytes(output[1]),
            )
        except BaseException:
            self.failed = True
            raise
        finally:
            self._terminate(child)
            self.children.discard(child)
            child.stdout.close()
            child.stderr.close()
            if child.stdin is not None:
                child.stdin.close()


def text(data: bytes, boundary: str, encoding: str = "utf-8") -> str:
    try:
        return data.decode(encoding, errors="strict")
    except UnicodeDecodeError as error:
        raise MakeProbeError(f"{boundary} is not strict {encoding}") from error
