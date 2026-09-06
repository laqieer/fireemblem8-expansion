"""TC-WORKFLOW-SEALED-ASSERTION-CAPSULE-001: real descriptor/process controls."""

from __future__ import annotations

import copy
import errno
import hashlib
import hmac
import inspect
import io
import json
import mmap
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest import mock

from scripts.workflow_pilot import event_classifier, sealed_capsule as capsule


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "build" / "test-artifacts"
CHECKER = """from checks import helper
def capsule_main(request, context):
    nested = context.invoke('assertion', request)
    return {'checker': helper.value(), 'assertion': nested}
"""
ASSERTION = """import json
from checks import helper
def capsule_main(request, context):
    values = {slot: json.loads(context.read(slot, 'inputs/state.json'))['value']
              for slot in ('base', 'origin', 'head')}
    return {'module': helper.value(), 'values': values, 'request': request,
            'status': 'pass' if values['origin'] == 'broken' and values['head'] == 'fixed' else 'fail'}
"""


def _process_state(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return int(fields[19]), fields[0], int(fields[1])


def _observe_descendants(observed):
    for pid, generation in tuple(observed.items()):
        state = _process_state(pid)
        if state is None or state[0] != generation:
            continue
        try:
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        for child in map(int, children):
            state = _process_state(child)
            if state is not None and state[2] == pid:
                observed.setdefault(child, state[0])


def _finish_owned_process(pid, generation, *, terminate=False):
    try:
        exited = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        state = _process_state(pid)
        return state is None or state[0] != generation
    state = _process_state(pid)
    if state is None:
        return False
    if state[0] != generation:
        return True
    if exited is not None:
        os.waitpid(pid, 0)
        return True
    if terminate:
        # This single waiter owns the child; its PID stays reserved until reaped.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return False


def _wait_owned_stop(process, expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pid, status = os.waitpid(process.pid, os.WUNTRACED | os.WNOHANG)
        if pid:
            if not os.WIFSTOPPED(status) or os.WSTOPSIG(status) != expected:
                raise AssertionError(f"owned child did not reach its expected stop: {status}")
            return
        time.sleep(0.005)
    raise AssertionError("owned child stop timed out")


def _observe_owned_exec(popen, command, options, observations):
    import ctypes
    ptrace = ctypes.CDLL(None, use_errno=True).ptrace
    ptrace.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    ptrace.restype = ctypes.c_long

    def trace():
        if ptrace(0, 0, None, None):
            os._exit(126)

    process = popen(command, **{**options, "preexec_fn": trace})
    try:
        # TRACEME's successful-exec trap precedes ld.so's first instruction.
        _wait_owned_stop(process, signal.SIGTRAP)
        with open(f"/proc/{process.pid}/status") as stream:
            uid = int(next(line.split()[2] for line in stream if line.startswith("Uid:")))
        if uid != os.geteuid():
            raise AssertionError("exec observer must have the victim's effective UID")
        access = []
        for fd in (0, 1, 2, *options["pass_fds"]):
            try:
                opened = os.open(f"/proc/{process.pid}/fd/{fd}", os.O_RDONLY | os.O_CLOEXEC)
            except OSError as error:
                access.append(error.errno)
            else:
                os.close(opened)
                access.append(0)
        observations.append({"same_uid": True, "fd_access_errno": access,
                             "proc_fd_owner": os.stat(f"/proc/{process.pid}/fd").st_uid})
        if not access or any(value != errno.EACCES for value in access):
            raise AssertionError("kernel exec-entry exposed inherited authority")
        if ptrace(17, process.pid, None, None):
            raise AssertionError("cannot detach the owned exec-stop observer")
        return process
    except BaseException:
        process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        raise


class CapsulePlatformTests(unittest.TestCase):
    def assert_python_unavailable_before_resources(self):
        spec = capsule.CapsuleSpec(
            trees={"base": "a" * 40}, programs={"checker": "checks/checker.py"})
        admissions = {
            "platform": capsule._platform,
            "prepare": lambda: capsule.prepare(ROOT, spec),
            "capsule": lambda: capsule.Capsule(b"{}", spec),
            "descriptor": lambda: capsule.SealedBytes(b"unavailable", "test", 100),
            "execute": lambda: capsule._execute(None, None, "checker", {}, 1, 0),
        }
        for name, admit in admissions.items():
            with (
                self.subTest(admission=name),
                mock.patch.object(capsule, "_make_bundle", side_effect=AssertionError) as collect,
                mock.patch.object(capsule, "_inherited_fds", side_effect=AssertionError) as descriptors,
                mock.patch.object(capsule, "_prctl", side_effect=AssertionError) as prctl,
                mock.patch.object(os, "memfd_create", create=True, side_effect=AssertionError) as create,
                mock.patch.object(os, "fork", create=True, side_effect=AssertionError) as fork,
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable) as error:
                    admit()
                self.assertEqual(error.exception.disposition, "sealed-capsule-unavailable")
                for operation in (collect, descriptors, prctl, create, fork, launch):
                    operation.assert_not_called()

    def test_older_preparation_python_fails_before_resources_or_worker_launch(self):
        with mock.patch.object(sys, "version_info", (3, 9, 0, "final", 0)):
            self.assert_python_unavailable_before_resources()

    def test_missing_preparation_capabilities_fail_before_resources_or_worker_launch(self):
        for module, name in (
            (sys, "stdlib_module_names"), (sys, "addaudithook"),
            (capsule, "fcntl"), (capsule, "resource"),
            (os, "fchmod"), (os, "waitid"), (os, "WNOWAIT"),
            (signal, "pthread_sigmask"),
        ):
            with self.subTest(capability=name), mock.patch.object(module, name, None, create=True):
                if name == "stdlib_module_names":
                    del sys.stdlib_module_names
                self.assert_python_unavailable_before_resources()

    def test_unsupported_guardian_python_is_unavailable_before_worker_creation(self):
        with (
            mock.patch.object(sys, "version_info", (3, 9, 0, "final", 0)),
            mock.patch.object(capsule, "_inherited_fds", side_effect=AssertionError) as descriptors,
            mock.patch.object(os, "fork", create=True, side_effect=AssertionError) as fork,
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
            mock.patch.object(os, "write") as diagnostic,
            mock.patch.object(os, "waitpid", side_effect=ChildProcessError),
        ):
            with self.assertRaises(SystemExit) as error:
                capsule._supervise(["3", "4", "5", "6", "7", "", "", ""])
            self.assertEqual(error.exception.code, 125)
            self.assertTrue(diagnostic.call_args.args[1].startswith(b"CapsuleUnavailable:"))
            for operation in (descriptors, fork, launch):
                operation.assert_not_called()

    def test_unvalidated_abis_fail_before_resources_or_process_creation(self):
        spec = capsule.CapsuleSpec(
            trees={"base": "a" * 40}, programs={"checker": "checks/checker.py"})
        admissions = {
            "platform": capsule._platform,
            "prepare": lambda: capsule.prepare(ROOT, spec),
            "capsule": lambda: capsule.Capsule(b"{}", spec),
            "descriptor": lambda: capsule.SealedBytes(b"unavailable", "test", 100),
            "kernel": capsule._lock_worker_kernel,
        }
        for machine in ("aarch64", "arm64", "i686", "riscv64"):
            for name, admit in admissions.items():
                with (
                    self.subTest(machine=machine, admission=name),
                    mock.patch.object(sys, "platform", "linux"),
                    mock.patch.object(os, "uname", create=True,
                                      return_value=types.SimpleNamespace(machine=machine)),
                    mock.patch.object(capsule, "_make_bundle", side_effect=AssertionError) as collect,
                    mock.patch.object(capsule, "_inherited_fds", side_effect=AssertionError) as descriptors,
                    mock.patch.object(capsule, "_prctl", side_effect=AssertionError) as prctl,
                    mock.patch.object(capsule.ctypes, "CDLL", side_effect=AssertionError) as native,
                    mock.patch.object(os, "memfd_create", create=True, side_effect=AssertionError) as create,
                    mock.patch.object(os, "fork", create=True, side_effect=AssertionError) as fork,
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
                ):
                    with self.assertRaises(capsule.CapsuleUnavailable) as error:
                        admit()
                    self.assertEqual(error.exception.disposition, "sealed-capsule-unavailable")
                    for operation in (collect, descriptors, prctl, native, create, fork, launch):
                        operation.assert_not_called()

    def test_32_bit_interpreter_cannot_install_the_x86_64_runtime_filter(self):
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(os, "uname", create=True,
                              return_value=types.SimpleNamespace(machine="x86_64")),
            mock.patch.object(capsule.ctypes, "sizeof", return_value=4),
            mock.patch.object(capsule.ctypes, "CDLL", side_effect=AssertionError) as native,
            mock.patch.object(capsule, "_inherited_fds", side_effect=AssertionError) as descriptors,
        ):
            for admit in (capsule._platform, capsule._lock_worker_kernel):
                with self.subTest(admission=admit.__name__):
                    with self.assertRaises(capsule.CapsuleUnavailable):
                        admit()
            native.assert_not_called()
            descriptors.assert_not_called()


@unittest.skipUnless(
    sys.platform == "linux" and os.uname().machine == "x86_64"
    and capsule.ctypes.sizeof(capsule.ctypes.c_void_p) == 8,
    "Linux x86-64 Python interpreter admission")
class CapsuleInterpreterTests(unittest.TestCase):
    def assert_probe_unavailable_before_resources(self, reply=None, failure=None):
        spec = capsule.CapsuleSpec(
            trees={"base": "a" * 40}, programs={"checker": "checks/checker.py"})
        for name, admit in (
            ("prepare", lambda: capsule.prepare(ROOT, spec)),
            ("capsule", lambda: capsule.Capsule(b"{}", spec)),
            ("execute", lambda: capsule._execute(None, None, "checker", {}, 1, 0)),
        ):
            before = capsule._inherited_fds()
            with (
                self.subTest(admission=name),
                mock.patch.object(capsule, "_make_bundle", side_effect=AssertionError) as collect,
                mock.patch.object(capsule, "_Bundle", side_effect=AssertionError) as bundle,
                mock.patch.object(os, "fork", side_effect=AssertionError) as fork,
                mock.patch.object(subprocess, "Popen") as launch,
                mock.patch.object(capsule, "_collect", return_value=reply,
                                  side_effect=failure) as probe,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable) as error:
                    admit()
                self.assertEqual(error.exception.disposition, "sealed-capsule-unavailable")
                for operation in (collect, bundle, fork):
                    operation.assert_not_called()
                launch.assert_called_once()
                command = launch.call_args.args[0]
                self.assertEqual(command[:5], [
                    capsule.PYTHON, "-I", "-S", "-c", capsule.PYTHON_STARTUP + capsule.PYTHON_PROBE])
                self.assertEqual(tuple(json.loads(command[6])), capsule._interpreter_identity(capsule.PYTHON))
                self.assertEqual(json.loads(command[7]),
                                 sorted(int(number) for number in signal.pthread_sigmask(signal.SIG_BLOCK, ())))
                self.assertEqual(json.loads(command[8]), capsule.PYTHON_APIS)
                self.assertEqual(launch.call_args.kwargs, {
                    "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE, "env": capsule.ENVIRONMENT, "cwd": "/",
                    "start_new_session": True, "close_fds": True,
                    "executable": f"/proc/self/fd/{command[5]}", "pass_fds": (int(command[5]),),
                })
                probe.assert_called_once_with(
                    launch.return_value, capsule.PYTHON_PROBE_SECONDS, capsule.MAX_PYTHON_PROBE_BYTES)
            self.assertEqual(capsule._inherited_fds(), before)

    def test_different_execution_python_must_meet_version_abi_and_capability_requirements(self):
        for field, value in (
            ("version", [3, 9]), ("version", [3, 10.0]),
            ("stdlib_module_names", False), ("stdlib_module_names", "true"),
            ("capabilities", False), ("capabilities", 1),
            ("machine", "aarch64"), ("platform", "unsupported"),
            ("pointer_bytes", 4), ("pointer_bytes", 8.0),
        ):
            with self.subTest(field=field, value=value):
                report = {**capsule._python_report(), field: value}
                self.assert_probe_unavailable_before_resources((0, capsule.canonical(report), b""))

    def test_failed_malformed_and_oversized_execution_probes_are_unavailable(self):
        valid = capsule.canonical(capsule._python_report())
        for reply in (
            (1, valid, b""), (0, valid, b"unexpected diagnostic"), (0, b"", b""),
            (0, b"not JSON", b""), (0, valid.rstrip(), b""), (0, b"{}\n", b""),
            (0, b"[]\n", b""), (0, b"x" * (capsule.MAX_PYTHON_PROBE_BYTES + 1), b""),
        ):
            with self.subTest(status=reply[0], output=reply[1][:40], stderr=reply[2]):
                self.assert_probe_unavailable_before_resources(reply)
        for failure in (capsule.CapsuleError("process timeout"),
                        capsule.CapsuleError("process output exceeds limit"),
                        subprocess.TimeoutExpired([capsule.PYTHON], capsule.PYTHON_PROBE_SECONDS)):
            with self.subTest(failure=failure):
                self.assert_probe_unavailable_before_resources(failure=failure)

    def test_execution_probe_startup_failure_is_unavailable(self):
        for number in (errno.ENOENT, errno.EACCES, errno.EPERM, errno.ENOEXEC):
            before = capsule._inherited_fds()
            with (
                self.subTest(errno=number),
                mock.patch.object(subprocess, "Popen",
                                  side_effect=OSError(number, "unavailable")) as launch,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    capsule._probe_python()
                launch.assert_called_once()
            self.assertEqual(capsule._inherited_fds(), before)

    def test_real_probe_timeout_and_output_bounds_reap_the_owned_child(self):
        processes = []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        for source in ("import time; time.sleep(10)", "import os; os.write(1, b'x' * 8192)"):
            with (
                self.subTest(source=source),
                mock.patch.object(capsule, "PYTHON_PROBE", source),
                mock.patch.object(capsule, "PYTHON_PROBE_SECONDS", 0.2),
                mock.patch.object(subprocess, "Popen", side_effect=launch),
            ):
                before = capsule._inherited_fds()
                with self.assertRaises(capsule.CapsuleUnavailable):
                    capsule._probe_python()
                self.assertIsNotNone(processes[-1].returncode)
                self.assertTrue(processes[-1].stdout.closed)
                self.assertTrue(processes[-1].stderr.closed)
                self.assertEqual(capsule._inherited_fds(), before)

    def test_current_interpreter_probe_is_real_and_matches_local_capabilities(self):
        self.assertEqual(capsule._probe_python(), capsule._python_report())
        with mock.patch.object(capsule, "_probe_python", wraps=capsule._probe_python) as probe:
            with capsule._ExecutionInterpreter() as admitted:
                admitted.check()
                with capsule.SealedBytes(b"current interpreter", "probe-positive", 100) as descriptor:
                    self.assertEqual(descriptor.read(), b"current interpreter")
                probe.assert_called_once_with(admitted)

    def test_different_supported_interpreter_is_probed_once_without_path_fallback(self):
        report = {**capsule._python_report(), "version": [3, 10]}
        with (
            mock.patch.object(sys, "executable", "/untrusted/python"),
            mock.patch.object(subprocess, "Popen") as launch,
            mock.patch.object(capsule, "_collect", return_value=(0, capsule.canonical(report), b"")),
        ):
            with capsule._ExecutionInterpreter() as admitted:
                admitted.check()
                admitted.check()
                launch.assert_called_once()
                self.assertEqual(launch.call_args.args[0][0], "/usr/bin/python3")

    def test_changed_interpreter_identity_invalidates_probe_and_admission(self):
        report = capsule.canonical(capsule._python_report())
        with capsule._ExecutionInterpreter(_probe=False) as admitted:
            with (
                mock.patch.object(capsule, "_interpreter_identity",
                                  side_effect=[admitted.identity, ("changed",)]),
                mock.patch.object(subprocess, "Popen"),
                mock.patch.object(capsule, "_collect", return_value=(0, report, b"")),
            ):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    capsule._probe_python(admitted)
            with (
                mock.patch.object(capsule, "_interpreter_identity", return_value=("changed",)),
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    admitted.check()
                launch.assert_not_called()

    def test_untrusted_system_interpreter_never_runs_a_probe(self):
        current = os.stat(capsule.PYTHON)
        for uid, mode in (
            (1, current.st_mode), (0, 0o100777), (0, 0o100644), (0, 0o040755),
        ):
            with (
                self.subTest(uid=uid, mode=mode),
                mock.patch.object(os, "stat", return_value=types.SimpleNamespace(st_uid=uid, st_mode=mode)),
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    capsule._ExecutionInterpreter()
                launch.assert_not_called()


@unittest.skipUnless(
    sys.platform == "linux" and os.uname().machine == "x86_64"
    and capsule.ctypes.sizeof(capsule.ctypes.c_void_p) == 8,
    "Linux x86-64 protected exec contract")
class CapsuleExecProtectionTests(unittest.TestCase):
    def test_non_user_dump_policies_are_admitted_without_changing_host_policy(self):
        path = Path("/proc/sys/fs/suid_dumpable")
        before = path.read_bytes()
        for policy in (b"0\n", b"2\n"):
            with self.subTest(policy=policy), mock.patch("builtins.open", mock.mock_open(read_data=policy)):
                self.assertEqual(capsule._exec_policy(), int(policy))
        self.assertEqual(path.read_bytes(), before)

    def test_startup_disables_root_only_dumps_before_any_descriptor_access(self):
        for initial, refuse_disable in ((0, False), (2, False), (2, True),
                                        (1, False), (3, False), (-1, False)):
            state = {"dumpable": initial}

            def prctl(option, value, *unused):
                if option == 3:
                    return state["dumpable"]
                self.assertEqual((option, value), (4, 0))
                if refuse_disable:
                    return -1
                state["dumpable"] = 0
                return 0

            def descriptor(fd):
                self.assertEqual(state["dumpable"], 0)
                return types.SimpleNamespace(st_dev=1, st_ino=2, st_mode=0o100111)

            admitted = initial in (0, 2) and not refuse_disable
            with (
                self.subTest(initial=initial, refuse_disable=refuse_disable),
                mock.patch.object(sys, "argv", ["-c", "200", "[0,0,0,0,0,0,0,0]", "[]"]),
                mock.patch.object(capsule.ctypes, "CDLL",
                                  return_value=types.SimpleNamespace(prctl=prctl)),
                mock.patch.object(os, "fstat", side_effect=descriptor) as inspect_fd,
                mock.patch.object(os, "stat", side_effect=descriptor),
                mock.patch.object(capsule.fcntl, "fcntl", return_value=capsule.SEALS),
                mock.patch.object(signal, "pthread_sigmask") as restore_mask,
                mock.patch.object(os, "write") as diagnostic,
            ):
                if admitted:
                    exec(compile(capsule.PYTHON_STARTUP, "sealed:startup-contract", "exec"), {})
                    self.assertEqual(state["dumpable"], 0)
                    inspect_fd.assert_called_once_with(200)
                    restore_mask.assert_called_once()
                    diagnostic.assert_not_called()
                else:
                    with self.assertRaises(SystemExit) as rejected:
                        exec(compile(capsule.PYTHON_STARTUP, "sealed:startup-contract", "exec"), {})
                    self.assertEqual(rejected.exception.code, 125)
                    self.assertEqual(state["dumpable"], initial)
                    inspect_fd.assert_not_called()
                    restore_mask.assert_not_called()
                    self.assertTrue(diagnostic.call_args.args[1].startswith(b"CapsuleUnavailable:"))

    def test_kernel_exec_stop_denies_fds_before_any_python_or_loader_instruction(self):
        source = ("import ctypes,json,os\n"
                  "print(json.dumps({'dumpable':ctypes.CDLL(None).prctl(3,0,0,0,0),"
                  "'uid':os.geteuid()}))\n")
        real_popen, observations = subprocess.Popen, []
        with (
            capsule.SealedBytes(b"sealed authority", "entry-fixture", 100) as fixture,
            capsule._ExecutionInterpreter() as interpreter,
        ):
            self.assertEqual(capsule.ctypes.CDLL(None).prctl(3, 0, 0, 0, 0), 0)

            def observe(command, **options):
                return _observe_owned_exec(real_popen, command, options, observations)

            with mock.patch.object(subprocess, "Popen", side_effect=observe), capsule._Child() as owner:
                process, _ = interpreter.launch(source, [], owner=owner, pass_fds=(fixture.fd,))
                status, stdout, stderr = capsule._collect(process, 5, 4096)
            self.assertEqual((status, stderr), (0, b""))
            self.assertEqual(json.loads(stdout), {"dumpable": 0, "uid": os.geteuid()})
            self.assertEqual(observations, [{
                "same_uid": True, "fd_access_errno": [errno.EACCES] * 5, "proc_fd_owner": 0,
            }])

            def ordinary_exec(command, **options):
                options["executable"] = capsule.PYTHON
                return _observe_owned_exec(real_popen, command, options, observations)

            with mock.patch.object(subprocess, "Popen", side_effect=ordinary_exec), capsule._Child() as owner:
                with self.assertRaisesRegex(AssertionError, "kernel exec-entry exposed"):
                    interpreter.launch(source, [], owner=owner, pass_fds=(fixture.fd,))
            self.assertEqual(observations[-1]["fd_access_errno"][3], 0)
            self.assertEqual(observations[-1]["proc_fd_owner"], os.geteuid())

    def test_same_uid_parent_ptrace_and_fd_reads_after_python_initialization(self):
        source = ("import ctypes,json,os,signal\n"
                  "os.kill(os.getpid(),signal.SIGSTOP)\n"
                  "print(json.dumps({'dumpable':ctypes.CDLL(None).prctl(3,0,0,0,0)}))\n")
        ptrace = capsule.ctypes.CDLL(None, use_errno=True).ptrace
        ptrace.argtypes = [capsule.ctypes.c_uint, capsule.ctypes.c_int,
                          capsule.ctypes.c_void_p, capsule.ctypes.c_void_p]
        ptrace.restype = capsule.ctypes.c_long
        with (
            capsule.SealedBytes(b"same-uid authority", "ptrace-fixture", 100) as fixture,
            capsule._ExecutionInterpreter() as interpreter,
        ):
            for protected in (False, True):
                with self.subTest(protected=protected), capsule._Child() as owner:
                    if protected:
                        process, _ = interpreter.launch(source, [], owner=owner, pass_fds=(fixture.fd,))
                    else:
                        process = subprocess.Popen(
                            [capsule.PYTHON, "-I", "-S", "-c", source],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            pass_fds=(fixture.fd,), env=capsule.ENVIRONMENT, close_fds=True,
                            start_new_session=True, cwd="/")
                    try:
                        _wait_owned_stop(process, signal.SIGSTOP)
                        path = f"/proc/{process.pid}/fd/{fixture.fd}"
                        try:
                            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                        except PermissionError:
                            self.assertTrue(protected)
                        else:
                            try:
                                self.assertFalse(protected)
                                self.assertEqual(os.read(fd, 100), fixture.read())
                            finally:
                                os.close(fd)
                        capsule.ctypes.set_errno(0)
                        attached = ptrace(16, process.pid, None, None)
                        error = capsule.ctypes.get_errno()
                        if attached == 0:
                            _wait_owned_stop(process, signal.SIGSTOP)
                            self.assertEqual(ptrace(17, process.pid, None, None), 0)
                        if protected:
                            self.assertEqual((attached, error), (-1, errno.EPERM))
                        else:
                            self.assertEqual(attached, 0)
                        os.kill(process.pid, signal.SIGCONT)
                        status, stdout, stderr = capsule._collect(process, 5, 4096)
                        self.assertEqual((status, stderr), (0, b""))
                        self.assertEqual(json.loads(stdout), {"dumpable": 0 if protected else 1})
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                        process.stdout.close()
                        process.stderr.close()

    def test_startup_rejects_unprotected_exec_instead_of_repairing_dumpability(self):
        real_popen = subprocess.Popen

        def ordinary_exec(command, **options):
            return real_popen(command, **{**options, "executable": capsule.PYTHON})

        before = capsule._inherited_fds()
        with mock.patch.object(subprocess, "Popen", side_effect=ordinary_exec):
            with self.assertRaises(capsule.CapsuleUnavailable):
                capsule._probe_python()
        self.assertEqual(capsule._inherited_fds(), before)

    def test_unavailable_exec_policy_or_memfd_permissions_prevent_capsule_launch(self):
        spec = capsule.CapsuleSpec(trees={"base": "a" * 40},
                                   programs={"checker": "checks/checker.py"})
        for value in (b"1\n", b"3\n", b"2", b"", b"invalid\n"):
            with (
                self.subTest(policy=value),
                mock.patch("builtins.open", mock.mock_open(read_data=value)),
                mock.patch.object(capsule, "_make_bundle", side_effect=AssertionError) as bundle,
                mock.patch.object(os, "memfd_create", side_effect=AssertionError) as create,
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    capsule.prepare(ROOT, spec)
                for operation in (bundle, create, launch):
                    operation.assert_not_called()
        with (
            mock.patch("builtins.open", side_effect=PermissionError(errno.EACCES, "policy hidden")),
            mock.patch.object(os, "memfd_create", side_effect=AssertionError) as create,
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
        ):
            with self.assertRaises(capsule.CapsuleUnavailable):
                capsule.prepare(ROOT, spec)
            create.assert_not_called()
            launch.assert_not_called()
        before = capsule._inherited_fds()
        with (
            mock.patch.object(os, "fchmod", side_effect=OSError(errno.EPERM, "no executable memfd")),
            mock.patch.object(capsule, "_make_bundle", side_effect=AssertionError) as bundle,
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
        ):
            with self.assertRaises(capsule.CapsuleUnavailable):
                capsule.prepare(ROOT, spec)
            bundle.assert_not_called()
            launch.assert_not_called()
        self.assertEqual(capsule._inherited_fds(), before)

    def test_bypassable_image_read_permissions_reject_before_launch(self):
        with capsule._ExecutionInterpreter(_probe=False) as interpreter:
            before = capsule._inherited_fds()
            real_open = os.open

            def bypass(path, *args, **kwargs):
                if path == f"/proc/self/fd/{interpreter.image.fd}":
                    return os.dup(interpreter.image.fd)
                return real_open(path, *args, **kwargs)

            with (
                mock.patch.object(os, "open", side_effect=bypass),
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
                capsule._Child() as owner,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    interpreter.launch("raise AssertionError", [], owner=owner)
                launch.assert_not_called()
            self.assertEqual(capsule._inherited_fds(), before)

    def test_system_image_is_immutable_execute_only_and_bounded(self):
        with capsule._ExecutionInterpreter() as interpreter:
            image = interpreter.image
            self.assertEqual(image.read(), Path(capsule.PYTHON).read_bytes())
            self.assertEqual(os.fstat(image.fd).st_mode & 0o777, 0o111)
            with self.assertRaises(OSError):
                os.write(image.fd, b"substitution")
            os.fchmod(image.fd, 0o555)
            with (mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
                  capsule._Child() as owner):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    interpreter.launch("raise AssertionError", [], owner=owner)
                launch.assert_not_called()
        before = capsule._inherited_fds()
        with (
            mock.patch.object(capsule, "MAX_PYTHON_IMAGE_BYTES", 1),
            mock.patch.object(os, "memfd_create", side_effect=AssertionError) as create,
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
        ):
            with self.assertRaises(capsule.CapsuleUnavailable):
                capsule._ExecutionInterpreter()
            create.assert_not_called()
            launch.assert_not_called()
        self.assertEqual(capsule._inherited_fds(), before)

    def test_reused_interpreter_fd_is_rejected_without_closing_its_replacement(self):
        with (
            capsule._ExecutionInterpreter() as interpreter,
            capsule.SealedBytes(b"unrelated replacement", "image-replacement", 100) as replacement,
        ):
            reused = interpreter.image.fd
            os.dup2(replacement.fd, reused)
            try:
                with (mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
                      capsule._Child() as owner):
                    with self.assertRaises(capsule.CapsuleUnavailable):
                        interpreter.launch("raise AssertionError", [], owner=owner)
                    launch.assert_not_called()
                interpreter.close()
                self.assertEqual(os.pread(reused, 100, 0), replacement.read())
            finally:
                os.close(reused)


@unittest.skipUnless(sys.platform == "linux", "Linux child supervision")
class CapsuleProcessObservationTests(unittest.TestCase):
    def test_production_cleanup_never_signals_or_reaps_unowned_or_reaped_children(self):
        for status in (None, 0, -9):
            process = types.SimpleNamespace(
                pid=12345, returncode=status, stdin=None, stdout=None, stderr=None,
                wait=mock.Mock(side_effect=AssertionError))
            with (
                self.subTest(status=status),
                mock.patch.object(os, "waitid", side_effect=ChildProcessError),
                mock.patch.object(capsule, "_kill_group", side_effect=AssertionError) as group,
                mock.patch.object(os, "kill", side_effect=AssertionError) as kill,
                mock.patch.object(os, "waitpid", side_effect=AssertionError) as reap,
            ):
                capsule._finish_child(process)
                capsule._stop_worker(process.pid)
                for operation in (group, kill, reap, process.wait):
                    operation.assert_not_called()

    def test_worker_cleanup_reaps_a_real_child_before_its_group_is_established(self):
        pid = os.fork()
        if pid == 0:
            try:
                while True:
                    signal.pause()
            finally:
                os._exit(0)
        try:
            self.assertNotEqual(os.getpgid(pid), pid)
            capsule._stop_worker(pid)
            with self.assertRaises(ChildProcessError):
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        finally:
            try:
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                pass
            else:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)

    def test_cleanup_never_signals_or_reaps_unowned_or_reused_pids(self):
        pid, generation = 12345, 100
        for owned, current, complete in (
            (False, (generation, "S", 1), False),
            (False, None, True),
            (False, (generation + 1, "S", 1), True),
            (True, None, False),
            (True, (generation + 1, "S", os.getpid()), True),
        ):
            with (
                self.subTest(owned=owned, current=current),
                mock.patch.object(os, "waitid", return_value=None,
                                  side_effect=None if owned else ChildProcessError),
                mock.patch(f"{__name__}._process_state", return_value=current),
                mock.patch.object(os, "kill", side_effect=AssertionError) as kill,
                mock.patch.object(os, "waitpid", side_effect=AssertionError) as reap,
            ):
                self.assertEqual(
                    _finish_owned_process(pid, generation, terminate=True), complete)
                kill.assert_not_called()
                reap.assert_not_called()

    def test_cleanup_signals_only_reserved_children_and_reaps_observed_exits(self):
        pid, generation = 12345, 100
        with (
            mock.patch.object(os, "waitid", return_value=None) as observe,
            mock.patch(f"{__name__}._process_state", return_value=(generation, "S", os.getpid())),
            mock.patch.object(os, "kill") as kill,
            mock.patch.object(os, "waitpid") as reap,
        ):
            self.assertFalse(_finish_owned_process(pid, generation))
            kill.assert_not_called()
            reap.assert_not_called()
            self.assertFalse(_finish_owned_process(pid, generation, terminate=True))
            observe.assert_called_with(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            kill.assert_called_once_with(pid, signal.SIGKILL)
            reap.assert_not_called()
            kill.reset_mock()
            observe.return_value = types.SimpleNamespace(si_pid=pid)
            self.assertTrue(_finish_owned_process(pid, generation, terminate=True))
            kill.assert_not_called()
            reap.assert_called_once_with(pid, 0)


@unittest.skipUnless(
    sys.platform == "linux" and os.uname().machine == "x86_64"
    and capsule.ctypes.sizeof(capsule.ctypes.c_void_p) == 8,
    "Linux x86-64 sealed capsule runtime contract")
class SealedCapsuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="sealed-capsule-", dir=ARTIFACTS)
        cls.root = Path(cls.temporary.name)
        cls.runtime = (ROOT / capsule.RUNTIME_PATH).read_bytes()
        cls.write(capsule.RUNTIME_PATH, cls.runtime)
        cls.write("scripts/workflow_pilot/__init__.py", b"")
        cls.write("scripts/workflow_pilot/event_classifier.py",
                  (ROOT / "scripts/workflow_pilot/event_classifier.py").read_bytes())
        cls.write("scripts/workflow_pilot/isolated_launcher.py",
                  (ROOT / "scripts/workflow_pilot/isolated_launcher.py").read_bytes())
        cls.write("checks/checker.py", CHECKER.encode())
        cls.write("checks/assertion.py", ASSERTION.encode())
        cls.write("checks/helper.py", b"def value():\n    return 'trusted-module'\n")
        cls.write("inputs/state.json", capsule.canonical({"value": "base"}))
        cls.git("init", "-q", "-b", "master")
        cls.git("config", "user.name", "Capsule fixture")
        cls.git("config", "user.email", "capsule@example.invalid")
        cls.base = cls.commit()
        cls.write("inputs/state.json", capsule.canonical({"value": "broken"}))
        cls.origin = cls.commit()
        cls.write("inputs/state.json", capsule.canonical({"value": "fixed"}))
        cls.head = cls.commit()
        cls.spec = capsule.CapsuleSpec(
            trees={"base": cls.base, "origin": cls.origin, "head": cls.head},
            programs={"checker": "checks/checker.py", "assertion": "checks/assertion.py"},
            data={slot: ("inputs/state.json",) for slot in ("base", "origin", "head")},
        )
        cls.bundle = capsule._make_bundle(cls.root, cls.spec.record())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def write(cls, path, raw):
        target = cls.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    @classmethod
    def git(cls, *args):
        completed = subprocess.run(
            ["/usr/bin/git", "--no-replace-objects", "-C", str(cls.root), *args],
            env=capsule.GIT_ENVIRONMENT, capture_output=True, check=True,
        )
        return completed.stdout.decode("ascii").strip()

    @classmethod
    def commit(cls):
        cls.git("add", ".")
        cls.git("commit", "-qm", "Ephemeral capsule test tree")
        return cls.git("rev-parse", "HEAD")

    def attack(self, source):
        self.write("checks/attack.py", source.encode("utf-8"))
        revision = self.commit()
        spec = capsule.CapsuleSpec(trees={"base": revision},
                                   programs={"attack": "checks/attack.py"})
        return capsule.prepare(self.root, spec)

    def descriptors(self):
        return {fd: capsule._descriptor_identity(fd) for fd in capsule._inherited_fds()}

    def restore_runtime(self):
        self.write(capsule.RUNTIME_PATH, self.runtime)
        if self.git("diff", "--name-only", "HEAD", "--", capsule.RUNTIME_PATH):
            self.git("add", capsule.RUNTIME_PATH)
            self.git("commit", "-qm", "Restore exact fixture runtime", "--", capsule.RUNTIME_PATH)

    def test_launch_and_collection_boundaries_reap_real_guardians(self):
        for boundary in ("constructor-signal", "thread-signal", "launch-return",
                         "collection-entry", "deadline", "fileno", "stdin-close", "reused-pipe"):
            with self.subTest(boundary=boundary), capsule.Capsule(self.bundle, self.spec) as prepared:
                before, processes, foreign = self.descriptors(), [], []
                previous_trace = sys.gettrace()
                previous_handler = signal.getsignal(signal.SIGINT)
                previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
                fired = False
                ready, done = threading.Event(), threading.Event()
                receiver = None

                def receive():
                    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
                    ready.set()
                    done.wait(5)

                if boundary == "thread-signal":
                    receiver = threading.Thread(target=receive)
                    receiver.start()
                    self.assertTrue(ready.wait(3))

                class BrokenStream:
                    def __init__(self, stream, operation):
                        self.stream, self.operation, self.failed = stream, operation, False

                    @property
                    def closed(self):
                        return self.stream.closed

                    def fileno(self):
                        if self.operation == "fileno" and not self.failed:
                            self.failed = True
                            raise RuntimeError("collector fileno setup failed")
                        return self.stream.fileno()

                    def close(self):
                        if self.operation == "close" and not self.failed:
                            self.failed = True
                            raise RuntimeError("post-launch stdin close failed")
                        self.stream.close()

                def interrupt(frame, event, value):
                    nonlocal fired
                    if (not fired and boundary in ("constructor-signal", "thread-signal")
                            and frame.f_code is subprocess.Popen.__init__.__code__ and event == "return"
                            and frame.f_locals.get("args", [None] * 5)[4]
                            == capsule.PYTHON_STARTUP + capsule.BOOTSTRAP):
                        processes.append(frame.f_locals["self"])
                        fired = True
                        if receiver is None:
                            os.kill(os.getpid(), signal.SIGINT)
                        else:
                            handled = threading.Event()
                            installed = signal.getsignal(signal.SIGINT)

                            def observe(number, interrupted_frame):
                                installed(number, interrupted_frame)
                                handled.set()

                            signal.signal(signal.SIGINT, observe)
                            signal.pthread_kill(receiver.ident, signal.SIGINT)
                            deadline = time.monotonic() + 3
                            while not handled.is_set() and time.monotonic() < deadline:
                                time.sleep(0.005)
                            if not handled.is_set():
                                raise AssertionError("cross-thread SIGINT was not delivered")
                    if (frame.f_code is capsule._ExecutionInterpreter.launch.__code__ and event == "return"
                            and frame.f_locals.get("source") == capsule.BOOTSTRAP and value is not None):
                        process = value[0]
                        if not processes:
                            processes.append(process)
                        if boundary == "launch-return":
                            fired = True
                            raise KeyboardInterrupt("guardian handle handoff interrupted")
                        if boundary == "fileno":
                            process.stdout = BrokenStream(process.stdout, "fileno")
                            fired = True
                        if boundary == "stdin-close":
                            process.stdin = BrokenStream(process.stdin, "close")
                            fired = True
                    if (boundary == "collection-entry" and frame.f_code is capsule._collect.__code__
                            and event == "call" and not fired):
                        fired = True
                        raise KeyboardInterrupt("collector has not entered its body")
                    if (boundary == "reused-pipe" and frame.f_code is capsule._Child.close_fd.__code__
                            and event == "return" and not fired):
                        owner, fd = frame.f_locals["self"], frame.f_locals["fd"]
                        if owner.fds and fd == owner.fds[0]:
                            fired = True
                            replacement = capsule.SealedBytes(b"unrelated live fd", "foreign-pipe", 100)
                            os.dup2(replacement.fd, fd)
                            foreign.append((replacement, fd))
                            raise KeyboardInterrupt("closed liveness reader integer was reused")
                    return interrupt

                real_clock = time.monotonic

                def clock():
                    nonlocal fired
                    if inspect.currentframe().f_back.f_code is capsule._collect.__code__ and not fired:
                        fired = True
                        raise RuntimeError("collector deadline setup failed")
                    return real_clock()

                try:
                    with ExitStack() as stack:
                        if boundary == "deadline":
                            stack.enter_context(mock.patch.object(time, "monotonic", new=clock))
                        sys.settrace(interrupt)
                        try:
                            with self.assertRaises((KeyboardInterrupt, RuntimeError)):
                                prepared.execute("checker", {}, timeout=3)
                        finally:
                            sys.settrace(previous_trace)
                    self.assertTrue(fired)
                    self.assertEqual(len(processes), 1)
                    process = processes[0]
                    self.assertIsNotNone(process.returncode)
                    with self.assertRaises(ChildProcessError):
                        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    self.assertTrue(all(stream is None or stream.closed
                                        for stream in (process.stdin, process.stdout, process.stderr)))
                    for replacement, fd in foreign:
                        self.assertEqual(os.pread(fd, 100, 0), replacement.read())
                    self.assertEqual(signal.getsignal(signal.SIGINT), previous_handler)
                    self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), previous_mask)
                finally:
                    sys.settrace(previous_trace)
                    signal.signal(signal.SIGINT, previous_handler)
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                    done.set()
                    if receiver is not None:
                        receiver.join(3)
                        self.assertFalse(receiver.is_alive())
                    for process in processes:
                        capsule._finish_child(process)
                    for replacement, fd in foreign:
                        capsule._close_owned_fd(fd, replacement.identity)
                        replacement.close()
                self.assertEqual(self.descriptors(), before)
                self.assertEqual(prepared.execute("assertion", {}).value["status"], "pass")

    def test_probe_and_git_launch_handoff_interruptions_are_reaped(self):
        for kind in ("probe", "git"):
            before, processes = self.descriptors(), []
            previous_trace = sys.gettrace()

            def interrupt(frame, event, value):
                if (event == "return" and value is not None
                        and ((kind == "probe" and frame.f_code is capsule._ExecutionInterpreter.launch.__code__
                              and frame.f_locals.get("source") == capsule.PYTHON_PROBE)
                             or (kind == "git" and frame.f_code is capsule._Child.start.__code__
                                 and frame.f_locals["self"].command[0] == capsule.GIT))):
                    processes.append(value[0])
                    raise KeyboardInterrupt("launch handoff interrupted")
                return interrupt

            try:
                with self.subTest(kind=kind), self.assertRaises(KeyboardInterrupt):
                    sys.settrace(interrupt)
                    if kind == "probe":
                        capsule._probe_python()
                    else:
                        capsule.head_commit(self.root)
                sys.settrace(previous_trace)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].returncode)
                with self.assertRaises(ChildProcessError):
                    os.waitid(os.P_PID, processes[0].pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                self.assertEqual(self.descriptors(), before)
            finally:
                sys.settrace(previous_trace)
                for process in processes:
                    capsule._finish_child(process)

    def test_python_child_and_caller_retain_the_original_signal_mask(self):
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        try:
            expected = sorted(int(number) for number in signal.pthread_sigmask(signal.SIG_BLOCK, ()))
            with self.attack(
                "import signal\n"
                "def capsule_main(request, context):\n"
                "    return sorted(int(number) for number in signal.pthread_sigmask(signal.SIG_BLOCK, ()))\n"
            ) as prepared:
                self.assertEqual(prepared.execute("attack", {}).value, expected)
            self.assertEqual(sorted(int(number) for number in signal.pthread_sigmask(signal.SIG_BLOCK, ())),
                             expected)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def test_nested_launch_and_collection_interruptions_reap_before_guardian_exit(self):
        for boundary in ("launch", "collection"):
            observation = self.root / "nested-interruption.jsonl"
            shim = f"""
_interrupted_child=None
_original_launch=_ExecutionInterpreter.launch
_original_collect=_collect
_original_exit=_Child.__exit__
def _interrupted_launch(self,*args,**kwargs):
    global _interrupted_child
    result=_original_launch(self,*args,**kwargs)
    _interrupted_child=result[0]
    if {boundary!r}=='launch':
        raise KeyboardInterrupt('nested launch handoff interrupted')
    return result
def _interrupted_collect(process,*args,**kwargs):
    if process is _interrupted_child:
        raise RuntimeError('nested collection setup failed')
    return _original_collect(process,*args,**kwargs)
def _observed_exit(self,*args):
    try:
        return _original_exit(self,*args)
    finally:
        if self.process is not None and self.process is _interrupted_child:
            try:
                os.waitid(os.P_PID,self.process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
                waitable=True
            except ChildProcessError:
                waitable=False
            record={{'waitable':waitable,'returncode':self.process.returncode,
                     'streams_closed':all(stream is None or stream.closed for stream in
                         (self.process.stdin,self.process.stdout,self.process.stderr))}}
            with open({str(observation)!r},'a') as stream:
                stream.write(json.dumps(record)+'\\n')
_ExecutionInterpreter.launch=_interrupted_launch
_collect=_interrupted_collect
_Child.__exit__=_observed_exit
"""
            self.write(capsule.RUNTIME_PATH, self.runtime + shim.encode("utf-8"))
            try:
                revision = self.commit()
                spec = capsule.CapsuleSpec(
                    trees={"base": revision, "origin": self.origin, "head": self.head},
                    programs=self.spec.programs, data=self.spec.data)
                before = self.descriptors()
                with self.subTest(boundary=boundary), capsule.prepare(self.root, spec) as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("checker", {}, timeout=3)
                records = [json.loads(line) for line in observation.read_text().splitlines()]
                self.assertEqual(len(records), 1)
                self.assertFalse(records[0]["waitable"])
                self.assertIsNotNone(records[0]["returncode"])
                self.assertTrue(records[0]["streams_closed"])
                self.assertEqual(self.descriptors(), before)
            finally:
                observation.unlink(missing_ok=True)
                self.restore_runtime()
            self.test_real_isolated_launcher_ignores_swapped_runtime_and_classifier_paths()

    def test_exact_checker_assertion_module_and_three_tree_data_execute(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            result = prepared.execute("checker", {"round": 2})
        self.assertEqual(result.value["checker"], "trusted-module")
        assertion = result.value["assertion"]
        self.assertEqual(assertion["value"], {
            "module": "trusted-module", "values": {"base": "base", "origin": "broken", "head": "fixed"},
            "request": {"round": 2}, "status": "pass",
        })
        self.assertEqual(assertion["receipt"]["program"], "assertion")
        self.assertEqual(result.receipt["program"], "checker")
        self.assertEqual(assertion["receipt"]["artifact_sha256"], result.receipt["artifact_sha256"])

    def test_root_only_policy_fixture_preserves_real_nested_execution_and_receipts(self):
        real_open = open
        actual_policy = Path("/proc/sys/fs/suid_dumpable").read_bytes()

        def root_only(path, *args, **kwargs):
            if path == "/proc/sys/fs/suid_dumpable":
                return io.BytesIO(b"2\n")
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", new=root_only):
            with capsule.Capsule(self.bundle, self.spec) as prepared:
                result = prepared.execute("checker", {})
        self.assertEqual(result.value["assertion"]["value"]["status"], "pass")
        key = b"test-only-root-dump-policy-key-123"
        self.assertEqual(capsule.verify_receipt(capsule.sign_receipt(result, key), key, result),
                         result.receipt)
        self.assertEqual(Path("/proc/sys/fs/suid_dumpable").read_bytes(), actual_policy)

    def test_outer_and_nested_guardians_deny_same_uid_access_at_kernel_exec_entry(self):
        observation_path = self.root / "exec-entry-observations.jsonl"
        observer = (
            inspect.getsource(_wait_owned_stop) + "\n" + inspect.getsource(_observe_owned_exec)
            + f"""
_exec_observations=[]
_exec_popen=subprocess.Popen
def _observed_popen(command,**options):
    process={_observe_owned_exec.__name__}(_exec_popen,command,options,_exec_observations)
    with open({str(observation_path)!r},'a') as output:
        output.write(json.dumps(_exec_observations[-1])+'\\n')
    return process
subprocess.Popen=_observed_popen
"""
        )
        self.write(capsule.RUNTIME_PATH, self.runtime + b"\n" + observer.encode("utf-8"))
        try:
            revision = self.commit()
            spec = capsule.CapsuleSpec(
                trees={"base": revision, "origin": self.origin, "head": self.head},
                programs=self.spec.programs, data=self.spec.data)
            outer = []
            real_popen = subprocess.Popen

            def observe(command, **options):
                return _observe_owned_exec(real_popen, command, options, outer)

            with capsule.prepare(self.root, spec) as prepared:
                with mock.patch.object(subprocess, "Popen", side_effect=observe):
                    result = prepared.execute("checker", {})
            expected = {"same_uid": True, "fd_access_errno": [errno.EACCES] * 9, "proc_fd_owner": 0}
            self.assertEqual(outer, [expected])
            self.assertEqual([json.loads(line) for line in observation_path.read_text().splitlines()],
                             [expected])
            self.assertEqual(result.value["assertion"]["value"]["status"], "pass")
            self.assertEqual(result.value["assertion"]["receipt"]["program"], "assertion")
            key = b"test-only-protected-exec-receipt-key"
            self.assertEqual(capsule.verify_receipt(capsule.sign_receipt(result, key), key, result),
                             result.receipt)
        finally:
            observation_path.unlink(missing_ok=True)
            self.restore_runtime()
        self.test_real_isolated_launcher_ignores_swapped_runtime_and_classifier_paths()

    def test_restored_runtime_fixture_is_the_classifier_git_authority(self):
        self.write(capsule.RUNTIME_PATH, b"raise RuntimeError('instrumented fixture runtime')\n")
        self.commit()
        try:
            self.restore_runtime()
            self.test_real_isolated_launcher_ignores_swapped_runtime_and_classifier_paths()
        finally:
            self.restore_runtime()

    def test_preparation_probes_different_python_once_and_reuses_admission_for_execution(self):
        with (
            mock.patch.object(capsule, "_probe_python", wraps=capsule._probe_python) as probe,
        ):
            with capsule.prepare(self.root, self.spec) as prepared:
                nested = prepared.execute("checker", {}).value
                direct = prepared.execute("assertion", {}).value
                self.assertEqual(nested["assertion"]["value"], direct)
                self.assertEqual(direct["status"], "pass")
            probe.assert_called_once()

    def test_prepared_interpreter_change_rejects_before_new_execution_resources(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            with (
                mock.patch.object(capsule, "_interpreter_identity", return_value=("changed",)),
                mock.patch.object(prepared.bundle_fd, "read", side_effect=AssertionError) as read,
                mock.patch.object(os, "memfd_create", side_effect=AssertionError) as create,
                mock.patch.object(os, "fork", side_effect=AssertionError) as fork,
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    prepared.execute("checker", {})
                for operation in (read, create, fork, launch):
                    operation.assert_not_called()

    def test_receipt_names_exact_executed_descriptor_bytes_and_argv(self):
        seen = {}
        real_popen = subprocess.Popen

        def capture(command, **kwargs):
            seen["argv"] = command
            runtime, program, request, artifacts, _, image = kwargs["pass_fds"]
            self.assertEqual(kwargs["executable"], f"/proc/self/fd/{image}")
            self.assertEqual(os.fstat(image).st_mode & 0o777, 0o111)
            seen["digests"] = {
                "runtime_sha256": capsule.digest(os.pread(runtime, capsule.MAX_PROGRAM_BYTES, 0)),
                "program_sha256": capsule.digest(os.pread(program, capsule.MAX_PROGRAM_BYTES, 0)),
                "request_sha256": capsule.digest(os.pread(request, capsule.MAX_REQUEST_BYTES, 0)),
                "artifact_sha256": capsule.digest(os.pread(artifacts, capsule.MAX_BUNDLE_BYTES, 0)),
            }
            return real_popen(command, **kwargs)

        with capsule.Capsule(self.bundle, self.spec) as prepared:
            with mock.patch.object(subprocess, "Popen", side_effect=capture):
                result = prepared.execute("assertion", {"round": 3})
        receipt = result.receipt
        self.assertEqual(receipt["argv"], seen["argv"])
        self.assertEqual({key: receipt[key] for key in seen["digests"]}, seen["digests"])
        self.assertEqual(receipt["output_sha256"], capsule.digest(result.output_bytes))
        self.assertEqual(receipt["payload_sha256"], capsule.digest(capsule.canonical({"round": 3})))
        paths = {(entry["tree"], entry["path"]) for entry in receipt["loaded"]}
        for slot in ("base", "origin", "head"):
            self.assertIn((slot, "inputs/state.json"), paths)
        self.assertIn(("base", "checks/helper.py"), paths)

    def test_near_output_limit_loaded_receipts_remain_usable_without_transport_growth(self):
        self.write("checks/bounds.py", b"""def capsule_main(request, context):
    if request.get('nested'):
        return context.invoke('bounds', {'paths': request['paths']})
    for path in request['paths']:
        context.entry('base', path)
    return None
""")
        revision = self.commit()
        spec = capsule.CapsuleSpec(trees={"base": revision},
                                   programs={"bounds": "checks/bounds.py"})
        bundle = capsule._Bundle(capsule._make_bundle(self.root, spec.record()))
        initial = [bundle.artifacts[("base", record["path"])] for record in bundle.modules.values()]
        binding = {
            "version": capsule.VERSION, "program": "bounds", "nonce": "0" * 64,
            **{field: "0" * 64 for field in ("program_sha256", "runtime_sha256",
               "artifact_sha256", "request_sha256", "payload_sha256")},
        }
        paths = [f"absent/{index:04d}" for index in range(1000)]
        metadata = [{"tree": "base", "path": path, "role": "data", "mode": None,
                     "blob": None, "sha256": None, "size": None} for path in paths]
        size = len(capsule.canonical({
            "binding": binding, "result": None, "loaded": initial + metadata,
            "diagnostics": {"stdout_sha256": capsule.digest(b""), "stderr_sha256": capsule.digest(b"")},
        }))
        key = b"test-only-capsule-signing-key-1234"
        collect = capsule._collect
        overhead = None
        for crossing in ("receipt", "signed-wrapper"):
            target = capsule.MAX_OUTPUT_BYTES - (128 if overhead is None else overhead + 40)
            padding, extra = divmod(target - size, len(paths))
            declared = tuple(path + "x" * (padding + (index < extra))
                             for index, path in enumerate(paths))
            self.assertLessEqual(max(len(path.encode("utf-8")) for path in declared), 1024)
            full = capsule.CapsuleSpec(trees=spec.trees, programs=spec.programs,
                                       data={"base": declared})
            observed = []

            def capture(process, timeout, limit, *args, **kwargs):
                status, stdout, stderr = collect(process, timeout, limit, *args, **kwargs)
                observed.append((len(stdout), limit))
                return status, stdout, stderr

            with self.subTest(crossing=crossing), capsule.prepare(self.root, full) as prepared:
                with mock.patch.object(capsule, "_collect", side_effect=capture):
                    result = prepared.execute("bounds", {"paths": list(declared)})
                self.assertEqual(observed, [(target, capsule.MAX_OUTPUT_BYTES + capsule.MAX_DIAGNOSTIC_BYTES)])
                overhead = len(result.receipt_bytes) - target
                self.assertIsNone(result.value)
                self.assertEqual(len(result.receipt["loaded"]), len(initial) + len(declared))
                self.assertEqual(
                    {entry["path"] for entry in result.receipt["loaded"] if entry["role"] == "data"},
                    set(declared),
                )
                if crossing == "receipt":
                    self.assertGreater(len(result.receipt_bytes), capsule.MAX_OUTPUT_BYTES)
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("bounds", {"paths": list(declared), "nested": True})
                else:
                    self.assertLessEqual(len(result.receipt_bytes), capsule.MAX_OUTPUT_BYTES)
                signed = capsule.sign_receipt(result, key)
                self.assertGreater(len(signed), capsule.MAX_OUTPUT_BYTES)
                self.assertLessEqual(len(result.receipt_bytes), capsule.MAX_RECEIPT_BYTES)
                self.assertLessEqual(len(signed), capsule.MAX_SIGNED_RECEIPT_BYTES)
                self.assertEqual(capsule.verify_receipt(signed, key, result), result.receipt)

    def test_receipt_and_signed_bounds_accept_exact_sizes_and_reject_one_byte_over(self):
        key = b"test-only-capsule-signing-key-1234"
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            result = prepared.execute("assertion", {})
            signed = capsule.sign_receipt(result, key)
            receipt_size, signed_size = len(result.receipt_bytes), len(signed)
            self.assertEqual(
                signed_size - receipt_size,
                capsule.MAX_SIGNED_RECEIPT_BYTES - capsule.MAX_RECEIPT_BYTES,
            )
            with mock.patch.multiple(capsule, MAX_RECEIPT_BYTES=receipt_size,
                                     MAX_SIGNED_RECEIPT_BYTES=signed_size):
                exact = prepared.execute("assertion", {})
                self.assertEqual(len(exact.receipt_bytes), receipt_size)
                self.assertEqual(capsule.verify_receipt(capsule.sign_receipt(exact, key), key, exact),
                                 exact.receipt)
                with mock.patch.object(capsule, "MAX_RECEIPT_BYTES", receipt_size - 1):
                    for operation in (
                        lambda: prepared.execute("assertion", {}),
                        lambda: capsule.ExecutionResult(result.receipt_bytes, result.output_bytes),
                        lambda: result.receipt,
                        lambda: capsule.sign_receipt(result, key),
                        lambda: capsule.verify_receipt(signed, key, result),
                    ):
                        with self.assertRaises(capsule.CapsuleError):
                            operation()
                with mock.patch.object(capsule, "MAX_SIGNED_RECEIPT_BYTES", signed_size - 1):
                    with self.assertRaises(capsule.CapsuleError):
                        capsule.sign_receipt(result, key)
                    with self.assertRaises(capsule.CapsuleError):
                        capsule.verify_receipt(signed, key, result)
        with self.assertRaises(capsule.CapsuleError):
            capsule.ExecutionResult(result.receipt_bytes, capsule.canonical("x" * capsule.MAX_OUTPUT_BYTES))

    def test_swap_restore_every_former_path_cannot_change_sealed_execution(self):
        paths = ("checks/checker.py", "checks/assertion.py", "checks/helper.py",
                 "inputs/state.json", capsule.RUNTIME_PATH)
        saved = {path: (self.root / path).read_bytes() for path in paths}
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            try:
                for path in paths:
                    self.write(path, b"raise RuntimeError('pathname substitution executed')\n")
                self.write("checks/__init__.py", b"raise RuntimeError('package hijack')\n")
                result = prepared.execute("checker", {"input": "original"})
            finally:
                for path, raw in saved.items():
                    self.write(path, raw)
                (self.root / "checks/__init__.py").unlink()
        self.assertEqual(result.value["assertion"]["value"]["status"], "pass")
        self.assertEqual(saved, {path: (self.root / path).read_bytes() for path in paths})

    def test_checkout_directory_move_and_git_disappearance_do_not_change_execution(self):
        moved = self.root.with_name(self.root.name + "-moved")
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            before = prepared.execute("assertion", {"path": "not-authority"})
            self.root.rename(moved)
            try:
                after = prepared.execute("assertion", {"path": "not-authority"})
            finally:
                moved.rename(self.root)
        self.assertEqual(before.value, after.value)
        for field in ("program_sha256", "runtime_sha256", "artifact_sha256", "payload_sha256"):
            self.assertEqual(before.receipt[field], after.receipt[field])

    def test_request_object_and_old_request_path_swap_after_sealing_are_inert(self):
        request = {"authority": "original"}
        old_path = self.root / "former-request.json"
        old_path.write_bytes(capsule.canonical(request))
        real_popen = subprocess.Popen

        def swap(command, **kwargs):
            request["authority"] = "forged"
            old_path.write_bytes(capsule.canonical(request))
            return real_popen(command, **kwargs)

        try:
            with capsule.Capsule(self.bundle, self.spec) as prepared:
                with mock.patch.object(subprocess, "Popen", side_effect=swap):
                    result = prepared.execute("assertion", request)
            self.assertEqual(result.value["request"], {"authority": "original"})
        finally:
            old_path.unlink()

    def test_pre_fix_validate_then_reopen_control_signs_forged_restored_program(self):
        # The abandoned #189 launch protocol: validate source, run its pathname
        # with --stdin, then inspect restored bytes before signing child output.
        path = self.root / "former-assertion.py"
        original = (b"import json,sys\nx=json.load(sys.stdin)\n"
                    b"status = 'pass' if x['head_state'] == 'fixed' else 'fail'\n"
                    b"print(json.dumps({'status':status,'binding':x}))\n")
        forged = original.replace(b"x['head_state'] == 'fixed'", b"True")
        request = {"origin": self.origin, "head": self.head, "head_state": "broken",
                   "program_sha256": capsule.digest(original)}
        path.write_bytes(original)
        checked = capsule.digest(path.read_bytes())
        try:
            for state, expected in (("broken", "fail"), ("fixed", "pass")):
                baseline = subprocess.run(
                    [capsule.PYTHON, "-I", path.name, "--stdin"], cwd=self.root,
                    input=capsule.canonical({**request, "head_state": state}),
                    capture_output=True, check=True,
                )
                self.assertEqual(json.loads(baseline.stdout)["status"], expected)
            path.write_bytes(forged)
            try:
                completed = subprocess.run(
                    [capsule.PYTHON, "-I", path.name, "--stdin"], cwd=self.root,
                    input=capsule.canonical(request), capture_output=True, check=True,
                )
            finally:
                path.write_bytes(original)
            self.assertEqual(capsule.digest(path.read_bytes()), checked)
            result = json.loads(completed.stdout)
            self.assertEqual(result, {"status": "pass", "binding": request})
            key = b"test-only-pre-fix-negative-key!!!"
            signature = hmac.new(key, completed.stdout, hashlib.sha256).digest()
            self.assertTrue(hmac.compare_digest(signature, hmac.new(key, completed.stdout, hashlib.sha256).digest()))
        finally:
            path.unlink()
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            with capsule.SealedBytes(forged, "forged-program", capsule.MAX_PROGRAM_BYTES) as substituted:
                with mock.patch.object(capsule._Bundle, "program", return_value=substituted.read()):
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("assertion", request)

    def test_real_production_classifier_uses_unchanged_semantic_predicate(self):
        cases = json.loads((ROOT / "scripts/workflow_pilot/tests/fixtures/event_classification.json").read_bytes())
        spec = capsule.CapsuleSpec(trees={"base": self.base},
                                   programs={"classifier": "scripts/workflow_pilot/event_classifier.py"})
        with capsule.prepare(self.root, spec) as prepared:
            for case in cases["cases"]:
                with self.subTest(case=case["id"]):
                    request = {"event_name": case["event_name"], "payload": case["payload"], **case["runner"]}
                    actual = prepared.execute("classifier", request)
                    expected = json.loads(event_classifier.classify_event(**request).canonical_json())
                    self.assertEqual(actual.value, expected)

    def test_real_isolated_launcher_ignores_swapped_runtime_and_classifier_paths(self):
        from scripts.workflow_pilot.tests.test_event_classifier import _launcher_command

        case = json.loads((ROOT / "scripts/workflow_pilot/tests/fixtures/event_classification.json").read_bytes())["cases"][0]
        event_path, output_path = self.root / "event.json", self.root / "event.out"
        event_path.write_bytes(capsule.canonical(case["payload"]))
        paths = (capsule.RUNTIME_PATH, "scripts/workflow_pilot/event_classifier.py")
        saved = {path: (self.root / path).read_bytes() for path in paths}
        command = _launcher_command(case, event_path, output_path)
        command[2] = str(self.root / "scripts/workflow_pilot/isolated_launcher.py")
        try:
            for path in paths:
                self.write(path, b"raise RuntimeError('substituted source executed')\n")
            completed = subprocess.run(
                command, cwd=self.root, capture_output=True,
                env={**capsule.ENVIRONMENT, "PYTHONPATH": str(self.root), "GIT_DIR": "/invalid"},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            expected = event_classifier.classify_event(
                case["event_name"], case["payload"], **case["runner"])
            self.assertEqual(completed.stdout.decode(), expected.canonical_json())
            self.assertIn("classification=" + expected.classification, output_path.read_text())
        finally:
            for path, raw in saved.items():
                self.write(path, raw)
            event_path.unlink()
            output_path.unlink(missing_ok=True)

    def test_event_snapshot_cannot_follow_a_path_swap_after_descriptor_open(self):
        path, backup = self.root / "event-snapshot.json", self.root / "event-snapshot.saved"
        original, forged = {"state": "trusted"}, {"state": "forged"}
        path.write_bytes(capsule.canonical(original))
        real_read = os.read
        swapped = False

        def read(fd, count):
            nonlocal swapped
            if not swapped:
                swapped = True
                path.rename(backup)
                path.write_bytes(capsule.canonical(forged))
            return real_read(fd, count)

        try:
            with mock.patch.object(os, "read", side_effect=read):
                try:
                    result = event_classifier.load_event(path)
                except event_classifier.EventClassificationError as error:
                    self.assertIn("changed while being read", str(error))
                else:
                    self.assertEqual(result, original)
            self.assertTrue(swapped)
        finally:
            path.unlink()
            backup.unlink(missing_ok=True)

    def test_immutable_seals_reject_writes_resize_mapping_and_seal_changes(self):
        with capsule.SealedBytes(b"immutable", "test", 100) as owned:
            self.assertEqual(capsule.fcntl.fcntl(owned.fd, capsule.fcntl.F_GET_SEALS) & capsule.SEALS,
                             capsule.SEALS)
            for operation in (
                lambda: os.write(owned.fd, b"forged"),
                lambda: os.ftruncate(owned.fd, 0),
                lambda: os.ftruncate(owned.fd, 1024),
                lambda: mmap.mmap(owned.fd, 0, access=mmap.ACCESS_WRITE),
                lambda: capsule.fcntl.fcntl(owned.fd, capsule.fcntl.F_ADD_SEALS, capsule.SEALS),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaises(OSError):
                        operation()
            duplicate = os.dup(owned.fd)
            try:
                with self.assertRaises(OSError):
                    os.pwrite(duplicate, b"x", 0)
            finally:
                os.close(duplicate)
            self.assertEqual(owned.read(), b"immutable")

    def test_mutable_and_regular_descriptors_are_not_accepted(self):
        fd = os.memfd_create("unsealed-test", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.write(fd, b"same bytes")
            with self.assertRaisesRegex(capsule.CapsuleError, "fully sealed"):
                capsule._read_descriptor(fd, 100)
        finally:
            os.close(fd)
        with (self.root / "inputs/state.json").open("rb") as file:
            with self.assertRaises(capsule.CapsuleError):
                capsule._read_descriptor(file.fileno(), 100)

    def test_reused_descriptor_rejects_without_closing_unowned_replacement(self):
        owned = capsule.SealedBytes(b"original", "test", 100)
        replacement = capsule.SealedBytes(b"replaced", "test", 100)
        number = owned.fd
        try:
            os.dup2(replacement.fd, number)
            with self.assertRaisesRegex(capsule.CapsuleError, "reused"):
                owned.read()
            owned.close()
            self.assertEqual(os.pread(number, 100, 0), b"replaced")
        finally:
            os.close(number)
            replacement.close()

    def test_replaced_runtime_and_unexpected_inherited_fd_fail_before_worker(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            old = prepared.runtime_fd.fd
            os.dup2(prepared.bundle_fd.fd, old)
            try:
                with self.assertRaises(capsule.CapsuleError):
                    prepared.execute("assertion", {})
            finally:
                prepared.runtime_fd.close()
                os.close(old)
        real_popen = subprocess.Popen
        with capsule.SealedBytes(b"unrelated", "extra", 100) as extra:
            def inherit(command, **kwargs):
                kwargs["pass_fds"] = (*kwargs["pass_fds"], extra.fd)
                return real_popen(command, **kwargs)
            with capsule.Capsule(self.bundle, self.spec) as prepared:
                with mock.patch.object(subprocess, "Popen", side_effect=inherit):
                    with self.assertRaisesRegex(capsule.CapsuleError, "unexpected inherited"):
                        prepared.execute("assertion", {})

    def test_artifact_identity_and_complete_closure_mutations_fail(self):
        original = capsule.parse(self.bundle, capsule.MAX_BUNDLE_BYTES)
        changes = {
            "missing": lambda value: value["artifacts"].pop(),
            "extra": lambda value: value["artifacts"].append({**value["artifacts"][0], "path": "extra"}),
            "duplicate": lambda value: value["artifacts"].append(value["artifacts"][0]),
            "wrong-mode": lambda value: value["artifacts"][0].update(mode="100755"),
            "wrong-blob": lambda value: value["artifacts"][0].update(blob="a" * 40),
            "wrong-digest": lambda value: value["artifacts"][0].update(sha256="a" * 64),
            "wrong-role": lambda value: value["artifacts"][0].update(role="program"),
            "wrong-tree": lambda value: value["artifacts"][0].update(tree="other"),
            "wrong-version-type": lambda value: value.update(version=True),
            "wrong-package-type": lambda value: value["modules"]["checks"].update(package=1),
            "missing-module": lambda value: value["modules"].pop("checks.helper"),
            "extra-module": lambda value: value["modules"].update(ambient={"path": "ambient.py", "package": False}),
            "missing-proof": lambda value: value["objects"].pop(),
            "duplicate-proof": lambda value: value["objects"].append(value["objects"][0]),
            "wrong-proof": lambda value: value["objects"][0].update(bytes="Zm9yZ2Vk"),
            "forged-spec": lambda value: value["spec"]["trees"].update(base=self.head),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                value = copy.deepcopy(original)
                change(value)
                with self.assertRaises(capsule.CapsuleError):
                    with capsule.Capsule(capsule.canonical(value), self.spec):
                        self.fail("malformed capsule was accepted")

    def test_git_proof_collection_stops_before_reading_over_limit(self):
        paths = tuple("inputs/proof-" + str(index) + "/"
                      + "/".join(f"level-{level}" for level in range(24)) + "/state.json"
                      for index in range(3))
        for index, path in enumerate(paths):
            self.write(path, capsule.canonical({"value": index}))
        self.write("checks/proof.py",
                   b"import json\n"
                   b"def capsule_main(request, context):\n"
                   b"    return [json.loads(context.read('base', path))['value']\n"
                   b"            for path in request['paths']]\n")
        revision = self.commit()
        spec = capsule.CapsuleSpec(trees={"base": revision},
                                   programs={"proof": "checks/proof.py"}, data={"base": paths})
        raw = capsule._make_bundle(self.root, spec.record())
        with capsule.Capsule(raw, spec) as prepared:
            self.assertEqual(prepared.execute("proof", {"paths": list(paths)}).value, [0, 1, 2])
        source = capsule._GitSource(self.root)
        with mock.patch.object(capsule, "MAX_ENTRIES", 8):
            with self.assertRaisesRegex(capsule.CapsuleError, "bounded Git object closure"):
                capsule._Bundle(raw, spec.record())
            with (
                mock.patch.object(capsule, "_GitSource", return_value=source),
                mock.patch.object(capsule, "_git", wraps=capsule._git) as read,
                mock.patch.object(capsule, "canonical", wraps=capsule.canonical) as serialize,
            ):
                with self.assertRaisesRegex(capsule.CapsuleError, "bounded Git object closure"):
                    capsule._make_bundle(self.root, spec.record())
                self.assertEqual(len(source.objects), capsule.MAX_ENTRIES * 8)
                self.assertEqual(read.call_count, capsule.MAX_ENTRIES * 8)
                serialize.assert_not_called()
                oid, (kind, content) = next(iter(source.objects.items()))
                read.reset_mock()
                self.assertEqual(source.get(kind, oid), content)
                read.assert_not_called()

    def test_shared_prefix_tree_parsing_is_linear_in_unique_proof_bytes(self):
        paths = tuple(f"inputs/cached/shared/leaf/value-{index:03d}.json" for index in range(64))
        contents = {path: capsule.canonical({"value": index}) for index, path in enumerate(paths)}
        for path, content in contents.items():
            self.write(path, content)
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={"base": revision}, programs={"assertion": "checks/assertion.py"},
            data={"base": paths},
        )
        consumed, tree_sizes = {}, {}

        class CountedTree(bytes):
            def find(self, sub, start=0, *args):
                if sub == b" " and start == 0:
                    oid = capsule._oid("tree", self)
                    consumed[oid] = consumed.get(oid, 0) + len(self)
                return super().find(sub, start, *args)

        git = capsule._git

        def read(root, *arguments):
            raw = git(root, *arguments)
            if arguments[:2] == ("cat-file", "tree"):
                tree_sizes[arguments[2]] = len(raw)
                return CountedTree(raw)
            return raw

        with mock.patch.object(capsule, "_git", side_effect=read):
            raw = capsule._make_bundle(self.root, spec.record())
        with self.subTest(stage="construction"):
            self.assertEqual(sum(consumed.values()), sum(tree_sizes.values()))
            self.assertEqual(consumed, tree_sizes)
        consumed.clear()
        decode = capsule.base64.b64decode

        def decode_proof(value, **kwargs):
            content = decode(value, **kwargs)
            return (CountedTree(content) if capsule._oid("tree", content) in tree_sizes
                    else content)

        with mock.patch.object(capsule.base64, "b64decode", side_effect=decode_proof):
            bundle = capsule._Bundle(raw, spec.record())
        with self.subTest(stage="independent-validation"):
            self.assertEqual(sum(consumed.values()), sum(tree_sizes.values()))
            self.assertEqual(consumed, tree_sizes)
        self.assertEqual({path: bundle.content("base", path) for path in paths}, contents)

    def test_tree_cache_revalidates_replaced_proofs_and_rejects_bad_suffixes(self):
        bundle = capsule._Bundle(self.bundle, self.spec.record())
        for source in (capsule._ObjectSource(dict(bundle.objects)), capsule._GitSource(self.root)):
            with self.subTest(source=type(source).__name__):
                oid = source.tree(self.base)
                expected = source.lookup(self.base, "checks/helper.py")
                kind, content = source.objects[oid]
                altered = content[:-1] + bytes([content[-1] ^ 1])
                source.objects[oid] = (kind, altered)
                with self.assertRaises(capsule.CapsuleError):
                    source.lookup(self.base, "checks/helper.py")
                source.objects[oid] = (kind, content)
                self.assertEqual(source.lookup(self.base, "checks/helper.py"), expected)
        value = capsule.parse(self.bundle, capsule.MAX_BUNDLE_BYTES)
        index = next(index for index, entry in enumerate(value["objects"]) if entry["kind"] == "tree")
        for change in ("oid", "bytes"):
            mutated = copy.deepcopy(value)
            mutated["objects"][index][change] = ("0" * 40 if change == "oid" else
                                                 capsule.base64.b64encode(altered).decode("ascii"))
            with self.subTest(change=change), self.assertRaises(capsule.CapsuleError):
                capsule._Bundle(capsule.canonical(mutated), self.spec.record())
        entry = b"100644 item\0" + bytes.fromhex("a" * 40)
        for suffix in (entry, b"100644 incomplete\0"):
            tree = entry + suffix
            tree_oid = capsule._oid("tree", tree)
            commit = f"tree {tree_oid}\n".encode("ascii")
            commit_oid = capsule._oid("commit", commit)
            source = capsule._ObjectSource({
                commit_oid: ("commit", commit), tree_oid: ("tree", tree),
            })
            for attempt in range(2):
                with self.subTest(suffix=suffix, attempt=attempt), self.assertRaises(capsule.CapsuleError):
                    source.lookup(commit_oid, "item")

    def test_undeclared_program_and_data_do_not_fall_back(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            with self.assertRaises(capsule.CapsuleError):
                prepared.execute("not-declared", {})
        with self.attack("def capsule_main(request, context):\n    return context.read('base', 'unlisted.json')\n") as prepared:
            with self.assertRaises(capsule.CapsuleError):
                prepared.execute("attack", {})

    def test_missing_static_trusted_import_is_rejected_during_preparation(self):
        with self.assertRaisesRegex(capsule.CapsuleError, "outside complete trusted closure"):
            self.attack("import nonexistent_capsule_module\n"
                        "def capsule_main(request, context):\n    return True\n")

    def test_transitive_relative_packages_and_explicit_dynamic_closure(self):
        self.write("checks/deep/__init__.py", b"from .leaf import answer\n")
        self.write("checks/deep/leaf.py", b"answer = 41\n")
        self.write("checks/second.py", b"from .deep import answer\ndef value():\n    return answer + 1\n")
        self.write("checks/dynamic.py", b"answer = 'declared-dynamic'\n")
        self.write("checks/transitive.py",
                   b"def capsule_main(request, context):\n"
                   b"    from checks import second\n"
                   b"    return [second.value(), context.load_module('checks.dynamic').answer]\n")
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={"base": revision}, programs={"transitive": "checks/transitive.py"},
            modules=("checks.dynamic",),
        )
        with capsule.prepare(self.root, spec) as prepared:
            result = prepared.execute("transitive", {})
        self.assertEqual(result.value, [42, "declared-dynamic"])
        paths = {entry["path"] for entry in result.receipt["loaded"]}
        self.assertTrue({"checks/deep/__init__.py", "checks/deep/leaf.py",
                         "checks/second.py", "checks/dynamic.py"}.issubset(paths))
        undeclared = capsule.CapsuleSpec(trees=spec.trees, programs=spec.programs)
        with capsule.prepare(self.root, undeclared) as prepared:
            with self.assertRaises(capsule.CapsuleError):
                prepared.execute("transitive", {})

    def test_static_stdlib_package_submodules_and_exported_attributes(self):
        self.write("checks/stdlib_helper.py",
               b"from xml.etree import ElementTree\n"
               b"def tag():\n    return ElementTree.fromstring('<sealed/>').tag\n")
        with self.attack(
            "from checks import stdlib_helper\n"
            "from collections import Counter, abc\n"
            "from concurrent.futures import Future\n"
            "from os.path import basename\n"
            "from urllib import parse\n"
            "def capsule_main(request, context):\n"
            "    return {'xml': stdlib_helper.tag(), 'counts': dict(Counter('aba')),\n"
            "            'mapping': isinstance({}, abc.Mapping), 'done': Future().done(),\n"
            "            'basename': basename('sealed/input'),\n"
            "            'query': parse.parse_qs('state=fixed')}\n"
        ) as prepared:
            bundle = capsule._Bundle(prepared.bundle_fd.read())
            self.assertEqual(bundle.spec["modules"], [])
            self.assertTrue({"xml.etree.ElementTree", "collections.abc", "urllib.parse"}
                        .issubset(bundle.stdlib))
            self.assertTrue({"collections.Counter", "concurrent.futures.Future"}
                            .isdisjoint(bundle.stdlib))
            result = prepared.execute("attack", {})
        self.assertEqual(result.value, {
            "xml": "sealed", "counts": {"a": 2, "b": 1}, "mapping": True, "done": False,
            "basename": "input", "query": {"state": ["fixed"]},
        })

    def test_static_stdlib_resolution_never_uses_candidate_importers(self):
        paths = ("xml/__init__.py", "xml/etree/__init__.py", "xml/etree/ElementTree.py")
        for path in paths:
            self.write(path, b"raise RuntimeError('candidate stdlib shadow executed')\n")
        shadow = types.ModuleType("xml")
        shadow.__path__ = [str(self.root / "xml")]

        class CandidateFinder:
            def find_spec(self, fullname, path=None, target=None):
                raise AssertionError(f"ambient importer consulted: {fullname}")

        try:
            with (
                mock.patch.object(sys, "path", [str(self.root), *sys.path]),
                mock.patch.object(sys, "meta_path", [CandidateFinder(), *sys.meta_path]),
                mock.patch.dict(sys.modules, {"xml": shadow}),
            ):
                with self.attack(
                    "from xml.etree import ElementTree\n"
                    "def capsule_main(request, context):\n"
                    "    return ElementTree.fromstring('<trusted/>').tag\n"
                ) as prepared:
                    result = prepared.execute("attack", {})
            self.assertEqual(result.value, "trusted")
        finally:
            for path in paths:
                (self.root / path).unlink()

    def test_undeclared_stdlib_submodules_and_missing_exports_still_reject(self):
        attempts = (
            "__import__('xml.etree.ElementTree', fromlist=['ElementTree'])",
            "from xml.etree import nonexistent_capsule_export",
            "__import__('importlib.util', fromlist=['util'])",
            "__import__('importlib', fromlist=['util']).util",
            "importlib.import_module('importlib.util')",
            "__import__('json', fromlist=['decoder']).decoder",
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                with self.attack(
                    "import xml.etree, importlib, json\n"
                    "def capsule_main(request, context):\n"
                    "    try:\n        " + attempt + "\n"
                    "    except Exception:\n        pass\n"
                    "    return {'status': 'pass'}\n"
                ) as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("attack", {})

    def test_program_visible_module_cache_excludes_runtime_preloads(self):
        with self.attack(
            "import sys\n"
            "def capsule_main(request, context):\n"
            "    return sorted(sys.modules)\n"
        ) as prepared:
            result = prepared.execute("attack", {})
        self.assertEqual(set(result.value), {"sys", "checks", "checks.attack"})

    def test_preloaded_undeclared_dynamic_imports_and_direct_cache_reads_reject(self):
        with self.attack("""import sys
cached_import = __import__
def capsule_main(request, context):
    name = request['module']
    if request['route'] == 'index':
        return sys.modules[name].__name__
    if request['route'] == 'get':
        return sys.modules.get(name) is None
    try:
        cached_import(name)
    except Exception:
        pass
    return {'status': 'pass'}
""") as prepared:
            for name in ("__capsule_runtime__", "subprocess", "ctypes"):
                for route in ("import", "index", "get"):
                    with self.subTest(module=name, route=route):
                        request = {"module": name, "route": route}
                        if route == "get":
                            self.assertTrue(prepared.execute("attack", request).value)
                        else:
                            with self.assertRaises(capsule.CapsuleError):
                                prepared.execute("attack", request)

    def test_cached_dynamic_imports_reject_undeclared_module_table_injection(self):
        with self.attack("""import builtins, importlib, sys
cached_import = __import__
cached_builtin = builtins.__import__
cached_portable_import = importlib.__import__
cached_import_module = importlib.import_module
def capsule_main(request, context):
    sys.modules[request['module']] = sys
    try:
        if request['route'] == 'builtin':
            cached_import(request['module'])
        elif request['route'] == 'builtins':
            cached_builtin(request['module'])
        elif request['route'] == 'portable':
            cached_portable_import(request['module'])
        else:
            cached_import_module(request['module'])
    except Exception:
        pass
    return {'status': 'pass'}
""") as prepared:
            for route in ("builtin", "builtins", "portable", "importlib"):
                with self.subTest(route=route), self.assertRaises(capsule.CapsuleError):
                    prepared.execute("attack", {"module": "subprocess", "route": route})

    def test_declared_cached_imports_preserve_static_dynamic_and_nested_execution(self):
        self.write("checks/cached_dynamic.py", b"value = 'declared-dynamic'\n")
        self.write("checks/cached_package/__init__.py", b"from .. import cached_dynamic as alias\n")
        self.write("checks/cached.py", b"""import importlib, sys
from checks import helper
from checks.cached_package import alias
cached_import = __import__
def capsule_main(request, context):
    json = cached_import('json')
    dynamic = importlib.import_module('checks.cached_dynamic')
    xml = importlib.import_module('xml.etree.ElementTree')
    value = {'static': helper.value(), 'dynamic': dynamic.value,
             'alias': alias is dynamic,
             'json': json.loads('{"sealed":true}'), 'xml': xml.fromstring('<sealed/>').tag,
             'cached': sys.modules['json'] is json and sys.modules[dynamic.__name__] is dynamic
                       and sys.modules['xml.etree.ElementTree'] is xml}
    if request.get('nested'):
        return value
    return {'value': value, 'nested': context.invoke('cached', {'nested': True})}
""")
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={"base": revision}, programs={"cached": "checks/cached.py"},
            modules=("json", "xml.etree.ElementTree", "checks.cached_dynamic"),
        )
        with capsule.prepare(self.root, spec) as prepared:
            result = prepared.execute("cached", {})
        expected = {"static": "trusted-module", "dynamic": "declared-dynamic",
                    "alias": True, "json": {"sealed": True}, "xml": "sealed", "cached": True}
        self.assertEqual(result.value["value"], expected)
        self.assertEqual(result.value["nested"]["value"], expected)
        for receipt in (result.receipt, result.value["nested"]["receipt"]):
            self.assertEqual(receipt["program"], "cached")
            self.assertTrue({"checks/helper.py", "checks/cached_dynamic.py"}.issubset(
                entry["path"] for entry in receipt["loaded"]))

    def test_artifact_existence_uses_sealed_entries_after_path_swap(self):
        self.write("checks/existence.py",
               b"def capsule_main(request, context):\n"
               b"    return {path: context.entry('head', path)['mode'] is not None\n"
               b"            for path in request['paths']}\n")
        revision = self.commit()
        paths = ("inputs/state.json", "inputs/not-present.json")
        spec = capsule.CapsuleSpec(
            trees={"base": revision, "head": revision},
            programs={"existence": "checks/existence.py"}, data={"head": paths},
        )
        saved = (self.root / paths[0]).read_bytes()
        try:
            with capsule.prepare(self.root, spec) as prepared:
                request = {"paths": list(paths)}
                before = prepared.execute("existence", request)
                (self.root / paths[0]).unlink()
                self.write(paths[1], b"ambient existence is not authority\n")
                after = prepared.execute("existence", request)
            self.assertEqual(before.value, {paths[0]: True, paths[1]: False})
            self.assertEqual(after.value, before.value)
            key = b"test-only-capsule-signing-key-1234"
            for result in (before, after):
                loaded = {(entry["tree"], entry["path"]) for entry in result.receipt["loaded"]}
                self.assertTrue({("head", path) for path in paths} <= loaded)
                self.assertEqual(
                    capsule.verify_receipt(capsule.sign_receipt(result, key), key, result),
                    result.receipt,
                )
        finally:
            self.write(paths[0], saved)
            (self.root / paths[1]).unlink(missing_ok=True)

    def test_live_path_existence_cannot_change_a_receipted_verdict(self):
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        with self.attack(
            "import os\n"
            "def capsule_main(request, context):\n"
            "    return {'status': 'pass' if os.access(request['path'], os.F_OK) else 'fail'}\n"
        ) as prepared:
            child = subprocess.Popen(
                [capsule.PYTHON, "-I", "-S", "-c", "import sys; sys.stdin.buffer.read()"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                request = {"path": f"/proc/{child.pid}"}
                for alive in (True, False):
                    if not alive:
                        child.stdin.close()
                        child.wait(timeout=3)
                    self.assertEqual(os.access(request["path"], os.F_OK), alive)
                    with self.subTest(alive=alive):
                        with self.assertRaises(capsule.CapsuleError):
                            prepared.execute("attack", request)
            finally:
                child.stdin.close()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_live_chroot_and_chown_oracles_cannot_return_receipted_verdicts(self):
        source = """import errno, os
def capsule_main(request, context):
    try:
        if request['operation'] == 'chroot':
            os.chroot(request['path'])
        else:
            os.chown(request['path'], -1, -1)
    except OSError as error:
        return {'exists': error.errno != errno.ENOENT, 'errno': error.errno}
    return {'exists': True, 'errno': 0}
"""
        control = (source + "\nimport json, sys\n"
                   "print(json.dumps(capsule_main(json.loads(sys.argv[1]), None)))\n")
        path = self.root / "inputs/owned-metadata-oracle"
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        try:
            with self.attack(source) as prepared:
                for operation in ("chroot", "chown"):
                    for present in (False, True):
                        with self.subTest(operation=operation, present=present):
                            path.unlink(missing_ok=True)
                            if present:
                                path.write_bytes(b"ordinary owned file, never a chroot directory\n")
                            request = {"operation": operation, "path": str(path)}
                            observed = subprocess.run(
                                [capsule.PYTHON, "-I", "-S", "-c", control,
                                 capsule.canonical(request).decode("ascii")],
                                env=capsule.ENVIRONMENT, capture_output=True, check=True, timeout=3,
                            )
                            expected_errno = (errno.ENOTDIR if operation == "chroot" else 0) if present else errno.ENOENT
                            self.assertEqual(json.loads(observed.stdout),
                                             {"exists": present, "errno": expected_errno})
                            with self.assertRaises(capsule.CapsuleError):
                                prepared.execute("attack", request)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_os_audit_namespace_is_closed_and_denials_are_latched(self):
        bundle = capsule._Bundle(self.bundle, self.spec.record())
        for event in ("os.chroot", "os.chown", "os.chmod", "os.utime", "os.rename",
                      "os.getxattr", "os.setxattr", "os.removexattr", "os.listxattr",
                      "os.future_path_operation"):
            with self.subTest(event=event):
                guard = capsule._Guard(bundle, "", b"")
                with self.assertRaises(capsule.CapsuleError):
                    guard.audit(event, ())
                self.assertEqual(len(guard.denied), 1)
        guard = capsule._Guard(bundle, "", b"")
        guard.audit("mmap.__new__", (-1, 4096, 0, 0))
        self.assertEqual(guard.denied, [])

    def test_caught_pathname_metadata_probes_cannot_return_pass(self):
        attempts = {
            "access": "os.access(path, os.F_OK)",
            "access-effective-ids": "os.access(path, os.F_OK, effective_ids=True)",
            "access-no-follow": "os.access(path, os.F_OK, follow_symlinks=False)",
            "stat": "os.stat(path)",
            "lstat": "os.lstat(path)",
            "readlink": "os.readlink(path)",
            "statvfs": "os.statvfs(path)",
            "pathconf": "os.pathconf(path, 'PC_NAME_MAX')",
            "getcwd": "os.getcwd()",
            "chroot": "os.chroot(path)",
            "chown": "os.chown(path, -1, -1)",
            "chown-no-follow": "os.chown(path, -1, -1, follow_symlinks=False)",
            "lchown": "os.lchown(path, -1, -1)",
            "chmod": "os.chmod(path, 0o600)",
            "getxattr": "os.getxattr(path, 'user.capsule')",
            "getxattr-no-follow": "os.getxattr(path, 'user.capsule', follow_symlinks=False)",
            "listxattr": "os.listxattr(path)",
            "listxattr-no-follow": "os.listxattr(path, follow_symlinks=False)",
            "setxattr": "os.setxattr(path, 'user.capsule', b'probe')",
            "removexattr": "os.removexattr(path, 'user.capsule')",
            "utime": "os.utime(path, None)",
        }
        for label, attempt in attempts.items():
            with self.subTest(operation=label):
                with self.attack(
                    "import os\n"
                    "def capsule_main(request, context):\n"
                    "    path = request['path']\n"
                    "    try:\n        " + attempt + "\n"
                    "    except Exception:\n        pass\n"
                    "    return {'status': 'pass'}\n"
                ) as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("attack", {"path": str(self.root / "inputs/state.json")})

    def test_metadata_syscall_filters_cover_native_and_prospective_abis(self):
        policies = {
            "x86_64": (0xC000003E, {
                0, 1, 3, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20,
                22, 23, 24, 25, 28, 32, 33, 35, 39, 60, 72, 96, 97, 98,
                102, 104, 107, 108, 110, 131, 186, 201, 202, 213, 219, 228,
                229, 230, 231, 232, 233, 270, 271, 281, 291, 292, 293,
                295, 296, 302, 318, 319, 327, 328, 441,
            }, {
                "access": 21, "faccessat": 269, "faccessat2": 439, "getcwd": 79,
                "stat": 4, "lstat": 6, "newfstatat": 262, "statx": 332,
                "ustat": 136, "statfs": 137, "fstatfs": 138, "lookup_dcookie": 212,
                "readlink": 89, "readlinkat": 267,
                "getdents": 78, "getdents64": 217, "utime": 132, "utimes": 235,
                "futimesat": 261, "utimensat": 280,
                "chroot": 161, "chdir": 80, "fchdir": 81, "pivot_root": 155,
                "chown": 92, "fchown": 93, "lchown": 94, "fchownat": 260,
                "chmod": 90, "fchmod": 91, "fchmodat": 268, "fchmodat2": 452,
                "open": 2, "creat": 85, "openat": 257, "openat2": 437,
                "name_to_handle_at": 303, "open_by_handle_at": 304,
                "mkdir": 83, "rmdir": 84, "mknod": 133, "mkdirat": 258, "mknodat": 259,
                "link": 86, "symlink": 88, "linkat": 265, "symlinkat": 266,
                "unlink": 87, "unlinkat": 263, "rename": 82, "renameat": 264, "renameat2": 316,
                "truncate": 76, "ftruncate": 77, "mount": 165, "umount2": 166,
                "quotactl": 179, "quotactl_fd": 443, "acct": 163, "uselib": 134,
                "swapon": 167, "swapoff": 168, "execve": 59, "execveat": 322,
                "inotify_add_watch": 254, "fanotify_mark": 301,
                **{f"xattr-{number}": number for number in range(188, 200)},
            }),
            "aarch64": (0xC00000B7, {
                20, 21, 22, 23, 24, 25, 57, 59, 62, 63, 64, 65, 66, 67, 68,
                69, 70, 72, 73, 80, 93, 94, 98, 101, 113, 114, 115, 124,
                128, 132, 134, 135, 139, 163, 165, 169, 172, 173, 174, 175,
                176, 177, 178, 214, 215, 216, 222, 226, 233, 261, 278,
                279, 286, 287, 441,
            }, {
                "faccessat": 48, "faccessat2": 439, "getcwd": 17,
                "newfstatat": 79, "statx": 291, "statfs": 43, "fstatfs": 44,
                "lookup_dcookie": 18, "readlinkat": 78, "getdents64": 61, "utimensat": 88,
                "chroot": 51, "chdir": 49, "fchdir": 50, "pivot_root": 41,
                "fchownat": 54, "fchown": 55, "fchmod": 52, "fchmodat": 53, "fchmodat2": 452,
                "openat": 56, "openat2": 437, "name_to_handle_at": 264, "open_by_handle_at": 265,
                "mknodat": 33, "mkdirat": 34, "unlinkat": 35, "symlinkat": 36, "linkat": 37,
                "renameat": 38, "renameat2": 276, "truncate": 45, "ftruncate": 46,
                "mount": 40, "umount2": 39, "quotactl": 60, "quotactl_fd": 443,
                "acct": 89, "swapon": 224, "swapoff": 225, "execve": 221, "execveat": 281,
                "inotify_add_watch": 27, "fanotify_mark": 263,
                **{f"xattr-{number}": number for number in range(5, 17)},
            }),
        }

        def verdict(instructions, architecture, number, arguments=(0,) * 6):
            data = {0: number & 0xFFFFFFFF, 4: architecture}
            for index, argument in enumerate(arguments):
                data[16 + 8 * index] = argument & 0xFFFFFFFF
                data[20 + 8 * index] = (argument >> 32) & 0xFFFFFFFF
            pc, accumulator = 0, None
            while pc < len(instructions):
                code, yes, no, operand = instructions[pc]
                if code == 0x20:
                    accumulator = data[operand]
                elif code == 0x15:
                    pc += yes if accumulator == operand else no
                elif code == 0x35:
                    pc += yes if accumulator >= operand else no
                elif code == 0x06:
                    return operand
                else:
                    self.fail(f"unsupported seccomp instruction: {code}")
                pc += 1
            self.fail("seccomp filter has no verdict")

        for machine, (architecture, allowed, denied) in policies.items():
            with self.subTest(machine=machine):
                program = capsule._worker_kernel_filter(machine)
                instructions = [(entry.code, entry.jt, entry.jf, entry.k)
                                for entry in program.filters[:program.length]]
                for name, number in denied.items():
                    with self.subTest(syscall=name):
                        self.assertEqual(verdict(instructions, architecture, number), 0x80000000)
                for number in allowed:
                    self.assertEqual(verdict(instructions, architecture, number), 0x7FFF0000)
                    self.assertEqual(verdict(instructions, architecture ^ 1, number), 0x80000000)
                    self.assertEqual(verdict(instructions, architecture, number | 0x40000000), 0x80000000)
                for number in {*range(512), 4096, -1} - allowed:
                    self.assertEqual(verdict(instructions, architecture, number), 0x80000000,
                                     f"{machine}: unexpected syscall capability {number}")
                fcntl_number, prlimit_number = (72, 302) if machine == "x86_64" else (25, 261)
                for command in (0, 1, 2, 3, 1030, 1032, 1033, 1034):
                    self.assertEqual(verdict(instructions, architecture, fcntl_number,
                                             (3, command, 0, 0, 0, 0)), 0x7FFF0000)
                for command in (4, 5, 6, 7, 8, 9, 10, 1024, 1025, 1031, 4096, 1 << 32):
                    self.assertEqual(verdict(instructions, architecture, fcntl_number,
                                             (3, command, 0, 0, 0, 0)), 0x80000000)
                for pid, new_limit in ((1, 0), (0, 1), (1 << 32, 0), (0, 1 << 32)):
                    self.assertEqual(verdict(instructions, architecture, prlimit_number,
                                             (pid, 7, new_limit, 0, 0, 0)), 0x80000000)
        with self.assertRaises(capsule.CapsuleUnavailable):
            capsule._worker_kernel_filter("riscv64")

    def test_absence_and_symlink_data_are_exact_inert_artifacts(self):
        link = self.root / "inputs/link.json"
        link.symlink_to("never-follow.json")
        self.write("checks/inert.py",
                   b"def capsule_main(request, context):\n"
                   b"    return {'missing': context.read('head', 'inputs/not-present.json'),\n"
                   b"            'link': context.read('head', 'inputs/link.json').decode(),\n"
                   b"            'mode': context.entry('head', 'inputs/link.json')['mode']}\n")
        try:
            revision = self.commit()
            spec = capsule.CapsuleSpec(
                trees={"base": revision, "head": revision},
                programs={"inert": "checks/inert.py"},
                data={"head": ("inputs/not-present.json", "inputs/link.json")},
            )
            with capsule.prepare(self.root, spec) as prepared:
                result = prepared.execute("inert", {})
            self.assertEqual(result.value, {"missing": None, "link": "never-follow.json", "mode": "120000"})
        finally:
            link.unlink()

    def test_caught_path_import_process_and_network_fallbacks_cannot_return_pass(self):
        attempts = {
            "open": "open('/etc/hostname').read()",
            "path": "__import__('pathlib').Path('/etc/hostname').read_bytes()",
            "import": "__import__('unlisted_module')",
            "file-loader": "__import__('importlib.util', fromlist=['util']).spec_from_file_location('x', '/etc/hostname').loader.get_data('/etc/hostname')",
            "fork": "__import__('os').fork()",
            "signal": "__import__('os').kill(__import__('os').getpid(), 0)",
            "process-group": "__import__('os').setpgid(0, __import__('os').getppid())",
            "session": "__import__('os').setsid()",
            "process": "__import__('subprocess').run(['/bin/true'])",
            "ctypes": "__import__('ctypes').CDLL(None)",
            "network": "__import__('socket').socket()",
            "data": "context.read('head', 'unlisted.json')",
        }
        for label, attempt in attempts.items():
            with self.subTest(label=label):
                source = ("import pathlib, importlib.util, os, subprocess, ctypes, socket\n"
                          "def capsule_main(request, context):\n"
                          "    try:\n        " + attempt + "\n"
                          "    except Exception:\n        pass\n"
                          "    return {'status': 'pass'}\n")
                with self.attack(source) as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("attack", {})

    def test_worker_cannot_escape_group_then_stall_cleanup(self):
        before = self.descriptors()
        with self.attack(
            "import os,time\n"
            "def capsule_main(request, context):\n"
            "    os.setpgid(0, os.getppid())\n"
            "    time.sleep(20)\n"
        ) as prepared:
            started = time.monotonic()
            with self.assertRaises(capsule.CapsuleError):
                prepared.execute("attack", {}, timeout=0.3)
            self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(before, self.descriptors())

    def test_worker_descriptor_limits_preserve_nested_invocation_and_cleanup(self):
        self.write("checks/descriptors.py", b"""import os, resource
def capsule_main(request, context):
    descriptors = []
    failure = None
    try:
        for _ in range(request['attempts']):
            try:
                created = ([os.memfd_create('bounded-worker', os.MFD_CLOEXEC)]
                           if request['kind'] == 'memfd' else os.pipe2(os.O_CLOEXEC))
            except OSError as error:
                failure = error.errno
                break
            descriptors.extend(created)
        nested = context.invoke('assertion', {'state': 'descriptor-bound'})
        result = {'allocated': len(descriptors), 'errno': failure,
                  'limit': list(resource.getrlimit(resource.RLIMIT_NOFILE)),
                  'nested_status': nested['value']['status'],
                  'nested_program': nested['receipt']['program']}
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    descriptor = os.memfd_create('recovered-worker', os.MFD_CLOEXEC)
    os.close(descriptor)
    result['recovered'] = True
    return result
""")
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={**self.spec.trees, "base": revision},
            programs={"descriptors": "checks/descriptors.py", "assertion": "checks/assertion.py"},
            data=self.spec.data,
        )
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        with capsule.prepare(self.root, spec) as prepared:
            for kind in ("memfd", "pipe"):
                with self.subTest(kind=kind):
                    result = prepared.execute("descriptors", {"kind": kind, "attempts": 65}).value
                    self.assertEqual(result["errno"], errno.EMFILE)
                    self.assertEqual(result["limit"], [64, 64])
                    self.assertGreater(result["allocated"], 0)
                    self.assertLessEqual(result["allocated"], 64)
                    self.assertEqual(result["nested_status"], "pass")
                    self.assertEqual(result["nested_program"], "assertion")
                    self.assertTrue(result["recovered"])
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_worker_memfd_growth_stops_at_file_size_limit(self):
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        with self.attack("""import os, resource
def capsule_main(request, context):
    descriptor = os.memfd_create('bounded-growth', os.MFD_CLOEXEC)
    try:
        initial = os.write(descriptor, b'x' * (request['limit'] - 1))
        crossing = os.write(descriptor, b'yz')
        errors = {}
        for name in ('write', 'pwrite'):
            try:
                if name == 'write':
                    os.write(descriptor, b'x')
                else:
                    os.pwrite(descriptor, b'x', request['limit'])
            except OSError as error:
                errors[name] = error.errno
        return {'initial': initial, 'crossing': crossing, 'errors': errors,
                'size': os.fstat(descriptor).st_size,
                'limit': list(resource.getrlimit(resource.RLIMIT_FSIZE))}
    finally:
        os.close(descriptor)
""") as prepared:
            limit = 1024 * 1024
            result = prepared.execute("attack", {"limit": limit}).value
        self.assertEqual(result["initial"], limit - 1)
        self.assertEqual(result["crossing"], 1)
        self.assertEqual(result["errors"], {"write": errno.EFBIG, "pwrite": errno.EFBIG})
        self.assertEqual(result["size"], limit)
        self.assertEqual(result["limit"], [limit, limit])
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_closed_kernel_policy_preserves_private_memory_ipc_and_nested_execution(self):
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        with self.attack("""import fcntl, mmap, os, selectors
def capsule_main(request, context):
    if request.get('nested'):
        return 'nested-sealed'
    descriptors = []
    try:
        descriptor = os.memfd_create('private-buffer', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        descriptors.append(descriptor)
        with mmap.mmap(-1, 4096) as memory:
            memory[:6] = b'sealed'
            os.write(descriptor, memory[:6])
        os.pwrite(descriptor, b'IPC', 0)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, 15)
        duplicate = os.dup(descriptor)
        descriptors.append(duplicate)
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        descriptors.extend((read_fd, write_fd))
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            os.write(write_fd, b'ready')
            ready = bool(selector.select(1))
            message = os.read(read_fd, 5)
        return {'memory': os.pread(duplicate, 6, 0).decode(), 'size': os.fstat(duplicate).st_size,
                'seals': fcntl.fcntl(duplicate, fcntl.F_GET_SEALS),
                'ready': ready, 'message': message.decode(),
                'nested': context.invoke('attack', {'nested': True})['value']}
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
""") as prepared:
            result = prepared.execute("attack", {})
        self.assertEqual(result.value, {
            "memory": "IPCled", "size": 6, "seals": 15, "ready": True,
            "message": "ready", "nested": "nested-sealed",
        })
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_mutating_sys_path_cannot_load_an_ambient_module(self):
        ambient = self.root / "unlisted_module.py"
        ambient.write_bytes(b"status = 'pass'\n")
        try:
            with self.attack(
                "import sys\n"
                "def capsule_main(request, context):\n"
                f"    sys.path.insert(0, {str(self.root)!r})\n"
                "    return __import__('unlisted_module').status\n"
            ) as prepared:
                with self.assertRaises(capsule.CapsuleError):
                    prepared.execute("attack", {})
        finally:
            ambient.unlink()

    def test_parent_credentials_are_not_in_child_environment(self):
        with self.attack("import os\ndef capsule_main(request, context):\n    return dict(os.environ)\n") as prepared:
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-secret", "WORKFLOW_HMAC_KEY": "test-key"}):
                result = prepared.execute("attack", {})
        self.assertNotIn("GITHUB_TOKEN", result.value)
        self.assertNotIn("WORKFLOW_HMAC_KEY", result.value)
        self.assertNotIn("PYTHONPATH", result.value)

    def test_signing_verification_rejects_forgery_transplant_and_replay(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            first = prepared.execute("assertion", {"round": 1})
            second = prepared.execute("assertion", {"round": 1})
        key = b"test-only-capsule-signing-key-1234"
        signed = capsule.sign_receipt(first, key)
        self.assertEqual(capsule.verify_receipt(signed, key, first), first.receipt)
        with self.assertRaises(capsule.CapsuleError):
            capsule.verify_receipt(signed, key, second)
        value = capsule.parse(signed)
        value["receipt"]["program_sha256"] = "f" * 64
        with self.assertRaises(capsule.CapsuleError):
            capsule.verify_receipt(capsule.canonical(value), key, first)
        with self.assertRaises(capsule.CapsuleError):
            capsule.sign_receipt(capsule.ExecutionResult(first.receipt_bytes, first.output_bytes), key)
        with self.assertRaises(capsule.CapsuleError):
            capsule.sign_receipt(first, b"short")

    def test_success_failure_and_timeout_leave_no_owned_descriptor(self):
        before = self.descriptors()
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            prepared.execute("assertion", {})
        self.assertEqual(before, self.descriptors())
        programs = {
            "exception": "raise RuntimeError('intentional crash')",
            "crash": "__import__('os')._exit(3)",
            "empty": "__import__('os')._exit(0)",
            "partial": "__import__('os').write(1, b'{')\n    return {'status': 'pass'}",
            "stdout": "print('forged pass')\n    return {'status': 'pass'}",
            "oversized": f"return 'x' * {capsule.MAX_OUTPUT_BYTES + 1}",
            "timeout": "while True:\n        pass",
        }
        for name, body in programs.items():
            with self.subTest(name=name):
                with self.attack("def capsule_main(request, context):\n    " + body + "\n") as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("attack", {}, timeout=0.3 if name == "timeout" else 3)
                self.assertEqual(before, self.descriptors())

    def test_interruption_closes_liveness_and_reaps_guardian(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            before = self.descriptors()
            real_selector = selectors.DefaultSelector

            class InterruptingSelector:
                def __enter__(self):
                    self.delegate = real_selector()
                    return self
                def __exit__(self, *exc):
                    self.delegate.close()
                def register(self, *args):
                    return self.delegate.register(*args)
                def get_map(self):
                    return self.delegate.get_map()
                def select(self, *args):
                    raise KeyboardInterrupt()

            with mock.patch.object(selectors, "DefaultSelector", InterruptingSelector):
                with self.assertRaises(KeyboardInterrupt):
                    prepared.execute("checker", {})
            self.assertEqual(before, self.descriptors())

    def test_missing_platform_fails_closed_without_process_creation(self):
        with mock.patch.object(capsule.sys, "platform", "unsupported"):
            with mock.patch.object(subprocess, "Popen") as launch:
                with self.assertRaises(capsule.CapsuleUnavailable) as error:
                    capsule.prepare(self.root, self.spec)
                self.assertEqual(error.exception.disposition, "sealed-capsule-unavailable")
                launch.assert_not_called()
        with mock.patch.object(os, "memfd_create", side_effect=OSError(errno.ENOSYS, "unavailable")):
            with self.assertRaises(capsule.CapsuleUnavailable):
                capsule.SealedBytes(b"must-not-fall-back", "test", 100)

    def test_missing_proc_descriptors_are_explicitly_unavailable_before_launch(self):
        capsule._platform()
        for number in (errno.ENOENT, errno.EACCES, errno.ENOTDIR):
            with self.subTest(errno=number):
                with (
                    mock.patch.object(os, "listdir", side_effect=OSError(number, "proc unavailable")),
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError(
                        "process created before platform admission")) as launch,
                    mock.patch.object(os, "memfd_create") as create,
                ):
                    with self.assertRaises(capsule.CapsuleUnavailable) as error:
                        capsule.prepare(self.root, self.spec)
                    self.assertEqual(error.exception.disposition, "sealed-capsule-unavailable")
                    launch.assert_not_called()
                    create.assert_not_called()
                    with (
                        mock.patch.object(os, "write") as diagnostic,
                        mock.patch.object(os, "waitpid", side_effect=ChildProcessError),
                        mock.patch.object(os, "fork") as fork,
                    ):
                        with self.assertRaises(SystemExit) as exit:
                            capsule._supervise(["3", "4", "5", "6", "7", "", "", ""])
                        self.assertEqual(exit.exception.code, 125)
                        self.assertEqual(diagnostic.call_args.args[0], 2)
                        self.assertTrue(diagnostic.call_args.args[1].startswith(b"CapsuleUnavailable:"))
                        fork.assert_not_called()

    @mock.patch.object(os, "pidfd_open", new=None, create=True)
    @mock.patch.object(signal, "pidfd_send_signal", new=None, create=True)
    def test_parent_death_reaps_outer_and_nested_execution_groups(self):
        previous = capsule.ctypes.c_int()
        libc = capsule.ctypes.CDLL(None, use_errno=True)
        self.assertEqual(libc.prctl(37, capsule.ctypes.byref(previous), 0, 0, 0), 0)
        capsule._prctl(36, 1)
        self.addCleanup(capsule._prctl, 36, previous.value)
        self.write("checks/hang.py", b"import time\ndef capsule_main(request, context):\n    time.sleep(20)\n")
        self.write("checks/hang_checker.py",
                   b"def capsule_main(request, context):\n    return context.invoke('hang', request)\n")
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={"base": revision},
            programs={"hang": "checks/hang.py", "hang-checker": "checks/hang_checker.py"},
        )
        bundle_path = self.root / "parent-death-bundle.json"
        bundle_path.write_bytes(capsule._make_bundle(self.root, spec.record()))
        helper = """
import pathlib,sys
sys.path.insert(0,sys.argv[1])
from scripts.workflow_pilot import sealed_capsule as c
raw=pathlib.Path(sys.argv[2]).read_bytes()
spec=c.CapsuleSpec(**c.parse(raw,c.MAX_BUNDLE_BYTES)['spec'])
launch=c.subprocess.Popen
def traced(*args,**kwargs):
    child=launch(*args,**kwargs)
    if len(kwargs.get('pass_fds',()))==6:
        print(child.pid,flush=True)
    return child
c.subprocess.Popen=traced
with c.Capsule(raw,spec) as prepared:
    prepared.execute('hang-checker',{},timeout=30)
"""
        process = subprocess.Popen(
            [capsule.PYTHON, "-I", "-S", "-c", helper, str(ROOT), str(bundle_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        observed = {}
        try:
            observed[process.pid] = _process_state(process.pid)[0]
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(5), "parent helper did not launch")
                line = process.stdout.readline()
                self.assertTrue(line.strip().isdigit(), f"helper failed: {line!r}")
                guardian = int(line)
            state = _process_state(guardian)
            self.assertIsNotNone(state, "outer guardian exited before observation")
            observed[guardian] = state[0]
            deadline = time.monotonic() + 5
            live = set()
            while time.monotonic() < deadline:
                _observe_descendants(observed)
                live = {
                    pid for pid, generation in observed.items()
                    if pid != process.pid and (state := _process_state(pid)) is not None
                    and state[0] == generation and state[1] not in {"Z", "X"}
                }
                if len(live) >= 4:
                    break
                time.sleep(0.02)
            self.assertGreaterEqual(len(live), 4, "nested guardian and worker were not live")
            process.kill()
            process.wait(timeout=3)
            observed.pop(process.pid)
            deadline = time.monotonic() + 3
            pending = dict(observed)
            while pending and time.monotonic() < deadline:
                for pid, generation in tuple(pending.items()):
                    if _finish_owned_process(pid, generation):
                        pending.pop(pid)
                if pending:
                    time.sleep(0.02)
            self.assertFalse(pending, f"parent death left live processes: {pending}")
        finally:
            try:
                _observe_descendants(observed)
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
                observed.pop(process.pid, None)
                deadline = time.monotonic() + 3
                while observed and time.monotonic() < deadline:
                    _observe_descendants(observed)
                    for pid, generation in tuple(observed.items()):
                        if _finish_owned_process(pid, generation, terminate=True):
                            observed.pop(pid)
                    if observed:
                        time.sleep(0.02)
                self.assertFalse(observed, f"test cleanup left owned processes: {observed}")
            finally:
                process.stdout.close()
                process.stderr.close()
                bundle_path.unlink()

    def test_noncanonical_and_duplicate_wire_json_rejected(self):
        for raw in (b'{"x":1,"x":1}\n', b'{"x":NaN}\n', b'{"x":1e999}\n',
                    b'{"x": 1}\n', b'{"x":1}', b'{}\n{}\n'):
            with self.subTest(raw=raw):
                with self.assertRaises(capsule.CapsuleError):
                    capsule.parse(raw)


if __name__ == "__main__":
    unittest.main()
