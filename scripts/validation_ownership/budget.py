"""One monotonic, byte-accounted lifetime for an entire ownership probe."""

from __future__ import annotations

import math
import os
import secrets
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

if __package__:
    from . import lifecycle as _lifecycle
    from .lifecycle import finish_cleanup, ordinary_executable
    from .producer_channel import ChannelError
else:
    import lifecycle as _lifecycle
    from lifecycle import finish_cleanup, ordinary_executable
    from producer_channel import ChannelError


class MakeProbeError(RuntimeError):
    """Authority could not be measured safely and exactly."""


MAX_PROBE_SECONDS = 3600
MAX_PENDING_RECORD_BYTES = 1024 * 1024
MAX_PLANNED_STATE_BYTES = 1024 * 1024
NAMESPACE_LAUNCHER = (
    "/usr/bin/unshare", "--mount", "--net", "--pid", "--fork",
    "--kill-child", "--propagation", "private",
)

_OUTCOME_KEY = object()
_OUTCOME_MAX = 256 * 1024
_OUTCOME_ENTRIES = 4 + 3 * _lifecycle._ERROR_ENTRIES


def _outcome_allocation(output_limit):
    if not _lifecycle._representation_supported():
        raise MakeProbeError("unsupported outcome representation")
    frames, errors = 4 * _lifecycle._FRAME_BYTES, 3 * _lifecycle._ERROR_BYTES
    reserved = 2 * output_limit + frames + errors
    # Fixed cells/buffer headers and the one read/conversion scratch fit inside
    # the frame/report subdivisions while mutable and immutable captures overlap.
    capture_peak = 2 * output_limit + 2 * _lifecycle._FRAME_BYTES + errors
    # Parse only after dropping mutable captures. Include the private W record,
    # all four retained slots, all three reports, and encode/decode/container
    # workspace (not merely the JSON wire length).
    decode_peak = (
        output_limit + frames + errors + 2 * _lifecycle._FRAME_BYTES
        + _lifecycle._JSON_WORK_BYTES
    )
    if max(capture_peak, decode_peak) > reserved:
        raise MakeProbeError("outcome representation cannot fit the frozen cache reservation")
    return reserved


def _capture_bytes(buffer, size):
    return bytes(memoryview(buffer)[:size])


@dataclass(frozen=True, slots=True)
class _RunSnapshot:
    outer_returncode: int | None
    outer_error: tuple | None
    outer_stage: str | None
    stdout: bytes | None = field(repr=False)
    stderr: bytes | None = field(repr=False)
    capture_complete: bool
    capture_available: bool
    frames: tuple
    frame_error: str | None
    custody_complete: bool
    cleanup: _lifecycle._CleanupValue
    api_returned: bool
    reaped: bool

    @property
    def qualified(self):
        # Neither the exact prefix nor a token authenticates a future worker.
        return False


class _RunOutcome:
    __slots__ = (
        "_budget", "_deadline", "_ordinal", "_token", "_limit", "_spent", "_released",
        "_binding", "_buffers", "_sizes", "_streams", "_freeze_attempted",
        "_capture_complete", "_capture_available", "_status", "_error", "_stage",
        "_frames", "_frame_error", "_parsed", "_cleanup", "_terminal", "_returned",
        "_reaped", "_snapshot", "_revision",
    )

    def __init__(self, key, budget, output_limit):
        if key is not _OUTCOME_KEY:
            raise TypeError("reserve outcome ownership through its budget")
        self._budget, self._deadline, self._ordinal = budget, budget.deadline, budget.runs + 1
        self._token, self._limit = secrets.token_hex(16), output_limit
        self._spent = self._released = self._freeze_attempted = False
        self._binding = None
        self._buffers = [
            bytearray(4 * _lifecycle._FRAME_BYTES),
            bytearray(output_limit - 4 * _lifecycle._FRAME_BYTES),
        ]
        self._sizes = [0, 0]
        self._streams = None
        self._capture_complete = self._capture_available = False
        self._status = self._error = self._stage = None
        self._frames = [None] * 4
        self._frame_error = None
        self._parsed = self._terminal = self._returned = self._reaped = False
        self._cleanup = _lifecycle._CleanupReport("C", self._deadline)
        self._snapshot = None
        self._revision = -1

    def _live(self):
        if self._released:
            raise MakeProbeError("outcome owner was released")

    def _bind_fixture(self, binding):
        self._live()
        if (
            self._spent or self._binding is not None or type(binding) is not _lifecycle._FixtureBinding
            or binding.deadline != self._deadline
        ):
            raise MakeProbeError("invalid private outcome fixture binding")
        self._binding = binding

    def _observe(self, status, complete):
        if not _lifecycle._status(status):
            raise MakeProbeError("outcome normal wait requires a signed integer status")
        self._status = status
        self._capture_complete = complete

    def _exception(self, stage, error):
        if self._error is None:
            self._stage, self._error = stage, _lifecycle._error_value(error)

    def _append(self, index, chunk):
        stop = self._sizes[index] + len(chunk)
        if stop > len(self._buffers[index]):
            self._budget.reject("outcome stream exceeds its fixed subdivision")
        self._buffers[index][self._sizes[index]:stop] = chunk
        self._sizes[index] = stop

    def _freeze(self):
        if self._freeze_attempted:
            return
        self._freeze_attempted = True
        try:
            stdout = _capture_bytes(self._buffers[0], self._sizes[0])
            stderr = _capture_bytes(self._buffers[1], self._sizes[1])
            self._streams = (stdout, stderr)
            self._capture_available = True
        finally:
            self._buffers = None

    def _parse(self):
        if self._parsed:
            return
        self._parsed = True
        if not self._capture_available:
            self._frame_error = "capture-unavailable"
            return
        data, offset, count = self._streams[0], 0, 0
        try:
            while offset < len(data):
                if count == 4 or len(data) - offset < 4:
                    raise ValueError("outcome header/count")
                size = int.from_bytes(memoryview(data)[offset:offset + 4], "little")
                if size > _lifecycle._FRAME_BYTES - 4 or size == 0 or size + 4 > len(data) - offset:
                    raise ValueError("outcome partial/oversize frame")
                body = data[offset + 4:offset + 4 + size]
                frame = _lifecycle._decode_frame(
                    body, self._token, self._binding,
                    (self._frames[0], self._frames[2]),
                )
                slot = (0 if frame.role == "R" else 2) + (frame.phase == "after")
                if self._frames[slot] is not None:
                    raise ValueError("outcome duplicate slot")
                self._frames[slot] = frame
                body = frame = None
                count += 1
                offset += size + 4
            if count != 4:
                self._frame_error = "missing-frame"
        except (ValueError, TypeError, OverflowError, MemoryError, RecursionError) as error:
            self._frame_error = "invalid-frame"
            _lifecycle._forget_error(error)
        if not self._capture_complete and self._frame_error is None:
            self._frame_error = "capture-incomplete"

    def snapshot(self):
        self._live()
        if not self._terminal:
            raise MakeProbeError("outcome snapshot requires a finished capture attempt")
        self._parse()
        if self._snapshot is None or self._revision != self._cleanup.revision:
            # No owner-held history. Caller aliases must be dropped before later
            # cleanup/release; invalidation cannot delete external references.
            self._snapshot = None
            self._revision = self._cleanup.revision
            streams = (None, None) if self._streams is None else self._streams
            self._snapshot = _RunSnapshot(
                self._status, self._error, self._stage, *streams,
                self._capture_complete, self._capture_available, tuple(self._frames),
                self._frame_error, self._frame_error is None, self._cleanup.value(),
                self._returned, self._reaped,
            )
        return self._snapshot

    def release(self):
        self._live()
        if self._spent and not self._terminal:
            raise MakeProbeError("cannot release a running outcome")
        self._released = True
        budget, self._budget = self._budget, None
        self._buffers = self._streams = self._frames = self._snapshot = self._binding = self._cleanup = None
        self._error = self._sizes = None
        if budget._outcome is self:
            budget._outcome = None


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
    observations: int | None = None

    @property
    def observation_count(self) -> int:
        return self.entries if self.observations is None else self.observations

    def __post_init__(self):
        definitions = fields(self)
        for definition in definitions:
            name, value = definition.name, getattr(self, definition.name)
            maximum = definition.default
            if name == "observations":
                if value is None:
                    continue
                if maximum is None:
                    maximum = next(item.default for item in definitions if item.name == "entries")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MakeProbeError(f"invalid {name} budget")
            if value <= 0 or value > maximum or not math.isfinite(value):
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
    planned_state_bytes: int = field(default=0, init=False)
    children: dict[subprocess.Popen, bool] = field(default_factory=dict, init=False)
    failed: bool = field(default=False, init=False)
    closed: bool = field(default=False, init=False)
    session_started: bool = field(default=False, init=False)
    producer_waiters: list = field(default_factory=list, init=False)
    _outcome: _RunOutcome | None = field(default=None, init=False, repr=False)
    _outcome_entries: int = field(default=0, init=False, repr=False)

    @property
    def deadline(self) -> float:
        return self.started + self.limits.seconds

    def cumulative_limit(self, name: str) -> int | float | None:
        """Read a work quota without changing the original unit-admission limits."""
        if name == "observations":
            return self.limits.observation_count
        return getattr(self.limits, name, None)

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if self.failed or self.closed or remaining <= 0:
            self.failed = True
            raise MakeProbeError("aggregate probe deadline/budget exhausted")
        for channel in self.producer_waiters:
            try:
                channel.ensure_idle()
            except ChannelError as error:
                self.reject(str(error))
        return remaining

    def reject(self, reason: str):
        self.failed = True
        raise MakeProbeError(reason)

    def charge(self, category: str, size: int):
        self.remaining()
        cap = self.cumulative_limit(f"{category}_bytes")
        if cap is None or isinstance(size, bool) or not isinstance(size, int) or size < 0:
            self.reject("invalid byte-accounting request")
        if category == "pending" and size > MAX_PENDING_RECORD_BYTES:
            self.reject("pending record exceeds 1048576-byte admission limit")
        used = self.bytes.get(category, 0) + size
        if used > cap or sum(self.bytes.values()) + size > self.cumulative_limit("total_bytes"):
            self.reject(f"aggregate {category} byte budget exhausted")
        self.bytes[category] = used

    def admit_planned_state(self, size: int):
        """Admit serialized plan bytes once without counting an attempted state."""
        self.remaining()
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            self.reject("invalid planned-state byte-admission request")
        if self.planned_state_bytes + size > MAX_PLANNED_STATE_BYTES:
            self.reject("aggregate planned-state admission exceeds 1048576-byte limit")
        self.charge("pending", size)
        self.planned_state_bytes += size

    def plan(self, states: int, pending: int = 1):
        self.remaining()
        if (
            isinstance(states, bool)
            or not isinstance(states, int)
            or states < 1
            or self.states + states > self.cumulative_limit("states")
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
    def _terminate(child: subprocess.Popen, privileged=False, *, report=None):
        if report is not None:
            _lifecycle._report(report)

            def wait():
                try:
                    child.wait(timeout=max(0, report.deadline - time.monotonic()))
                    if not _lifecycle._status(child.returncode):
                        raise MakeProbeError("outcome cleanup did not confirm a reap")
                except BaseException:
                    if not _lifecycle._status(child.returncode):
                        report.unsure("wait")
                    raise

            return finish_cleanup([
                ("lifetime", lambda: _lifecycle._close_stream(child, "stdin", report)),
                ("wait", wait),
            ], report=report)
        # Every launch has a sole-reaper watchdog. This outer Popen may already
        # be reaped: its numeric PID is never authority for a signal.
        if child.stdin is not None:
            child.stdin.close()
        child.wait()

    def close(self, *, report=None):
        if report is not None:
            _lifecycle._report(report)
            if (
                self._outcome is None or self._outcome._cleanup is not report
                or report.role != "C" or report.deadline != self.deadline
            ):
                self.reject("foreign outcome cleanup report")
            self.closed = True

            def stop(child, privileged):
                try:
                    self._terminate(child, privileged, report=report)
                finally:
                    if _lifecycle._status(child.returncode):
                        self.children.pop(child, None)

            actions = []
            for child, privileged in tuple(self.children.items()):
                actions.append(("leader", lambda child=child, privileged=privileged: stop(child, privileged)))
                for name in ("stdout", "stderr"):
                    actions.append((name, lambda child=child, name=name: _lifecycle._close_stream(child, name, report)))
            try:
                finish_cleanup(actions, report=report)
            except BaseException:
                self.failed = True
                raise
            return
        self.closed = True
        def stop(child, privileged):
            self._terminate(child, privileged)
            del self.children[child]
        finish_cleanup([
            lambda child=child, privileged=privileged: stop(child, privileged)
            for child, privileged in tuple(self.children.items())
        ])

    def reserve_outcome(self, *, output_limit=262144):
        self.remaining()
        if (
            self.session_started or self.producer_waiters or self._outcome is not None
            or self.children or self.runs >= self.cumulative_limit("runs")
            or type(output_limit) is not int or not 4 * _lifecycle._FRAME_BYTES < output_limit <= _OUTCOME_MAX
            or output_limit > self.limits.process_output_bytes
            or self.bytes.get("output", 0) + output_limit > self.cumulative_limit("output_bytes")
            or self._outcome_entries + _OUTCOME_ENTRIES > self.limits.entries
        ):
            self.reject("outcome requires one unspent bare-budget reservation")
        try:
            size = _outcome_allocation(output_limit)
        except MakeProbeError:
            self.failed = True
            raise
        self.plan(1, pending=1)
        self.charge("pending", _lifecycle._FRAME_BYTES)
        self.charge("cache", size)
        self._outcome_entries += _OUTCOME_ENTRIES
        try:
            self._outcome = _RunOutcome(_OUTCOME_KEY, self, output_limit)
        except BaseException:
            self.failed = True
            raise
        return self._outcome

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path | None = None,
        output_limit: int | None = None,
        input_data: bytes | None = None,
        category: str = "output",
        privileged: bool = False,
        producer_channel=None,
        producer_handler=None,
        outcome=None,
    ) -> subprocess.CompletedProcess[bytes]:
        if outcome is not None:
            return self._run_outcome(
                argv, env=env, cwd=cwd, output_limit=output_limit, input_data=input_data,
                category=category, privileged=privileged, producer_channel=producer_channel,
                producer_handler=producer_handler, outcome=outcome,
            )
        self.remaining()
        if (producer_channel is None) != (producer_handler is None):
            self.reject("incomplete private producer channel")
        self.runs += 1
        if self.runs > self.cumulative_limit("runs"):
            self.reject("aggregate process-launch budget exhausted")
        if privileged:
            if (
                input_data is not None or len(argv) <= len(NAMESPACE_LAUNCHER)
                or tuple(argv[:len(NAMESPACE_LAUNCHER)]) != NAMESPACE_LAUNCHER
            ):
                self.reject("privileged probe requires the guarded PID-namespace lifecycle")
            try:
                ordinary_executable(argv[0])
                ordinary_executable(argv[len(NAMESPACE_LAUNCHER)])
            except (OSError, ValueError) as error:
                self.reject(f"unsupported privileged namespace lifecycle: {error}")
        limit = self.limits.process_output_bytes if output_limit is None else output_limit
        if limit <= 0 or limit > getattr(self.limits, f"{category}_bytes", 0):
            self.reject("invalid process stream budget")
        if input_data is not None:
            self.charge("pending", len(input_data))
        child = None
        input_read = None
        input_write = None
        input_stream = None
        output = [bytearray(), bytearray()]
        primary = None
        try:
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, (signal.SIGINT, signal.SIGTERM))
            try:
                if input_data is not None:
                    input_read, input_write = os.pipe()
                    input_stream = os.fdopen(input_write, "wb", buffering=0)
                    input_write = None
                launcher = [
                    "/usr/bin/python3", "-I", "-S", "-B",
                    str(Path(__file__).resolve().with_name("lifecycle.py")),
                    str(self.deadline),
                    *(["--stdin-fd", str(input_read)] if input_read is not None else []),
                    "--", *argv,
                ]
                if privileged:
                    launcher = ["/usr/bin/sudo", "-n", "--", *launcher]
                self.charge("pending", sum(len(os.fsencode(arg)) + 1 for arg in launcher))
                child = subprocess.Popen(
                    launcher, cwd=cwd, env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    pass_fds=() if input_read is None else (input_read,),
                    close_fds=True, start_new_session=True,
                    preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, mask),
                )
                if input_read is not None:
                    os.close(input_read)
                    input_read = None
                self.children[child] = privileged
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            with selectors.DefaultSelector() as selector:
                for index, stream in enumerate((child.stdout, child.stderr)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, index)
                supplied = 0
                if input_data is not None:
                    os.set_blocking(input_stream.fileno(), False)
                    selector.register(input_stream, selectors.EVENT_WRITE, 2)
                if producer_channel is not None:
                    selector.register(producer_channel, selectors.EVENT_READ, 3)
                for ancestor in self.producer_waiters:
                    selector.register(ancestor, selectors.EVENT_READ, 4)
                count = 0
                producer_finished = producer_channel is None
                while selector.get_map():
                    if producer_channel is not None and producer_channel.listening and child.poll() is not None:
                        raise ChannelError("supervisor exited before private producer connection")
                    if (
                        producer_finished and all(key.data == 4 for key in selector.get_map().values())
                        and child.poll() is not None
                    ):
                        break
                    for key, _ in selector.select(min(self.remaining(), 0.05)):
                        if key.fd not in selector.get_map():
                            continue
                        if key.data == 4:
                            key.fileobj.ensure_idle()
                            continue
                        if key.data == 3:
                            if producer_channel.listening:
                                selector.unregister(producer_channel)
                                producer_channel.accept(
                                    launcher_pid=child.pid, peer_uid=0 if privileged else os.getuid(),
                                    ancestry_limit=self.limits.entries,
                                )
                                selector.register(producer_channel, selectors.EVENT_READ, 3)
                                continue
                            if producer_finished:
                                if producer_channel.receive_eof():
                                    selector.unregister(producer_channel)
                                continue
                            packet = producer_channel.receive()
                            if packet is None:
                                continue
                            # Parked guests cannot issue new output writes;
                            # drain their already completed output before nested work.
                            for stream, index in ((child.stdout, 0), (child.stderr, 1)):
                                while True:
                                    try:
                                        chunk = os.read(stream.fileno(), min(65536, limit - count + 1))
                                    except BlockingIOError:
                                        break
                                    if not chunk:
                                        if stream in selector.get_map():
                                            selector.unregister(stream)
                                        break
                                    count += len(chunk)
                                    self.charge(category, len(chunk))
                                    if count > limit:
                                        self.reject("process output exceeds streaming byte bound")
                                    output[index].extend(chunk)
                            self.producer_waiters.append(producer_channel)
                            try:
                                reply = producer_handler(packet)
                            finally:
                                self.producer_waiters.pop()
                            if reply is None:
                                producer_finished = True
                                producer_channel.shutdown_write()
                            else:
                                producer_channel.send(reply)
                            continue
                        if key.data == 2:
                            if supplied < len(input_data):
                                supplied += os.write(key.fd, input_data[supplied:supplied + 65536])
                            if supplied == len(input_data):
                                selector.unregister(key.fileobj)
                                input_stream.close()
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
        except BaseException as error:
            primary = error
            self.failed = True
            raise
        finally:
            actions = []
            if child is not None:
                def stop():
                    self._terminate(child, privileged)
                    self.children.pop(child, None)
                actions.extend((stop, child.stdout.close, child.stderr.close, child.stdin.close))
            if input_read is not None:
                actions.append(lambda: os.close(input_read))
            if input_write is not None:
                actions.append(lambda: os.close(input_write))
            if input_stream is not None:
                actions.append(input_stream.close)
            try:
                finish_cleanup(actions, primary=primary)
            except BaseException:
                self.failed = True
                raise

    def _run_outcome(
        self, argv, *, env, cwd, output_limit, input_data, category,
        privileged, producer_channel, producer_handler, outcome,
    ):
        self.remaining()
        limit = self.limits.process_output_bytes if output_limit is None else output_limit
        if (
            type(outcome) is not _RunOutcome or self._outcome is not outcome
            or outcome._budget is not self or outcome._released or outcome._spent
            or outcome._ordinal != self.runs + 1 or outcome._deadline != self.deadline
            or self.session_started or self.producer_waiters or self.children
            or type(limit) is not int or limit != outcome._limit or category != "output"
            or input_data is not None or producer_channel is not None or producer_handler is not None
            or privileged is not True or len(argv) <= len(NAMESPACE_LAUNCHER)
            or tuple(argv[:len(NAMESPACE_LAUNCHER)]) != NAMESPACE_LAUNCHER
        ):
            self.reject("outcome requires its single-use exact guarded bare-budget launch")
        if (
            self.runs >= self.cumulative_limit("runs") or limit > self.limits.process_output_bytes
            or self.bytes.get("output", 0) + limit > self.cumulative_limit("output_bytes")
            or sum(self.bytes.values()) + limit > self.cumulative_limit("total_bytes")
        ):
            self.reject("outcome stream reservation exceeds remaining budget")
        outcome._spent = True
        self.runs += 1
        child = selector = result = None
        primary = None
        stage = "setup"
        report = outcome._cleanup
        try:
            ordinary_executable(argv[0])
            ordinary_executable(argv[len(NAMESPACE_LAUNCHER)])
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
            acquisition_error = None
            try:
                signal.pthread_sigmask(signal.SIG_BLOCK, (signal.SIGINT, signal.SIGTERM))
                launcher = [
                    "/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-S", "-B",
                    str(Path(__file__).resolve().with_name("lifecycle.py")), str(self.deadline),
                    "--outcome-v1", outcome._token, "--", *argv,
                ]
                self.charge("pending", sum(len(os.fsencode(arg)) + 1 for arg in launcher))
                if sum(self.bytes.values()) + limit > self.cumulative_limit("total_bytes"):
                    self.reject("outcome aggregate stream headroom exhausted before launch")
                child = subprocess.Popen(
                    launcher, cwd=cwd, env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(),
                    close_fds=True, start_new_session=True,
                    preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, mask),
                )
                self.children[child] = True
            except BaseException as error:
                acquisition_error = error
                outcome._exception("setup", error)
                raise
            finally:
                report.attempt("mask")
                try:
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                except BaseException as error:
                    report.unsure("mask")
                    report.error("mask", error)
                    if acquisition_error is None:
                        raise
                    if error is not acquisition_error:
                        _lifecycle._forget_error(error)
            selector = selectors.DefaultSelector()
            body_error = None
            try:
                for index, stream in enumerate((child.stdout, child.stderr)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, index)
                eof = 0
                stage = "capture"
                while selector.get_map():
                    for key, _ in selector.select(min(self.remaining(), 0.05)):
                        if key.fd not in selector.get_map():
                            continue
                        available = len(outcome._buffers[key.data]) - outcome._sizes[key.data]
                        chunk = os.read(key.fd, min(_lifecycle._FRAME_BYTES, available + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            eof |= 1 << key.data
                            continue
                        self.charge("output", len(chunk))
                        outcome._append(key.data, chunk)
                        chunk = None
                stage = "wait"
                status = child.wait(timeout=self.remaining())
                outcome._observe(status, eof == 3)
                if type(child.returncode) is not int or child.returncode != status:
                    raise MakeProbeError("outcome normal wait/reap disagreement")
                stage = "freeze"
                outcome._freeze()
                self.remaining()
            except BaseException as error:
                body_error = error
                outcome._exception(stage, error)
                raise
            finally:
                stage = "selector"
                finish_cleanup([("selector", selector.close)], primary=body_error, report=report)
                selector = None
            stage = "freeze"
            result = subprocess.CompletedProcess(argv, outcome._status, *outcome._streams)
        except BaseException as error:
            primary = error
            outcome._exception(stage, error)
            self.failed = True
            raise
        finally:
            if not outcome._freeze_attempted:
                try:
                    outcome._freeze()
                except BaseException as error:
                    report.error("freeze", error)
                    if primary is None:
                        primary = error
                    elif error is not primary:
                        _lifecycle._forget_error(error)
            actions = []
            if child is not None:
                def stop():
                    try:
                        self._terminate(child, True, report=report)
                    finally:
                        if _lifecycle._status(child.returncode):
                            self.children.pop(child, None)
                actions.append(("leader", stop))
                for name in ("stdout", "stderr"):
                    actions.append((name, lambda name=name: _lifecycle._close_stream(child, name, report)))
            try:
                finish_cleanup(actions, primary=primary, report=report)
            except BaseException as error:
                self.failed = True
                outcome._exception("cleanup", error)
                raise
            finally:
                outcome._terminal = True
                outcome._reaped = child is not None and child not in self.children
            if primary is not None and not self.failed:
                self.failed = True
                outcome._exception("freeze", primary)
                raise primary
        outcome._returned = True
        return result


def text(data: bytes, boundary: str, encoding: str = "utf-8") -> str:
    try:
        return data.decode(encoding, errors="strict")
    except UnicodeDecodeError as error:
        raise MakeProbeError(f"{boundary} is not strict {encoding}") from error
