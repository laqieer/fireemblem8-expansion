"""Actual phase pipe/watchdog controls; cgroup operations are explicit unit shims."""

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


if __name__ == "__main__":
    unittest.main()
