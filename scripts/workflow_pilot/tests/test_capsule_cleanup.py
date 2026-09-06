"""Owned-process cleanup preserves the primary execution failure."""

import errno
import os
import signal
import subprocess
import sys
import unittest
from unittest import mock

from scripts.workflow_pilot import sealed_capsule as capsule


@unittest.skipUnless(sys.platform == "linux", "Linux owned-child cleanup")
class CapsuleCleanupTests(unittest.TestCase):
    @staticmethod
    def cleanup_owned(child):
        try:
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            pass
        else:
            os.kill(child.pid, signal.SIGKILL)
            _, status = os.waitpid(child.pid, 0)
            child.returncode = os.waitstatus_to_exitcode(status)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()

    def start(self, owner, source="import time; time.sleep(30)"):
        child, _ = owner.start(
            [capsule.PYTHON, "-I", "-c", source],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=capsule.ENVIRONMENT, start_new_session=True)
        self.addCleanup(self.cleanup_owned, child)
        return child

    def assert_reaped(self, child):
        self.assertIsNotNone(child.returncode)
        with self.assertRaises(ChildProcessError):
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        self.assertTrue(child.stdout.closed and child.stderr.closed)

    def test_grace_timeout_is_successful_escalation_without_a_primary_error(self):
        with capsule._Child() as owner:
            owner.pipe()
            child = self.start(owner)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assert_reaped(child)

    def test_real_execution_timeout_survives_grace_timeout_and_fallback_reaping(self):
        with self.assertRaises(capsule.CapsuleError) as original:
            with capsule._Child() as owner:
                owner.pipe()
                child = self.start(owner)
                capsule._collect(child, 0.05, 1024, owner.abort)
        self.assertEqual(str(original.exception), "process timeout")
        self.assertNotIsInstance(original.exception.__cause__, subprocess.TimeoutExpired)
        self.assert_reaped(child)

    def test_real_output_limit_failure_survives_group_signal_failure(self):
        cleanup = PermissionError(errno.EPERM, "group signal failed")
        with mock.patch.object(capsule, "_kill_group", side_effect=cleanup):
            with self.assertRaises(capsule.CapsuleError) as original:
                with capsule._Child() as owner:
                    owner.pipe()
                    child = self.start(owner, "import os,time; os.write(1,b'x'*100); time.sleep(30)")
                    capsule._collect(child, 5, 1, owner.abort)
        self.assertEqual(str(original.exception), "process output exceeds limit")
        self.assertIs(original.exception.__cause__, cleanup)
        self.assert_reaped(child)

    def test_interruption_keeps_its_identity_while_group_failure_is_chained(self):
        primary = KeyboardInterrupt("original interruption")
        cleanup = PermissionError(errno.EPERM, "group signal failed")
        with mock.patch.object(capsule, "_kill_group", side_effect=cleanup):
            with self.assertRaises(KeyboardInterrupt) as original:
                with capsule._Child() as owner:
                    child = self.start(owner)
                    raise primary
        self.assertIs(original.exception, primary)
        self.assertIs(original.exception.__cause__, cleanup)
        self.assert_reaped(child)

    def test_group_failure_without_primary_still_pid_kills_and_reaps_before_raising(self):
        cleanup = PermissionError(errno.EPERM, "group signal failed")
        with mock.patch.object(capsule, "_kill_group", side_effect=cleanup):
            with self.assertRaises(PermissionError) as error:
                with capsule._Child() as owner:
                    child = self.start(owner)
        self.assertIs(error.exception, cleanup)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assert_reaped(child)

    def test_abort_failure_does_not_skip_pid_cleanup_or_mask_primary(self):
        primary = ValueError("original execution error")
        cleanup = OSError(errno.EIO, "liveness cleanup failed")
        with self.assertRaises(ValueError) as error:
            with capsule._Child() as owner:
                owner.pipe()
                child = self.start(owner)
                owner.abort = mock.Mock(side_effect=cleanup)
                raise primary
        self.assertIs(error.exception, primary)
        self.assertIs(error.exception.__cause__, cleanup)
        self.assert_reaped(child)

    def test_non_timeout_grace_error_still_reaps_and_is_chained(self):
        primary = ValueError("original execution error")
        cleanup = OSError(errno.EIO, "grace wait failed")
        calls = []
        with self.assertRaises(ValueError) as error:
            with capsule._Child() as owner:
                owner.pipe()
                child = self.start(owner)
                wait = child.wait

                def failing_once(*args, **kwargs):
                    calls.append(True)
                    if len(calls) == 1:
                        raise cleanup
                    return wait(*args, **kwargs)

                child.wait = failing_once
                raise primary
        self.assertIs(error.exception, primary)
        self.assertIs(error.exception.__cause__, cleanup)
        self.assertGreaterEqual(len(calls), 2)
        self.assert_reaped(child)

    def test_raw_worker_group_error_still_reaps_its_owned_pid(self):
        pid = os.fork()
        if pid == 0:
            try:
                while True:
                    signal.pause()
            finally:
                os._exit(0)
        cleanup = PermissionError(errno.EPERM, "worker group failed")
        try:
            with mock.patch.object(capsule, "_kill_group", side_effect=cleanup):
                with self.assertRaises(PermissionError) as error:
                    capsule._stop_worker(pid)
            self.assertIs(error.exception, cleanup)
            with self.assertRaises(ChildProcessError):
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        finally:
            if capsule._owns_pid(pid):
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)


if __name__ == "__main__":
    unittest.main()
