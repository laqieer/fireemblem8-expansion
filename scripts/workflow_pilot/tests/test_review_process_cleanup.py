"""Ordinary owned-process lifetime controls for TC-WORKFLOW-REVIEW-FAMILY-001."""

from __future__ import annotations

import ctypes
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.workflow_pilot import raw_diff_check as raw
from scripts.workflow_pilot import trusted_review_gate as gate
from scripts.workflow_pilot.tests.review_support import ROOT, ENV, request, snapshot


def process_state(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except FileNotFoundError:
        return None
    return {"pid": pid, "start": int(fields[19]), "group": int(fields[2]),
            "session": int(fields[3]), "state": fields[0]}


class OwnedFixture(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        directory = self.enterContext(tempfile.TemporaryDirectory(
            prefix="review-process-tests-", dir=ROOT / "build"))
        self.directory = Path(directory)
        self.records = self.directory / "processes.jsonl"
        self.records.write_text("")
        self.past_processes = []
        self.program = self.directory / "ordinary-tool"
        self.stop_interrupt = threading.Event()
        self.interrupter = None
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.previous_reaper = ctypes.c_int()
        self.assertEqual(self.libc.prctl(37, ctypes.byref(self.previous_reaper), 0, 0, 0), 0)
        self.assertEqual(self.libc.prctl(36, 1, 0, 0, 0), 0)
        self.addCleanup(self.cleanup_processes)
        self.configure("sleep")

    def configure(self, mode, code=0):
        self.past_processes.extend(identity for row in self.recorded()
                                   for identity in row["processes"])
        self.records.write_text("")
        self.program.write_text(
            "#!/usr/bin/python3\n"
            "import json,os,subprocess,sys,time\n"
            "from pathlib import Path\n"
            f"mode={mode!r}\n"
            "pipes={} if mode=='inherited-pipes' else "
            "{'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}\n"
            "child=subprocess.Popen([sys.executable,'-I','-B','-c',"
            "'import time;time.sleep(12)'],stdin=subprocess.DEVNULL,"
            "**pipes)\n"
            "processes=[]\n"
            "for pid in (os.getpid(),child.pid):\n"
            " fields=Path('/proc/%d/stat'%pid).read_text().rsplit(') ',1)[1].split()\n"
            " processes.append({'pid':pid,'start':int(fields[19]),"
            "'group':int(fields[2]),'session':int(fields[3])})\n"
            f"with open({str(self.records)!r},'a') as stream:\n"
            " stream.write(json.dumps({'processes':processes,'stage':str(Path.cwd())})+'\\n')\n"
            " stream.flush();os.fsync(stream.fileno())\n"
            "if mode=='closed-stdio':\n"
            " os.close(1);os.close(2)\n"
            "if mode=='closed-stdin':\n"
            " os.close(0)\n"
            "if mode=='flood':\n"
            " os.write(1,b'x'*(5*1024*1024))\n"
            "if mode in ('sleep','closed-stdio'):\n"
            " time.sleep(12)\n"
            "else:\n"
            " sys.stdout.buffer.write(b'actual output\\x00\\n')\n"
            " sys.stderr.buffer.write(b'actual diagnostic\\n')\n"
            f" raise SystemExit({code})\n")
        self.program.chmod(0o700)

    def recorded(self):
        return [json.loads(line) for line in self.records.read_text().splitlines()]

    def present(self, previous=False):
        identities = [identity for row in self.recorded() for identity in row["processes"]]
        if previous:
            identities.extend(self.past_processes)
        return [state for identity in identities
                if (state := process_state(identity["pid"])) is not None
                and state["start"] == identity["start"]]

    def assert_reaped(self):
        self.assertTrue(self.recorded(), "the ordinary child fixture did not execute")
        self.assertEqual(self.present(), [])

    def cleanup_processes(self):
        self.stop_interrupt.set()
        if self.interrupter is not None:
            self.interrupter.join(timeout=5)
        for _ in range(2):
            for state in self.present(previous=True):
                try:
                    descriptor = os.pidfd_open(state["pid"])
                except ProcessLookupError:
                    continue
                try:
                    current = process_state(state["pid"])
                    if current is None or current["start"] != state["start"]:
                        continue
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        os.waitid(os.P_PIDFD, descriptor, os.WEXITED)
                    except ChildProcessError:
                        pass
                finally:
                    os.close(descriptor)
        self.assertEqual(self.libc.prctl(36, self.previous_reaper.value, 0, 0, 0), 0)

    def interrupt_when_ready(self):
        def interrupt():
            deadline = time.monotonic() + 5
            while not self.stop_interrupt.wait(0.01):
                if self.records.stat().st_size:
                    os.kill(os.getpid(), signal.SIGINT)
                    return
                if time.monotonic() >= deadline:
                    return
        self.interrupter = threading.Thread(target=interrupt)
        self.interrupter.start()

    @contextmanager
    def creation_interrupt(self, number):
        original = subprocess.Popen
        observations = []
        expected_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())

        def recover(process, descriptor):
            try:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            finally:
                os.close(descriptor)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

        def create(*args, **kwargs):
            process = original(*args, **kwargs)
            if "process_group" not in kwargs:
                return process
            descriptor = os.pidfd_open(process.pid)
            self.addCleanup(recover, process, descriptor)
            deadline = time.monotonic() + 5
            while not self.records.stat().st_size and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.recorded(), "the real creation-boundary fixture did not start")
            status = Path(f"/proc/{process.pid}/status").read_text().splitlines()
            mask = int(next(line.split()[1] for line in status if line.startswith("SigBlk:")), 16)
            self.assertEqual(mask, sum(1 << (int(item) - 1) for item in expected_mask))
            observed = {"process": process, "descriptor": descriptor, "returned": False}
            observations.append(observed)
            os.kill(os.getpid(), number)
            observed["returned"] = True
            return process

        with patch.object(subprocess, "Popen", create):
            yield observations

    @contextmanager
    def creation_handler(self, number):
        previous = signal.getsignal(number)
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        calls = []

        def handled_term(received, _frame):
            calls.append(received)
            raise SystemExit(128 + received)

        handler = signal.default_int_handler if number == signal.SIGINT else handled_term
        signal.signal(number, handler)
        try:
            yield calls
            self.assertIs(signal.getsignal(number), handler)
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), mask | {signal.SIGUSR1})
        finally:
            signal.signal(number, previous)
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)

    @contextmanager
    def restoration_outcome(self, fail):
        original = ctypes.CDLL
        restorations = []

        class Library:
            def __init__(self, *args, **kwargs):
                self.library = original(*args, **kwargs)
                self.updates = 0

            def prctl(self, option, *args):
                if option == 36:
                    self.updates += 1
                    if self.updates == 2:
                        restorations.append(args[0])
                        if fail:
                            ctypes.set_errno(errno.EIO)
                            return -1
                return self.library.prctl(option, *args)

        with patch.object(ctypes, "CDLL", Library):
            yield restorations


class ProcessRunnerTests(OwnedFixture):
    def run_tool(self, **kwargs):
        return raw.run_process([str(self.program)], cwd=self.directory, env=ENV, **kwargs)

    def test_creation_boundary_interrupt_reaps_before_propagation(self):
        unrelated = subprocess.Popen([sys.executable, "-I", "-B", "-c",
                                      "import time;time.sleep(12)"])
        self.addCleanup(unrelated.wait)
        self.addCleanup(unrelated.kill)
        caller_group = os.getpgrp()
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                self.configure("sleep")
                expected = KeyboardInterrupt if number == signal.SIGINT else SystemExit
                with self.creation_handler(number) as calls:
                    with self.creation_interrupt(number) as created:
                        with self.assertRaises(expected) as caught:
                            self.run_tool(timeout=5)
                    self.assert_reaped()
                    self.assertTrue(created and all(item["returned"] for item in created))
                    self.assertTrue(all(item["process"].returncode is not None for item in created))
                    self.assertIsNone(unrelated.poll())
                    self.assertEqual(os.getpgrp(), caller_group)
                    if number == signal.SIGTERM:
                        self.assertEqual(caught.exception.code, 128 + number)
                        self.assertEqual(calls, [number])

    def test_pending_creation_signal_is_not_lost_when_real_popen_fails(self):
        original = subprocess.Popen
        failures = []
        with self.assertRaises(FileNotFoundError):
            raw.run_process([str(self.directory / "absent-tool")],
                            cwd=self.directory, env=ENV)

        def fail_creation(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGINT)
            try:
                return original(*args, **kwargs)
            except FileNotFoundError as error:
                failures.append(error.errno)
                raise

        with self.creation_handler(signal.SIGINT), patch.object(subprocess, "Popen", fail_creation):
            with self.assertRaises(KeyboardInterrupt):
                raw.run_process([str(self.directory / "absent-tool")],
                                cwd=self.directory, env=ENV)
        self.assertEqual(failures, [errno.ENOENT])
        self.assertEqual(self.recorded(), [])

    def test_restore_only_failure_raises_and_success_returns_real_output(self):
        for fail in (False, True):
            with self.subTest(failed_restore=fail):
                self.configure("exit")
                with self.restoration_outcome(fail) as attempts:
                    if fail:
                        with self.assertRaises(OSError) as caught:
                            self.run_tool(timeout=5)
                        self.assertEqual(caught.exception.errno, errno.EIO)
                    else:
                        result = self.run_tool(timeout=5)
                        self.assertEqual((result.returncode, result.stdout),
                                         (0, b"actual output\0\n"))
                self.assertEqual(len(attempts), 1)
                self.assert_reaped()

    def test_timeout_and_closed_stdio_reap_ordinary_descendants(self):
        for mode in ("sleep", "closed-stdio"):
            for new_session in (True, False):
                with self.subTest(mode=mode, new_session=new_session):
                    self.configure(mode)
                    with self.assertRaisesRegex(ValueError, "timed out"):
                        self.run_tool(timeout=0.25, new_session=new_session)
                    self.assert_reaped()

    def test_early_leader_exit_preserves_output_and_exit_status(self):
        for mode in ("exit", "inherited-pipes"):
            for code in (0, 1, 7):
                with self.subTest(mode=mode, code=code):
                    self.configure(mode, code)
                    result = self.run_tool(timeout=2)
                    self.assertEqual(result.returncode, code)
                    self.assertEqual(result.stdout, b"actual output\0\n")
                    self.assertEqual(result.stderr, b"actual diagnostic\n")
                    self.assertGreater(result.peak_rss_bytes, 0)
                    self.assertEqual(result.pid, self.recorded()[0]["processes"][0]["pid"])
                    self.assert_reaped()

    def test_bounded_input_delivery_and_real_broken_pipe(self):
        payload = bytes(range(256)) * 4096
        result = raw.run_process(
            [sys.executable, "-I", "-B", "-c",
             "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());"
             "sys.stderr.buffer.write(b'diagnostic')"],
            cwd=self.directory, env=ENV, input=payload)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, payload, b"diagnostic"))
        self.configure("closed-stdin", 7)
        result = self.run_tool(input=b"x" * raw.MAX_BYTES, timeout=2)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"actual output\0\n")
        self.assert_reaped()
        self.configure("sleep")
        with self.assertRaisesRegex(ValueError, "timed out"):
            self.run_tool(input=payload, timeout=0.25)
        self.assert_reaped()
        for invalid in ("text", b"x" * (raw.MAX_BYTES + 1)):
            with self.subTest(type=type(invalid)), patch.object(
                    raw.subprocess, "Popen", side_effect=AssertionError("invalid input launched")):
                with self.assertRaisesRegex(ValueError, "input"):
                    self.run_tool(input=invalid)

    def test_output_overage_reaps_owned_children(self):
        self.configure("flood")
        with self.assertRaisesRegex(ValueError, "output exceeds"):
            self.run_tool(timeout=2, max_bytes=4096)
        self.assert_reaped()

    def test_interrupt_and_timeout_preserve_unrelated_process_and_caller_group(self):
        unrelated = subprocess.Popen([sys.executable, "-I", "-B", "-c",
                                      "import time;time.sleep(12)"])
        self.addCleanup(unrelated.wait)
        self.addCleanup(unrelated.kill)
        caller_group = os.getpgrp()
        self.interrupt_when_ready()
        with self.assertRaises(KeyboardInterrupt):
            self.run_tool(timeout=5, new_session=False)
        self.assert_reaped()
        self.assertIsNone(unrelated.poll())
        self.assertEqual(os.getpgrp(), caller_group)
        self.stop_interrupt.set()
        self.interrupter.join(timeout=5)
        self.configure("sleep")
        with self.assertRaisesRegex(ValueError, "timed out"):
            self.run_tool(timeout=0.25)
        self.assert_reaped()
        self.assertIsNone(unrelated.poll())
        self.assertEqual(os.getpgrp(), caller_group)

    def test_runner_adopts_and_reaps_in_its_own_process_then_restores_state(self):
        code = (
            "import ctypes,json,sys\n"
            f"sys.path.insert(0,{str(ROOT)!r})\n"
            "from scripts.workflow_pilot.raw_diff_check import run_process\n"
            "libc=ctypes.CDLL(None);before=ctypes.c_int();after=ctypes.c_int()\n"
            "assert libc.prctl(37,ctypes.byref(before),0,0,0)==0\n"
            "assert before.value==0\n"
            "try:\n"
            f" run_process([{str(self.program)!r}],cwd={str(self.directory)!r},env={{}},timeout=0.25)\n"
            "except ValueError:\n"
            " pass\n"
            "assert libc.prctl(37,ctypes.byref(after),0,0,0)==0\n"
            "assert before.value==after.value\n")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_reaped()

    def test_default_sigterm_cleans_before_runner_exit(self):
        code = (
            "import sys\n"
            f"sys.path.insert(0,{str(ROOT)!r})\n"
            "from scripts.workflow_pilot.raw_diff_check import run_process\n"
            f"run_process([{str(self.program)!r}],cwd={str(self.directory)!r},env={{}},timeout=10)\n")
        process = subprocess.Popen([sys.executable, "-I", "-B", "-c", code],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.stderr.close)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 5
        while not self.records.stat().st_size and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.recorded(), "the real command did not start")
        process.terminate()
        _, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, stderr)
        self.assert_reaped()


class StagedProcessTests(OwnedFixture):
    @classmethod
    def setUpClass(cls):
        cls.workspace = snapshot()
        cls.repo = cls.workspace.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.workspace.__exit__(None, None, None)

    def tools(self):
        return gate.ReviewTools(gate.GitTree(self.repo.root, self.repo.base), self.repo.root,
                                arm_tools={"MODERN_CC": str(self.program)})

    def members(self, tools, native=False):
        data = request("TC-GAMEPLAY-006", "aoe-item-dispatch", self.repo.base, self.repo.base)
        return tuple(member for member in tools.members(data)
                     if member.probe.startswith("aoe-reference:" if native else "aoe-arm:"))

    def run_staged(self, *, native=False, outer_timeout=3, inner_timeout=None,
                   bootstrap=None):
        tools = self.tools()
        members = self.members(tools, native)
        seen_timeouts = []
        before_cleanup = []
        stages = []
        original_stage = tools._stage
        original_run = getattr(tools.subjects, "run_process", gate.subprocess.run)
        original_cleanup = gate.tempfile.TemporaryDirectory.cleanup

        def stage(tree, root, obligations):
            original_stage(tree, root, obligations)
            stages.append(root)
            if native:
                for name in ("native-enabled", "native-disabled"):
                    (root / "build" / name).write_bytes(self.program.read_bytes())
                    (root / "build" / name).chmod(0o700)

        def shortened(*args, **kwargs):
            if kwargs.get("timeout") == 240:
                seen_timeouts.append(kwargs["timeout"])
                kwargs["timeout"] = outer_timeout
            return original_run(*args, **kwargs)

        def cleanup(directory):
            if Path(directory.name) in stages:
                before_cleanup.append((Path(directory.name).is_dir(), self.present()))
            return original_cleanup(directory)

        code = gate.WORKER_CODE if bootstrap is None else bootstrap
        if inner_timeout is not None:
            code = (
                "import sys,subprocess\nsys.path.insert(0,sys.argv[1])\n"
                "from scripts.workflow_pilot import raw_diff_check as clocked\n"
                "def short_clock(function):\n"
                " def call(*args,**kwargs):\n"
                f"  if kwargs.get('timeout') in (20,60): kwargs['timeout']={inner_timeout!r}\n"
                "  return function(*args,**kwargs)\n"
                " return call\n"
                "clocked.run_process=short_clock(clocked.run_process)\n"
                "subprocess.run=short_clock(subprocess.run)\n") + code
        attribute = "run_process" if hasattr(tools.subjects, "run_process") else None
        target = tools.subjects if attribute else gate.subprocess
        try:
            with patch.object(tools, "_stage", stage), patch.object(
                    target, attribute or "run", shortened), patch.object(
                    gate, "WORKER_CODE", code), patch.object(
                    gate.tempfile.TemporaryDirectory, "cleanup", cleanup):
                return tools.run_obligations(members, self.repo.base)
        finally:
            self.assertEqual(seen_timeouts, [240])
            self.assertTrue(before_cleanup, "the actual staged directory was not cleaned")
            self.assertTrue(all(exists and not present for exists, present in before_cleanup),
                            before_cleanup)
            self.assertTrue(all(not path.exists() for path in stages))

    def test_real_staged_outer_timeout_reaps_nested_groups_before_cleanup(self):
        result = self.run_staged(outer_timeout=0.7)
        self.assertTrue(all(item.verdict == "unavailable" and item.checks == 0
                            for item in result))
        self.assert_reaped()
        processes = self.recorded()[0]["processes"]
        self.assertEqual(processes[0]["session"], processes[1]["session"])
        self.assertNotEqual(processes[0]["group"], processes[0]["session"])

    def test_real_staged_inner_command_and_native_timeouts(self):
        for native in (False, True):
            for mode in ("sleep", "closed-stdio"):
                with self.subTest(native=native, mode=mode):
                    self.configure(mode)
                    result = self.run_staged(native=native, inner_timeout=0.2)
                    self.assertTrue(all(item.verdict == "unavailable" and item.checks == 0
                                        for item in result))
                    self.assertEqual({item.kind for item in result},
                                     {"native" if native else "arm-object"})
                    self.assert_reaped()

    def test_staged_interruption_reaps_nested_groups_before_cleanup(self):
        self.interrupt_when_ready()
        with self.assertRaises(KeyboardInterrupt):
            self.run_staged(outer_timeout=5)
        self.assert_reaped()

    def test_staged_creation_interrupt_reaps_before_directory_cleanup(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                self.configure("sleep")
                expected = KeyboardInterrupt if number == signal.SIGINT else SystemExit
                bootstrap = f"import runpy;runpy.run_path({str(self.program)!r},run_name='__main__')"
                with self.creation_handler(number) as calls:
                    with self.creation_interrupt(number) as created:
                        with self.assertRaises(expected) as caught:
                            self.run_staged(outer_timeout=5, bootstrap=bootstrap)
                    self.assert_reaped()
                    self.assertTrue(created and all(item["returned"] for item in created))
                    self.assertTrue(all(item["process"].returncode is not None for item in created))
                    if number == signal.SIGTERM:
                        self.assertEqual(caught.exception.code, 128 + number)
                        self.assertEqual(calls, [number])

    def test_creation_interrupt_retains_live_staging_when_termination_fails(self):
        self.assert_failed_termination_staging(fail_restore=False)

    def test_restore_failure_preserves_unsafe_staging_and_both_diagnostics(self):
        self.assert_failed_termination_staging(fail_restore=True)

    def assert_failed_termination_staging(self, *, fail_restore):
        tools = self.tools()
        stages = []
        created = []
        original_stage = tools._stage

        def stage(tree, root, members):
            stages.append(root)
            original_stage(tree, root, members)

        bootstrap = f"import runpy;runpy.run_path({str(self.program)!r},run_name='__main__')"
        try:
            with self.creation_handler(signal.SIGINT):
                with self.restoration_outcome(fail_restore) as restored, patch.object(
                        tools, "_stage", stage), patch.object(
                        gate, "WORKER_CODE", bootstrap), self.creation_interrupt(
                        signal.SIGINT) as created, patch.object(
                        signal, "pidfd_send_signal",
                        side_effect=PermissionError(errno.EPERM, "controlled termination failure")):
                    result = tools.run_obligations(self.members(tools), self.repo.base)
                self.assertTrue(created and all(item["returned"] for item in created))
                self.assertTrue(all(os.waitid(
                    os.P_PIDFD, item["descriptor"], os.WEXITED | os.WNOHANG | os.WNOWAIT
                ) is None for item in created))
                self.assertTrue(self.present())
                self.assertEqual(len(restored), 1)
                self.assertTrue(stages and all(path.is_dir() for path in stages), {
                    "stages": [(str(path), path.is_dir()) for path in stages],
                    "processes": self.present(),
                    "diagnostics": [item.detail for item in result],
                })
                self.assertTrue(all(item.verdict == "unavailable" and item.checks == 0
                                    and str(stages[0]) in item.detail for item in result))
                self.assertTrue(all("owned process cleanup" in item.detail for item in result))
                if fail_restore:
                    self.assertTrue(all("cannot restore child subreaper state" in item.detail
                                        and f"[Errno {errno.EIO}]" in item.detail for item in result))
        finally:
            for item in created:
                try:
                    signal.pidfd_send_signal(item["descriptor"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
                item["process"].wait(timeout=5)
            self.cleanup_processes()
            for path in stages:
                shutil.rmtree(path, ignore_errors=True)

    def test_native_early_exit_retains_return_code_classification(self):
        for code, verdict, checks in ((0, "satisfied", 1),
                                      (1, "contract-violation", 1), (7, "unavailable", 0)):
            with self.subTest(code=code):
                self.configure("exit", code)
                result = self.run_staged(native=True)
                self.assertEqual({(item.kind, item.verdict, item.checks) for item in result},
                                 {("native", verdict, checks)})
                self.assert_reaped()

    def test_staged_failed_stdin_and_closed_stdio_are_not_completion(self):
        for mode in ("closed-stdin", "closed-stdio", "exit", "inherited-pipes"):
            with self.subTest(mode=mode):
                self.configure(mode, 7)
                bootstrap = f"import runpy;runpy.run_path({str(self.program)!r},run_name='__main__')"
                result = self.run_staged(outer_timeout=0.7, bootstrap=bootstrap)
                self.assertTrue(all(item.verdict == "unavailable" and item.checks == 0
                                    for item in result))
                self.assert_reaped()

    def test_missing_or_substituted_checkout_helper_never_executes(self):
        helper = self.repo.root / "scripts/workflow_pilot/raw_diff_check.py"
        original = helper.read_bytes()
        try:
            for value in (None, b"raise RuntimeError('ambient helper executed')\n"):
                with self.subTest(value=value):
                    if value is None:
                        helper.unlink()
                    else:
                        helper.write_bytes(value)
                    tools = gate.ReviewTools(
                        gate.GitTree(self.repo.root, self.repo.base), self.repo.root)
                    data = request(base=self.repo.base, head=self.repo.base)
                    result = tools.run_obligations(tools.members(data), self.repo.base)
                    self.assertTrue(all(item.kind == "host" and item.verdict == "satisfied"
                                        and item.tool_revision == self.repo.base for item in result))
        finally:
            helper.write_bytes(original)

    def test_candidate_helper_cannot_replace_the_selected_tool_revision(self):
        candidate = self.repo.commit({
            "scripts/workflow_pilot/raw_diff_check.py":
                "raise RuntimeError('candidate helper executed')\n",
        })
        tools = gate.ReviewTools(gate.GitTree(self.repo.root, self.repo.base), self.repo.root)
        data = request(base=self.repo.base, head=candidate)
        result = tools.run_obligations(tools.members(data), candidate)
        self.assertTrue(all(item.verdict == "satisfied" and item.kind == "host"
                            and item.tool_revision == self.repo.base for item in result))

    def test_unverifiable_cleanup_retains_staging_and_reports_unavailable(self):
        tools = self.tools()
        stages = []
        original_stage = tools._stage

        def stage(tree, root, members):
            stages.append(root)
            original_stage(tree, root, members)

        error = tools.subjects.ProcessCleanupError("controlled unavailable cleanup observation")
        try:
            with patch.object(tools, "_stage", stage), patch.object(
                    tools.subjects, "run_process", side_effect=error):
                result = tools.run_obligations(self.members(tools), self.repo.base)
            self.assertTrue(stages and all(path.is_dir() for path in stages))
            self.assertTrue(all(item.verdict == "unavailable" and item.checks == 0
                                and str(stages[0]) in item.detail for item in result))
        finally:
            for path in stages:
                shutil.rmtree(path, ignore_errors=True)

    def test_overlapping_exact_tool_modules_restore_process_reaper_state(self):
        code = (
            "import ctypes,sys\nfrom pathlib import Path\n"
            "from concurrent.futures import ThreadPoolExecutor\n"
            f"sys.path.insert(0,{str(ROOT)!r})\n"
            "from scripts.workflow_pilot.trusted_review_gate import GitTree,ReviewTools\n"
            "libc=ctypes.CDLL(None);before=ctypes.c_int();after=ctypes.c_int()\n"
            "assert libc.prctl(37,ctypes.byref(before),0,0,0)==0 and before.value==0\n"
            f"tree=GitTree(Path({str(self.repo.root)!r}),{self.repo.base!r})\n"
            "first=ReviewTools(tree,tree.root);second=ReviewTools(tree,tree.root)\n"
            "def run(tools,timeout):\n"
            " try:\n"
            f"  tools.subjects.run_process([{str(self.program)!r}],"
            f"cwd={str(self.directory)!r},env={{}},timeout=timeout)\n"
            " except ValueError as error:\n"
            "  assert str(error)=='process timed out',str(error)\n"
            "with ThreadPoolExecutor(max_workers=2) as pool:\n"
            " futures=[pool.submit(run,first,0.2),pool.submit(run,second,0.5)]\n"
            " for future in futures:future.result()\n"
            "assert libc.prctl(37,ctypes.byref(after),0,0,0)==0\n"
            "assert before.value==after.value\n")
        completed = subprocess.run([sys.executable, "-I", "-B", "-c", code],
                                   capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_reaped()


if __name__ == "__main__":
    unittest.main()
