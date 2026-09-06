"""Trusted exclusive child reaper for every bounded probe launch."""

from __future__ import annotations

import ctypes
import errno
import math
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


LIBC = ctypes.CDLL(None, use_errno=True)
TERMINATING = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)


class WatchdogInterrupted(RuntimeError):
    pass


def finish_cleanup(actions, *, primary=None, handlers=None):
    """Finish owned teardown before replaying terminating signals."""
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


def terminate(child):
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


def interrupted(signum, frame):
    # selectors catches InterruptedError, including exceptions from handlers.
    raise WatchdogInterrupted(f"namespace watchdog interrupted by signal {signum}")


def run(argv, deadline, *, lifetime=0, payload_input=None):
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
    handlers = {}
    primary = None
    try:
        handlers[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for sig in TERMINATING:
            handlers[sig] = signal.signal(sig, interrupted)
        with selectors.DefaultSelector() as selector:
            selector.register(lifetime, selectors.EVENT_READ)
            if selector.select(0):
                raise BrokenPipeError("caller lifetime ended before namespace launch")
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, TERMINATING)
            try:
                child = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL if payload_input is None else payload_input,
                    close_fds=True,
                    start_new_session=True, preexec_fn=lambda: parent_death(parent, mask),
                )
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            while True:
                # WNOWAIT retains the group leader until privileged cleanup.
                if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("aggregate probe deadline exhausted in namespace watchdog")
                if selector.select(min(remaining, 0.05)):
                    raise BrokenPipeError("caller lifetime ended during namespace execution")
    except BaseException as error:
        primary = error
        raise
    finally:
        finish_cleanup(
            [] if child is None else [lambda: terminate(child)],
            primary=primary, handlers=handlers,
        )
    return child.returncode


def main():
    if not sys.flags.isolated or not sys.flags.no_site or len(sys.argv) < 4:
        raise SystemExit("namespace watchdog requires Python -I -S and trusted arguments")
    try:
        arguments = sys.argv[2:]
        payload_input = None
        if arguments[0] == "--stdin-fd":
            payload_input = int(arguments[1])
            arguments = arguments[2:]
        if not arguments or arguments[0] != "--":
            raise ValueError("watchdog command delimiter is missing")
        status = run(arguments[1:], float(sys.argv[1]), payload_input=payload_input)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
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
