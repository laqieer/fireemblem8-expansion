import errno
import json
import os
import signal
import stat
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.validation_ownership import private_install
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.make_probe import ProbeSession
from scripts.validation_ownership.python_commands import python_command
from scripts.validation_ownership.tests import test_foundation as foundation


ROOT = Path(__file__).resolve().parents[3]


class PrivateInstallCleanupTests(unittest.TestCase):
    """Actual install owners and supervisor teardown with inert descriptors."""

    def setUp(self):
        from scripts.validation_ownership import lifecycle, syscall_guard
        self.guard = syscall_guard
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.attempted, self.live, self.faults = [], set(), {}

        def close(descriptor):
            self.assertIn(descriptor, self.live)
            self.assertNotIn(descriptor, self.attempted, "ambiguous close was retried")
            self.attempted.append(descriptor)
            error, after = self.faults.get(descriptor, (None, False))
            if error is not None and not after:
                raise error
            self.live.remove(descriptor)
            if error is not None:
                raise error

        self.os = SimpleNamespace(
            close=close, O_RDONLY=os.O_RDONLY, O_DIRECTORY=os.O_DIRECTORY,
            O_NOFOLLOW=os.O_NOFOLLOW, O_CLOEXEC=os.O_CLOEXEC, O_PATH=os.O_PATH,
            open=Mock(side_effect=AssertionError("unmodeled open")),
            stat=Mock(side_effect=AssertionError("unmodeled stat")),
            fstat=Mock(side_effect=AssertionError("unmodeled fstat")),
            dup=Mock(side_effect=AssertionError("unmodeled dup")),
        )
        self.stack.enter_context(patch.object(syscall_guard, "os", self.os))
        self.stack.enter_context(patch.object(lifecycle, "signal", SimpleNamespace(
            SIG_BLOCK=signal.SIG_BLOCK, SIG_SETMASK=signal.SIG_SETMASK,
            pthread_sigmask=lambda *args: set(), sigpending=lambda: set(),
        )))
        self.info = SimpleNamespace(
            st_dev=1, st_ino=2, st_mode=stat.S_IFREG | 0o600, st_size=4,
            st_mtime_ns=5, st_ctime_ns=6, st_nlink=1,
        )
        self.parent = SimpleNamespace(st_dev=1, st_ino=3, st_mode=stat.S_IFDIR | 0o700)

    def own(self, *descriptors, faults=(), after=False):
        self.attempted.clear()
        self.live = set(descriptors)
        self.faults = {
            descriptor: (OSError(errno.EIO, "inert descriptor " + str(descriptor)), after)
            for descriptor in faults
        }

    def pending(self, source=101, parent=102):
        return self.guard._PendingInstall(
            "/work/out/temp", "/work/out/result", self.guard.publication_identity(self.info),
            "/work/out", parent, source, ((1, "temp"), (2, "result")), 1,
        )

    def assert_errors(self, primary, descriptors):
        notes = getattr(primary, "cleanup_errors", ())
        self.assertEqual(len(notes), len(descriptors))
        for descriptor in descriptors:
            self.assertTrue(any(str(self.faults[descriptor][0]) in note for note in notes), notes)

    def test_pending_and_parent_pins_attempt_every_close_once(self):
        for owner in ("pending", "parents"):
            for faults in ((), (101,), (102,), (101, 102)):
                for after in (False, True):
                    with self.subTest(owner=owner, faults=faults, after=after):
                        self.own(101, 102, faults=faults, after=after)
                        pending = self.pending()
                        policy = SimpleNamespace(install_parent_fds={"/work": 101, "/work/out": 102})
                        close = pending.close if owner == "pending" else lambda: (
                            self.guard.Policy.close_private_install_parents(policy)
                        )
                        if faults:
                            with self.assertRaises(OSError) as caught:
                                close()
                            self.assertIs(caught.exception, self.faults[faults[0]][0])
                            self.assert_errors(caught.exception, faults[1:])
                        else:
                            close()
                        self.assertEqual(self.attempted, [101, 102])
                        if owner == "pending":
                            self.assertEqual((pending.source_fd, pending.parent_fd), (-1, -1))
                        else:
                            self.assertEqual(policy.install_parent_fds, {})
                        close()
                        self.assertEqual(self.attempted, [101, 102])
                        self.assertEqual(self.live, set(faults) if not after else set())

    def test_process_withdraws_pending_and_pidfd_before_all_attempt_cleanup(self):
        for faults in ((), (101,), (103,), (101, 102, 103)):
            for active in (False, True):
                with self.subTest(faults=faults, active=active):
                    self.own(101, 102, 103, faults=faults)
                    pending = self.pending()
                    process = self.guard.Process("command", pending=("private-install", pending), pidfd=103)
                    primary = self.guard.Violation("original rejection") if active else None
                    if faults and primary is None:
                        with self.assertRaises(OSError) as caught:
                            process.close()
                        self.assertIs(caught.exception, self.faults[faults[0]][0])
                        self.assert_errors(caught.exception, faults[1:])
                    else:
                        process.close(primary=primary)
                        if active:
                            self.assert_errors(primary, faults)
                    self.assertEqual(self.attempted, [101, 102, 103])
                    self.assertIsNone(process.pending)
                    self.assertEqual((process.pidfd, pending.source_fd, pending.parent_fd), (-1, -1, -1))
                    process.close()
                    pending.close()
                    self.assertEqual(self.attempted, [101, 102, 103])
        self.own(103)
        process = self.guard.Process("command", pending=("read", 8), pidfd=103)
        process.close()
        self.assertEqual(process.pending, ("read", 8))
        self.assertEqual(self.attempted, [103])

    def install_policy(self):
        return SimpleNamespace(
            private_install=SimpleNamespace(destinations=("/work/out/result",), scope="owned/launch"),
            mode="command", processes={9: object()}, newborn_stops={}, install_attempts=set(),
            install_completed=set(), install_parents={"/work/out": (1, 3, stat.S_IFDIR | 0o700)},
            _install_path=Mock(side_effect=[
                ("/work/out/temp", (1, "temp")), ("/work/out/result", (2, "result")),
            ]),
            _install_parent=Mock(return_value=102), _install_aliases=Mock(),
            reserve_creation=Mock(), observe=Mock(),
        )

    def test_acquisition_rejection_keeps_primary_and_every_acquired_close(self):
        for phase in ("destination", "open", "source", "aliases", "reservation", "success"):
            for faults in ((), (101, 102)):
                with self.subTest(phase=phase, faults=faults):
                    acquired = (102,) if phase in ("destination", "open") else (101, 102)
                    self.own(*acquired, faults=tuple(fd for fd in faults if fd in acquired))
                    primary = self.guard.Violation("original acquisition rejection")
                    policy, state = self.install_policy(), self.guard.Process("command", bootstrap=False)
                    self.os.stat.side_effect = None if phase == "destination" else FileNotFoundError()
                    self.os.open.side_effect = primary if phase == "open" else None
                    self.os.open.return_value = 101
                    self.os.fstat.side_effect = None
                    self.os.fstat.return_value = (
                        SimpleNamespace(**{**vars(self.info), "st_nlink": 2}) if phase == "source" else self.info
                    )
                    if phase == "aliases":
                        policy._install_aliases.side_effect = primary
                    if phase == "reservation":
                        policy.reserve_creation.side_effect = primary
                    registers = SimpleNamespace(orig_rax=82, rdi=1, rsi=2)
                    if phase == "success":
                        self.guard.Policy.prepare_private_install(policy, 9, state, registers)
                        self.assertEqual(self.attempted, [])
                        self.assertEqual(policy.install_attempts, {"/work/out/result"})
                        self.assertEqual((state.pending[1].source_fd, state.pending[1].parent_fd), (101, 102))
                        state.close(primary=primary)
                    else:
                        with self.assertRaises(self.guard.Violation) as caught:
                            self.guard.Policy.prepare_private_install(policy, 9, state, registers)
                        if phase in ("open", "aliases", "reservation"):
                            self.assertIs(caught.exception, primary)
                        self.assert_errors(caught.exception, tuple(fd for fd in faults if fd in acquired))
                        self.assertIsNone(state.pending)
                        self.assertFalse(policy.install_attempts)
                    self.assertEqual(self.attempted, list(acquired))

    def test_parent_identity_rejection_is_not_masked_by_temporary_close(self):
        self.own(101, 102, faults=(102,))
        policy = SimpleNamespace(
            install_parents={"/work": (1, 3, stat.S_IFDIR | 0o700)},
            install_parent_fds={"/work": 101}, config={"root": "/inert"},
        )
        self.os.open.side_effect, self.os.open.return_value = None, 102
        self.os.fstat.side_effect = [self.parent, self.info]
        with self.assertRaises(self.guard.Violation) as caught:
            self.guard.Policy._install_parent(policy, "/work")
        self.assertEqual(self.attempted, [102])
        self.assertEqual(policy.install_parent_fds, {"/work": 101})
        self.assert_errors(caught.exception, (102,))
        self.os.dup.assert_not_called()

    def test_completion_keeps_rejection_and_both_close_failures(self):
        for phase in ("pathname", "parent", "observation", "success", "failed-syscall", "invalid-status"):
            for faults in ((), (101,), (101, 102)):
                with self.subTest(phase=phase, faults=faults):
                    self.own(101, 102, 103, faults=faults)
                    pending, policy = self.pending(), self.install_policy()
                    policy._install_parent.return_value = 103
                    primary = self.guard.Violation("original completion rejection")
                    self.os.fstat.side_effect = lambda fd: self.parent if fd == 102 else self.info
                    self.os.stat.side_effect = lambda name, **kwargs: (
                        self.info if name == "result" else (_ for _ in ()).throw(FileNotFoundError())
                    )
                    if phase == "parent":
                        policy.install_parents["/work/out"] = (1, 99, self.parent.st_mode)
                    if phase == "observation":
                        policy.observe.side_effect = primary
                    outcome = -errno.ENOENT if phase == "failed-syscall" else 1 if phase == "invalid-status" else 0
                    with patch.object(self.guard, "cstring", side_effect=(
                        primary if phase == "pathname" else lambda pid, pointer: {1: "temp", 2: "result"}[pointer]
                    )):
                        if phase not in ("success", "failed-syscall"):
                            with self.assertRaises(self.guard.Violation) as caught:
                                self.guard.Policy.finish_private_install(policy, 9, pending, outcome)
                            if phase in ("pathname", "observation"):
                                self.assertIs(caught.exception, primary)
                            self.assert_errors(caught.exception, faults)
                        elif faults:
                            with self.assertRaises(OSError) as caught:
                                self.guard.Policy.finish_private_install(policy, 9, pending, outcome)
                            self.assertIs(caught.exception, self.faults[101][0])
                            self.assert_errors(caught.exception, faults[1:])
                        else:
                            self.guard.Policy.finish_private_install(policy, 9, pending, outcome)
                    expected = [101, 102] if phase in ("pathname", "parent") else [103, 101, 102]
                    self.assertEqual(self.attempted, expected)
                    self.assertEqual((pending.source_fd, pending.parent_fd), (-1, -1))
                    pending.close()
                    self.assertEqual(self.attempted, expected)
                    if phase in ("success", "failed-syscall"):
                        row = json.loads(policy.observe.call_args.args[1].split(":", 1)[1])
                        self.assertEqual(row["result"], outcome)
                        self.assertEqual(row["identity"], list(pending.source_identity) if outcome == 0 else None)

    def supervised(self, *, phase="setup", faults=(), no_children=False, reporting=False,
                   main_status=0, child_status=0, after=False, mode=None, metadata=False,
                   toolchain_stage=None, main_state=None, child_state=None, intermediate_failure=False):
        guard = self.guard
        descriptors = (90, 101, 102, 103, 201, 202, 203, 401, 402) + ((303,) if phase != "success" else ())
        self.own(*descriptors, faults=faults, after=after)
        primary = guard.Violation("original terminal rejection")
        reporting_error = OSError(errno.ENOSPC, "inert report failure")
        events, reports, records, waited, cleanup_primaries = [], [], [], [], []
        policy = SimpleNamespace(
            toolchain=None if toolchain_stage is None else {"stage": toolchain_stage, "stdin": False},
            toolchain_intermediate=SimpleNamespace(
                birth=Mock(), exited=Mock(side_effect=primary if intermediate_failure else None),
                emit=Mock(), close=Mock(),
            ),
            filter_kernel=None, header_runtime=object(), directory_installs=None,
            read_trace=None, source_effects=None, journal_receipts=None, private_install=None,
            file_cleanup_enabled=False,
            producer_requests=(), consumed=set(), code_consumed=set(), accessed=set(), events=[],
            total_processes=0, live_process_peak=0, calls=0, written=0, created=0, memory_peak=0,
            observation_bytes=0, observation_attempts={}, metadata=[], install_parent_fds={},
            charge_metadata=Mock(), closed_processes=set(),
            retire_job=Mock(side_effect=primary if phase == "exit" else None),
            reserve_memory=Mock(side_effect=primary if phase == "setup" else None),
        )
        def account():
            first = policy.processes[9]
            first.pending = ("private-install", self.pending())
            second = guard.Process("command", pidfd=203, pending=("private-install", self.pending(201, 202)))
            for record, changes in ((first, main_state), (second, child_state)):
                for name, value in ({} if changes is None else changes).items():
                    setattr(record, name, value)
            policy.processes[10] = second
            if phase != "success":
                policy.newborn_stops[11] = 303
            records.extend((first, second))
        policy.account_processes = account
        policy.pin_private_install_parents = lambda: policy.install_parent_fds.update({"/work": 401, "/work/out": 402})
        close_parents = guard.Policy.close_private_install_parents
        policy.close_private_install_parents = lambda **kwargs: close_parents(policy, **kwargs)
        def terminal(code):
            return ("exit", code) if code >= 0 else ("signal", -code)
        waits = iter([(9, ("stop", 19)), (9, terminal(main_status)),
                      (10, terminal(child_status)), (11, ("exit", 0))])
        def waitpid(pid, flags):
            events.append(("wait", pid))
            waited.append(pid)
            self.assertLessEqual(len(waited), 5, "inert reaper repeated its finite wait inventory")
            if no_children and len(waited) > (2 if phase == "exit" else 1):
                raise ChildProcessError()
            return next(waits)
        for name, function in {
            "getpid": lambda: 8, "pidfd_open": lambda pid: 90 if pid == 8 else 103,
            "fork": lambda: 9, "waitpid": waitpid, "WIFSTOPPED": lambda status: status[0] == "stop",
            "WIFEXITED": lambda status: status[0] == "exit", "WIFSIGNALED": lambda status: status[0] == "signal",
            "WSTOPSIG": lambda status: status[1],
            "waitstatus_to_exitcode": lambda status: -status[1] if status[0] == "signal" else status[1],
        }.items():
            setattr(self.os, name, function)
        self.os.WNOHANG = 1
        def write_report(data, **kwargs):
            reports.append(json.loads(data))
            if reporting:
                raise reporting_error
        paths = Mock(return_value=SimpleNamespace(read_text=lambda: "", write_text=write_report))
        signals = SimpleNamespace(
            SIGKILL=9, SIGSTOP=19, SIGTRAP=5,
            pidfd_send_signal=lambda *args: events.append(("signal", *args)),
        )
        config = {name: 10 for name in (
            "descendant_limit", "syscall_limit", "write_limit", "creation_limit",
            "observation_count", "observation_limit", "process_limit", "memory_limit",
        )}
        config.update(mode="make" if phase == "exit" else "command", deadline=130, report="/inert/report")
        if mode is not None:
            config["mode"] = mode
        if metadata:
            config["metadata_validation"] = True
        cleanup = guard.finish_cleanup
        def observe_cleanup(actions, **kwargs):
            if kwargs.get("primary") is not None:
                cleanup_primaries.append(kwargs["primary"])
            return cleanup(actions, **kwargs)
        failure = result = None
        with patch.object(guard, "Policy", return_value=policy), \
             patch.object(guard, "Path", paths), patch.object(guard, "signal", signals), \
             patch.object(guard, "ptrace", return_value=0), \
             patch.object(guard, "resource", SimpleNamespace(prlimit=lambda *args: None)), \
             patch.object(guard, "time", SimpleNamespace(monotonic=lambda: 100)), \
             patch.object(guard, "encode_metadata_transport", return_value={}), \
             patch.object(guard, "finish_cleanup", side_effect=observe_cleanup):
            try:
                result = guard.supervise(config, lambda: self.fail("child execution"))
            except BaseException as error:
                failure = error
        return SimpleNamespace(
            result=result, failure=failure, primary=primary, reporting_error=reporting_error,
            reports=reports, events=events, policy=policy, records=records,
            cleanup_primaries=cleanup_primaries, owned=descriptors,
        )

    def test_supervisor_reaps_every_owner_and_preserves_terminal_and_report_errors(self):
        for phase in ("setup", "exit", "success"):
            for no_children in (False, True):
                if phase == "success" and no_children:
                    continue
                for reporting in (False, True):
                    faults = (101, 102, 103, 201, 202, 203, 401, 402)
                    with self.subTest(phase=phase, no_children=no_children, reporting=reporting):
                        value = self.supervised(phase=phase, faults=faults, no_children=no_children, reporting=reporting)
                        self.assertIsNone(value.failure)
                        self.assertEqual(value.result, 125)
                        self.assertEqual(set(self.attempted), set(self.live) | {90, 303} if phase != "success"
                                         else set(self.live) | {90})
                        self.assertEqual(len(self.attempted), len(set(self.attempted)))
                        self.assertFalse(value.policy.processes)
                        self.assertFalse(value.policy.newborn_stops)
                        self.assertFalse(value.policy.install_parent_fds)
                        self.assertTrue(all(record.pidfd == -1 and record.pending is None for record in value.records))
                        failure = self.faults[101][0] if phase == "success" else value.primary
                        expected = faults[1:] if phase == "success" else faults
                        notes = getattr(failure, "cleanup_errors", ())
                        self.assertEqual(len(notes), len(expected) + reporting)
                        for fd in expected:
                            self.assertTrue(any(str(self.faults[fd][0]) in note for note in notes), notes)
                        if reporting:
                            self.assertTrue(any(str(value.reporting_error) in note for note in notes), notes)
                        self.assertEqual(value.reports[0]["error"], str(failure))
                        self.assertFalse(value.reports[0]["ok"])

    def test_supervisor_normal_and_cleanup_only_failures_do_not_publish_false_success(self):
        value = self.supervised(phase="success")
        self.assertIsNone(value.failure)
        self.assertEqual(value.result, 0)
        self.assertTrue(value.reports[0]["ok"])
        self.assertFalse(self.live)
        for reporting in (False, True):
            value = self.supervised(phase="success", faults=(401, 402), reporting=reporting)
            self.assertIs(value.failure, self.faults[401][0])
            self.assertEqual(len(value.failure.cleanup_errors), 1 + reporting)
            self.assertEqual(set(self.attempted), {90, 101, 102, 103, 201, 202, 203, 401, 402})
            self.assertFalse(value.reports[0]["ok"])
            self.assertEqual(value.reports[0]["error"], str(self.faults[401][0]))

    def assert_terminal_cleanup(self, value, faults, after):
        self.assertIsNone(value.failure)
        self.assertEqual(set(self.attempted), set(value.owned))
        self.assertEqual(len(self.attempted), len(value.owned))
        self.assertEqual(self.live, set() if after else set(faults))
        self.assertFalse(value.policy.processes)
        self.assertFalse(value.policy.newborn_stops)
        self.assertFalse(value.policy.install_parent_fds)
        for record in value.records:
            self.assertIsNone(record.pending)
            self.assertEqual(record.pidfd, -1)
            record.close()
        self.assertEqual(len(self.attempted), len(value.owned))
        self.assertEqual(len(value.reports), 1)

    def test_supervisor_observed_terminal_status_and_rejection_precede_close_faults(self):
        for status in (0, 7, -9):
            for faults in ((), (103,), (101, 102, 103)):
                for after in (False, True):
                    with self.subTest(status=status, faults=faults, after=after):
                        value = self.supervised(phase="success", main_status=status, faults=faults, after=after)
                        self.assert_terminal_cleanup(value, faults, after)
                        report = value.reports[0]
                        self.assertEqual(report["returncode"], status)
                        self.assertEqual(value.result, 125 if status or faults else 0)
                        self.assertEqual(report["ok"], not (status or faults))
                        if status:
                            first = value.cleanup_primaries[-1]
                            self.assertIsInstance(first, self.guard.Violation)
                            self.assertEqual(str(first), f"sandbox process exited unsuccessfully: {status}")
                            self.assert_errors(first, faults)
                        elif faults:
                            first = self.faults[faults[0]][0]
                            self.assertIs(value.cleanup_primaries[-1], first)
                            self.assert_errors(first, faults[1:])
                        else:
                            first = None
                            self.assertEqual(value.cleanup_primaries, [])
                        self.assertEqual(report["error"], None if first is None else str(first))

    def test_supervisor_terminal_status_exceptions_keep_their_exact_scope(self):
        helper = {
            "role": "helper", "toolchain_status": 1,
            "toolchain_status_queried": True, "producer_event_written": True,
        }
        cases = [
            ({"mode": "make", "main_status": code}, True, code, 103) for code in (7, -9)
        ] + [
            ({"metadata": True, "main_status": code}, code in (1, 2), code, 103)
            for code in (1, 2, 7, -9)
        ] + [
            ({"mode": "compile", "toolchain_stage": stage, "main_status": code}, True, code, 103)
            for stage in (0, 4) for code in (7, -9)
        ] + [
            ({"mode": "compile", "main_status": 1}, False, 1, 103),
            ({"mode": "make", "child_status": 7}, False, 7, 203),
            ({"metadata": True, "child_status": 1}, False, 1, 203),
            ({"mode": "compile", "toolchain_stage": 4, "child_status": 7}, True, 7, 203),
            ({"child_status": 1, "child_state": helper}, True, 1, 203),
        ] + [
            ({"child_status": 1, "child_state": {**helper, key: wrong}}, False, 1, 203)
            for key, wrong in (("role", "command"), ("toolchain_status", 0),
                               ("toolchain_status_queried", False), ("producer_event_written", False))
        ] + [
            ({"child_status": code, "child_state": {**helper, "toolchain_status": code}}, False, code, 203)
            for code in (2, -9)
        ]
        for options, allowed, code, pin in cases:
            for faults, after in (((), False), ((pin,), False), ((pin,), True)):
                with self.subTest(options=options, faults=faults, after=after):
                    value = self.supervised(phase="success", faults=faults, after=after, **options)
                    self.assert_terminal_cleanup(value, faults, after)
                    report = value.reports[0]
                    self.assertEqual(report["returncode"], options.get("main_status", 0))
                    self.assertEqual(report["ok"], allowed and not faults)
                    self.assertEqual(value.result, 0 if allowed and not faults else 125)
                    if not allowed:
                        first = value.cleanup_primaries[-1]
                        self.assertIsInstance(first, self.guard.Violation)
                        self.assertEqual(str(first), f"sandbox process exited unsuccessfully: {code}")
                        self.assert_errors(first, faults)
                    elif faults:
                        first = self.faults[pin][0]
                        self.assertIs(value.cleanup_primaries[-1], first)
                    else:
                        first = None
                        self.assertEqual(value.cleanup_primaries, [])
                    self.assertEqual(report["error"], None if first is None else str(first))
                    if options.get("toolchain_stage") == 4:
                        value.policy.toolchain_intermediate.close.assert_called_once()
                        if allowed and not faults:
                            value.policy.toolchain_intermediate.emit.assert_called_once_with(
                                options.get("main_status", 0),
                            )
                        else:
                            value.policy.toolchain_intermediate.emit.assert_not_called()

    def test_supervisor_terminal_or_earlier_validation_failure_remains_primary(self):
        faults = (101, 102, 103, 201, 202, 203, 401, 402)
        for status in (0, 7, -9):
            for earlier in ("retirement", "source-validation", None):
                if status == 0 and earlier is None:
                    continue
                for after in (False, True):
                    with self.subTest(status=status, earlier=earlier, after=after):
                        value = self.supervised(
                            phase="exit" if earlier == "retirement" else "success",
                            main_status=status, faults=faults, after=after, reporting=True,
                            toolchain_stage=4 if earlier == "source-validation" else None,
                            intermediate_failure=earlier == "source-validation",
                        )
                        self.assert_terminal_cleanup(value, faults, after)
                        self.assertEqual(value.result, 125)
                        first = value.cleanup_primaries[-1]
                        if earlier is not None:
                            self.assertIs(first, value.primary)
                        else:
                            self.assertIsInstance(first, self.guard.Violation)
                            self.assertEqual(str(first), f"sandbox process exited unsuccessfully: {status}")
                        self.assertEqual(len(first.cleanup_errors), len(faults) + 1)
                        for error in (*[self.faults[pin][0] for pin in faults], value.reporting_error):
                            self.assertTrue(any(str(error) in note for note in first.cleanup_errors))
                        self.assertEqual(value.reports[0]["returncode"], status)
                        self.assertEqual(value.reports[0]["error"], str(first))
                        self.assertFalse(value.reports[0]["ok"])

    def test_supervisor_unfulfilled_helper_rejection_precedes_close(self):
        for status in (0, 1, -9):
            for after in (False, True):
                with self.subTest(status=status, after=after):
                    value = self.supervised(
                        phase="success", main_status=status, toolchain_stage=0,
                        main_state={"producer_requested": True}, faults=(103,), after=after,
                    )
                    self.assert_terminal_cleanup(value, (103,), after)
                    self.assertEqual(value.reports[0]["returncode"], status)
                    self.assertEqual(value.result, 125)
                    first = value.cleanup_primaries[-1]
                    self.assertIsInstance(first, self.guard.Violation)
                    self.assertEqual(str(first), "parked or unfulfilled producer helper exited")
                    self.assert_errors(first, (103,))
                    self.assertEqual(value.reports[0]["error"], str(first))


class PrivateInstallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        witness = "scripts/generated_data/chapterbundle/__init__.py"
        self.assertEqual((ROOT / witness).read_bytes(), b"")
        self.fixture.add(witness, b"")

    def tearDown(self):
        self.fixture.tearDown()

    def assert_clean(self, session):
        self.fixture.assert_clean(session)
        self.assertFalse(session._private_install_commands)
        self.assertFalse(session._private_install_launches)
        self.assertFalse(session._private_install_issued)
        self.assertFalse(session._private_install_launch_issued)

    def command(self, session, body, *, destinations=("out/result",), outputs=("out/result",), issued=True):
        command = python_command(session, body, outputs=outputs)
        return session._private_install_command(command, destinations) if issued else command

    def test_real_text_installs_have_exact_native_outcomes_and_default_denial(self):
        for name in ("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"):
            self.fixture.add(name, (ROOT / name).read_bytes())
        self.fixture.add("text.txt", "#0001\n[X][Y][X]\n#0002\n[Y][X][Y]\n")
        self.fixture.add("defs.txt", "[X] = 0\n[Y] = 1\n")
        body = (
            "sys.path.insert(0,'/repo/scripts/texttools');import textprocess;"
            "sys.argv[0]='scripts/texttools/textprocess.py';"
            "textprocess.main(['/repo/text.txt','/repo/defs.txt','/work/out/data.c','/work/out/header.h','utf8'])"
        )
        outputs = ("out/data.c", "out/header.h")
        with self.fixture.session() as session:
            command = python_command(
                session, body, sources=("defs.txt", "text.txt"), outputs=outputs,
                code=("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"),
            )
            session._private_install_command(command, outputs)
            recorded = []
            sandbox = session._sandbox_run

            def capture(root, **options):
                result, observed = sandbox(root, **options)
                recorded.extend(observed["private_install_records"])
                return result, observed

            with patch.object(session, "_sandbox_run", capture):
                result = session.command(command)
            generated = {item.path: item for item in result.generated}
            self.assertEqual(set(generated), set(outputs))
            self.assertTrue(generated["out/data.c"].data.startswith(b'#include "global.h"'))
            self.assertIn(b"#define MSG_001 0x0001", generated["out/header.h"].data)
            self.assertEqual(set(result.consumed), {"defs.txt", "text.txt"})
            self.assertEqual(set(result.code_consumed),
                             {"scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"})
            self.assertEqual([item.sequence for item in recorded], [1, 2])
            self.assertEqual([item.destination for item in recorded], ["/work/out/header.h", "/work/out/data.c"])
            self.assertTrue(all(item.result == 0 and item.identity[6] == 1 for item in recorded))
            self.assertFalse(session._private_install_launches)
        self.assert_clean(session)
        with self.fixture.session() as denied:
            command = python_command(
                denied, "import os;" + "os.mkdir('/work/out');" + body,
                sources=("defs.txt", "text.txt"), outputs=outputs,
                code=("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"),
            )
            with self.assertRaisesRegex(MakeProbeError, "directory-entry relocation"):
                denied.command(command)
        self.assert_clean(denied)

    def test_private_install_uses_actual_absolute_relative_and_dirfd_lookups(self):
        prefix = "import os\nwith open('/work/out/temp','wb') as target: target.write(b'actual')\n"
        for action in (
            "os.replace('/work/out/temp','/work/out/result')\n",
            "os.chdir('/work/out');os.replace('temp','result')\n",
            "directory=os.open('/work/out',os.O_RDONLY|os.O_DIRECTORY)\n"
            "try: os.replace('temp','result',src_dir_fd=directory,dst_dir_fd=directory)\n"
            "finally: os.close(directory)\n",
        ):
            with self.subTest(action=action):
                with self.fixture.session() as session:
                    result = session.command(self.command(session, prefix + action))
                    self.assertEqual([(item.path, item.data) for item in result.generated], [("out/result", b"actual")])
                self.assert_clean(session)

    def test_private_install_rejects_path_type_alias_actor_and_mapping_changes(self):
        prefix = "import os\nwith open('/work/out/temp','wb') as target: target.write(b'actual')\n"
        attempts = (
            ("existing", prefix + "open('/work/out/result','wb').close()\nos.replace('/work/out/temp','/work/out/result')"),
            ("directory", "import os\nos.mkdir('/work/out/temp')\nos.replace('/work/out/temp','/work/out/result')"),
            ("hardlink", prefix + "os.link('/work/out/temp','/work/out/alias')\nos.replace('/work/out/temp','/work/out/result')"),
            ("open-fd", prefix + "descriptor=os.open('/work/out/temp',os.O_RDONLY)\nos.replace('/work/out/temp','/work/out/result')"),
            ("duplicated-fd", prefix + "descriptor=os.open('/work/out/temp',os.O_RDONLY)\n"
             "alias=os.dup(descriptor);os.close(descriptor)\nos.replace('/work/out/temp','/work/out/result')"),
            ("cross-parent", prefix + "os.replace('/work/out/temp','/work/result')"),
            ("unlisted", prefix + "os.replace('/work/out/temp','/work/out/other')"),
            ("outside-work", prefix + "os.replace('/work/out/temp','/repo/result')"),
            ("parent-spelling", prefix + "os.replace('/work/out/../out/temp','/work/out/result')"),
            ("symlink", prefix + "os.symlink('temp','/work/out/alias')\nos.replace('/work/out/alias','/work/out/result')"),
            ("changed-parent", "import os\nos.rmdir('/work/out');os.mkdir('/work/out')\n"
             "open('/work/out/temp','wb').close()\nos.replace('/work/out/temp','/work/out/result')"),
            ("fork", prefix + "os.fork()\nos.replace('/work/out/temp','/work/out/result')"),
            ("repeated", prefix + "os.replace('/work/out/temp','/work/out/result')\n"
             "os.unlink('/work/out/result');open('/work/out/temp','wb').close()\n"
             "os.replace('/work/out/temp','/work/out/result')"),
            ("mapped", prefix + "import mmap\nsource=open('/work/out/temp','rb')\n"
             "mapping=mmap.mmap(source.fileno(),0,access=mmap.ACCESS_READ)\n"
             "source.close()\nos.replace('/work/out/temp','/work/out/result')"),
            ("flags", prefix + "import ctypes\nlibc=ctypes.CDLL(None)\n"
             "libc.renameat2(-100,b'/work/out/temp',-100,b'/work/out/result',1)\n"),
        )
        for name, program in attempts:
            with self.subTest(name=name):
                with self.fixture.session() as session:
                    with self.assertRaises(MakeProbeError):
                        session.command(self.command(session, program))
                self.assert_clean(session)

    def test_equal_cloned_foreign_mutated_and_expired_commands_do_not_inherit_grants(self):
        body = (
            "import os\nos.makedirs('/work/out',exist_ok=True)\n"
            "with open('/work/out/temp','wb') as target: target.write(b'actual')\n"
            "os.replace('/work/out/temp','/work/out/result')\n"
        )
        with self.fixture.session() as session:
            issued = self.command(session, body)
            clone = replace(issued)
            self.assertEqual(clone, issued)
            with self.assertRaisesRegex(MakeProbeError, "directory-entry relocation"):
                session.command(clone)
        self.assert_clean(session)
        with self.fixture.session() as foreign:
            with self.assertRaisesRegex(MakeProbeError, "directory-entry relocation"):
                foreign.command(issued)
        self.assert_clean(foreign)
        with self.fixture.session() as modified:
            command = self.command(modified, body)
            object.__setattr__(command, "argv", (*command.argv[:-1], command.argv[-1] + "\n# changed"))
            with self.assertRaisesRegex(MakeProbeError, "changed|issued"):
                modified.command(command)
        self.assert_clean(modified)
        with self.fixture.session() as forged:
            command = self.command(forged, body)
            record = forged._private_install_commands[id(command)]
            forged._private_install_commands[id(command)] = replace(record)
            with self.assertRaisesRegex(MakeProbeError, "issued"):
                forged.command(command)
        self.assert_clean(forged)
        with self.fixture.session() as expired:
            command = self.command(expired, body)
        self.assert_clean(expired)
        with self.assertRaisesRegex(MakeProbeError, "not active"):
            expired.command(command)

    def test_private_install_cannot_cross_actual_views_or_replay_a_launch(self):
        body = (
            "import os\nwith open('/work/out/temp','wb') as target: target.write(b'actual')\n"
            "os.replace('/work/out/temp','/work/out/result')\n"
        )
        budget = ProbeBudget()
        base = self.fixture.capture_view(budget)
        self.fixture.add("other.txt", "different selected view\n")
        current = self.fixture.capture_view(budget)
        with ProbeSession(current, scratch_root=self.fixture.scratch, budget=budget) as session:
            first = self.command(session, body)
            with session.select_view(base):
                with self.assertRaisesRegex(MakeProbeError, "issued view"):
                    session._require_private_install(first)
            with self.assertRaisesRegex(MakeProbeError, "issued view"):
                session._require_private_install(first)
            fresh = self.command(session, body)
            sandbox = session._sandbox_run
            replays = []

            def attempted_replay(root, **options):
                result = sandbox(root, **options)
                before = budget.runs
                with self.assertRaisesRegex(MakeProbeError, "forged|consumed"):
                    sandbox(root, **options)
                self.assertEqual(budget.runs, before)
                replays.append(True)
                return result

            with patch.object(session, "_sandbox_run", attempted_replay):
                result = session.command(fresh)
            self.assertEqual(result.generated[0].data, b"actual")
            self.assertEqual(replays, [True])
        self.assert_clean(session)

    def test_forged_launch_and_missing_native_install_outcomes_reject(self):
        body = (
            "import os\nwith open('/work/out/temp','wb') as target: target.write(b'actual')\n"
            "os.replace('/work/out/temp','/work/out/result')\n"
        )
        with self.fixture.session() as session:
            command = self.command(session, body)
            with patch.object(session, "_private_install_launch", return_value={"private_install": True}):
                with self.assertRaisesRegex(MakeProbeError, "forged|expired"):
                    session.command(command)
        self.assert_clean(session)
        with self.fixture.session() as missing:
            command = self.command(missing, body)
            read = missing.budget.read_bytes

            def omit(path, category):
                data = read(path, category)
                if category == "control" and Path(path).name.startswith("report-"):
                    report = json.loads(data)
                    report["accessed"] = [item for item in report["accessed"] if not item.startswith(private_install.PREFIX)]
                    return json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
                return data

            with patch.object(missing.budget, "read_bytes", omit):
                with self.assertRaisesRegex(MakeProbeError, "incomplete|outcomes"):
                    missing.command(command)
        self.assert_clean(missing)
        with self.fixture.session() as absent:
            command = self.command(absent, "open('/work/out/result','wb').write(b'not installed')")
            with self.assertRaisesRegex(MakeProbeError, "omitted|incomplete"):
                absent.command(command)
        self.assert_clean(absent)

    def test_private_install_protocol_rejects_unbound_and_malformed_data(self):
        config = {
            "mode": "command", "root": "/owned/session/command-root-1",
            "argv": ["/usr/bin/python3", "-c", "pass"], "code": [], "sources": [],
            "enumerations": [], "environment": {}, "observation_count": 100, "creation_limit": 100,
        }
        value = {
            "version": 1, "scope": private_install.launch_scope(config["root"]),
            "binding": private_install.launch_binding(config),
            "parents": [["/work", 1, 2, 0o40700]], "destinations": ["/work/result"],
        }
        spec = private_install.validate_config(value, config)
        record = {
            "version": 1, "scope": spec.scope, "sequence": 1,
            "source": "/work/temp", "destination": "/work/result", "result": 0,
            "identity": [1, 3, 0o100644, 4, 5, 6, 1],
        }
        encoded = private_install.PREFIX + json.dumps(record)
        self.assertEqual(private_install.validate_records([encoded], spec)[0].identity, tuple(record["identity"]))
        for key, wrong in (
            ("version", True), ("scope", "other/root"), ("binding", "0" * 64),
            ("parents", [["/work", 1, 2, 0o100644]]),
            ("destinations", ["/repo/result"]), ("destinations", ["/work/result", "/work/result"]),
        ):
            with self.subTest(key=key, wrong=wrong):
                changed = {**value, key: wrong}
                with self.assertRaises(private_install.InstallError):
                    private_install.validate_config(changed, config)
        for changed in (
            {**record, "scope": "other/root"}, {**record, "sequence": True},
            {**record, "sequence": 2}, {**record, "result": -2},
            {**record, "destination": "/work/other"}, {**record, "extra": True},
            {**record, "source": "/work/result"},
            {**record, "identity": [1, 3, 0o40700, 4, 5, 6, 1]},
            {**record, "identity": [1, 3, 0o100644, 4, 5, 6, 2]},
        ):
            with self.subTest(record=changed):
                with self.assertRaises(private_install.InstallError):
                    private_install.validate_records([private_install.PREFIX + json.dumps(changed)], spec)
        for records in ([], [encoded, encoded]):
            with self.assertRaises(private_install.InstallError):
                private_install.validate_records(records, spec)
        with self.assertRaises(private_install.InstallError):
            private_install.validate_records([encoded], None)
        with self.assertRaises(private_install.InstallError):
            private_install.validate_records([encoded[:-1] + ',"result":0}'], spec)


if __name__ == "__main__":
    unittest.main()
