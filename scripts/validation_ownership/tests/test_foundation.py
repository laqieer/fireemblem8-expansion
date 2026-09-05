from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, GitTreeEntry, git_tree_entries
from scripts.validation_ownership.budget import Limits, MakeProbeError, NAMESPACE_LAUNCHER, ProbeBudget
from scripts.validation_ownership.make_probe import (
    Command, ProbeSession, TRUSTED_ROOT, _make_interpreter, _make_runtime,
    _read_events, _read_observation, _trusted_runtime_bytes,
)


ROOT = Path(__file__).resolve().parents[3]


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts/ownership-foundation-tests" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.entries = {}
        self.scratch = self.root / "build/probe"

    def tearDown(self):
        shutil.rmtree(self.directory)

    def add(self, path, value, mode="100644"):
        data = value.encode("utf-8") if isinstance(value, str) else value
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        self.entries[path] = GitTreeEntry(path, mode, "blob", hashlib.sha1(data).hexdigest())

    def session(self, **limits):
        return ProbeSession(
            AuthorityLoader(self.root, dict(self.entries)),
            scratch_root=self.scratch,
            budget=ProbeBudget(Limits(**limits)),
        )

    def assert_clean(self, session):
        self.assertFalse(session.cache)
        self.assertFalse(session.mappings)
        self.assertFalse(session.make_runtime)
        self.assertFalse(session.budget.children)
        self.assertIsNone(session.snapshot)
        self.assertIsNone(session.base)
        self.assertFalse(self.scratch.exists())

    def test_make_option_channels_cannot_inject_unrequested_evaluation(self):
        self.add("Makefile", (
            "ifeq ($(INJECTED),yes)\nDEP = injected\nelse\nDEP = ordinary\nendif\n"
            "all: $(DEP)\n\t@printf '%s' '$^'\ninjected ordinary: ;\n"
        ))
        self.add("other.mk", "INJECTED := yes\n")
        before = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env={**ENVIRONMENT, "GNUMAKEFLAGS": "--eval=INJECTED=yes"},
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(before.stdout, b"injected")
        for name in ("GNUMAKEFLAGS", "MAKEFLAGS"):
            for origin in ("environment", "command-line"):
                for value in ("--eval=INJECTED=yes", "-f other.mk"):
                    with self.subTest(name=name, origin=origin, value=value):
                        session = self.session()
                        with session:
                            runs = session.budget.runs
                            with self.assertRaisesRegex(MakeProbeError, "execution-authority Make assignment"):
                                session.make("all", assignments=((origin, name, value),))
                            self.assertEqual(session.budget.runs, runs)
                        self.assert_clean(session)

    def test_conditional_graphs_match_ordinary_make_not_probe_markers(self):
        controls = (
            "ifeq ($(SHELL),/bin/vo-shell)",
            "ifeq ($(origin SHELL),command line)",
            "ifeq ($(MAKE),/bin/vo-make)",
            "ifeq ($(origin MAKE),command line)",
            "ifeq ($(origin .SHELLFLAGS),command line)",
            "ifneq ($(findstring n,$(firstword $(MAKEFLAGS))),)",
            "ifneq ($(findstring B,$(firstword $(MAKEFLAGS))),)",
            "ifneq ($(findstring j1,$(MAKEFLAGS)),)",
            "ifneq ($(findstring --no-print-directory,$(MAKEFLAGS)),)",
            "ifneq ($(origin LD_PRELOAD),undefined)",
            "ifneq ($(origin VO_OBSERVE_TARGET),undefined)",
            "ifneq ($(origin SOURCE_DATE_EPOCH),undefined)",
        )
        for condition in controls:
            with self.subTest(condition=condition):
                self.add("Makefile", (
                    condition + "\nDEP = hidden\nelse\nDEP = genuine\nendif\n"
                    "all: $(DEP)\n\t@printf '%s' '$^'\nhidden genuine: ;\n"
                ))
                normal = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", "all"],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
                )
                self.assertEqual(normal.stdout, b"genuine")
                with self.session() as session:
                    observed = session.make("all")
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": normal.stdout.decode("ascii"), "order_only": False},
                    ])
                    self.assertEqual(observed.events, ())
                self.assert_clean(session)

    def test_default_file_and_requested_domains_preserve_production_make_context(self):
        names = (
            "SHELL", "MAKE", "MAKE_COMMAND", "MAKEFLAGS", "MFLAGS", "MAKELEVEL",
            "LD_PRELOAD", "GNUMAKEFLAGS", "MODE", "FLAGS", "FLAGS_ORIGIN", "FLAGS_FLAVOR",
        )
        aliases = "".join(
            f"CTX_{index}_{field} = $({form}{name})\n"
            for index, name in enumerate(names)
            for field, form in (("value", ""), ("origin", "origin "), ("flavor", "flavor "))
        )
        arguments = " ".join(
            f"'$(CTX_{index}_{field})'"
            for index in range(len(names)) for field in ("value", "origin", "flavor")
        )
        for prefix, assignments in (
            ("MODE ?= file\n", ()),
            ("SHELL := /bin/bash\nMODE ?= file\n", (("environment", "MODE", "environment"),)),
            (".POSIX:\nMODE ?= file\n", (("command-line", "MODE", "command"),)),
        ):
            with self.subTest(prefix=prefix, assignments=assignments):
                self.add("Makefile", (
                    prefix + "FLAGS = $(.SHELLFLAGS)\nFLAGS_ORIGIN = $(origin .SHELLFLAGS)\n"
                    "FLAGS_FLAVOR = $(flavor .SHELLFLAGS)\n" + aliases
                    + "ifeq ($(MODE),command)\nDEP = command\nelse\nDEP = ordinary\nendif\n"
                    + f"all: $(DEP)\n\t@printf '%s\\n' '$^' {arguments}\n"
                    + "command ordinary: ;\n"
                ))
                environment = dict(ENVIRONMENT)
                cli = []
                for origin, name, value in assignments:
                    if origin == "environment":
                        environment[name] = value
                    else:
                        cli.append(name + "=" + value)
                normal = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", *cli, "all"],
                    cwd=self.root, env=environment, capture_output=True, check=True, timeout=10,
                )
                lines = normal.stdout.decode("ascii").splitlines()
                self.assertEqual(len(lines), 1 + 3 * len(names), normal.stdout)
                expected = {
                    name: dict(zip(("value", "origin", "flavor"), lines[1 + index * 3:4 + index * 3]))
                    for index, name in enumerate(names)
                }
                with self.session() as session:
                    observed = session.make("all", variables=names, assignments=assignments)
                    self.assertEqual(observed.semantics["domains"], expected)
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": lines[0], "order_only": False},
                    ])
                    self.assertEqual(observed.events, ())
                self.assert_clean(session)

    def test_secondary_expansion_keeps_target_specific_shell_context(self):
        self.add("Makefile", (
            ".SECONDEXPANSION:\nall: SHELL := /bin/bash\n"
            "all: $$(if $$(filter /bin/bash,$$(SHELL)),file-shell,wrong-context)\n"
            "\t@printf '%s' '$^'\nfile-shell wrong-context: ;\n"
        ))
        normal = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(normal.stdout, b"file-shell")
        with self.session() as session:
            observed = session.make("all", variables=("SHELL",))
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                {"name": "file-shell", "order_only": False},
            ])
            self.assertEqual(observed.semantics["files"][0]["variables"]["SHELL"], {
                "value": "/bin/bash", "origin": "file", "flavor": "simple",
            })
            self.assertEqual(observed.semantics["domains"]["SHELL"]["value"], "/bin/sh")
        self.assert_clean(session)

    def test_recipe_commands_are_metadata_but_make_expansion_effects_still_reject(self):
        self.add("Makefile", "all:\n\t@printf '%s' recipe > recipe-effect\n")
        subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        effect = self.root / "recipe-effect"
        self.assertEqual(effect.read_bytes(), b"recipe")
        effect.unlink()
        with self.session() as session:
            observed = session.make("all")
            self.assertFalse(effect.exists())
            self.assertFalse((session.tree / "recipe-effect").exists())
            self.assertEqual(observed.events, ())
            self.assertIn("recipe-effect", observed.semantics["files"][0]["recipe"])
        self.assert_clean(session)
        self.add("Makefile", "all:\n\t$(file >recipe-effect,forged)\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "write outside"):
            with session:
                session.make("all")
        self.assertFalse(effect.exists())
        self.assert_clean(session)

    def test_dispatch_classifies_identical_recipe_and_expansion_by_native_context(self):
        for prefix in ("", ".POSIX:\n"):
            for command in ("printf %s dynamic", "printf '%s' dynamic; printf ''"):
                with self.subTest(prefix=prefix, command=command):
                    self.add("Makefile", (
                        prefix + f"VALUE := $(shell {command})\nall: $(VALUE)\n"
                        f"\t@{command}\ndynamic: ;\n"
                    ))
                    with self.session() as session:
                        observed = session.make(
                            "all", variables=("VALUE",),
                            commands={command: Command(("/usr/bin/printf", "%s", "dynamic"))},
                        )
                        self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "dynamic")
                        self.assertEqual(len(observed.events), 1)
                        self.assertEqual(observed.events[0]["match"], 0)
                        self.assertEqual(observed.stdout, b"")
                    self.assert_clean(session)

    def test_recursive_and_makefile_remake_dispatch_still_requires_real_mappings(self):
        self.add("Makefile", "all:\n\t+@printf %s recursive\n")
        for registered in (False, True):
            with self.subTest(registered=registered):
                session = self.session()
                with session:
                    if registered:
                        observed = session.make("all", commands={
                            "printf %s recursive": Command(("/usr/bin/printf", "%s", "recursive")),
                        })
                        self.assertEqual(observed.stdout, b"recursive")
                        self.assertEqual(len(observed.events), 1)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "unregistered eager/recursive"):
                            session.make("all")
                self.assert_clean(session)
        self.add("Makefile", "include missing.mk\nmissing.mk:\n\t@printf missing\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unregistered eager/recursive"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_private_dispatch_and_observer_inputs_cannot_be_candidate_authority(self):
        for operation in (
            "$(file </control/interceptor)",
            "$(file </lib/vo-observer.so)",
            "$(wildcard /lib/*)",
        ):
            with self.subTest(operation=operation):
                self.add("Makefile", f"VALUE := {operation}\nall: ;\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "channel denied|observer image|directory enumeration"):
                    with session:
                        session.make("all")
                self.assert_clean(session)
        from scripts.validation_ownership.syscall_guard import VO_READY, VO_DISPATCH, VO_QUERY_KIND
        for marker in (VO_READY, VO_DISPATCH, VO_QUERY_KIND):
            with self.subTest(marker=marker):
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "unauthenticated"):
                    with session:
                        session.command(Command((
                            "/usr/bin/python3", "-I", "-c",
                            "import ctypes; ctypes.CDLL(None).syscall(39, ctypes.c_ulong("
                            + str(marker) + "), 0, 0)",
                        )))
                self.assert_clean(session)

    def test_partial_scratch_setup_releases_created_parents_and_descriptors(self):
        self.add("Makefile", "all: ;\n")
        original_open, original_mkdir = os.open, os.mkdir
        failures = ["tracked", "open", "mkdir", "long-name", "interrupt"]
        if os.geteuid() != 0:
            failures.append("inaccessible")
        for failure in failures:
            with self.subTest(failure=failure):
                self.scratch = self.root / "partial" / "parents" / ("x" * 300 if failure == "long-name" else "leaf")
                entries = dict(self.entries)
                if failure == "tracked":
                    entries["partial/parents/leaf"] = GitTreeEntry(
                        "partial/parents/leaf", "100644", "blob", "0" * 40,
                    )
                primary = OSError(errno.EIO, "owned scratch setup failure")
                def opening(path, flags, *args, **kwargs):
                    if path == "leaf" and kwargs.get("dir_fd") is not None:
                        if failure == "open":
                            raise primary
                        if failure == "interrupt":
                            raise KeyboardInterrupt("owned scratch interruption")
                    return original_open(path, flags, *args, **kwargs)
                def making(path, mode=0o777, *, dir_fd=None):
                    if path == "leaf" and failure == "mkdir":
                        raise primary
                    result = original_mkdir(path, mode, dir_fd=dir_fd)
                    if path == "leaf" and failure == "inaccessible":
                        os.chmod(path, 0, dir_fd=dir_fd)
                    return result
                descriptors = set(os.listdir("/proc/self/fd"))
                session = ProbeSession(AuthorityLoader(self.root, entries), scratch_root=self.scratch)
                with patch("os.open", opening), patch("os.mkdir", making):
                    with self.assertRaises(KeyboardInterrupt if failure == "interrupt" else MakeProbeError) as caught:
                        with session:
                            self.fail("unsafe scratch setup was admitted")
                if failure in {"open", "mkdir"}:
                    self.assertIs(caught.exception.__cause__, primary)
                self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)
                self.assertFalse((self.root / "partial").exists())
                self.assert_clean(session)

    def test_scratch_setup_interruption_waits_for_resource_ownership(self):
        self.add("Makefile", "all: ;\n")
        original = os.mkdir
        sent = False
        def creating(path, mode=0o777, *, dir_fd=None):
            nonlocal sent
            result = original(path, mode, dir_fd=dir_fd)
            if dir_fd is not None and str(path).startswith("probe-"):
                os.kill(os.getpid(), signal.SIGTERM)
                sent = True
            return result
        session = self.session()
        with patch("os.mkdir", creating):
            with self.assertRaises(KeyboardInterrupt):
                with session:
                    self.fail("setup interruption was lost")
        self.assertTrue(sent)
        self.assert_clean(session)

    def test_scratch_cleanup_preserves_existing_parents_and_primary_failure(self):
        self.add("Makefile", "all: ;\n")
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep").write_bytes(b"not allocator-owned")
        self.scratch = existing / "created" / "leaf"
        original_open, original_remove = os.open, os.rmdir
        primary = OSError(errno.EIO, "primary setup failure")
        def opening(path, flags, *args, **kwargs):
            if path == "leaf":
                raise primary
            return original_open(path, flags, *args, **kwargs)
        session = self.session()
        with patch("os.open", opening):
            with self.assertRaises(MakeProbeError) as caught:
                with session:
                    self.fail("setup failure disappeared")
        self.assertIs(caught.exception.__cause__, primary)
        self.assertEqual((existing / "keep").read_bytes(), b"not allocator-owned")
        self.assertFalse((existing / "created").exists())
        self.assert_clean(session)

        def removing(path, *args, **kwargs):
            if path == "leaf":
                raise PermissionError("modeled owned cleanup failure")
            return original_remove(path, *args, **kwargs)
        primary = OSError(errno.EIO, "primary setup failure")
        session = self.session()
        with patch("os.open", opening), patch("os.rmdir", removing):
            with self.assertRaises(MakeProbeError) as caught:
                with session:
                    self.fail("setup failure disappeared")
        self.assertIs(caught.exception.__cause__, primary)
        if hasattr(primary, "__notes__"):
            self.assertTrue(any("owned scratch cleanup failed" in note for note in primary.__notes__))
        self.assertEqual((existing / "keep").read_bytes(), b"not allocator-owned")

    def test_privileged_cleanup_delegates_before_wait_and_never_hides_permission_errors(self):
        child = SimpleNamespace(pid=999999999, stdin=Mock(), wait=Mock())
        with patch("os.killpg", side_effect=PermissionError("modeled root-owned group")) as kill:
            with self.assertRaises(PermissionError):
                ProbeBudget._terminate(child)
            child.wait.assert_not_called()
            kill.reset_mock()
            ProbeBudget._terminate(child, privileged=True)
            kill.assert_not_called()
            child.stdin.close.assert_called_once_with()
            child.wait.assert_called_once_with()
        for argv, supplied in ((["/usr/bin/true"], None), ([*NAMESPACE_LAUNCHER, "/usr/bin/true"], b"input")):
            with patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(MakeProbeError, "guarded PID-namespace lifecycle"):
                    ProbeBudget().run(argv, env=ENVIRONMENT, privileged=True, input_data=supplied)
                launch.assert_not_called()
        from scripts.validation_ownership import lifecycle
        mode = os.stat("/usr/bin/unshare")
        elevated = list(mode)
        elevated[0] |= stat.S_ISUID
        with patch.object(lifecycle.os, "stat", return_value=os.stat_result(elevated)), patch(
            "subprocess.Popen",
        ) as launch:
            with self.assertRaisesRegex(MakeProbeError, "unsupported privileged namespace lifecycle"):
                ProbeBudget().run([*NAMESPACE_LAUNCHER, "/usr/bin/true"], env=ENVIRONMENT, privileged=True)
            launch.assert_not_called()
        with patch.object(lifecycle.os, "getxattr", return_value=b"modeled file capabilities"), patch(
            "subprocess.Popen",
        ) as launch:
            with self.assertRaisesRegex(MakeProbeError, "file capabilities"):
                ProbeBudget().run([*NAMESPACE_LAUNCHER, "/usr/bin/true"], env=ENVIRONMENT, privileged=True)
            launch.assert_not_called()

    def test_privileged_budget_uses_real_watchdog_without_running_sudo(self):
        budget = ProbeBudget()
        original = subprocess.Popen
        invocations = []
        def same_uid_fixture(argv, **kwargs):
            self.assertEqual(argv[:7], [
                "/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-S", "-B",
            ])
            self.assertEqual(Path(argv[7]), TRUSTED_ROOT / "lifecycle.py")
            self.assertEqual(float(argv[8]), budget.deadline)
            self.assertEqual(argv[9], "--")
            self.assertEqual(tuple(argv[10:10 + len(NAMESPACE_LAUNCHER)]), NAMESPACE_LAUNCHER)
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            self.assertTrue(kwargs["close_fds"])
            invocations.append(argv)
            # Exercise the identical watchdog and PID lifecycle at our own
            # credentials. This is not evidence of a real sudo transition.
            owned = [
                *argv[3:11], "--user", "--map-root-user", *argv[11:],
            ]
            return original(owned, **kwargs)
        with patch("subprocess.Popen", same_uid_fixture), patch(
            "scripts.validation_ownership.budget.os.killpg",
            side_effect=PermissionError("outer caller cannot signal privileged groups"),
        ) as kill:
            result = budget.run(
                [*NAMESPACE_LAUNCHER, "/usr/bin/printf", "%s", "guarded namespace"],
                env=ENVIRONMENT, privileged=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"guarded namespace")
            kill.assert_not_called()
        self.assertEqual(len(invocations), 1)
        self.assertFalse(budget.children)

    def test_sudo_preflight_and_capsules_share_the_privileged_lifecycle_contract(self):
        self.add("Makefile", "all: ;\n")
        session = self.session()
        original_run = session.budget.run
        original_file = Path.is_file
        guarded = []
        def fake_namespace(argv, **kwargs):
            if argv[0] == "/usr/bin/unshare" and "--user" in argv:
                return subprocess.CompletedProcess(argv, 1, b"", b"modeled user namespace denial")
            if kwargs.get("privileged"):
                self.assertEqual(tuple(argv[:len(NAMESPACE_LAUNCHER)]), NAMESPACE_LAUNCHER)
                guarded.append(argv)
                if argv[-1] != "/usr/bin/true":
                    config = json.loads(Path(argv[-1]).read_bytes())
                    self.assertTrue(config["sudo_drop"])
                    Path(config["report"]).write_text(json.dumps({
                        "ok": True, "returncode": 0, "error": None,
                        "consumed": [], "code_consumed": [], "accessed": [],
                        "processes": 1, "syscalls": 1, "written_bytes": 0,
                        "created_files": 0, "memory_peak": 1, "observation_bytes": 0,
                    }))
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            return original_run(argv, **kwargs)
        with patch.object(session.budget, "run", fake_namespace), patch.object(
            Path, "is_file", lambda path: str(path) == "/usr/bin/sudo" or original_file(path),
        ), patch("os.getuid", return_value=1000), patch("os.getgid", return_value=1000):
            with session:
                self.assertTrue(session.sudo_drop)
                session._sandbox_run(
                    session._new_root("lifecycle-contract"), mode="command",
                    argv=["/usr/bin/true"], environment=ENVIRONMENT, mounts=[],
                )
        self.assertEqual(len(guarded), 2)
        self.assertEqual(guarded[0][-1], "/usr/bin/true")
        self.assertEqual(Path(guarded[1][-2]).name, "sandbox_exec.py")
        self.assert_clean(session)

    def test_privileged_lifetime_closes_on_budget_rejection_and_interruption(self):
        original = subprocess.Popen
        def same_uid_fixture(argv, **kwargs):
            self.assertEqual(argv[:3], ["/usr/bin/sudo", "-n", "--"])
            return original([*argv[3:11], "--user", "--map-root-user", *argv[11:]], **kwargs)
        for action in ("deadline", "output", "interrupt"):
            with self.subTest(action=action):
                budget = ProbeBudget(Limits(
                    seconds=0.5 if action == "deadline" else 10,
                    process_output_bytes=32 if action == "output" else 1024,
                ))
                charge = budget.charge
                def interrupt_output(category, size):
                    if action == "interrupt" and category == "output":
                        raise KeyboardInterrupt("modeled caller interruption")
                    charge(category, size)
                program = "import os,time\nos.fork()\n"
                if action != "deadline":
                    program += "os.write(1, b'x'*100)\n"
                program += "time.sleep(20)\n"
                with patch("subprocess.Popen", same_uid_fixture), patch.object(
                    budget, "charge", interrupt_output,
                ), patch("os.killpg", side_effect=PermissionError("outer caller lacks permission")) as kill:
                    with self.assertRaises(KeyboardInterrupt if action == "interrupt" else MakeProbeError):
                        budget.run(
                            [*NAMESPACE_LAUNCHER, "/usr/bin/python3", "-I", "-c", program],
                            env=ENVIRONMENT, privileged=True,
                        )
                    kill.assert_not_called()
                self.assertFalse(budget.children)
                self.assertLess(time.monotonic() - budget.started, 5)

    def test_watchdog_rejects_missing_lifetime_and_kernel_support_before_launch(self):
        from scripts.validation_ownership import lifecycle
        read, write = os.pipe()
        try:
            with patch.object(lifecycle, "prctl", side_effect=OSError("unsupported kernel")), patch(
                "subprocess.Popen",
            ) as launch:
                with self.assertRaisesRegex(OSError, "unsupported kernel"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=read)
                launch.assert_not_called()
            os.close(write)
            write = None
            with patch.object(lifecycle, "prctl"), patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(BrokenPipeError, "before namespace launch"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=read)
                launch.assert_not_called()
            with open("/dev/null", "rb") as nonpipe, patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "lifetime pipe"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=nonpipe.fileno())
                launch.assert_not_called()
        finally:
            os.close(read)
            if write is not None:
                os.close(write)

    def test_watchdog_reaps_owned_orphans_on_completion_eof_deadline_and_signal(self):
        program = (
            "import ctypes,json,os,signal,sys,time\n"
            "death = ctypes.c_int()\n"
            "assert ctypes.CDLL(None).prctl(2, ctypes.byref(death), 0, 0, 0) == 0\n"
            "assert death.value == signal.SIGKILL\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            " time.sleep(20)\n os._exit(0)\n"
            "print(json.dumps([os.getpid(), child]), flush=True)\n"
            "if sys.argv[1] != 'complete': time.sleep(20)\n"
        )
        for action in ("complete", "eof", "deadline", "signal"):
            with self.subTest(action=action):
                started = time.monotonic()
                watchdog = subprocess.Popen(
                    ["/usr/bin/python3", "-I", "-S", "-B", str(TRUSTED_ROOT / "lifecycle.py"),
                     str(started + (1 if action == "deadline" else 10)), "--",
                     "/usr/bin/python3", "-I", "-c", program, action],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    close_fds=True, start_new_session=True, env=ENVIRONMENT,
                )
                pids = []
                try:
                    with selectors.DefaultSelector() as ready:
                        ready.register(watchdog.stdout, selectors.EVENT_READ)
                        self.assertTrue(ready.select(3), "owned payload did not start")
                    line = watchdog.stdout.readline()
                    if not line:
                        self.fail(watchdog.stderr.read())
                    pids = json.loads(line)
                    if action == "eof":
                        watchdog.stdin.close()
                    elif action == "signal":
                        os.kill(watchdog.pid, signal.SIGTERM)
                    watchdog.wait(timeout=5)
                    self.assertEqual(watchdog.returncode, 0 if action == "complete" else 125)
                    self.assertLess(time.monotonic() - started, 5)
                    for pid in pids:
                        self.assertFalse(Path(f"/proc/{pid}").exists(), f"owned PID {pid} was not reaped")
                finally:
                    if watchdog.poll() is None:
                        watchdog.stdin.close()
                        watchdog.wait(timeout=5)
                    watchdog.stdin.close()
                    watchdog.stdout.close()
                    watchdog.stderr.close()

    def test_make_runtime_uses_captured_non_multiarch_closure_and_real_make(self):
        trusted = dict(_make_runtime(ProbeBudget()))
        interpreter = _make_interpreter(trusted["/usr/bin/make"])
        relocated = {
            name if name in {"/usr/bin/make", interpreter} else "/usr/lib/" + Path(name).name: data
            for name, data in trusted.items()
        }
        listing = "\tlinux-vdso.so.1 (0x1)\n" + "".join(
            f"\t{Path(name).name} => {name} (0x2)\n"
            for name in relocated if name not in {"/usr/bin/make", interpreter}
        ) + f"\t{interpreter} (0x3)\n"
        self.add("Makefile", "VALUE := captured-runtime\nall: dependency\ndependency: ;\n")
        session = self.session()
        original_run = session.budget.run
        def runtime_listing(argv, **kwargs):
            if argv == [interpreter, "--list", "/usr/bin/make"]:
                self.assertEqual(kwargs["env"], ENVIRONMENT)
                self.assertEqual(kwargs["cwd"], Path("/"))
                return subprocess.CompletedProcess(argv, 0, listing.encode("ascii"), b"")
            return original_run(argv, **kwargs)
        def captured_runtime(name, budget):
            budget.charge("control", len(relocated[name]))
            return relocated[name]
        with patch(
            "scripts.validation_ownership.make_probe._trusted_runtime_bytes", captured_runtime,
        ), patch.object(session.budget, "run", runtime_listing):
            with session:
                captured = dict(session.make_runtime)
                self.assertEqual(captured, relocated)
                relocated.clear()
                with patch(
                    "scripts.validation_ownership.make_probe._trusted_runtime_bytes",
                    side_effect=AssertionError("captured runtime was read again"),
                ):
                    root = session._new_root("inspect-runtime", make=True)
                    for name, data in captured.items():
                        target = root / name.lstrip("/")
                        self.assertEqual(target.read_bytes(), data)
                        self.assertFalse(target.stat().st_mode & 0o222)
                    self.assertFalse((root / "lib/x86_64-linux-gnu/libc.so.6").exists())
                    self.assertTrue((root / "usr/lib/libc.so.6").is_file())
                    observation = session.make("all", variables=("VALUE",))
                    self.assertEqual(observation.semantics["domains"]["VALUE"]["value"], "captured-runtime")
                    self.assertEqual(observation.semantics["files"][0]["prerequisites"], [
                        {"name": "dependency", "order_only": False},
                    ])
        self.assert_clean(session)

    def test_runtime_capture_rejects_mutable_aliases_and_malformed_elf_or_listing(self):
        alias = self.directory / "make"
        alias.symlink_to("/usr/bin/make")
        with self.assertRaisesRegex(MakeProbeError, "trusted system"):
            _trusted_runtime_bytes(str(alias), ProbeBudget())
        original = Path.lstat
        resolved = Path("/usr/bin/make").resolve()
        def mutable(path):
            value = original(path)
            if path == resolved:
                fields = list(value)
                fields[0] |= stat.S_IWGRP
                return os.stat_result(fields)
            return value
        with patch.object(Path, "lstat", mutable):
            with self.assertRaisesRegex(MakeProbeError, "mutable/untrusted"):
                _trusted_runtime_bytes("/usr/bin/make", ProbeBudget())
        binary = Path("/usr/bin/make").read_bytes()
        invalid_headers = bytearray(binary)
        invalid_headers[56:58] = b"\0\0"
        for data in (b"", b"\x7fELF", bytes(invalid_headers)):
            with self.assertRaises(MakeProbeError):
                _make_interpreter(data)
        for output in (
            b"", b"\tlibc.so.6 => not found\n",
            b"\tlibc.so.6 => /work/libc.so.6 (0x1)\n",
            b"\tlibc.so.6 => /usr/lib/../bin/make (0x1)\n",
        ):
            with self.subTest(output=output):
                budget = ProbeBudget()
                with patch.object(budget, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
                    with self.assertRaises(MakeProbeError):
                        _make_runtime(budget)

    def test_directory_permission_attacks_reject_without_masked_errors_or_residue(self):
        controls = [
            ("os.chmod('/work', 0)", "pathname permission loss"),
            ("os.chmod('/work/nested', 0)", "pathname permission loss"),
            ("os.chmod('nested', 0, dir_fd=directory)", "pathname permission loss"),
            ("os.fchmod(directory, 0)", "directory permission changes"),
            ("os.fchmod(os.dup(directory), 0)", "directory permission changes"),
            ("ctypes.CDLL(None).syscall(452, directory, b'nested', 0, 0)", "unadmitted syscall"),
            ("os.mkdir('/work/locked', 0)", "untraversable directory creation"),
            ("os.mkdir('locked', 0, dir_fd=directory)", "untraversable directory creation"),
            ("os.umask(0o700)\nos.mkdir('/work/locked')", "owner permission masking"),
        ]
        for operation, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes,os\nos.mkdir('/work/nested', 0o700)\n"
                    "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                    "try:\n " + operation.replace("\n", "\n ") + "\n"
                    "except OSError:\n pass\nos._exit(7)\n"
                ))
                session = self.session()
                descriptors = []
                with session:
                    original = session._sandbox_run
                    def retain_owned_directory(root, **kwargs):
                        for mount in kwargs["mounts"]:
                            if mount["target"] == "/work":
                                descriptors.append(os.open(
                                    mount["source"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                ))
                        return original(root, **kwargs)
                    try:
                        with patch.object(session, "_sandbox_run", retain_owned_directory):
                            with self.assertRaisesRegex(MakeProbeError, expected):
                                session.command(Command(
                                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                                ))
                        for descriptor in descriptors:
                            self.assertEqual(os.fstat(descriptor).st_mode & 0o700, 0o700)
                    finally:
                        # Regression controls remain safe even against the old
                        # guard: restore only retained, explicitly owned fixtures.
                        for descriptor in descriptors:
                            os.fchmod(descriptor, 0o700)
                            for name in ("nested", "locked"):
                                try:
                                    os.chmod(name, 0o700, dir_fd=descriptor, follow_symlinks=False)
                                except FileNotFoundError:
                                    pass
                            os.close(descriptor)
                self.assert_clean(session)

    def test_safe_output_permissions_and_regular_file_fchmod_remain_supported(self):
        self.add("reader.py", (
            "import os,stat\nos.umask(0o077)\nos.mkdir('/work/owned', 0o700)\n"
            "descriptor = os.open('/work/owned/file', os.O_CREAT | os.O_WRONLY, 0o600)\n"
            "os.fchmod(descriptor, 0)\nos.fchmod(descriptor, 0o644)\nos.close(descriptor)\n"
            "os.chmod('/work/owned/file', 0o755)\nos.chmod('/work/owned', 0o700)\n"
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.chmod('owned', 0o700, dir_fd=directory)\nos.close(directory)\n"
            "assert stat.S_IMODE(os.stat('/work/owned/file').st_mode) == 0o755\n"
            "print('safe permissions')\n"
        ))
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
            ))
            self.assertEqual(output.stdout, b"safe permissions\n")
        self.assert_clean(session)

    @contextmanager
    def stopped_tracee(self, setup):
        from scripts.validation_ownership.syscall_guard import ptrace, SETOPTIONS
        child = os.fork()
        if child == 0:
            try:
                setup()
                os._exit(0)
            except BaseException:
                os._exit(125)
        stopped = False
        try:
            waited, status = os.waitpid(child, 0)
            self.assertEqual(waited, child)
            stopped = os.WIFSTOPPED(status)
            self.assertTrue(stopped, status)
            ptrace(SETOPTIONS, child, 0, 0x100000)
            yield child
        finally:
            if stopped:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)

    def test_ptrace_bootstrap_restores_post_drop_memory_observation(self):
        from scripts.validation_ownership.syscall_guard import memory, ptrace, trace_me, TRACEME
        libc = ctypes.CDLL(None, use_errno=True)
        buffer = ctypes.create_string_buffer(b"owned")
        def drop_dumpability():
            if libc.prctl(4, 0, 0, 0, 0):
                raise OSError(ctypes.get_errno(), "cannot model post-setuid dumpability")
        def previous_bootstrap():
            drop_dumpability()
            ptrace(TRACEME, 0)
            os.kill(os.getpid(), signal.SIGSTOP)
        with self.stopped_tracee(previous_bootstrap) as child:
            with self.assertRaises(OSError) as caught:
                memory(child, ctypes.addressof(buffer), 6)
            self.assertEqual(caught.exception.errno, errno.EIO)
        with self.stopped_tracee(lambda: trace_me(drop_dumpability)) as child:
            self.assertEqual(memory(child, ctypes.addressof(buffer), 6), b"owned\0")

    def test_ptrace_pathname_stops_at_nul_before_an_unmapped_page(self):
        from scripts.validation_ownership.syscall_guard import cstring, memory, trace_me, Violation
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mmap.restype = ctypes.c_void_p
        libc.mmap.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_long,
        ]
        libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        page = os.sysconf("SC_PAGE_SIZE")
        address = libc.mmap(None, page * 2, 3, 0x22, -1, 0)
        self.assertNotIn(address, (None, ctypes.c_void_p(-1).value))
        try:
            for payload, expected in (
                (b"end\0", "end"), (b"\xc3\xa9\0", "é"), (b"\0", ""),
                (b"\xff\0", "strict UTF-8"), (b"x" * 4096, "pathname exceeds bound"),
            ):
                with self.subTest(payload_length=len(payload), expected=expected):
                    start = address + page - len(payload)
                    ctypes.memmove(start, payload, len(payload))
                    def unmap_guard_page():
                        if libc.munmap(address + page, page):
                            raise OSError(ctypes.get_errno(), "cannot unmap owned guard page")
                    with self.stopped_tracee(lambda: trace_me(unmap_guard_page)) as child:
                        with self.assertRaises(OSError) as caught:
                            memory(child, address + page - 4, 8)
                        self.assertEqual(caught.exception.errno, errno.EIO)
                        if payload in (b"\xff\0", b"x" * 4096):
                            with self.assertRaisesRegex(Violation, expected):
                                cstring(child, start)
                        else:
                            self.assertEqual(cstring(child, start), expected)
        finally:
            self.assertEqual(libc.munmap(address, page * 2), 0)

    def test_authentic_make_target_and_domain_semantics(self):
        self.add("Makefile", "MODE ?= red\ninclude rules.mk\n")
        self.add("rules.mk", (
            "MODE_DEPS = dep-$(MODE)\n"
            "define rule\n"
            "$(1): $$(MODE_DEPS) | order\n"
            "\t@printf '%s\\n' '$$@'\n"
            "endef\n"
            "$(eval $(call rule,owned))\n"
            "dep-red dep-blue order: ;\n"
            ".PHONY: owned\n"
        ))
        with self.session() as session:
            observations = session.variants(
                "owned", [(), (("command-line", "MODE", "blue"),)],
                variables=("MODE",), owner_inputs=("rules.mk",),
            )
            for observation, expected in zip(observations, ("red", "blue")):
                target = observation.semantics["files"][0]
                self.assertEqual(target["target"], "owned")
                self.assertEqual(target["prerequisites"], [
                    {"name": "dep-" + expected, "order_only": False},
                    {"name": "order", "order_only": True},
                ])
                self.assertIn("printf", target["recipe"])
                self.assertEqual(observation.semantics["domains"]["MODE"]["value"], expected)
            self.assertNotEqual(observations[0].semantic_digest, observations[1].semantic_digest)
        self.assert_clean(session)

    def test_raw_binary_registered_output_and_concrete_source(self):
        self.add("data/value.bin", b"\x00\xff\r\n")
        self.add("reader.py", "import os\nos.write(1, open('data/value.bin', 'rb').read())\n")
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                code=("reader.py",), sources=("data/value.bin",),
            ))
            self.assertEqual(output.stdout, b"\x00\xff\r\n")
            self.assertEqual(output.consumed, ("data/value.bin",))
        self.assert_clean(session)

    def test_native_prefixed_controls_reproduce_descriptor_forgery(self):
        """Real benign pre-fix Make load/SHELL controls write their inherited FD."""
        channel_path = self.directory / "pre-fix-channel"
        with channel_path.open("wb") as channel:
            fd = channel.fileno()
            self.add("payload.c", (
                "#include <unistd.h>\nint plugin_is_GPL_compatible;\n"
                f"static void payload(void) {{ write({fd}, \"forged\", 6); }}\n"
                "int payload_gmk_setup(void) { payload(); return 1; }\n"
                "int main(void) { payload(); return 0; }\n"
            ))
            for flags, name in ((("-shared", "-fPIC"), "payload.so"), ((), "payload")):
                compiled = subprocess.run(
                    ["/usr/bin/cc", *flags, str(self.root / "payload.c"), "-o", str(self.root / name)],
                    env={**ENVIRONMENT, "TMPDIR": str(self.directory)}, capture_output=True, timeout=20,
                )
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                self.add(name, (self.root / name).read_bytes(), "100755")
            for prefix in (
                "load ./payload.so\n",
                "override SHELL := ./payload\nX := $(shell ignored)\n",
            ):
                with self.subTest(prefix=prefix):
                    self.add("Makefile", prefix + "all: ;\n")
                    channel.seek(0)
                    channel.truncate()
                    before = subprocess.run(
                        ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
                        cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                        pass_fds=(fd,), capture_output=True, timeout=10,
                    )
                    self.assertEqual(before.returncode, 0, before.stderr)
                    self.assertEqual(channel_path.read_bytes(), b"forged")
                    channel.seek(0)
                    channel.truncate()
                    session = self.session()
                    with self.assertRaises(MakeProbeError):
                        with session:
                            session.make("all")
                    self.assertEqual(channel_path.read_bytes(), b"")
                    self.assert_clean(session)

    def test_make_cannot_open_observation_mapping_event_or_fd_paths(self):
        controls = [
            "$(file >/control/events,forged)",
            "$(file >/control/result,VOMAKE1)",
            "$(file </control/map/count)",
            "include /control/result",
            "$(eval $(file >/control/events,forged))",
            "$(file >/proc/self/fd/3,forged)",
            "$(file >/dev/fd/3,forged)",
            "$(file >/repo/../control/result,forged)",
        ]
        for payload in controls:
            with self.subTest(payload=payload):
                self.add("Makefile", payload + "\nall: ;\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "channel denied|namespace denied"):
                    with session:
                        session.make("all")
                self.assert_clean(session)

    def test_make_native_dispatch_and_shell_flags_are_not_authority(self):
        controls = [
            "override SHELL := /usr/bin/make\nX := $(shell --version)\n",
            "override SHELL := /lib64/ld-linux-x86-64.so.2\nX := $(shell ignored)\n",
            "override .SHELLFLAGS := -ec\nX := $(shell printf ok)\n",
            "override SHELL := /repo/native\nX := $(shell ignored)\n",
        ]
        self.add("native", Path("/usr/bin/true").read_bytes(), "100755")
        for payload in controls:
            with self.subTest(payload=payload):
                self.add("Makefile", payload + "all: ;\n")
                session = self.session()
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.make("all")
                self.assert_clean(session)

    def test_stdout_cannot_forge_native_target_or_domain_results(self):
        self.add("Makefile", (
            "$(info VOMAKE1 fake-domain blue)\n"
            "$(info Considering target file 'forged'.)\n"
            "$(info Makefile:1: update target 'all' due to: forged)\n"
            "MODE := red\nall: genuine\n\t@printf ok\n"
            "genuine: ;\n"
        ))
        with self.session() as session:
            result = session.make("all", variables=("MODE",))
            self.assertIn(b"forged", result.stdout)
            self.assertEqual(result.semantics["domains"]["MODE"]["value"], "red")
            self.assertEqual(result.semantics["files"][0]["prerequisites"], [
                {"name": "genuine", "order_only": False},
            ])
            self.assertEqual({item["target"] for item in result.semantics["files"]}, {"all", "genuine"})
        self.assert_clean(session)

    def test_parse_time_file_reads_are_confined_and_source_symlinks_reject(self):
        self.add("owner.txt", b"real")
        self.add("Makefile", "VALUE := $(file <owner.txt)\nall: ;\n")
        with self.session() as session:
            result = session.make("all", variables=("VALUE",), owner_inputs=("owner.txt",))
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "real")
        self.assert_clean(session)
        (self.root / "link").symlink_to("owner.txt")
        self.entries["link"] = GitTreeEntry("link", "120000", "blob", "0" * 40)
        self.add("Makefile", "VALUE := $(file <link)\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "nonregular candidate source"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_eager_command_replay_after_parse_failure_is_exact_and_real(self):
        self.add("value.txt", "alpha\n")
        self.add("reader.py", "import os\nos.write(1, open('value.txt', 'rb').read())\n")
        self.add("Makefile", (
            "VALUE := $(shell python3 -I -B reader.py)\n"
            "ifeq ($(VALUE),)\n$(error value has not been supplied)\nendif\nall: ;\n"
        ))
        command = Command(
            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
            code=("reader.py",), sources=("value.txt",),
        )
        with self.session() as session:
            result = session.make(
                "all", variables=("VALUE",), owner_inputs=("Makefile", "value.txt"),
                commands={"python3 -I -B reader.py": command},
            )
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "alpha")
            self.assertTrue(result.events)
            self.assertTrue(all(event["match"] == 0 for event in result.events))
            self.assertEqual(len(session.cache), 1)
        self.assert_clean(session)

    def test_registry_source_open_mmap_stat_and_directory_glob_are_real(self):
        self.add("data/a.bin", b"ab")
        self.add("data/b.bin", b"cd")
        self.add("reader.py", (
            "import glob, mmap, os\n"
            "paths = sorted(glob.glob('data/*.bin'))\n"
            "for path in paths:\n"
            " assert os.stat(path).st_size == 2\n"
            " with open(path, 'rb') as source:\n"
            "  with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as view:\n"
            "   os.write(1, view[:])\n"
        ))
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                code=("reader.py",), sources=("data/*.bin",), directories=("data",),
            ))
            self.assertEqual(output.stdout, b"abcd")
            self.assertEqual(output.consumed, ("data/a.bin", "data/b.bin"))
        self.assert_clean(session)

    def test_undeclared_sources_reject_even_when_errors_are_caught(self):
        self.add("data/admitted", "yes")
        self.add("hidden/value", "secret-test-fixture")
        operations = [
            "open('hidden/value').read()",
            "os.stat('hidden/value')",
            "os.lstat('hidden/value')",
            "os.access('hidden/value', os.R_OK)",
            "os.listdir('hidden')",
            "list(glob.iglob('hidden/*'))",
            "os.readlink('hidden/value')",
            "mmap.mmap(os.open('hidden/value', os.O_RDONLY), 0, access=mmap.ACCESS_READ)",
            "os.stat('hid' + 'den/' + 'value')",
            "os.stat('/usr/share/doc')",
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import glob, mmap, os\nopen('data/admitted').read()\n"
                    "try:\n " + operation + "\nexcept OSError:\n pass\nprint('accepted')\n"
                ))
                # Negative control uses the same real function and files without
                # confinement, rather than a string assertion about its source.
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, capture_output=True, timeout=10, env=ENVIRONMENT,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertIn(b"accepted", before.stdout)
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                            code=("reader.py",), sources=("data/admitted",),
                        ))
                self.assert_clean(session)

    def test_registry_cannot_reach_channels_descriptors_or_escape_syscalls(self):
        operations = [
            "open('/control/events', 'wb').write(b'forged')",
            "open('/control/result', 'wb').write(b'forged')",
            "open('/proc/self/fd/3', 'rb').read()",
            "os.fstat(3)",
            "os.open('/repo', os.O_PATH); os.fstat(100)",
            "ctypes.CDLL(None).syscall(101, 0, 0, 0, 0)",
            "ctypes.CDLL(None).syscall(319, b'payload', 0)",
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\ntry:\n " + operation
                    + "\nexcept OSError:\n pass\nprint('accepted')\n"
                ))
                session = self.session()
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_declared_reported_and_consumed_sources_must_agree(self):
        self.add("data/a", "a")
        self.add("data/b", "b")
        for read, reported, expected in (
            ("open('data/a').read()", ["data/a"], "declared/consumed"),
            ("open('data/a').read(); open('data/b').read()", ["data/a"], "declared/reported/consumed"),
        ):
            self.add("reader.py", read + "\nprint(" + repr(json.dumps({
                "name": "fixture", "version": 1, "record_count": 2, "source_paths": reported,
            })) + ")\n")
            session = self.session()
            with self.assertRaisesRegex(MakeProbeError, expected):
                with session:
                    session.registry(Command(
                        ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                        code=("reader.py",), sources=("data/a", "data/b"),
                    ))
            self.assert_clean(session)

    def test_semantic_identity_excludes_unrelated_snapshot_and_live_cache_drift(self):
        self.add("Makefile", "VALUE := $(file <owner.txt)\nall: ;\n")
        self.add("owner.txt", "one")
        self.add("notes.md", "documentation")
        self.add("other.c", "int unrelated;\n")
        identities = []
        for path, content in (
            (None, None), ("notes.md", "new unrelated documentation"),
            ("other.c", "int unrelated = 2;\n"), ("owner.txt", "two"),
        ):
            if path:
                # Deliberately do not change Git entry IDs: live-byte identity
                # must not accidentally reuse an index-only command namespace.
                (self.root / path).write_text(content)
            with self.session() as session:
                result = session.make("all", variables=("VALUE",), owner_inputs=("Makefile", "owner.txt"))
                output = session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "-c", "import os; os.write(1, open('owner.txt','rb').read())"),
                    sources=("owner.txt",),
                ))
                identities.append((result.semantic_digest, result.execution_digest, output.stdout))
            self.assert_clean(session)
        self.assertEqual(len({row[0] for row in identities[:3]}), 1)
        self.assertEqual(len({row[1] for row in identities}), 4)
        self.assertNotEqual(identities[0][0], identities[3][0])
        self.assertEqual([row[2] for row in identities], [b"one", b"one", b"one", b"two"])

    def test_snapshot_is_stable_within_one_session(self):
        self.add("owner.txt", "one")
        command = Command(
            ("/usr/bin/python3", "-I", "-B", "-c", "import os; os.write(1,open('owner.txt','rb').read())"),
            sources=("owner.txt",),
        )
        with self.session() as session:
            first = session.command(command)
            (self.root / "owner.txt").write_text("changed")
            second = session.command(command)
            self.assertIs(first, second)
            self.assertEqual(second.stdout, b"one")
        self.assert_clean(session)

    def test_variant_limit_rejects_before_any_variant_launch(self):
        self.add("Makefile", "all: ;\n")
        with self.session(states=2) as session:
            runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "before launch"):
                session.variants("all", [(), (), ()])
            self.assertEqual(session.budget.runs, runs)
        self.assert_clean(session)

    def test_global_deadline_is_not_reset_per_process(self):
        before = time.monotonic()
        for _ in range(2):
            subprocess.run(
                ["/usr/bin/python3", "-I", "-c", "import time; time.sleep(0.18)"],
                env=ENVIRONMENT, timeout=0.3, check=True,
            )
        self.assertGreater(time.monotonic() - before, 0.3)
        budget = ProbeBudget(Limits(seconds=0.3))
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            for _ in range(2):
                budget.run(
                    ["/usr/bin/python3", "-I", "-c", "import time; time.sleep(0.18)"],
                    env=ENVIRONMENT,
                )
        self.assertFalse(budget.children)
        self.assertLess(time.monotonic() - budget.started, 1.5)

    def test_streaming_output_cache_and_scratch_storage_are_aggregate_bounded(self):
        for limits, program, expected in (
            ({"process_output_bytes": 512}, "import os\nos.write(1,b'x'*10000)", "output"),
            ({"cache_bytes": 200}, "import os\nos.write(1,b'x'*300)", "cache"),
            ({"sandbox_bytes": 2048}, "open('/work/a','wb').write(b'x'*5000)", "aggregate sandbox"),
            ({"created_files": 2}, "[open('/work/'+str(n),'wb').close() for n in range(4)]", "creation"),
        ):
            with self.subTest(limits=limits):
                self.add("reader.py", program)
                session = self.session(**limits)
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_candidate_symlink_and_relocated_directory_aliases_reject(self):
        controls = [
            "os.symlink('../../repo', '/work/alias')\n"
            "assert os.access('/work/alias/reader.py', os.R_OK)\n"
            "assert not os.access('/work/alias/undeclared', os.R_OK)\n",
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.symlink('../../repo', 'alias', dir_fd=directory)\n",
            "os.rename('/work/a/b', '/work/b')\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n"
            "assert not os.access('../../../repo/undeclared', os.R_OK)\n",
            "directory = os.open('.', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.rename('/work/a/b', '/work/b')\n"
            "assert os.access('../../../repo/reader.py', os.R_OK, dir_fd=directory)\n",
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.rename('a/b', 'b', src_dir_fd=directory, dst_dir_fd=directory)\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n",
            "assert ctypes.CDLL(None).syscall(316, -100, b'/work/a/b', -100, b'/work/b', 0) == 0\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n",
        ]
        for operation in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\nos.makedirs('/work/a/b/c')\nos.chdir('/work/a/b/c')\n"
                    + operation + "print('alias admitted')\n"
                ))
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "symlink creation|directory-entry relocation"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_trusted_runtime_symlinks_use_the_authorized_source_destination(self):
        runtime = self.directory / "runtime"
        runtime.mkdir()
        (runtime / "alias").symlink_to("../../../../repo")
        (runtime / "absolute").symlink_to("/repo")
        (runtime / "file").symlink_to("/repo/data/admitted")
        self.add("data/admitted", b"owned")
        prefix = "/usr/lib/x86_64-linux-gnu/gconv/"
        for name in ("alias", "absolute", "alias/../repo"):
            for operation, accepted in (
                (
                    "descriptor = os.open(alias + '/data/admitted', os.O_RDONLY)\n"
                    "descriptor = os.dup(descriptor)\n"
                    "with mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ) as view:\n"
                    " os.write(1, view[:])\n", True,
                ),
                ("os.access(alias + '/undeclared', os.R_OK)\n", False),
                ("os.chdir(alias)\nos.access('undeclared', os.R_OK)\n", False),
                (
                    "directory = os.open(alias, os.O_RDONLY | os.O_DIRECTORY)\n"
                    "os.access('undeclared', os.R_OK, dir_fd=directory)\n", False,
                ),
            ):
                with self.subTest(alias=name, operation=operation):
                    self.add("reader.py", (
                        "import mmap, os\nalias = " + repr(prefix + name) + "\n" + operation
                    ))
                    session = self.session()
                    with session:
                        run = session._sandbox_run
                        def with_runtime_alias(root, **kwargs):
                            kwargs["mounts"].append(session._mount(runtime, prefix.rstrip("/")))
                            return run(root, **kwargs)
                        with patch.object(session, "_sandbox_run", with_runtime_alias):
                            command = Command(
                                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                                code=("reader.py",), sources=("data/admitted",) if accepted else (),
                            )
                            if accepted:
                                result = session.command(command)
                                self.assertEqual(result.stdout, b"owned")
                                self.assertEqual(result.consumed, ("data/admitted",))
                            else:
                                with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                                    session.command(command)
                    self.assert_clean(session)
        self.add("reader.py", (
            "import os, stat\nlink = " + repr(prefix + "file") + "\n"
            "assert stat.S_ISLNK(os.lstat(link).st_mode)\n"
            "assert os.readlink(link) == '/repo/data/admitted'\n"
            "descriptor = os.open(link, os.O_PATH | os.O_NOFOLLOW)\n"
            "assert stat.S_ISLNK(os.fstat(descriptor).st_mode)\n"
            "assert os.readlink('', dir_fd=descriptor) == '/repo/data/admitted'\n"
            "os.close(descriptor)\nprint('nofollow metadata')\n"
        ))
        with self.session() as session:
            run = session._sandbox_run
            def with_runtime_alias(root, **kwargs):
                kwargs["mounts"].append(session._mount(runtime, prefix.rstrip("/")))
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", with_runtime_alias):
                result = session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
                self.assertEqual(result.stdout, b"nofollow metadata\n")
                self.assertEqual(result.consumed, ())
        self.assert_clean(session)

    @staticmethod
    def mapping_program(operation):
        return (
            "import ctypes, mmap, os\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "libc.mmap.restype = ctypes.c_void_p\n"
            "libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, "
            "ctypes.c_int, ctypes.c_int, ctypes.c_long]\n"
            "libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]\n"
            "libc.mremap.restype = ctypes.c_void_p\n"
            "libc.mremap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, "
            "ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p]\n"
            "libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]\n"
            "def mapping(protection, flags, descriptor=-1):\n"
            " address = libc.mmap(None, 4096, protection, flags, descriptor, 0)\n"
            " assert address not in (None, ctypes.c_void_p(-1).value), ctypes.get_errno()\n"
            " return address\n"
            "def child_write(address):\n"
            " child = os.fork()\n"
            " if child == 0:\n"
            "  ctypes.memmove(address, b'child\\0', 6)\n"
            "  os._exit(0)\n"
            " assert os.waitpid(child, 0)[1] == 0\n"
            + operation
        )

    def test_shared_mapping_protection_upgrade_and_fork_reject(self):
        for protection in (0, 1):
            with self.subTest(protection=protection):
                self.add("reader.py", self.mapping_program(
                    f"address = mapping({protection}, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)\n"
                    "assert libc.mprotect(address, 4096, 3) == 0\n"
                    "ctypes.memmove(address, b'parent\\0', 7)\n"
                    "child_write(address)\n"
                    "assert ctypes.string_at(address, 6) == b'child\\0'\n"
                    "class Vector(ctypes.Structure):\n"
                    " _fields_ = [('base', ctypes.c_void_p), ('length', ctypes.c_size_t)]\n"
                    "vector = Vector.from_address(address + 128)\n"
                    "child = os.fork()\n"
                    "if child == 0:\n"
                    " ctypes.memmove(address, b'reader.py\\0', 10)\n"
                    " ctypes.memmove(address + 256, b'child\\n', 6)\n"
                    " vector.base, vector.length = address + 256, 6\n"
                    " os._exit(0)\n"
                    "assert os.waitpid(child, 0)[1] == 0\n"
                    "assert libc.access(ctypes.c_void_p(address), os.R_OK) == 0\n"
                    "assert libc.writev(1, ctypes.byref(vector), 1) == 6\n"
                    "assert libc.munmap(address, 4096) == 0\nprint('shared across fork')\n"
                ))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"child\nshared across fork\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "shared anonymous"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_mutable_backing_file_mappings_reject_even_readonly_private_aliases(self):
        for flags in ("mmap.MAP_PRIVATE", "mmap.MAP_SHARED"):
            with self.subTest(flags=flags):
                self.add("reader.py", self.mapping_program(
                    "path = os.environ.get('MAPPING_FIXTURE', '/work/backing')\n"
                    "with open(path, 'wb') as stream: stream.write(b'parent\\0' + b'\\0'*4089)\n"
                    "original = os.open(path, os.O_RDONLY)\n"
                    "descriptor = os.dup(original)\nos.close(original)\n"
                    f"address = mapping(1, {flags}, descriptor)\nos.close(descriptor)\n"
                    "child = os.fork()\n"
                    "if child == 0:\n"
                    " descriptor = os.open(path, os.O_WRONLY)\n"
                    " assert os.pwrite(descriptor, b'child\\0', 0) == 6\n"
                    " os.close(descriptor)\n os._exit(0)\n"
                    "assert os.waitpid(child, 0)[1] == 0\n"
                    "assert ctypes.string_at(address, 6) == b'child\\0'\n"
                    "assert libc.munmap(address, 4096) == 0\nprint('mutable backing observed')\n"
                ))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env={
                        **ENVIRONMENT, "MAPPING_FIXTURE": str(self.directory / "backing"),
                    }, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"mutable backing observed\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "mutable backing"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_protection_and_remap_alias_families_reject(self):
        self.add("data/page", b"immutable" + b"\0" * (4096 - 9))
        controls = [
            (
                "address = mapping(1, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "assert libc.mprotect(address, 4096, 3) == 0\n",
                (), "writable memory protection",
            ),
            (
                "descriptor = os.open('data/page', os.O_RDONLY)\n"
                "address = mapping(1, mmap.MAP_SHARED, descriptor)\n"
                "alias = libc.mremap(address, 0, 4096, 1, None)\n"
                "assert alias != ctypes.c_void_p(-1).value\n"
                "assert ctypes.string_at(alias, 9) == b'immutable'\n",
                ("data/page",), "remap alias",
            ),
        ]
        for flags in (3, 5, 7):
            controls.append((
                "address = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "destination = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "ctypes.memmove(address, b'private\\0', 8)\n"
                f"alias = libc.mremap(address, 4096, 4096, {flags}, destination)\n"
                "assert alias != ctypes.c_void_p(-1).value\n"
                "assert ctypes.string_at(alias, 8) == b'private\\0'\n",
                (), "remap alias",
            ))
        for operation, sources, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", self.mapping_program(operation + "print('upgrade admitted')\n"))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"upgrade admitted\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                            code=("reader.py",), sources=sources,
                        ))
                self.assert_clean(session)

    def test_private_mapping_resize_fork_and_read_protection_stay_supported(self):
        self.add("reader.py", self.mapping_program(
            "address = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
            "ctypes.memmove(address, b'parent\\0', 7)\nchild_write(address)\n"
            "assert ctypes.string_at(address, 7) == b'parent\\0'\n"
            "address = libc.mremap(address, 4096, 8192, 1, None)\n"
            "assert address != ctypes.c_void_p(-1).value\n"
            "assert ctypes.string_at(address, 7) == b'parent\\0'\n"
            "assert libc.mprotect(address, 8192, 1) == 0\n"
            "assert libc.munmap(address, 8192) == 0\n"
            "print('private fork and resize')\n"
        ))
        with self.session() as session:
            result = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
            ))
            self.assertEqual(result.stdout, b"private fork and resize\n")
        self.assert_clean(session)

    def test_shared_clone_state_rejects_but_suspended_parent_spawn_stays_supported(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sched.h>\n#include <signal.h>\n"
            "#include <stdio.h>\n#include <stdlib.h>\n#include <sys/mman.h>\n"
            "#include <sys/wait.h>\n#include <unistd.h>\n"
            "static int child(void *argument) { (void)argument; _exit(0); }\n"
            "int main(int argc, char **argv) {\n"
            " if(argc != 2) return 2;\n"
            " void *stack = mmap(NULL, 16384, PROT_READ | PROT_WRITE,\n"
            "  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);\n"
            " if(stack == MAP_FAILED) return 3;\n"
            " int flags = (int)strtoul(argv[1], NULL, 0) | SIGCHLD;\n"
            " pid_t pid = clone(child, (char *)stack + 16384, flags, NULL);\n"
            " int status;\n"
            " if(pid < 0 || waitpid(pid, &status, 0) != pid || status != 0) return 4;\n"
            " if(munmap(stack, 16384)) return 5;\n"
            " puts(\"owned child reaped\"); return 0;\n}\n"
        ))
        with self.session() as session:
            tool = session.compile_native(("native.c",))
            for flags in (0, 0x100 | 0x4000):
                self.assertEqual(session.native(tool, (str(flags),)).stdout, b"owned child reaped\n")
        self.assert_clean(session)
        for flags in (0x100, 0x200, 0x400):
            with self.subTest(flags=flags):
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "shared-memory candidate threads|shared-state clone"):
                    with session:
                        tool = session.compile_native(("native.c",))
                        session.native(tool, (str(flags),))
                self.assert_clean(session)

    def test_alternate_memory_alias_and_creation_interfaces_remain_fail_closed(self):
        # Invalid IDs/addresses keep these calls harmless even if an admission
        # regression lets the kernel see them. No global IPC object is created.
        for number in (29, 30, 31, 67, 133, 216, 259, 310, 311, 319, 323, 329, 425, 437, 440):
            with self.subTest(syscall=number):
                self.add("reader.py", (
                    "import ctypes\n"
                    f"ctypes.CDLL(None).syscall({number}, -1, -1, -1, -1, -1, -1)\n"
                ))
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, f"unadmitted syscall {number}"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_all_supported_creation_attempts_reserve_the_aggregate_quota(self):
        controls = [
            ("", "os.close(os.open('/work/'+str(index), os.O_CREAT | os.O_WRONLY, 0o600))"),
            ("", "os.close(libc.syscall(85, ('/work/'+str(index)).encode(), 0o600))"),
            ("", "os.mkdir('/work/'+str(index))"),
            ("", "os.mkdir(str(index), dir_fd=directory)"),
            ("", "os.close(os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600))"),
            ("", "os.close(libc.syscall(2, b'/work', os.O_TMPFILE | os.O_RDWR, 0o600))"),
            ("open('/work/seed', 'wb').close()\n", "os.link('/work/seed', '/work/'+str(index))"),
            ("open('/work/seed', 'wb').close()\n",
             "os.link('seed', str(index), src_dir_fd=directory, dst_dir_fd=directory)"),
        ]
        for setup, operation in controls:
            for allowed in (True, False):
                with self.subTest(operation=operation, allowed=allowed):
                    self.add("reader.py", (
                        "import ctypes, os\nlibc = ctypes.CDLL(None)\nos.chdir('/work')\n"
                        "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                        + setup + "for index in range(2):\n " + operation + "\nprint('created')\n"
                    ))
                    creations = 2 + bool(setup)
                    session = self.session(created_files=creations if allowed else 1)
                    if allowed:
                        with session:
                            output = session.command(Command(
                                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                            ))
                            self.assertEqual(output.stdout, b"created\n")
                            self.assertEqual(session.files_created, creations)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                            with session:
                                session.command(Command(
                                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                                ))
                        self.assertEqual(session.files_created, 2)
                    self.assert_clean(session)

    def test_unsupported_creation_and_empty_path_link_cannot_bypass_low_quota(self):
        controls = [
            ("os.symlink('missing', '/work/link')", "symlink creation"),
            ("os.symlink('missing', 'link', dir_fd=directory)", "symlink creation"),
            ("os.mkfifo('/work/fifo')", "unadmitted syscall"),
            (
                "descriptor = os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600)\n"
                "libc.syscall(265, descriptor, b'', directory, b'link', 0x1000)",
                "file-creation budget",
            ),
        ]
        for operation, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\nlibc = ctypes.CDLL(None)\nos.chdir('/work')\n"
                    "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                    + operation + "\nprint('unsupported creation admitted')\n"
                ))
                session = self.session(created_files=1)
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_creation_quota_is_not_reset_between_commands(self):
        self.add("Makefile", "all: ;\n")
        with self.session(created_files=1) as session:
            for index in range(2):
                command = Command((
                    "/usr/bin/python3", "-I", "-B", "-c",
                    "import os; os.close(os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600))",
                    str(index),
                ))
                if index == 0:
                    session.command(command)
                    self.assertEqual(session.files_created, 1)
                else:
                    with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                        session.command(command)
        self.assert_clean(session)

    def test_fanout_bound_and_parent_interrupt_clean_descendants(self):
        self.add("reader.py", (
            "import os,time\n"
            "for n in range(5):\n"
            " if os.fork()==0:\n"
            "  time.sleep(20)\n"
            "  os._exit(0)\n"
            "time.sleep(20)\n"
        ))
        session = self.session(processes=3)
        with self.assertRaisesRegex(MakeProbeError, "descendant-process"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        self.add("reader.py", "import os,time\nif os.fork()==0: time.sleep(20)\ntime.sleep(20)\n")
        session = self.session()
        with self.assertRaises(KeyboardInterrupt):
            with session:
                timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
                timer.start()
                try:
                    session.command(Command(
                        ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                    ))
                finally:
                    timer.cancel()
                    timer.join()
        self.assert_clean(session)

    def test_strict_named_protocols_reject_binary_and_truncated_frames(self):
        self.add("reader.py", "import os\nos.write(1,b'\\xff\\x00\\r\\n')\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "strict utf-8"):
            with session:
                session.registry(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        for raw in (b"", b"VOMAKE1\0", b"forged", b"VOMAKE1\0" + b"\xff" * 4):
            with self.assertRaises(MakeProbeError):
                _read_observation(raw, "all", ())
        for raw in (b"x", b"\xff" * 20, b"\0" * 20):
            with self.assertRaises(MakeProbeError):
                _read_events(raw, expected_mapping_count=0)

    def test_worktree_symlink_escape_and_invalid_limit_values_fail(self):
        self.add("Makefile", "all: ;\n")
        self.add("nested/owner", "data")
        (self.root / "nested/owner").unlink()
        (self.root / "nested").rmdir()
        (self.root / "nested").symlink_to(self.directory)
        with self.assertRaises(MakeProbeError):
            with self.session():
                self.fail("symlinked source must not be materialized")
        self.assertFalse(self.scratch.exists())
        for value in (0, -1, float("nan"), float("inf"), 3601, True):
            with self.assertRaises(MakeProbeError):
                Limits(seconds=value)

    def test_pattern_rules_and_target_specific_variable_values_are_native(self):
        self.add("Makefile", (
            "LOCAL = global\n"
            "%.out: LOCAL = pattern-$*\n"
            "%.out: %.src | order\n\t@printf '%s' '$(LOCAL)'\n"
            "foo.out: LOCAL = target-$*\n"
            "foo.src bar.src order: ;\n"
        ))
        with self.session() as session:
            for target, expected in (("foo.out", "target-foo"), ("bar.out", "pattern-bar")):
                result = session.make(target, variables=("LOCAL",))
                self.assertEqual(result.semantics["files"][0]["variables"]["LOCAL"]["value"], expected)
                self.assertEqual(result.semantics["domains"]["LOCAL"]["value"], "global")
                self.assertEqual(result.semantics["files"][0]["prerequisites"], [
                    {"name": target.replace(".out", ".src"), "order_only": False},
                    {"name": "order", "order_only": True},
                ])
        self.assert_clean(session)

    def test_event_mapping_and_pending_byte_bounds_cover_real_commands(self):
        value = "x" * 512
        source_command = "printf %s " + value
        self.add("Makefile", "VALUE := $(shell " + source_command + ")\nall: ;\n")
        commands = {source_command: Command(("/usr/bin/printf", "%s", value))}
        with self.session() as session:
            result = session.make("all", variables=("VALUE",), commands=commands)
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], value)
        self.assert_clean(session)
        for limits in ({"event_bytes": 96}, {"mapping_bytes": 32}):
            with self.subTest(limits=limits):
                session = self.session(**limits)
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.make("all", variables=("VALUE",), commands=commands)
                self.assert_clean(session)
        with self.session(pending_bytes=8192) as session:
            remaining = session.budget.limits.pending_bytes - session.budget.bytes.get("pending", 0)
            previous_runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "pending byte"):
                session.command(Command(("/usr/bin/printf", "%s", "x" * remaining)))
            self.assertEqual(previous_runs, session.budget.runs)
        self.assert_clean(session)

    def test_native_tools_compile_and_run_only_in_channel_free_capsules(self):
        self.add("native.c", (
            "#include <stdio.h>\n"
            "int main(void) { int ch; FILE *f=fopen(\"data/value\", \"rb\");"
            "if(!f) return 2; while((ch=fgetc(f))!=EOF) putchar(ch); return fclose(f); }\n"
        ))
        self.add("data/value", b"native\x00\xff")
        with self.session() as session:
            tool = session.compile_native(("native.c",))
            output = session.native(tool, sources=("data/value",))
            self.assertEqual(output.stdout, b"native\x00\xff")
            self.assertEqual(output.consumed, ("data/value",))
            self.assertTrue(tool.path.is_file())
            self.assertEqual(tool.path.read_bytes()[:4], b"\x7fELF")
            tool.path.chmod(0o700)
            tool.path.write_bytes(b"not the sealed ELF")
            with self.assertRaisesRegex(MakeProbeError, "sealed native tool changed"):
                session.native(tool, sources=("data/value",))
        self.assert_clean(session)

    def test_native_candidate_cannot_write_channels_or_read_inherited_fds(self):
        for operation in (
            'fopen("/control/events", "wb")',
            'fdopen(3, "w")',
        ):
            self.add("native.c", (
                "#define _POSIX_C_SOURCE 200809L\n#include <stdio.h>\n"
                f"int main(void) {{ FILE *f = {operation};"
                'if(f) { fputs("forged", f); fclose(f); } return 0; }\n'
            ))
            session = self.session()
            with self.assertRaises(MakeProbeError):
                with session:
                    tool = session.compile_native(("native.c",))
                    session.native(tool)
            self.assert_clean(session)

    def test_real_immutable_tree_consumer_reports_make_and_bundle_sources(self):
        from scripts.validation_ownership.consumer import check
        result = check(ROOT, "HEAD")
        self.assertEqual(result["scope"], "ownership-probe-foundation")
        self.assertEqual(result["make"]["target"], "localization-check")
        self.assertEqual(result["make"]["semantics"]["files"][0]["prerequisites"], [
            {"name": "localization-generate", "order_only": False},
        ])
        self.assertEqual(result["generated_registry"]["name"], "chapterbundle")
        self.assertEqual(result["generated_registry"]["source_paths"], ["src/data/ch2_bundle.json"])
        self.assertGreater(result["generated_registry"]["record_count"], 0)

    def test_cxx_native_compilation_and_worker_failure_are_confined(self):
        self.add("native.cpp", "#include <cstdio>\nint main() { std::puts(\"cxx\"); }\n")
        with self.session() as session:
            tool = session.compile_native(("native.cpp",), cxx=True)
            self.assertEqual(session.native(tool).stdout, b"cxx\n")
        self.assert_clean(session)
        self.add("reader.py", "import os\nos._exit(7)\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unsuccessfully: 7"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)

    def test_fifo_input_and_malformed_native_result_do_not_leave_residue(self):
        self.add("Makefile", "all: ;\n")
        (self.root / "Makefile").unlink()
        os.mkfifo(self.root / "Makefile")
        started = time.monotonic()
        with self.assertRaises(MakeProbeError):
            with self.session():
                self.fail("FIFO input was admitted as a regular source")
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(self.scratch.exists())
        for data in (b"", b"\x7fELF", b"\0" * 128):
            with self.assertRaises(MakeProbeError):
                ProbeSession._validate_native(data)

    def test_direct_argument_boundaries_cannot_collide_and_quote_refactors_survive(self):
        registration = Command(("/usr/bin/printf", "%s", "a b"))
        commands = {
            "printf %s 'a b'": registration,
            'printf "%s" "a b"': registration,
        }
        values = []
        for expression in ("printf %s 'a b'", 'printf "%s" "a b"'):
            # Isolate argv semantics from the separate identity of recipe-owning
            # source bytes: this goal deliberately has no recipe.
            self.add("Makefile", "VALUE := $(shell " + expression + ")\n.PHONY: all\nall:\n")
            with self.session() as session:
                result = session.make("all", variables=("VALUE",), commands=commands)
                self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "a b")
                values.append(result.semantic_digest)
            self.assert_clean(session)
        self.assertEqual(values[0], values[1])
        self.add("Makefile", "VALUE := $(shell printf %s a b)\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unregistered eager"):
            with session:
                session.make("all", variables=("VALUE",), commands=commands)
        self.assert_clean(session)

    def test_live_symlink_and_mode_state_bind_execution_not_unrelated_owner(self):
        self.add("Makefile", "all: ;\n")
        self.add("data/one", "one")
        self.add("data/two", "two")
        link = self.root / "data/link"
        link.symlink_to("one")
        self.entries["data/link"] = GitTreeEntry("data/link", "120000", "blob", "0" * 40)
        states = []
        for change in (None, "mode", "symlink"):
            if change == "mode":
                (self.root / "data/one").chmod(0o755)
            elif change == "symlink":
                link.unlink()
                link.symlink_to("two")
            with self.session() as session:
                result = session.make("all")
                states.append((result.execution_digest, result.semantic_digest))
            self.assert_clean(session)
        self.assertEqual(len({row[0] for row in states}), 3)
        self.assertEqual(len({row[1] for row in states}), 1)
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "symlink/gitlink"):
                session.sources(("data/*",))

    def test_memory_and_filesystem_observations_are_aggregate_bounded(self):
        self.add("reader.py", (
            "import os,time\n"
            "allocation = bytearray(16*1024*1024)\n"
            "for index in range(6):\n"
            " if os.fork()==0:\n"
            "  time.sleep(20)\n"
            "  os._exit(0)\n"
            "time.sleep(20)\n"
        ))
        session = self.session(address_space_bytes=96 * 1024 * 1024)
        with self.assertRaisesRegex(MakeProbeError, "aggregate address-space"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        self.add("Makefile", "all: ;\n")
        with self.session(entries=64) as session:
            session.make("all")
        self.assert_clean(session)
        self.add("Makefile", "$(foreach n," + " ".join(map(str, range(200))) + ",$(file <missing$(n)))\nall: ;\n")
        session = self.session(entries=64)
        with self.assertRaisesRegex(MakeProbeError, "filesystem-observation"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_one_session_cannot_hide_parallel_workers(self):
        self.add("Makefile", "all: ;\n")
        errors = []
        with self.session() as session:
            runs = session.budget.runs
            def other_worker():
                try:
                    session.make("all")
                except MakeProbeError as error:
                    errors.append(error)
            worker = threading.Thread(target=other_worker)
            worker.start()
            worker.join()
            self.assertEqual(len(errors), 1)
            self.assertEqual(session.budget.runs, runs)
            self.assertTrue(session.budget.failed)
        self.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
