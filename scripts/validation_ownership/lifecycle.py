"""Trusted exclusive child reaper for every bounded probe launch."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import math
import os
import select
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


LIBC = ctypes.CDLL(None, use_errno=True)
TERMINATING = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)

# These are subdivisions of the caller's existing budget, not workload limits.
_FRAME_BYTES = 4096
_ERROR_BYTES = 4096
_ERROR_ENTRIES = 16
_JSON_NODES = 192
_JSON_WORK_BYTES = _JSON_NODES * 768 + 4 * _FRAME_BYTES + 8192
_STAGES = (
    "setup", "wait", "capture", "freeze", "selector", "lifetime", "stdout",
    "stderr", "leader", "descendant", "pidfd", "handler", "mask", "deferred",
    "publication", "agreement", "ownership", "cleanup", "creator", "worker",
)
_MODES = ("readonly", "writable", "wrong-device", "substituted", "unsupported", "locked", "old")
_ERROR_TYPES = (OSError, ValueError, TimeoutError, RuntimeError, KeyboardInterrupt, SystemExit)


def _representation_supported():
    # The 768-byte/node envelope includes ASCII strings, bounded integers,
    # dict/list capacity, decoder memo/pairs and encoder temporaries. Refuse an
    # interpreter whose object headers invalidate those conservative estimates.
    return sys.implementation.name == "cpython" and all((
        sys.getsizeof({}) <= 128, sys.getsizeof([]) <= 128,
        sys.getsizeof((None, None, None)) <= 128, sys.getsizeof("") <= 128,
        sys.getsizeof(b"") <= 64, sys.getsizeof((1 << 64) - 1) <= 64,
    ))


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _status(value):
    return _integer(value, -(signal.NSIG - 1), 255)


def _token(value):
    return (
        type(value) is str and len(value) == 32
        and all(char in "0123456789abcdef" for char in value)
    )


def _error_value(error):
    """Never format, retain, or walk an exception's arguments/history."""
    try:
        tag = next((index + 1 for index, kind in enumerate(_ERROR_TYPES)
                    if isinstance(error, kind)), 7)
        number = error.errno if isinstance(error, OSError) else None
        if number is not None and not _integer(number, 0, 4095):
            return (8, None)
        return (tag, number)
    except BaseException:
        return (8, None)


def _forget_error(error):
    for name in ("__traceback__", "__context__", "__cause__"):
        BaseException.__setattr__(error, name, None)


@dataclass(frozen=True, slots=True)
class _CleanupValue:
    attempted: int
    failed: int
    uncertain: int
    count: int
    overflow: bool
    metadata_failed: bool
    errors: tuple

    @property
    def complete(self):
        return not (self.failed or self.uncertain or self.overflow or self.metadata_failed)


class _CleanupReport:
    """One role's prepaid, cumulative cell; never a list of live exceptions."""

    __slots__ = (
        "role", "deadline", "attempted", "failed", "uncertain", "count",
        "overflow", "metadata_failed", "_entries", "_value", "revision",
    )

    def __init__(self, role, deadline):
        if role not in ("C", "L", "R") or type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError("invalid outcome cleanup binding")
        self.role, self.deadline = role, deadline
        self.attempted = self.failed = self.uncertain = self.count = self.revision = 0
        self.overflow = self.metadata_failed = False
        self._entries = bytearray(6 * _ERROR_ENTRIES)
        self._value = None

    def _change(self):
        self._value = None
        self.revision = min(self.revision + 1, 0x7fffffff)

    def attempt(self, stage):
        self._change()
        self.attempted |= 1 << _STAGES.index(stage)

    def unsure(self, stage):
        self._change()
        self.uncertain |= 1 << _STAGES.index(stage)

    def error(self, stage, error):
        self._change()
        index = _STAGES.index(stage)
        self.failed |= 1 << index
        tag, number = _error_value(error)
        if tag == 8:
            self.metadata_failed = True
        if self.count < _ERROR_ENTRIES:
            struct.pack_into("<BBi", self._entries, 6 * self.count, index, tag,
                             -1 if number is None else number)
        else:
            self.overflow = True
        self.count = min(self.count + 1, _ERROR_ENTRIES + 1)

    def value(self):
        if self._value is None:
            entries = tuple(
                (stage, tag, None if number == -1 else number)
                for stage, tag, number in struct.iter_unpack(
                    "<BBi", memoryview(self._entries)[:6 * min(self.count, _ERROR_ENTRIES)],
                )
            )
            self._value = _CleanupValue(
                self.attempted, self.failed, self.uncertain, self.count,
                self.overflow, self.metadata_failed, entries,
            )
        return self._value


def _report(report):
    if type(report) is not _CleanupReport:
        raise TypeError("outcome cleanup requires its concrete admitted report")


def _finish_bounded(actions, primary, handlers, report):
    _report(report)
    first = primary
    previous_mask = None
    pending = []

    def failure(stage, error, recorded=False):
        nonlocal first
        if not recorded:
            report.error(stage, error)
        if first is None:
            first = error
        elif error is not first:
            _forget_error(error)

    try:
        report.attempt("mask")
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, TERMINATING)
    except BaseException as error:
        report.unsure("mask")
        failure("mask", error)
    try:
        for item in actions:
            stage, action = item if type(item) is tuple else ("cleanup", item)
            report.attempt(stage)
            previous_errors = report.count
            try:
                action()
            except BaseException as error:
                failure(stage, error, report.count != previous_errors or report.count == 17)
    finally:
        if handlers is not None:
            for signum, handler in tuple(handlers.items()):
                report.attempt("handler")
                try:
                    signal.signal(signum, handler)
                    del handlers[signum]
                except BaseException as error:
                    report.unsure("handler")
                    failure("handler", error)
        if previous_mask is not None:
            try:
                for signum in TERMINATING:
                    if signum not in previous_mask and signum in signal.sigpending():
                        if signal.sigtimedwait({signum}, 0) is not None:
                            pending.append(signum)
            except BaseException as error:
                report.unsure("deferred")
                failure("deferred", error)
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            except BaseException as error:
                report.unsure("mask")
                failure("mask", error)
    for signum in pending:
        report.attempt("deferred")
        try:
            signal.raise_signal(signum)
        except BaseException as error:
            failure("deferred", error)
    # In particular, no notes or cleanup_errors history is added in this mode.
    actions = handlers = item = action = None
    if primary is None and first is not None:
        raise first


def _close_stream(child, name, report):
    stream = getattr(child, name)
    setattr(child, name, None)  # Withdraw authority before a possibly ambiguous close.
    if stream is not None:
        try:
            stream.close()
        except BaseException:
            if not stream.closed:
                report.unsure("lifetime" if name == "stdin" else name)
            raise


def _close_fd(descriptor, report):
    # Callers first remove the integer from their ownership cell.
    try:
        os.close(descriptor)
    except BaseException:
        report.unsure("pidfd")
        raise


def _json_scan(data, limit):
    """Admit decoder representations before the decoder can allocate them."""
    if type(data) is not bytes or len(data) > limit:
        raise ValueError("outcome frame length")
    nodes = depth = index = 0
    while index < len(data):
        char = data[index]
        if char > 127:
            raise ValueError("outcome frame ASCII")
        if char == 34:
            index += 1
            length = 0
            while index < len(data) and data[index] != 34:
                if data[index] < 32 or data[index] > 127:
                    raise ValueError("outcome frame string")
                if data[index] == 92:
                    index += 1
                    if index == len(data) or data[index] not in b'"\\/bfnrtu':
                        raise ValueError("outcome frame escape")
                    if data[index] == 117:
                        if index + 4 >= len(data) or any(
                            data[position] not in b"0123456789abcdefABCDEF"
                            for position in range(index + 1, index + 5)
                        ):
                            raise ValueError("outcome frame Unicode escape")
                        index += 4
                length += 1
                if length > 64:
                    raise ValueError("outcome frame string bound")
                index += 1
            if index == len(data):
                raise ValueError("outcome frame string bound")
            nodes += 1
        elif char in (91, 123):
            depth += 1
            nodes += 1
            if depth > 6:
                raise ValueError("outcome frame depth")
        elif char in (93, 125):
            depth -= 1
        elif char not in b" \r\n\t,:":
            start = index
            while index < len(data) and data[index] not in b" \r\n\t,:]}":
                index += 1
            if index - start > 24:
                raise ValueError("outcome frame scalar bound")
            nodes += 1
            index -= 1
        if nodes > _JSON_NODES or depth < 0:
            raise ValueError("outcome frame representation bound")
        index += 1


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate outcome key")
        value[key] = item
    return value


def _constant(value):
    raise ValueError("nonfinite outcome number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite outcome number")
    return number


def _json_read(data, *, limit=_FRAME_BYTES - 4):
    if not _representation_supported():
        raise ValueError("unsupported outcome representation")
    _json_scan(data, limit)
    return json.loads(data.decode("ascii"), object_pairs_hook=_pairs,
                      parse_constant=_constant, parse_float=_finite_float)


def _wire_size(value, depth=0, count=None):
    # The fixed writer admits the entire encode/transient representation before
    # json.dumps; a wire-size check after encoding alone would be too late.
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > _JSON_NODES or depth > 6:
        raise ValueError("outcome encode representation bound")
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is int:
        if not -(1 << 63) <= value < 1 << 64:
            raise ValueError("outcome integer bound")
        return len(str(value))
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("outcome finite deadline")
        return len(repr(value))
    if type(value) is str:
        if len(value) > 64 or any(ord(char) < 32 or ord(char) > 126 or char in '\\"' for char in value):
            raise ValueError("outcome encode string bound")
        return len(value) + 2
    if type(value) in (list, tuple):
        return 2 + max(0, len(value) - 1) + sum(
            _wire_size(item, depth + 1, count) for item in value
        )
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("outcome object keys")
        return 2 + max(0, len(value) - 1) + sum(
            _wire_size(key, depth + 1, count) + 1 + _wire_size(item, depth + 1, count)
            for key, item in value.items()
        )
    raise ValueError("outcome encode type")


def _encode_frame(value):
    if not _representation_supported():
        raise ValueError("unsupported outcome representation")
    size = _wire_size(value)
    if size > _FRAME_BYTES - 4:
        raise ValueError("outcome encoded frame bound")
    body = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    if len(body) != size:
        raise ValueError("outcome representation disagreement")
    return struct.pack("<I", size) + body


def _fields(value, names):
    if type(value) is not dict or value.keys() != set(names.split()):
        raise ValueError("outcome fields")


def _error_read(value):
    if value is None:
        return None
    if (
        type(value) is not list or len(value) != 2 or not _integer(value[0], 1, 8)
        or value[1] is not None and not _integer(value[1], 0, 4095)
    ):
        raise ValueError("outcome error tag")
    return tuple(value)


def _cleanup_wire(value):
    return {
        "attempted": value.attempted, "failed": value.failed, "uncertain": value.uncertain,
        "count": value.count, "overflow": value.overflow, "metadata_failed": value.metadata_failed,
        "errors": value.errors,
    }


def _cleanup_read(value):
    _fields(value, "attempted failed uncertain count overflow metadata_failed errors")
    if (
        any(not _integer(value[name], 0, (1 << len(_STAGES)) - 1)
            for name in ("attempted", "failed", "uncertain"))
        or not _integer(value["count"], 0, 17)
        or type(value["overflow"]) is not bool or type(value["metadata_failed"]) is not bool
        or type(value["errors"]) is not list
        or len(value["errors"]) != min(value["count"], 16)
        or value["overflow"] != (value["count"] == 17)
    ):
        raise ValueError("outcome cleanup shape")
    entries = []
    for entry in value["errors"]:
        if type(entry) is not list or len(entry) != 3 or not _integer(entry[0], 0, len(_STAGES) - 1):
            raise ValueError("outcome cleanup stage")
        tag = _error_read(entry[1:])
        if not value["failed"] & (1 << entry[0]):
            raise ValueError("outcome cleanup failure bit")
        entries.append((entry[0], *tag))
    return _CleanupValue(*(value[name] for name in (
        "attempted", "failed", "uncertain", "count", "overflow", "metadata_failed",
    )), tuple(entries))


def _wait_value(pid, value):
    items = (value.si_pid, value.si_uid, value.si_signo, value.si_code, value.si_status)
    if (
        not _integer(pid, 1, 0x7fffffff) or not _integer(items[0], 1, 0x7fffffff) or items[0] != pid
        or not _integer(items[1], 0, 0xffffffff) or type(items[2]) is not int or items[2] != signal.SIGCHLD
        or type(items[3]) is not int
    ):
        raise ValueError("outcome wait identity")
    if items[3] == os.CLD_EXITED and _integer(items[4], 0, 255):
        return items, items[4]
    if items[3] in (os.CLD_KILLED, os.CLD_DUMPED) and _integer(items[4], 1, signal.NSIG - 1):
        return items, -items[4]
    raise ValueError("outcome wait status")


@dataclass(frozen=True, slots=True)
class _FixtureBinding:
    mode: str
    uid: int
    gid: int
    deadline: float
    device: int
    inode: int
    owner: int

    def __post_init__(self):
        if (
            type(self.mode) is not str or self.mode not in _MODES or not _integer(self.uid, 1, 0xfffffffe)
            or not _integer(self.gid, 1, 0xfffffffe) or type(self.owner) is not int or self.owner != self.uid
            or type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)
            or not _integer(self.device, 0, (1 << 64) - 1) or not _integer(self.inode, 1, (1 << 64) - 1)
        ):
            raise ValueError("outcome fixture binding")

    def wire(self):
        return [self.mode, self.uid, self.gid, self.deadline, self.device, self.inode, self.owner]


def _worker_read(value):
    """Data-only semantic slots. No worker bytes are a publisher envelope."""
    _fields(value, "mode before after failure calls caps denied null_io fd_closed local_nonzero_topology")
    if value["mode"] not in _MODES:
        raise ValueError("outcome worker mode")
    for name, length in (("before", 6), ("after", 6), ("caps", 6), ("denied", 3)):
        if (
            type(value[name]) is not list or len(value[name]) != length
            or any(not _integer(item, 0, (1 << 64) - 1) for item in value[name])
        ):
            raise ValueError("outcome worker integers")
    if (
        type(value["null_io"]) is not bool or type(value["fd_closed"]) is not bool
        or type(value["local_nonzero_topology"]) is not bool
        or type(value["calls"]) is not list or len(value["calls"]) > 1
    ):
        raise ValueError("outcome worker result")
    for call in value["calls"]:
        if type(call) is not list or not (
            len(call) == 7 and all(_integer(item, 0, (1 << 64) - 1) for item in call)
            or len(call) == 2 and call[0] == "legacy-remount" and _integer(call[1], 0, 0xffffffff)
        ):
            raise ValueError("outcome worker operation")
    return (value["mode"], tuple(value["before"]), tuple(value["after"]), _error_read(value["failure"]),
            tuple(tuple(call) for call in value["calls"]), tuple(value["caps"]), tuple(value["denied"]),
            value["null_io"], value["fd_closed"], value["local_nonzero_topology"])


def _private_worker_record(data):
    # The future W->R buffer has its own F pending reservation. This helper is
    # only serialization/validation, not a worker, writer barrier or bootstrap.
    return _worker_read(_json_read(data, limit=_FRAME_BYTES))


@dataclass(frozen=True, slots=True)
class _OutcomeFrame:
    role: str
    phase: str
    pid: int
    kind: str | None = None
    stage: str | None = None
    status: int | None = None
    wait: tuple | None = None
    error: tuple | None = None
    binding: _FixtureBinding | None = None
    result: tuple | None = None
    setup_status: int | None = None
    cleanup: _CleanupValue | None = None
    disposition: str | None = None


def _decode_frame(data, token, binding, before=None):
    value = _json_read(data)
    if type(value) is not dict or type(value.get("v")) is not int or value["v"] != 1:
        raise ValueError("outcome version")
    role, phase = value.get("role"), value.get("phase")
    if role not in ("R", "L") or phase not in ("before", "after"):
        raise ValueError("outcome role/phase")
    identity = "token" if role == "L" else "binding"
    common = "v role phase pid " + identity
    if role == "L":
        if not _token(value.get("token")) or value["token"] != token:
            raise ValueError("outcome token binding")
    else:
        if type(binding) is not _FixtureBinding or type(value.get("binding")) is not list:
            raise ValueError("outcome fixture unavailable")
        supplied = value["binding"]
        if len(supplied) != 7 or _FixtureBinding(*supplied) != binding:
            raise ValueError("outcome fixture mismatch")
    pid = value.get("pid")
    if not _integer(pid, 0, 0x7fffffff):
        raise ValueError("outcome child identity")
    if phase == "after":
        _fields(value, common + " before cleanup disposition status")
        if type(before) is tuple and len(before) == 2:
            before = before[role == "L"]
        if (
            type(before) is not _OutcomeFrame or before.role != role
            or before.pid != pid or value["before"] != "before"
            or value["disposition"] not in ("return", "raise")
            or value["status"] is not None and not _status(value["status"])
            or value["disposition"] == "raise" and value["status"] is not None
            or value["disposition"] == "return" and (
                before.kind != "normal-exit" or value["status"] != before.status
            )
        ):
            raise ValueError("outcome after binding")
        cleanup = _cleanup_read(value["cleanup"])
        if role == "L" and value["disposition"] == "return" and not cleanup.complete:
            raise ValueError("outcome failed watchdog disposition")
        return _OutcomeFrame(role, phase, pid, status=value["status"],
                             cleanup=cleanup, disposition=value["disposition"])
    _fields(value, common + " kind stage status error " + (
        "wait" if role == "L" else "result setup_status"
    ))
    kind, stage, status_value = value["kind"], value["stage"], value["status"]
    if kind not in ("normal-exit", "exception", "unavailable") or stage not in _STAGES:
        raise ValueError("outcome first fact")
    error = _error_read(value["error"])
    if (
        kind == "normal-exit" and (not _status(status_value) or pid == 0 or error is not None)
        or kind != "normal-exit" and status_value is not None
        or (kind == "exception") != (error is not None)
    ):
        raise ValueError("outcome first status")
    if role == "L":
        wait = value["wait"]
        if kind == "normal-exit":
            if type(wait) is not list or len(wait) != 5:
                raise ValueError("outcome wait tuple")
            from types import SimpleNamespace
            observed, normalized = _wait_value(pid, SimpleNamespace(**dict(zip(
                ("si_pid", "si_uid", "si_signo", "si_code", "si_status"), wait,
            ))))
            if normalized != status_value:
                raise ValueError("outcome normalized status")
        else:
            if wait is not None:
                raise ValueError("outcome unavailable wait")
            observed = None
        return _OutcomeFrame(role, phase, pid, kind, stage, status_value, observed, error)
    result = None if value["result"] is None else _worker_read(value["result"])
    setup_status = value["setup_status"]
    if (
        setup_status is not None and not _status(setup_status)
        or result is not None and (
            kind != "normal-exit" or stage != "worker" or result[0] != binding.mode or setup_status != 0
        )
        or kind == "normal-exit" and stage == "creator" and status_value == 0
    ):
        raise ValueError("outcome creator is not a mode result")
    return _OutcomeFrame(role, phase, pid, kind, stage, status_value, error=error,
                         binding=binding, result=result, setup_status=setup_status)


class _OutcomePublisher:
    __slots__ = ("token", "deadline", "sent", "attempted")

    def __init__(self, token, deadline, lifetime):
        if not _token(token):
            raise ValueError("invalid outcome token")
        output, guard = os.fstat(1), os.fstat(lifetime)
        if (
            not stat.S_ISFIFO(output.st_mode) or not stat.S_ISFIFO(guard.st_mode)
            or (output.st_dev, output.st_ino) == (guard.st_dev, guard.st_ino)
            or fcntl.fcntl(1, fcntl.F_GETFL) & os.O_ACCMODE != os.O_WRONLY
            or os.fpathconf(1, "PC_PIPE_BUF") < _FRAME_BYTES
        ):
            raise ValueError("outcome requires distinct inherited FIFO writer")
        os.set_blocking(1, False)
        self.token, self.deadline, self.sent, self.attempted = token, deadline, 0, 0

    def publish(self, value):
        if (
            value["role"] != "L" or self.attempted > 1
            or value["phase"] != ("before" if self.attempted == 0 else "after")
        ):
            raise ValueError("outcome publication order")
        self.attempted += 1
        frame = _encode_frame(value)
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("outcome publication deadline")
            try:
                count = os.write(1, frame)
            except BlockingIOError:
                select.select([], [1], [], remaining)
                continue
            if count != len(frame):
                raise OSError(errno.EIO, "incomplete outcome publication")
            self.sent += 1
            return


class WatchdogInterrupted(RuntimeError):
    pass


def finish_cleanup(actions, *, primary=None, handlers=None, report=None):
    """Finish owned teardown before replaying terminating signals."""
    if report is not None:
        return _finish_bounded(actions, primary, handlers, report)
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, TERMINATING)
    errors = []
    pending = []
    try:
        for action in actions:
            try:
                action()
            except BaseException as error:
                errors.append(error)
    finally:
        if handlers is not None:
            for signum, handler in tuple(handlers.items()):
                try:
                    signal.signal(signum, handler)
                    del handlers[signum]
                except BaseException as error:
                    errors.append(error)
        # Replay separately so one raising Python handler cannot hide another
        # pending termination. Signals already blocked by the caller stay pending.
        for signum in sorted(set(TERMINATING) & signal.sigpending() - previous_mask):
            if signal.sigtimedwait({signum}, 0) is not None:
                pending.append(signum)
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        except BaseException as error:
            errors.append(error)
    for signum in pending:
        try:
            signal.raise_signal(signum)
        except BaseException as error:
            errors.append(error)
    if errors:
        failure = primary if primary is not None else errors.pop(0)
        for error in errors:
            message = f"after owned cleanup: {type(error).__name__}: {error}"
            failure.cleanup_errors = (*getattr(failure, "cleanup_errors", ()), message)
            if hasattr(failure, "add_note"):
                failure.add_note(message)
        if primary is None:
            raise failure


@contextmanager
def cleanup_scope(actions):
    primary = None
    try:
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        finish_cleanup(actions, primary=primary)


def ordinary_executable(path):
    mode = os.stat(path)
    if (
        not stat.S_ISREG(mode.st_mode) or mode.st_uid != 0
        or mode.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError("namespace lifecycle requires immutable ordinary system executables")
    try:
        os.getxattr(path, "security.capability")
    except OSError as error:
        if error.errno not in {errno.ENODATA, errno.EOPNOTSUPP}:
            raise
    else:
        raise ValueError("file capabilities would clear the namespace parent-death signal")


def prctl(option, value):
    if LIBC.prctl(option, value, 0, 0, 0):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def parent_death(parent, mask):
    prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG survives the ordinary unshare exec.
    if os.getppid() != parent:
        os._exit(125)
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def require_pidfds():
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("namespace lifecycle requires pidfd support")
    descriptor = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(descriptor, 0)
    finally:
        os.close(descriptor)


def owned_children():
    data = Path(f"/proc/self/task/{os.getpid()}/children").read_bytes()
    values = data.split()
    if any(not value.isdigit() or int(value) <= 0 for value in values):
        raise RuntimeError("cannot identify watchdog-owned children")
    return [int(value) for value in values]


def terminate(child, *, report=None):
    if report is not None:
        return _terminate_bounded(child, report)
    if child.returncode is not None:
        return
    # The unreaped session leader pins its PID/group until the signal is sent.
    # This fresh watchdog is the exclusive waiter, including for orphaned
    # children. Never signal a group after releasing the leader's identity.
    os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()
    while owned_children():
        descriptors = []
        try:
            # Children remain waitable until this sole reaper consumes them.
            # pidfds also cover descendants that left the original group.
            for pid in owned_children():
                descriptors.append(os.pidfd_open(pid))
            for descriptor in descriptors:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            os.waitpid(-1, 0)
        except ChildProcessError:
            break
        finally:
            for descriptor in descriptors:
                os.close(descriptor)


def _terminate_bounded(child, report):
    _report(report)
    if child.returncode is not None:
        return

    def leader():
        try:
            observed = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if observed is not None:
                _wait_value(child.pid, observed)

            def signal_group():
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            finish_cleanup([
                ("leader", signal_group),
                ("wait", lambda: child.wait(timeout=max(0, report.deadline - time.monotonic()))),
            ], report=report)
        except BaseException:
            if child.returncode is None:
                report.unsure("leader")
            raise

    def descendants():
        first = None
        while True:
            children = owned_children()
            if child.returncode is None:
                children = [pid for pid in children if pid != child.pid]
            if not children:
                break
            for pid in children:
                descriptor = None
                reaped = False
                previous_errors = report.count
                try:
                    descriptor = os.pidfd_open(pid)

                    def signal_child():
                        try:
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

                    def reap():
                        nonlocal reaped
                        while True:
                            found, _ = os.waitpid(pid, os.WNOHANG)
                            if type(found) is not int or found not in (0, pid):
                                raise RuntimeError("outcome descendant wait identity")
                            if found == pid:
                                reaped = True
                                return
                            remaining = report.deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError("outcome descendant reap deadline")
                            select.select([descriptor], [], [], remaining)

                    finish_cleanup([("descendant", signal_child), ("wait", reap)], report=report)
                except BaseException as error:
                    if not reaped:
                        report.unsure("descendant")
                    if report.count == previous_errors and report.count != 17:
                        report.error("descendant", error)
                    if first is None:
                        first = error
                    elif error is not first:
                        _forget_error(error)
                finally:
                    if descriptor is not None:
                        owned, descriptor = descriptor, None
                        try:
                            finish_cleanup([("pidfd", lambda: _close_fd(owned, report))],
                                           primary=first, report=report)
                        except BaseException as error:
                            first = error
            if first is not None:
                raise first
            if time.monotonic() >= report.deadline:
                report.unsure("descendant")
                raise TimeoutError("outcome descendant cleanup deadline")

    finish_cleanup([("leader", leader), ("descendant", descendants)], report=report)


def interrupted(signum, frame):
    # selectors catches InterruptedError, including exceptions from handlers.
    raise WatchdogInterrupted(f"namespace watchdog interrupted by signal {signum}")


def run(argv, deadline, *, lifetime=0, payload_input=None, outcome_token=None):
    if outcome_token is not None:
        return _run_outcome(argv, deadline, lifetime=lifetime,
                            payload_input=payload_input, token=outcome_token)
    if not argv or not math.isfinite(deadline) or deadline <= time.monotonic():
        raise ValueError("namespace watchdog requires a live aggregate deadline")
    if not stat.S_ISFIFO(os.fstat(lifetime).st_mode):
        raise ValueError("namespace watchdog requires a caller-owned lifetime pipe")
    if payload_input is not None and (
        payload_input < 3 or payload_input == lifetime
        or not stat.S_ISFIFO(os.fstat(payload_input).st_mode)
    ):
        raise ValueError("watchdog payload input requires a separate pipe")
    ordinary_executable(argv[0])
    require_pidfds()
    if owned_children():
        raise RuntimeError("watchdog requires an isolated child-reaper process")
    # Unsupported kernel lifecycle controls fail before launching anything.
    prctl(36, 1)  # PR_SET_CHILD_SUBREAPER; reap orphaned descendants as well.
    parent = os.getpid()
    child = None
    completion_fd = None
    selector = None
    handlers = {}
    primary = None
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())

    def launch():
        nonlocal child, completion_fd, selector
        selector = selectors.DefaultSelector()
        selector.register(lifetime, selectors.EVENT_READ)
        if selector.select(0):
            raise BrokenPipeError("caller lifetime ended before namespace launch")
        child = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL if payload_input is None else payload_input,
            close_fds=True,
            start_new_session=True, preexec_fn=lambda: parent_death(parent, mask),
        )
        completion_fd = os.pidfd_open(child.pid)
        selector.register(completion_fd, selectors.EVENT_READ)

    try:
        handlers[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for sig in TERMINATING:
            handlers[sig] = signal.signal(sig, interrupted)
        # The existing signal-deferring guard also makes acquisition atomic:
        # retain setup errors if unmasking delivers a queued interruption.
        finish_cleanup([launch])
        while True:
            # WNOWAIT retains the group leader until privileged cleanup.
            if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("aggregate probe deadline exhausted in namespace watchdog")
            if any(key.fd == lifetime for key, _ in selector.select(remaining)):
                raise BrokenPipeError("caller lifetime ended during namespace execution")
    except BaseException as error:
        primary = error
        raise
    finally:
        actions = []
        if child is not None:
            actions.append(lambda: terminate(child))
        if completion_fd is not None:
            actions.append(lambda: os.close(completion_fd))
        if selector is not None:
            actions.append(selector.close)
        finish_cleanup(actions, primary=primary, handlers=handlers)
    return child.returncode


def _run_outcome(argv, deadline, *, lifetime, payload_input, token):
    if (
        not _representation_supported() or not _token(token) or not argv
        or type(deadline) not in (int, float) or not math.isfinite(deadline)
    ):
        raise ValueError("invalid outcome watchdog invocation")
    report = _CleanupReport("L", deadline)
    child = completion_fd = selector = publisher = None
    primary = observation = None
    handlers = {}
    stage = "setup"

    def secondary(where, error):
        nonlocal primary
        report.error(where, error)
        if primary is None:
            primary = error
        elif primary is not error:
            _forget_error(error)

    def publish_before():
        if publisher is not None:
            publisher.publish({
                "v": 1, "role": "L", "phase": "before", "token": token,
                "pid": 0 if child is None else child.pid,
                "kind": "normal-exit" if observation is not None else "exception",
                "stage": stage, "status": None if observation is None else observation[1],
                "wait": None if observation is None else observation[0],
                "error": _error_value(primary) if observation is None else None,
            })

    def stop():
        if child is not None:
            terminate(child, report=report)

    def close_completion():
        nonlocal completion_fd
        owned, completion_fd = completion_fd, None
        if owned is not None:
            _close_fd(owned, report)

    def close_selector():
        nonlocal selector
        owned, selector = selector, None
        if owned is not None:
            owned.close()

    # Register before acquisition. Even frame construction/publication failure
    # must not bypass owned cleanup; the first action still precedes teardown.
    actions = (
        ("publication", publish_before), ("leader", stop),
        ("pidfd", close_completion), ("selector", close_selector),
    )
    try:
        if deadline <= time.monotonic() or payload_input is not None:
            raise ValueError("outcome requires original live deadline and no payload pipe")
        publisher = _OutcomePublisher(token, deadline, lifetime)
        ordinary_executable(argv[0])
        require_pidfds()
        if owned_children():
            raise RuntimeError("watchdog requires an isolated child-reaper process")
        prctl(36, 1)
        parent = os.getpid()
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())

        def launch():
            nonlocal child, completion_fd, selector
            selector = selectors.DefaultSelector()
            selector.register(lifetime, selectors.EVENT_READ)
            if selector.select(0):
                raise BrokenPipeError("caller lifetime ended before namespace launch")
            child = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, close_fds=True,
                start_new_session=True, preexec_fn=lambda: parent_death(parent, mask),
            )
            completion_fd = os.pidfd_open(child.pid)
            selector.register(completion_fd, selectors.EVENT_READ)

        handlers[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for signum in TERMINATING:
            handlers[signum] = signal.signal(signum, interrupted)
        finish_cleanup([("setup", launch)], report=report)
        stage = "wait"
        while True:
            observed = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if observed is not None:
                observation = _wait_value(child.pid, observed)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("aggregate probe deadline exhausted in namespace watchdog")
            if any(key.fd == lifetime for key, _ in selector.select(remaining)):
                raise BrokenPipeError("caller lifetime ended during namespace execution")
    except BaseException as error:
        primary = error
    finally:
        try:
            finish_cleanup(actions, primary=primary, handlers=handlers, report=report)
        except BaseException as error:
            primary = error
    try:
        if child is not None and not _status(child.returncode):
            report.unsure("leader")
        if observation is not None and (not _status(child.returncode) or child.returncode != observation[1]):
            secondary("agreement", RuntimeError("outcome reaped status disagrees with wait observation"))
        if primary is None and not report.value().complete:
            primary = RuntimeError("outcome cleanup incomplete")
        if publisher is not None:
            publisher.publish({
                "v": 1, "role": "L", "phase": "after", "token": token,
                "pid": 0 if child is None else child.pid,
                "before": "before", "cleanup": _cleanup_wire(report.value()),
                "disposition": "raise" if primary is not None else "return",
                "status": None if primary is not None else child.returncode,
            })
    except BaseException as error:
        secondary("publication", error)
    actions = selector = None
    if primary is not None:
        raise primary
    return child.returncode


def main():
    if not sys.flags.isolated or not sys.flags.no_site or len(sys.argv) < 4:
        raise SystemExit("namespace watchdog requires Python -I -S and trusted arguments")
    outcome_requested = False
    try:
        arguments = sys.argv[2:]
        payload_input = None
        outcome_token = None
        if arguments[0] == "--stdin-fd":
            payload_input = int(arguments[1])
            arguments = arguments[2:]
        if arguments and arguments[0] == "--outcome-v1":
            outcome_requested = True
            if len(arguments) < 3 or not _token(arguments[1]) or payload_input is not None:
                raise ValueError("invalid outcome option")
            outcome_token = arguments[1]
            arguments = arguments[2:]
        if not arguments or arguments[0] != "--":
            raise ValueError("watchdog command delimiter is missing")
        if outcome_token is None:
            status = run(arguments[1:], float(sys.argv[1]), payload_input=payload_input)
        else:
            status = run(arguments[1:], float(sys.argv[1]), outcome_token=outcome_token)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, MemoryError) as error:
        if isinstance(error, MemoryError) and not outcome_requested:
            raise
        if outcome_requested:
            tag, number = _error_value(error)
            print(f"namespace watchdog outcome failure: {tag}/{number}", file=sys.stderr)
        else:
            print(f"namespace watchdog: {error}", file=sys.stderr)
        return 125
    if status < 0:
        if -status not in {signal.SIGKILL, signal.SIGSTOP}:
            signal.signal(-status, signal.SIG_DFL)
        os.kill(os.getpid(), -status)
        os._exit(128 - status)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
