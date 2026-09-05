"""Trusted same-credential watchdog for the privileged namespace launcher."""

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


LIBC = ctypes.CDLL(None, use_errno=True)
TERMINATING = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)


class WatchdogInterrupted(RuntimeError):
    pass


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


def terminate(child):
    # The unreaped session leader pins its PID/group until the signal is sent.
    # The watchdog has the launcher's credentials, unlike the outer caller.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()
    while True:
        try:
            os.waitpid(-1, 0)
        except ChildProcessError:
            break


def interrupted(signum, frame):
    # selectors catches InterruptedError, including exceptions from handlers.
    raise WatchdogInterrupted(f"namespace watchdog interrupted by signal {signum}")


def run(argv, deadline, *, lifetime=0):
    if not argv or not math.isfinite(deadline) or deadline <= time.monotonic():
        raise ValueError("namespace watchdog requires a live aggregate deadline")
    if not stat.S_ISFIFO(os.fstat(lifetime).st_mode):
        raise ValueError("namespace watchdog requires a caller-owned lifetime pipe")
    ordinary_executable(argv[0])
    # Unsupported kernel lifecycle controls fail before launching anything.
    prctl(36, 1)  # PR_SET_CHILD_SUBREAPER; reap orphaned descendants as well.
    parent = os.getpid()
    child = None
    handlers = {}
    try:
        for sig in TERMINATING:
            handlers[sig] = signal.signal(sig, interrupted)
        with selectors.DefaultSelector() as selector:
            selector.register(lifetime, selectors.EVENT_READ)
            if selector.select(0):
                raise BrokenPipeError("caller lifetime ended before namespace launch")
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, TERMINATING)
            try:
                child = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL, close_fds=True,
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
    finally:
        try:
            if child is not None:
                for sig in TERMINATING:
                    signal.signal(sig, signal.SIG_IGN)
                terminate(child)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return child.returncode


def main():
    if not sys.flags.isolated or len(sys.argv) < 4 or sys.argv[2] != "--":
        raise SystemExit("namespace watchdog requires isolated Python and trusted arguments")
    try:
        status = run(sys.argv[3:], float(sys.argv[1]))
    except (OSError, ValueError, subprocess.SubprocessError, WatchdogInterrupted) as error:
        print(f"namespace watchdog: {error}", file=sys.stderr)
        return 125
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    raise SystemExit(main())
