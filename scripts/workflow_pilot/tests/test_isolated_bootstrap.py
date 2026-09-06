"""Real-process controls for the pre-capsule Git bootstrap lifecycle."""

import errno
import inspect
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from scripts.workflow_pilot import isolated_launcher as launcher


ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENT = {
    "LC_ALL": "C", "PATH": "/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0",
}


def descriptors():
    result = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            entry = os.fstat(int(name))
        except OSError:
            continue
        result[int(name)] = (entry.st_dev, entry.st_ino, entry.st_size)
    return result


@unittest.skipUnless(sys.platform == "linux", "Linux owned-child bootstrap supervision")
class BootstrapGitLifecycleTests(unittest.TestCase):
    def assert_reaped(self, process):
        self.assertIsNotNone(process.returncode)
        with self.assertRaises(ChildProcessError):
            os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_success_returns_exact_git_output_and_releases_owned_resources(self):
        before = descriptors()
        self.assertEqual(
            launcher._bootstrap_git(ROOT, ENVIRONMENT, "rev-parse", "--show-object-format"),
            b"sha1\n")
        self.assertEqual(descriptors(), before)

    def test_constructor_handoff_and_collection_setup_failures_reap_real_children(self):
        for boundary in ("constructor-signal", "handoff", "fileno", "deadline", "selector"):
            with self.subTest(boundary=boundary):
                before, children, fired = descriptors(), [], False
                old_trace = sys.gettrace()
                old_handler = signal.getsignal(signal.SIGINT)
                old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
                real_clock = time.monotonic

                class BrokenOutput:
                    def __init__(self, stream):
                        self.stream = stream

                    @property
                    def closed(self):
                        return self.stream.closed

                    def fileno(self):
                        raise RuntimeError("Git output descriptor setup failed")

                    def close(self):
                        self.stream.close()

                def trace(frame, event, value):
                    nonlocal fired
                    if (boundary == "constructor-signal" and not fired
                            and frame.f_code is subprocess.Popen.__init__.__code__
                            and event == "return"
                            and frame.f_locals.get("args", [None])[0] == "/usr/bin/git"):
                        children.append(frame.f_locals["self"])
                        fired = True
                        os.kill(os.getpid(), signal.SIGINT)
                    if (frame.f_code is launcher._BootstrapGitChild.start.__code__
                            and event == "return" and value is not None):
                        if not children:
                            children.append(value)
                        if boundary == "handoff":
                            fired = True
                            raise KeyboardInterrupt("Git handle handoff interrupted")
                        if boundary == "fileno":
                            fired = True
                            value.stdout = BrokenOutput(value.stdout)
                    return trace

                def clock():
                    nonlocal fired
                    if (boundary == "deadline" and not fired
                            and inspect.currentframe().f_back.f_code is launcher._bootstrap_git.__code__):
                        fired = True
                        raise RuntimeError("Git deadline setup failed")
                    return real_clock()

                def selector():
                    nonlocal fired
                    fired = True
                    raise RuntimeError("Git selector setup failed")

                try:
                    with mock.patch.object(time, "monotonic", new=clock):
                        with mock.patch.object(
                            launcher.selectors, "DefaultSelector",
                            new=selector if boundary == "selector" else launcher.selectors.DefaultSelector,
                        ):
                            sys.settrace(trace)
                            try:
                                with self.assertRaises((KeyboardInterrupt, RuntimeError)):
                                    launcher._bootstrap_git(
                                        ROOT, ENVIRONMENT, "rev-parse", "--show-object-format")
                            finally:
                                sys.settrace(old_trace)
                    self.assertTrue(fired)
                    self.assertEqual(len(children), 1)
                    self.assert_reaped(children[0])
                    self.assertEqual(descriptors(), before)
                    self.assertEqual(signal.getsignal(signal.SIGINT), old_handler)
                    self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), old_mask)
                finally:
                    sys.settrace(old_trace)
                    signal.signal(signal.SIGINT, old_handler)
                    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
                    for child in children:
                        try:
                            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                        except ChildProcessError:
                            pass
                        else:
                            os.kill(child.pid, signal.SIGKILL)
                            child.wait()
                        for stream in (child.stdout, child.stderr):
                            if stream is not None:
                                stream.close()

    def test_exited_group_race_reaps_and_preserves_the_real_timeout(self):
        self.check_cleanup_failure("timeout", ProcessLookupError(errno.ESRCH, "group exited"))

    def test_cleanup_error_reaps_and_preserves_the_real_output_limit_failure(self):
        self.check_cleanup_failure("output", PermissionError(errno.EPERM, "group signal rejected"))

    def check_cleanup_failure(self, failure, cleanup_error):
        before, children = descriptors(), []
        real_popen = subprocess.Popen
        real_clock = time.monotonic
        ticks = iter((0.0, 100.0))

        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        def clock():
            if failure == "timeout" and inspect.currentframe().f_back.f_code is launcher._bootstrap_git.__code__:
                return next(ticks, 100.0)
            return real_clock()

        def group_exited(pid, sig):
            self.assertEqual(sig, signal.SIGKILL)
            self.assertIsNotNone(os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT))
            raise cleanup_error

        try:
            with (mock.patch.object(subprocess, "Popen", side_effect=launch),
                  mock.patch.object(time, "monotonic", new=clock),
                  mock.patch.object(os, "killpg", side_effect=group_exited)):
                with self.assertRaises(ValueError) as original:
                    launcher._bootstrap_git(
                        ROOT, ENVIRONMENT, "rev-parse", "--show-object-format",
                        bound=1 if failure == "output" else 1024)
            self.assertEqual(str(original.exception), "sealed classifier Git bootstrap " +
                             ("timed out" if failure == "timeout" else "exceeds bound"))
            if failure == "output":
                self.assertIs(original.exception.__cause__, cleanup_error)
            self.assertEqual(len(children), 1)
            self.assert_reaped(children[0])
            self.assertEqual(descriptors(), before)
        finally:
            for child in children:
                if child.returncode is None:
                    child.wait(timeout=5)
                child.stdout.close()
                child.stderr.close()

    def test_reaped_child_never_causes_group_signals_or_another_wait(self):
        before = descriptors()
        with launcher._BootstrapGitChild() as owner:
            child = owner.start(["/usr/bin/git", "--version"], ENVIRONMENT)
            os.waitpid(child.pid, 0)
            self.assertIsNone(child.returncode)
            with (mock.patch.object(os, "killpg", side_effect=AssertionError) as group,
                  mock.patch.object(os, "kill", side_effect=AssertionError) as kill,
                  mock.patch.object(child, "wait", side_effect=AssertionError) as wait):
                owner.close()
                for operation in (group, kill, wait):
                    operation.assert_not_called()
        self.assertEqual(descriptors(), before)

    def test_missing_group_still_terminates_and_reaps_its_owned_live_child(self):
        before = descriptors()
        primary = ValueError("original bootstrap failure")
        with self.assertRaises(ValueError) as error:
            with launcher._BootstrapGitChild() as owner:
                child = owner.start(
                    [sys.executable, "-I", "-c", "import time; time.sleep(30)"], ENVIRONMENT)
                with mock.patch.object(os, "killpg", side_effect=ProcessLookupError(errno.ESRCH, "no group")):
                    try:
                        raise primary
                    finally:
                        owner.close()
        self.assertIs(error.exception, primary)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assert_reaped(child)
        self.assertEqual(descriptors(), before)

    def test_stream_cleanup_failure_cannot_mask_the_original_error_or_skip_other_streams(self):
        before = descriptors()
        primary = RuntimeError("original collection failure")
        cleanup_error = OSError(errno.EIO, "stream close failed")

        class FailingClose:
            def __init__(self, stream):
                self.stream = stream

            @property
            def closed(self):
                return self.stream.closed

            def close(self):
                self.stream.close()
                raise cleanup_error

        with self.assertRaises(RuntimeError) as error:
            with launcher._BootstrapGitChild() as owner:
                child = owner.start(["/usr/bin/git", "--version"], ENVIRONMENT)
                child.stdout = FailingClose(child.stdout)
                raise primary
        self.assertIs(error.exception, primary)
        self.assertIs(error.exception.__cause__, cleanup_error)
        self.assert_reaped(child)
        self.assertEqual(descriptors(), before)

    def test_unsuccessful_git_exit_is_rejected_without_open_streams(self):
        before = descriptors()
        with self.assertRaisesRegex(ValueError, "locally available exact Git authority"):
            launcher._bootstrap_git(ROOT, ENVIRONMENT, "not-a-git-subcommand")
        self.assertEqual(descriptors(), before)


if __name__ == "__main__":
    unittest.main()
