"""Actual phase pipe/watchdog controls; cgroup operations are explicit unit shims."""

import errno
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.ci_calibration import policy, supervisor


class OutputPhaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ci180-output-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_phase(self, *, mode="output", prior_stderr=False, cleanup_fault=False):
        root = self.root
        control = root / "control"
        control.mkdir()

        class UnitCgroup:
            def __init__(self, name, _memory, _pids):
                self.path = root / name
                self.path.mkdir()
                for field in policy.CGROUP_FILES:
                    (self.path / field).write_text("")
                self.samples = 0

            def prepare(self):
                pass

            def snapshot(self):
                self.samples += 1
                if cleanup_fault and self.samples > 1:
                    raise policy.GuardError("independent cleanup snapshot failure")
                return {"io": {}, "memory_events": {}, "pids_current": 0, "populated": 0}

            def kill(self):
                pass

            def empty(self):
                return True

        owner = SimpleNamespace(
            scope={"run_id": "unit"}, groups=[], control=control, uid=os.getuid(), gid=os.getgid(),
            candidate=root, harness=root, etc=root, apparmor_profile=None, tracked_paths=0,
            runtime_manifest={}, listener=SimpleNamespace(getsockname=lambda: ("127.0.0.1", 0)),
            artifacts=supervisor.Artifacts(root / "records"),
        )
        volume = SimpleNamespace(size=policy.MIB, filesystem_device=root.stat().st_dev, mountpoint=root)
        ready = policy.encoded({"scope": "unit/" + mode, "kind": "ready", "data": {"unit_stream_fixture": True}}) + b"\n"
        payload = (
            "b'x'*131072" if mode == "output" else
            f"json.dumps({{'scope':{'unit/' + mode!r},'kind':'progress','data':{{'owned':'x'*131072}}}}).encode()+b'\\n'"
        )
        program = (
            "import json,os,time\n"
            f"os.write(1,{ready!r})\n"
            + ("os.write(2,b'unexpected earlier stderr\\n');time.sleep(0.1)\n" if prior_stderr else "")
            + f"os.write(1,{payload})\n"
            "time.sleep(10)\n"
        )
        actual_popen = subprocess.Popen
        children = []

        def launch(argv, **kwargs):
            # Only replace the privileged namespace payload with owned benign bytes.
            # The phase reader, lifetime pipe and original watchdog are real.
            child = actual_popen([
                *argv[:7], "/usr/bin/python3", "-I", "-S", "-B", "-c", program,
            ], **kwargs)
            children.append(child)
            return child

        with mock.patch.object(supervisor, "Cgroup", UnitCgroup), mock.patch.object(
            supervisor.subprocess, "Popen", side_effect=launch,
        ), mock.patch.object(policy, "OUTPUT_BYTES", 65536):
            try:
                return supervisor.phase(owner, mode, volume, memory=policy.MIB, pids=8, seconds=3)
            finally:
                self.assertTrue(all(child.returncode is not None for child in children))

    def test_real_phase_overflow_then_watchdog_stderr_is_one_expected_negative(self):
        result = self.run_phase()
        self.assertEqual(result["first_cause"]["type"], "output-bound")
        self.assertNotIn("supervisor_error", result)
        self.assertTrue(result["output_exceeded"])
        self.assertGreater(result["stderr_bytes"], 0)
        self.assertGreater(result["output_bytes"], 65536)
        self.assertEqual(result["output_bytes"], result["stdout_bytes"] + result["stderr_bytes"])
        self.assertTrue(result["watchdog_reaped"])
        supervisor.validate_probe(result)
        for key, value in (
            ("empty", False), ("watchdog_reaped", False),
            ("supervisor_error", {"independent": "must not be swallowed"}),
            ("first_cause", {"type": "unexpected-stderr"}),
        ):
            with self.subTest(key=key), self.assertRaises(policy.GuardError):
                supervisor.validate_probe({**result, key: value})

    def test_real_phase_prior_stderr_remains_a_failure(self):
        result = self.run_phase(prior_stderr=True)
        self.assertEqual(result["first_cause"]["type"], "unexpected-stderr")
        with self.assertRaises(policy.GuardError):
            supervisor.validate_probe(result)

    def test_real_pipe_overflow_cannot_qualify_graph_mode(self):
        result = self.run_phase(mode="graph")
        self.assertEqual(result["first_cause"]["type"], "output-bound")
        self.assertTrue(result["output_exceeded"])
        self.assertEqual(result["graph_check_attempts"], 0)
        with self.assertRaises(policy.GuardError):
            supervisor.validate_probe(result)

    def test_real_pipe_overflow_cannot_qualify_root_mode(self):
        result = self.run_phase(mode="root")
        self.assertEqual(result["first_cause"]["type"], "output-bound")
        self.assertTrue(result["output_exceeded"])
        self.assertEqual((result["root_check_attempts"], result["graph_check_attempts"]), (0, 0))
        with self.assertRaises(policy.GuardError):
            supervisor.validate_root_phase(result)

    def test_real_phase_does_not_swallow_an_independent_cleanup_error(self):
        with self.assertRaisesRegex(policy.GuardError, "independent cleanup snapshot"):
            self.run_phase(cleanup_fault=True)

    def test_both_streams_use_one_typed_bound_and_keep_all_observed_bytes(self):
        parser = supervisor.Protocol("scope", 4)
        parser.observe_output(b"ab", stderr=True)
        parser.feed(b"cd")
        with self.assertRaises(supervisor.OutputLimitExceeded):
            parser.feed(b"e")
        with self.assertRaises(supervisor.OutputLimitExceeded):
            parser.observe_output(b"watchdog", stderr=True)
        self.assertEqual((parser.total, parser.stderr_total), (3, 10))
        self.assertTrue(parser.output_exceeded)
        with self.assertRaises(policy.GuardError) as caught:
            supervisor.Protocol("scope", 64).feed(b"not-json\n")
        self.assertNotIsInstance(caught.exception, supervisor.OutputLimitExceeded)

    def test_deadline_and_lifetime_do_not_reclassify_unexpected_overflow(self):
        base = {
            "identity": {"unit": True}, "empty": True, "watchdog_reaped": True,
            "escaped": {"sid": 2}, "held_descendants_terminal": 2,
            "caller_lifetime_control_exercised": True, "empty_before_outer_cleanup": True,
        }
        for mode, cause in (("deadline", "deadline"), ("lifetime", "lifetime-eof")):
            result = {**base, "mode": mode, "first_cause": {"type": cause}}
            supervisor.validate_probe(result)
            with self.subTest(mode=mode), self.assertRaises(policy.GuardError):
                supervisor.validate_probe({**result, "output_exceeded": True})


class MemberPinPhaseTests(unittest.TestCase):
    """Finite phase effects only: no pipes, processes, kernel reads or filesystem writes."""

    observations = []

    def run_phase(self, *, members=b"101 102 103", vanished=(), pin_errors=None,
                  read_error=None, prior_error=False, mode="lifetime", preempty=True,
                  cleanup=None, close_errors=None, reaped=True):
        state = self.last_state = {
            "case": self.id(), "mode": mode, "pin_attempts": [], "opened": [],
            "close_attempts": [], "closed": [], "kills": 0, "waits": [],
            "snapshots": 0, "empty_observations": [], "effects": [], "clock_calls": 0,
        }
        type(self).observations.append(state)
        pin_errors, close_errors = pin_errors or {}, close_errors or {}

        class UnitPath:
            def __init__(self, value):
                self.value = value
                self.name = value.rsplit("/", 1)[-1]

            def __truediv__(self, value):
                return UnitPath(self.value + "/" + value)

            def __str__(self):
                return self.value

            def mkdir(self):
                state["effects"].append(["mkdir", self.value])

            def stat(self):
                return SimpleNamespace(st_dev=1, st_ino=2)

            def write_bytes(self, data):
                state["config"] = policy.parse_json(data)

            def chmod(self, value):
                state["effects"].append(["chmod", value])

        class UnitCgroup:
            def __init__(self, name, memory, pids):
                self.path = UnitPath("/finite/" + name)
                state["limits"] = {"memory": memory, "pids": pids}

            def prepare(self):
                state["effects"].append(["prepare"])

            def snapshot(self):
                state["snapshots"] += 1
                if cleanup == "snapshot" and state["snapshots"] > 1:
                    raise OSError(errno.EIO, "finite cleanup snapshot")
                return {"io": {}, "memory_events": {}, "pids_current": 0, "populated": 0}

            def kill(self):
                state["kills"] += 1
                if cleanup == "kill":
                    raise OSError(errno.EIO, "finite cgroup kill")

            def empty(self):
                value = preempty if not state["empty_observations"] else cleanup != "empty"
                state["empty_observations"].append(value)
                return value

        def close(descriptor):
            state["close_attempts"].append(descriptor)
            if descriptor in close_errors:
                raise close_errors[descriptor]
            state["closed"].append(descriptor)

        def pin(pid):
            state["pin_attempts"].append(pid)
            if pid in vanished:
                raise ProcessLookupError(errno.ESRCH, "finite listed member exited")
            if pid in pin_errors:
                raise pin_errors[pid]
            descriptor = 1000 + pid
            state["opened"].append(descriptor)
            return descriptor

        def read_members(path):
            self.assertEqual(path.name, "cgroup.procs")
            state["effects"].append(["member-snapshot"])
            if read_error is not None:
                raise read_error
            return members

        def clock():
            state["clock_calls"] += 1
            if state["clock_calls"] > 64:
                raise AssertionError("finite clock exhausted")
            return 100 + state["clock_calls"] / 4 + (
                2 if mode == "deadline" and state["clock_calls"] >= 3 else 0
            )

        def stream_close(name):
            state["effects"].append(["stream-close", name])
            if cleanup == "stream-close" and name == "stdout":
                raise OSError(errno.EIO, "finite stream close")

        child = SimpleNamespace(
            stdout=SimpleNamespace(fileno=lambda: 11, close=lambda: stream_close("stdout")),
            stderr=SimpleNamespace(fileno=lambda: 12, close=lambda: stream_close("stderr")),
            returncode=None,
        )

        def wait(*, timeout):
            state["waits"].append(timeout)
            if cleanup == "wait" and len(state["waits"]) == 2:
                raise subprocess.TimeoutExpired("finite-watchdog", timeout)
            child.returncode = 0 if reaped else None
            return 0

        child.wait = wait
        records = [{"scope": "finite/" + mode, "kind": "ready", "data": {"finite": True}}]
        if prior_error:
            records.append({"scope": "finite/" + mode, "kind": "error", "data": {"finite_fault": True}})
        records.append({"scope": "finite/" + mode, "kind": "escaped", "data": {"sid": 101}})
        payloads = {11: [b"".join(policy.encoded(record) + b"\n" for record in records), b""], 12: [b""]}
        selectors_created = []

        class UnitSelector:
            def __init__(self):
                self.streams = not selectors_created
                selectors_created.append(self)
                self.keys, self.calls = {}, 0

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def register(self, item, events, data=None):
                if not self.streams and cleanup == "register":
                    raise OSError(errno.EBADF, "finite pidfd registration")
                descriptor = item.fileno() if self.streams else item
                self.keys[descriptor] = SimpleNamespace(fd=descriptor, fileobj=item, data=data)

            def unregister(self, item):
                del self.keys[item.fileno()]

            def get_map(self):
                return self.keys

            def select(self, timeout):
                self.calls += 1
                if self.streams and self.calls == 1:
                    return [(self.keys[11], 1)]
                if not self.streams and cleanup == "select":
                    raise OSError(errno.EIO, "finite pidfd readiness")
                keys = list(self.keys.values())
                if not self.streams and cleanup == "terminal":
                    keys = keys[:1]
                return [(key, 1) for key in keys]

        root = UnitPath("/finite")
        owner = SimpleNamespace(
            scope={"run_id": "finite"}, groups=[], control=root / "control", uid=999, gid=998,
            candidate=root, harness=root, etc=root, apparmor_profile=None, tracked_paths=0,
            runtime_manifest={}, listener=SimpleNamespace(getsockname=lambda: ("127.0.0.1", 0)),
            artifacts=SimpleNamespace(append=lambda *value: state["effects"].append(["artifact", value[0]])),
        )
        volume = SimpleNamespace(size=policy.MIB, filesystem_device=1, mountpoint=root)
        effects = {
            "Cgroup": UnitCgroup,
            "os": SimpleNamespace(
                O_CLOEXEC=os.O_CLOEXEC, pipe2=lambda flags: (20, 21), close=close,
                pidfd_open=pin, set_blocking=lambda *args: None,
                read=lambda descriptor, maximum: payloads[descriptor].pop(0),
                statvfs=lambda path: SimpleNamespace(f_blocks=10, f_bfree=9, f_frsize=4096, f_bavail=8),
            ),
            "kernel": SimpleNamespace(namespaces=lambda: {"pid": 1}, read=read_members),
            "subprocess": SimpleNamespace(
                PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired,
                Popen=lambda *args, **kwargs: child,
            ),
            "selectors": SimpleNamespace(DefaultSelector=UnitSelector, EVENT_READ=1),
            "time": SimpleNamespace(monotonic=clock, sleep=lambda seconds: None),
        }
        with mock.patch.dict(supervisor.phase.__globals__, effects):
            state["result"] = supervisor.phase(owner, mode, volume, memory=policy.MIB, pids=8, seconds=2)
        return state["result"]

    def assert_released(self):
        state = self.last_state
        self.assertCountEqual([fd for fd in state["close_attempts"] if fd >= 1000], state["opened"])
        self.assertCountEqual([fd for fd in state["closed"] if fd >= 1000], state["opened"])

    def test_snapshot_to_pin_disappearance_retains_two_survivors(self):
        result = self.run_phase(vanished=(101,))
        supervisor.validate_probe(result)
        self.assertEqual(result["unpinned_member_exits"], [101])
        self.assertEqual(result["held_descendants_terminal"], 2)
        self.assertEqual(self.last_state["pin_attempts"], [101, 102, 103])
        self.assertEqual(self.last_state["opened"], [1102, 1103])
        self.assertEqual(self.last_state["config"]["deadline"] - result["started_at"], 2)
        self.assertEqual(self.last_state["limits"], {"memory": policy.MIB, "pids": 8})
        self.assert_released()

    def test_valid_members_without_disappearance_keep_all_captures(self):
        result = self.run_phase()
        supervisor.validate_probe(result)
        self.assertNotIn("unpinned_member_exits", result)
        self.assertEqual(result["held_descendants_terminal"], 3)
        self.assertEqual(self.last_state["opened"], [1101, 1102, 1103])
        self.assert_released()

    def test_exits_before_and_after_captures_and_member_order(self):
        for members in (b"101 102 103 104 105", b"105 104 103 102 101"):
            for mode in ("deadline", "lifetime"):
                with self.subTest(members=members, mode=mode):
                    result = self.run_phase(members=members, vanished=(101, 103, 105), mode=mode)
                    self.assertCountEqual(result["unpinned_member_exits"], [101, 103, 105])
                    self.assertEqual(result["held_descendants_terminal"], 2)
                    self.assertCountEqual(self.last_state["opened"], [1102, 1104])
                    self.assert_released()
                    supervisor.validate_probe(result)

    def test_disappeared_members_never_supply_descendant_credit(self):
        for members, vanished, held in (
            (b"", (), 0), (b"101", (101,), 0), (b"101 102", (101,), 1),
            (b"101 102 103", (101, 103), 1), (b"101 102", (101, 102), 0),
        ):
            with self.subTest(members=members, vanished=vanished):
                result = self.run_phase(members=members, vanished=vanished)
                self.assertEqual(result.get("unpinned_member_exits", []), list(vanished))
                self.assertEqual(result["held_descendants_terminal"], held)
                self.assert_released()
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_probe(result)

    def test_independent_pin_errors_fail_even_after_two_captures(self):
        for error in (
            PermissionError(errno.EPERM, "finite pin permission"),
            PermissionError(errno.EACCES, "finite pin access"),
            OSError(errno.EINVAL, "finite pin argument"), OSError(errno.EBADF, "finite pin descriptor"),
            OSError(errno.EMFILE, "finite pin process limit"), OSError(errno.ENFILE, "finite pin system limit"),
            OSError(errno.EIO, "finite pin IO"), TypeError("finite pin type"),
            ProcessLookupError(errno.EPERM, "not an exit race"), ProcessLookupError(),
        ):
            with self.subTest(error=type(error).__name__, errno=getattr(error, "errno", None)):
                result = self.run_phase(pin_errors={103: error})
                self.assertNotIn("unpinned_member_exits", result)
                self.assertEqual(result["supervisor_error"]["chain"][0]["type"], type(error).__name__)
                self.assertEqual(result["first_cause"]["type"], "supervisor-error")
                self.assertEqual(result["held_descendants_terminal"], 2)
                self.assert_released()
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_probe(result)

    def test_read_decode_and_pid_type_errors_are_not_exit_races(self):
        for options, error_type in (
            ({"read_error": ProcessLookupError(errno.ESRCH, "finite read failure")}, "ProcessLookupError"),
            ({"read_error": PermissionError(errno.EACCES, "finite read access")}, "PermissionError"),
            ({"read_error": OSError(errno.EIO, "finite read IO")}, "OSError"),
            ({"members": None}, "AttributeError"), ({"members": b"\xff"}, "UnicodeDecodeError"),
            ({"members": b"101 102 invalid"}, "ValueError"),
        ):
            with self.subTest(error_type=error_type):
                result = self.run_phase(**options)
                self.assertNotIn("unpinned_member_exits", result)
                self.assertEqual(result["supervisor_error"]["chain"][0]["type"], error_type)
                self.assert_released()
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_probe(result)

    def test_worker_first_cause_survives_independent_pin_or_read_failure(self):
        for options in (
            {"pin_errors": {103: PermissionError(errno.EPERM, "finite second cause")}},
            {"read_error": OSError(errno.EIO, "finite second cause")},
        ):
            with self.subTest(options=tuple(options)):
                result = self.run_phase(prior_error=True, **options)
                self.assertEqual(result["first_cause"], {"type": "worker-error", "error": {"finite_fault": True}})
                self.assertIn("supervisor_error", result)
                self.assert_released()
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_probe(result)

    def test_expected_exit_does_not_clear_worker_first_cause(self):
        result = self.run_phase(vanished=(101,), prior_error=True)
        self.assertEqual(result["first_cause"], {"type": "worker-error", "error": {"finite_fault": True}})
        self.assertEqual(result["unpinned_member_exits"], [101])
        self.assertEqual(result["held_descendants_terminal"], 2)
        self.assertNotIn("supervisor_error", result)
        self.assert_released()
        with self.assertRaises(policy.GuardError):
            supervisor.validate_probe(result)

    def test_outer_kill_and_unreaped_watchdog_cannot_qualify(self):
        for options in ({"preempty": False}, {"reaped": False}):
            with self.subTest(options=options):
                result = self.run_phase(vanished=(101,), **options)
                self.assertTrue(result["empty"])
                self.assertTrue(result["caller_lifetime_control_exercised"])
                self.assert_released()
                with self.assertRaises(policy.GuardError):
                    supervisor.validate_probe(result)

    def test_terminal_and_selector_failures_release_all_captured_handles(self):
        for cleanup, error_type in (("terminal", policy.GuardError), ("register", OSError), ("select", OSError)):
            with self.subTest(cleanup=cleanup):
                with self.assertRaises(error_type) as caught:
                    try:
                        self.run_phase(members=b"101 102", cleanup=cleanup)
                    finally:
                        self.assert_released()
                self.last_state["raised"] = policy.error_record(caught.exception)

    def test_outer_cleanup_failures_release_all_captured_handles(self):
        for cleanup, error_type in (
            ("kill", OSError), ("empty", policy.GuardError), ("wait", policy.GuardError),
            ("stream-close", OSError), ("snapshot", OSError),
        ):
            with self.subTest(cleanup=cleanup):
                with self.assertRaises(error_type) as caught:
                    try:
                        self.run_phase(members=b"101 102", cleanup=cleanup)
                    finally:
                        self.assert_released()
                self.last_state["raised"] = policy.error_record(caught.exception)

    def test_multiple_close_failures_are_visible_and_all_attempted(self):
        with self.assertRaises(OSError) as caught:
            self.run_phase(members=b"101 102", close_errors={
                1101: OSError(errno.EIO, "finite first handle close"),
                1102: OSError(errno.EBADF, "finite second handle close"),
            })
        state = self.last_state
        self.assertCountEqual([fd for fd in state["close_attempts"] if fd >= 1000], state["opened"])
        state["raised"] = policy.error_record(caught.exception)
        self.assertEqual(len(state["raised"]["chain"]), 2)
        self.assertEqual(caught.exception.errno, errno.EIO)

    def test_unexpected_close_type_error_fails_and_attempts_all_handles(self):
        with self.assertRaises(TypeError) as caught:
            self.run_phase(members=b"101 102", close_errors={1101: TypeError("finite close type")})
        state = self.last_state
        self.assertCountEqual([fd for fd in state["close_attempts"] if fd >= 1000], state["opened"])
        state["raised"] = policy.error_record(caught.exception)
        self.assertEqual(state["raised"]["chain"][0]["type"], "TypeError")

    def test_close_failure_keeps_prior_terminal_error_in_exception_chain(self):
        with self.assertRaises(OSError) as caught:
            self.run_phase(members=b"101 102", cleanup="terminal", close_errors={
                1101: OSError(errno.EIO, "finite handle close"),
            })
        state = self.last_state
        self.assertCountEqual([fd for fd in state["close_attempts"] if fd >= 1000], state["opened"])
        state["raised"] = policy.error_record(caught.exception)
        self.assertEqual([row["type"] for row in state["raised"]["chain"]], ["OSError", "GuardError"])


if __name__ == "__main__":
    unittest.main()
