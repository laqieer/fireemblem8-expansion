"""Benign preparation only: all effectful role/API boundaries are inert."""

import ast
import copy
import ctypes
import errno
import importlib.util
import io
import os
from pathlib import Path
import selectors
import signal
import stat
import struct
import subprocess
import sys
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

from . import bootstrap as b


def selected_control():
    # This test-only path is not an execution argument or a bootstrap bypass.
    # The command invoking the suite must first verify this checkout is c8.
    root = Path(os.environ["ISSUE180_SELECTED_SOURCE"]).resolve() / "scripts/validation_ownership"
    name = "_issue180_benign_c8"
    spec = importlib.util.spec_from_file_location(
        name, root / "__init__.py", submodule_search_locations=[str(root)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    budget = __import__(name + ".budget", fromlist=["budget"])
    life = __import__(name + ".lifecycle", fromlist=["lifecycle"])
    for leaf in ("budget", "lifecycle", "producer_channel"):
        if Path(sys.modules[name + "." + leaf].__file__).resolve() != root / (leaf + ".py"):
            raise AssertionError("foreign selected test control")
    return budget, life


budgeting, life = selected_control()


def pipe_value(inode, access):
    return (8, inode, stat.S_IFIFO, access)


def mount_value(flags=15):
    return (3, 4, stat.S_IFCHR | 0o666, os.makedev(1, 3), 7, flags)


def worker_value():
    return {
        "mode": "readonly", "before": list(mount_value()), "after": list(mount_value(11)),
        "failure": None, "calls": [list(b.SELECTIVE_CALL)], "caps": [0, 0, 0, 0, 0, 1],
        "denied": [errno.EROFS, errno.EROFS, errno.EACCES], "null_io": True,
        "fd_closed": True, "local_nonzero_topology": True,
    }


def status_bytes(*, pid=77, parent=1, uid=(0, 1001, 0, 1001), gid=(0, 1002, 0, 1002)):
    fields = {
        "Uid": " ".join(map(str, uid)), "Gid": " ".join(map(str, gid)),
        "Groups": "", "NoNewPrivs": "1", "NSpid": str(pid), "PPid": str(parent),
        "CapInh": "0", "CapPrm": "200000", "CapEff": "200000", "CapBnd": "ffffffff",
        "CapAmb": "0",
    }
    return "".join(key + ":\t" + value + "\n" for key, value in fields.items()).encode()


class Inert(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        trap = AssertionError("unmocked forbidden operation")
        for module, name in (
            (b, "libc"), (b, "load_subject"), (b.subprocess, "Popen"), (b.subprocess, "run"),
            (b.os, "fork"), (b.os, "waitid"), (b.os, "waitpid"), (b.os, "pidfd_open"),
            (b.os, "setuid"), (b.os, "setgid"), (b.os, "setresuid"), (b.os, "setresgid"),
            (b.os, "setgroups"), (b.os, "kill"), (b.os, "killpg"), (b.os, "_exit"),
            (b.signal, "pidfd_send_signal"), (life, "prctl"), (life, "parent_death"),
            (life, "require_pidfds"), (life, "owned_children"),
            (life, "ordinary_executable"), (budgeting, "ordinary_executable"),
        ):
            self.stack.enter_context(patch.object(module, name, side_effect=trap))
        self.stack.enter_context(patch.object(signal, "pthread_sigmask", return_value=set()))
        self.stack.enter_context(patch.object(signal, "signal", return_value=signal.SIG_DFL))
        self.stack.enter_context(patch.object(signal, "sigpending", return_value=set()))
        self.stack.enter_context(patch.object(signal, "sigtimedwait", return_value=None))
        self.stack.enter_context(patch.object(signal, "raise_signal", side_effect=trap))
        self.stack.enter_context(patch.object(b.time, "monotonic", return_value=100.0))
        self.stack.enter_context(patch.object(b, "_cleanup_life", life))
        self.report = life._CleanupReport("R", 130.0)
        self.stack.enter_context(patch.object(b, "_cleanup_report", self.report))
        self.binding = life._FixtureBinding("readonly", 1001, 1002, 130.0, 3, 4, 1001)
        self.outer = {"user": (1, 10), "mount": (1, 11), "label": b"unconfined"}
        self.target = b.Target((1, 20), (1, 21), (3, 8, stat.S_IFDIR | 0o755, 0, 29, 0))
        self.harness = Path(b.__file__).resolve().parents[2]

    def supervisor(self):
        root = b.Bootstrap(
            life, self.binding, Path("/work/issue180-null-bootstrap-1-42/fixture"),
            Path("/work/candidate"), Path("/work/harness"),
        )
        root.outer, root.target = self.outer, self.target
        return root

    def state(self, entered=False):
        return {
            "uid": (0,) * 4 if entered else (1001,) * 4,
            "gid": (0,) * 4 if entered else (1002,) * 4, "groups": (), "caps": (0,) * 5,
            "nnp": 1, "uid_map": ((0, 1001, 1),) if entered else b.FULL_MAP,
            "gid_map": ((0, 1002, 1),) if entered else b.FULL_MAP,
            "user": self.target.user if entered else self.outer["user"],
            "mount": self.target.mount if entered else self.outer["mount"],
            "label": self.outer["label"], "parent": 1, "pids": (77,),
        }

    def setup_state(self):
        local = (1 << 41) - 1
        return {**self.state(True), "caps": (0, local, local, local, 0)}

    def records(self, status=0):
        cleanup = life._cleanup_wire(life._CleanupReport("R", 130.0).value())
        return [
            {"v": 1, "role": "R", "phase": "before", "binding": self.binding.wire(), "pid": 77,
             "kind": "normal-exit", "stage": "worker", "status": status, "error": None,
             "result": worker_value(), "setup_status": 0},
            {"v": 1, "role": "R", "phase": "after", "binding": self.binding.wire(), "pid": 77,
             "before": "before", "cleanup": cleanup, "disposition": "return", "status": status},
            {"v": 1, "role": "L", "phase": "before", "token": "a" * 32, "pid": 55,
             "kind": "normal-exit", "stage": "wait", "status": status, "error": None,
             "wait": [55, 0, int(signal.SIGCHLD), os.CLD_EXITED if status >= 0 else os.CLD_KILLED,
                      status if status >= 0 else -status]},
            {"v": 1, "role": "L", "phase": "after", "token": "a" * 32, "pid": 55,
             "before": "before", "cleanup": cleanup, "disposition": "return", "status": status},
        ]

    def push(self):
        sha = "b" * 40
        event = {
            "repository": {"full_name": b.REPOSITORY, "private": False},
            "sender": {"login": b.OWNER}, "ref": "refs/heads/" + b.BRANCH,
            "before": "0" * 40, "after": sha, "created": True, "deleted": False,
        }
        context = {
            "GITHUB_SHA": sha, "GITHUB_RUN_ID": "42", "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": event["ref"], "GITHUB_REPOSITORY": b.REPOSITORY,
            "GITHUB_ACTOR": b.OWNER, "GITHUB_TRIGGERING_ACTOR": b.OWNER,
            "GITHUB_RUN_NUMBER": "1", "GITHUB_RUN_ATTEMPT": "1",
            "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
            "GITHUB_WORKFLOW_REF": b.REPOSITORY + "/" + b.WORKFLOW + "@" + event["ref"],
            "GITHUB_WORKFLOW_SHA": sha, "GITHUB_WORKSPACE": "/work", "GITHUB_EVENT_PATH": "/work/event",
        }
        return event, context


class Benign(Inert):
    def test_root_context_checks_private_proc_full_maps_and_actual_M0_owner(self):
        full = (1 << 41) - 1
        for fault in (None, "root-id", "same-mount", "proc-view", "map", "owner", "death-signal"):
            value = {
                **self.state(), "uid": (0,) * 4, "gid": (0,) * 4,
                "caps": (0, full, full, full, 0), "parent": 0, "pids": (1,),
            }
            if fault == "map":
                value["uid_map"] = ((0, 1001, 1),)
            host = status_bytes(pid=888, parent=777, uid=(0,) * 4, gid=(0,) * 4)
            host = host.replace(b"NSpid:\t888\n", b"NSpid:\t888 1\n")
            ancestor = {21: (1, 10), 22: (1, 90), 23: (1, 91), 24: (1, 92),
                        26: (1, 999) if fault == "owner" else (1, 10)}
            current = {"user": (1, 10), "mnt": (1, 90) if fault == "same-mount" else (1, 11),
                       "pid": (1, 12), "net": (1, 13)}
            with self.subTest(fault=fault), patch.object(b.os, "getpid", return_value=1), \
                 patch.object(b.os, "getresuid", return_value=(1001,) * 3 if fault == "root-id" else (0,) * 3), \
                 patch.object(b.os, "getresgid", return_value=(0,) * 3), patch.object(b.os, "setgroups"), \
                 patch.object(b.os, "open", side_effect=[21, 22, 23, 24, 25]), patch.object(b.os, "close"), \
                 patch.object(b, "read_file", side_effect=[host, status_bytes(pid=777, parent=555)]), \
                 patch.object(b, "fd_identity", side_effect=lambda fd: ancestor[fd]), \
                 patch.object(b, "namespace", side_effect=lambda name: current[name]), \
                 patch.object(b, "mount") as mount, patch.object(b, "self_state", return_value=value), \
                 patch.object(b.os, "readlink", return_value="888" if fault == "proc-view" else "1"), \
                 patch.object(b, "death_signal", return_value=0 if fault == "death-signal" else signal.SIGKILL), \
                 patch.object(b.fcntl, "ioctl", side_effect=lambda fd, op: b.NEWNS if op == b.NS_GET_NSTYPE else 26):
                if fault:
                    with self.assertRaises(b.Refusal):
                        b.root_context()
                else:
                    self.assertEqual(b.root_context(), value)
                    mount.assert_called_once_with("proc", "/proc", 14, "proc")

    def test_creator_real_control_flow_has_only_one_creation_and_normalizes_saved_ids(self):
        for fault in (None, "missing-cap", "dumpable", "label", "map"):
            root = self.supervisor()
            root.fds = {"request.read": 10, "request.write": 11, "response.read": 12,
                        "response.write": 13, "fixture": 14}
            table = {fd: pipe_value(fd + 100, 0) for fd in (0, 1, 2, 10, 11, 12, 13)}
            table[14] = (1, 99, stat.S_IFDIR, 0)
            first = {
                **self.state(), "uid": (0, 1001, 0, 1001), "gid": (0, 1002, 0, 1002),
                "caps": (0, 0 if fault == "missing-cap" else 1 << b.CAP_SYS_ADMIN, 0, 0xFFFFFFFF, 0),
            }
            ready = {**first, "caps": (0, 1 << b.CAP_SYS_ADMIN, 1 << b.CAP_SYS_ADMIN, 0xFFFFFFFF, 0)}
            created = {**self.state(True), "label": b"foreign" if fault == "label" else b"unconfined"}
            values = iter([first, ready, created, self.state(True)])
            events = []
            content = {
                "/proc/self/uid_map": b"0 0 1\n" if fault == "map" else b"0 1001 1\n",
                "/proc/self/gid_map": b"0 1002 1\n", "/proc/self/setgroups": b"deny\n",
            }
            native = SimpleNamespace(unshare=lambda flags: events.append(("unshare", flags)) or 0)
            with self.subTest(fault=fault), ExitStack() as stack:
                stack.enter_context(patch.object(b, "fd_inventory", side_effect=lambda: dict(table)))
                stack.enter_context(patch.object(b.os, "close", side_effect=lambda fd: table.pop(fd)))
                stack.enter_context(patch.object(b, "self_state", side_effect=lambda: next(values)))
                stack.enter_context(patch.object(b, "read_file", side_effect=lambda path: content[path]))
                stack.enter_context(patch.object(b, "libc", return_value=native))
                stack.enter_context(patch.object(b, "prctl", side_effect=lambda option, *args: 1 if option == 3 and fault == "dumpable" else 0))
                for name in ("setgroups", "setresgid", "setresuid"):
                    stack.enter_context(patch.object(b.os, name, side_effect=lambda *args, name=name: events.append((name, *args))))
                for name in ("capset", "bounding", "parent_guard", "send_token", "receive_token"):
                    stack.enter_context(patch.object(b, name, side_effect=lambda *args, name=name: events.append((name, *args))))
                if fault:
                    with self.assertRaises(b.Refusal):
                        root.creator()
                    self.assertNotIn(("setresuid", 0, 0, 0), events)
                else:
                    root.creator()
                    self.assertEqual([row for row in events if row[0] == "unshare"],
                                     [("unshare", b.NEWUSER | b.NEWNS)])
                    self.assertIn(("setresuid", -1, 1001, -1), events)
                    self.assertIn(("setresgid", -1, 1002, -1), events)
                    self.assertIn(("setresuid", 0, 0, 0), events)
                    self.assertIn(("setresgid", 0, 0, 0), events)
                    self.assertEqual([row for row in events if row[0] == "capset"],
                                     [("capset", 1 << b.CAP_SYS_ADMIN), ("capset", 0)])
                    self.assertEqual(root.fds, {})
                if fault in ("missing-cap", "dumpable"):
                    self.assertFalse(any(row[0] == "unshare" for row in events))

    def test_parent_guard_and_fixed_state_machine(self):
        with patch.object(b, "prctl") as prctl, patch.object(b.os, "getppid", return_value=1):
            b.parent_guard()
            prctl.assert_called_once_with(1, signal.SIGKILL)
            with patch.object(b.os, "getppid", return_value=2), self.assertRaises(b.Refusal):
                b.parent_guard()
        root = self.supervisor()
        for old, new in (("new", "fixture"), ("fixture", "creator"), ("creator", "mapped"),
                         ("mapped", "blocked"), ("blocked", "retired"), ("retired", "released")):
            root.advance(old, new)
        with self.assertRaises(b.Refusal):
            root.advance("mapped", "blocked")
        with patch.object(b.time, "monotonic", return_value=130), self.assertRaises(TimeoutError):
            root.advance("released", "released")

    def test_worker_release_requires_actual_parent_observed_FDs_then_retired_capabilities(self):
        for fault in (None, "alias", "close", "retained-cap"):
            root = self.supervisor()
            root.state, root.fds = "mapped", {"user": 10, "mount": 11}
            table = {0: pipe_value(30, 0), 1: pipe_value(40, 1), 2: pipe_value(50, 1),
                     10: (1, 20, stat.S_IFREG, 0), 11: (1, 21, stat.S_IFREG, 0)}
            root.capture = table[1][:2]
            pipes = iter([(12, 13), (14, 15)])
            events = []

            def pipe(flags):
                left, right = next(pipes)
                table[left], table[right] = pipe_value(left + 100, 0), pipe_value(left + 100, 1)
                return left, right

            def fork(role):
                child = root.children["W"] = b.Child(77, 16, 17, 123)
                root.fds.update({"W.pidfd": 16, "W.proc": 17})
                table[16], table[17] = (1, 888, stat.S_IFREG, 0), (1, 999, stat.S_IFDIR, 0)
                return child

            def close(fd):
                events.append(("close", fd))
                table.pop(fd)
                if fd == 10 and fault == "close":
                    raise OSError(errno.EIO, "inert target-close")

            def remote(directory):
                expected = dict(root.expected_worker)
                if fault == "alias":
                    expected[19] = pipe_value(40, 1)
                return expected

            caps = (0, 1 << b.CAP_KILL, 1 << b.CAP_KILL, 1 << b.CAP_KILL, 0)
            if fault == "retained-cap":
                caps = (0, (1 << b.CAP_KILL) | (1 << b.CAP_SYS_ADMIN), 1 << b.CAP_KILL, 1 << b.CAP_KILL, 0)
            retired = {**self.state(), "uid": (0,) * 4, "gid": (0,) * 4, "caps": caps, "pids": (1,), "parent": 0}
            with self.subTest(fault=fault), ExitStack() as stack:
                stack.enter_context(patch.object(b.os, "pipe2", side_effect=pipe))
                stack.enter_context(patch.object(root, "fork_role", side_effect=fork))
                stack.enter_context(patch.object(b.os, "close", side_effect=close))
                stack.enter_context(patch.object(b, "fd_inventory", side_effect=lambda: dict(table)))
                stack.enter_context(patch.object(b, "remote_fds", side_effect=remote))
                stack.enter_context(patch.object(b, "live_child"))
                stack.enter_context(patch.object(b, "self_state", return_value=retired))
                stack.enter_context(patch.object(b.os, "getpid", return_value=1))
                stack.enter_context(patch.object(b, "death_signal", return_value=signal.SIGKILL))
                stack.enter_context(patch.object(b, "receive_token"))
                for name in ("bounding", "capset", "prctl", "send_token"):
                    stack.enter_context(patch.object(b, name, side_effect=lambda *args, name=name: events.append((name, *args))))
                if fault:
                    with self.assertRaises((b.Refusal, OSError)):
                        root.setup_worker()
                    self.assertFalse(any(row[0] == "send_token" for row in events))
                else:
                    root.setup_worker()
                    self.assertEqual(root.state, "released")
                    self.assertEqual(root.fds, {"W.pidfd": 16, "worker.read": 14})
                    go = events.index(("send_token", 13, b"GO\n", 130.0))
                    for required in (("close", 17), ("close", 10), ("close", 11),
                                     ("bounding", 1 << b.CAP_KILL), ("capset", 1 << b.CAP_KILL), ("prctl", 38, 1)):
                        self.assertLess(events.index(required), go)

    def test_push_identity_rejects_every_reuse_or_foreign_identity(self):
        event, context = self.push()
        self.assertEqual(b.identity(event, context)["source"], b.SOURCE)
        for key, wrong in (
            ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_ACTOR", "foreign"),
            ("GITHUB_TRIGGERING_ACTOR", "foreign"), ("GITHUB_RUN_NUMBER", "2"),
            ("GITHUB_RUN_ATTEMPT", "2"), ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("GITHUB_WORKFLOW_SHA", "c" * 40), ("GITHUB_REF", "refs/heads/master"),
        ):
            with self.subTest(key=key), self.assertRaises(b.Refusal):
                b.identity(event, {**context, key: wrong})
        for mutation in (
            {"created": False}, {"deleted": True}, {"before": "a" * 40},
            {"repository": {"full_name": b.REPOSITORY, "private": True}},
            {"sender": {"login": "foreign"}}, {"after": "c" * 40},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(b.Refusal):
                b.identity({**event, **mutation}, context)

    def test_parsed_workflow_has_one_closed_job_and_allowlist(self):
        workflow = yaml.load((self.harness / b.WORKFLOW).read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"], {"push": {"branches": [b.BRANCH]}})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(set(workflow["jobs"]), {"readonly-bootstrap"})
        job = workflow["jobs"]["readonly-bootstrap"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["env"]["EXPECTED_SOURCE_SHA"], b.SOURCE)
        guards = {part.strip() for part in job["if"].split("&&")}
        self.assertEqual(guards, {
            "github.repository == 'laqieer/fireemblem8-expansion'",
            "github.event.repository.private == false", "github.actor == 'laqieer'",
            "github.triggering_actor == 'laqieer'", "github.event.sender.login == 'laqieer'",
            "github.event.created == true", "github.event.deleted == false",
            "github.run_number == 1", "github.run_attempt == 1",
        })
        checkouts = [step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual([step["with"]["ref"] for step in checkouts], ["${{ github.sha }}", b.SOURCE])
        for step in checkouts:
            self.assertRegex(step["uses"], r"^actions/checkout@[0-9a-f]{40}$")
            self.assertEqual(step["with"]["persist-credentials"], "false")
        artifact = [step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(artifact), 1)
        self.assertRegex(artifact[0]["uses"], r"^actions/upload-artifact@[0-9a-f]{40}$")
        self.assertEqual(artifact[0]["if"], "always()")
        self.assertEqual(
            {Path(row.strip()).name for row in artifact[0]["with"]["path"].splitlines()}, set(b.ARTIFACTS),
        )
        commands = [step["run"] for step in job["steps"] if "run" in step]
        self.assertEqual(sum("bootstrap.py\" run" in command for command in commands), 1)
        self.assertEqual(sum("sudo " in command for command in commands), 1)  # dependency setup only

    def test_source_guard_checks_exact_HEAD_parent_and_additive_inventory(self):
        sha = "b" * 40
        inventory = b"".join(b"A\0" + path.encode() + b"\0" for path in sorted(b.FILES))

        def replies(root, *args, **kwargs):
            if args[:2] == ("rev-parse", "HEAD"):
                return sha.encode() + b"\n"
            if args[0] == "rev-list":
                return (sha + " " + b.BASE + "\n").encode()
            if "--name-status" in args:
                return inventory
            return b""

        with patch.object(b, "canonical"), patch.object(b, "git", side_effect=replies), \
             patch.object(b.os, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o644)):
            b.verify_checkout(Path("/work/harness"), sha, harness=True)
            with self.assertRaises(b.Refusal):
                b.verify_checkout(Path("/work/harness"), "c" * 40, harness=True)
            for malformed in (
                inventory.replace(b"A\0", b"M\0", 1), inventory + b"A\0other\0",
                inventory.replace(b.PROGRAM.encode(), b"scripts/other.py"),
            ):
                prior, inventory = inventory, malformed
                with self.assertRaises(b.Refusal):
                    b.verify_checkout(Path("/work/harness"), sha, harness=True)
                inventory = prior

    def test_environment_is_data_not_an_arbitrary_import(self):
        with patch.object(b, "read_file", return_value=("ENVIRONMENT = " + repr(b.ENVIRONMENT)).encode()):
            b.verify_environment(Path("/source"))
        changed = {**b.ENVIRONMENT, "SUDO_UID": "1001"}
        with patch.object(b, "read_file", return_value=("ENVIRONMENT = " + repr(changed)).encode()), \
             self.assertRaises(b.Refusal):
            b.verify_environment(Path("/source"))

    def test_reaper_arguments_bind_sudo_and_independent_nonzero_ids(self):
        parent = Path("/work/issue180-null-bootstrap-1-42/fixture")
        arguments = ["--reaper", "readonly", "1001", "1002", "130.0", str(parent), "3", "4", "1001"]
        info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=3, st_ino=4, st_uid=1001, st_gid=1002)
        context = {"SUDO_UID": "1001", "SUDO_GID": "1002"}
        with patch.object(b, "canonical", side_effect=Path), patch.object(b.os, "lstat", return_value=info):
            bound, actual = b.reaper_arguments(arguments, life, Path("/work/harness"), context)
            self.assertEqual((bound, actual), (self.binding, parent))
            for index, wrong in ((1, "writable"), (2, "0"), (3, "1001"), (4, "131.0"), (7, "8"), (8, "0")):
                changed = list(arguments)
                changed[index] = wrong
                with self.subTest(index=index), self.assertRaises((b.Refusal, ValueError)):
                    b.reaper_arguments(changed, life, Path("/work/harness"), context)
            for args in (arguments + ["--command", "true"], arguments + ["--fd", "7"], arguments[:-1]):
                with self.assertRaises(b.Refusal):
                    b.reaper_arguments(args, life, Path("/work/harness"), context)
            with self.assertRaises(b.Refusal):
                b.reaper_arguments(arguments, life, Path("/work/harness"), {})

    def test_preentry_credentials_caps_maps_and_owner_restorations(self):
        b.validate_preentry(self.state(), self.binding, self.outer)
        bad = [
            ("uid", (0,) * 4), ("uid", (1001, 1001, 0, 1001)), ("gid", (1001,) * 4),
            ("groups", (1002,)), ("nnp", 0), ("user", (1, 99)), ("mount", (1, 99)),
            ("uid_map", ((0, 1001, 1),)), ("gid_map", ((0, 1002, 1),)), ("label", b"changed"),
        ] + [("caps", tuple(1 if index == bit else 0 for index in range(5))) for bit in range(5)]
        for key, value in bad:
            with self.subTest(key=key, value=value), self.assertRaises(b.Refusal):
                b.validate_preentry({**self.state(), key: value}, self.binding, self.outer)
        b.validate_target(self.state(True), self.binding, self.outer, self.target)
        for key, value in (
            ("user", self.outer["user"]), ("mount", self.outer["mount"]), ("nnp", 0),
            ("uid_map", ((0, 0, 1),)), ("gid_map", ((0, 1001, 1),)), ("uid", (1001,) * 4),
            ("groups", (0,)), ("label", b"borrowed-profile"),
        ):
            with self.subTest(key=key), self.assertRaises(b.Refusal):
                b.validate_target({**self.state(True), key: value}, self.binding, self.outer, self.target)
        b.validate_local_setup(self.setup_state())
        for caps in ((0,) * 5, (1, 1 << 21, 1 << 21, 1 << 21, 0), (0, 1 << 21, 1 << 21, 1 << 21, 0)):
            with self.subTest(caps=caps), self.assertRaises(b.Refusal):
                b.validate_local_setup({**self.setup_state(), "caps": caps})

    def test_fd_identity_direction_and_duplicate_alias_controls(self):
        expected = {0: pipe_value(1, 0), 1: pipe_value(2, 1), 2: pipe_value(3, 1)}
        b.validate_fds(expected, expected, (8, 4))
        for altered in (
            {**expected, 9: pipe_value(4, 1)}, {**expected, 9: expected[1]},
            {**expected, 9: (8, 12, stat.S_IFDIR, 0)},
            {**expected, 9: (8, 13, stat.S_IFCHR, 0)},
            {**expected, 0: pipe_value(1, 1)}, {**expected, 1: expected[2]},
        ):
            with self.subTest(altered=altered), self.assertRaises(b.Refusal):
                b.validate_fds(altered, expected, (8, 4))
        with self.assertRaises(b.Refusal):
            b.validate_fds(expected, expected, expected[1][:2])

    def test_live_child_rejects_exit_recycle_proc_or_pidfd_disagreement(self):
        child = b.Child(77, 10, 11, 123)

        def files(path, *args, **kwargs):
            return b"Pid:\t77\n" if "fdinfo" in str(path) else status_bytes()

        with patch.object(b, "proc_start", return_value=123), patch.object(b.select, "select", return_value=([], [], [])), \
             patch.object(b.os, "waitid", return_value=None), patch.object(b, "read_file", side_effect=files):
            b.live_child(child)
            for mutate in (
                patch.object(child, "reaped", True), patch.object(child, "start", 124),
                patch.object(child, "pidfd", None), patch.object(b.os, "waitid", return_value=object()),
                patch.object(b.select, "select", return_value=([10], [], [])),
                patch.object(b, "read_file", return_value=b"Pid:\t78\n"),
            ):
                with mutate, self.assertRaises(b.Refusal):
                    b.live_child(child)

    def test_typed_namespace_parent_owner_and_mount_owner(self):
        for fault in (None, "type", "owner", "parent", "mount-owner", "same-user", "same-mount"):
            root, child = self.supervisor(), b.Child(77, 8, 9, 123)
            identities = {20: self.target.user, 21: self.target.mount, 30: self.outer["user"], 31: self.target.user}
            if fault == "parent":
                identities[30] = (1, 90)
            if fault == "mount-owner":
                identities[31] = self.outer["user"]
            if fault == "same-user":
                identities[20] = self.outer["user"]
            if fault == "same-mount":
                identities[21] = self.outer["mount"]

            def ioctl(fd, op, *args):
                if op == b.NS_GET_NSTYPE:
                    return 0 if fault == "type" else b.NEWUSER if fd == 20 else b.NEWNS
                if op == b.NS_GET_OWNER_UID:
                    args[0][:] = struct.pack("=I", 0 if fault == "owner" else 1001)
                    return 0
                return 30 if op == b.NS_GET_PARENT else 31

            with self.subTest(fault=fault), patch.object(b, "live_child"), \
                 patch.object(b.os, "open", side_effect=[20, 21]), patch.object(b.os, "close"), \
                 patch.object(b, "fd_identity", side_effect=lambda fd: identities[fd]), \
                 patch.object(b.fcntl, "ioctl", side_effect=ioctl), \
                 patch.object(b, "mount_state", return_value=self.target.root):
                if fault:
                    with self.assertRaises(b.Refusal):
                        root.pin_target(child)
                else:
                    root.pin_target(child)
                    self.assertEqual(root.target, self.target)

    def map_run(self, fault=None):
        root, child = self.supervisor(), b.Child(77, 8, 9, 123)
        contents, offsets = {20: b"allow\n", 21: b"", 22: b""}, {}
        if fault == "present":
            contents[21] = b"0 0 1\n"
        writes, closes = [], []

        def read(fd, count):
            return contents[fd]

        def write(fd, data):
            writes.append((fd, data))
            if fault == "partial" and fd == 21:
                return len(data) - 1
            contents[fd] = b"0 0 1\n" if fault == "readback" and fd == 22 else data
            return len(data)

        def close(fd):
            closes.append(fd)
            if fault == "close" and fd == 20:
                raise OSError(errno.EIO, "inert close")

        with patch.object(b, "live_child"), patch.object(root, "pin_target_recheck"), \
             patch.object(b.os, "open", side_effect=[20, 21, 22]), patch.object(b.os, "read", side_effect=read), \
             patch.object(b.os, "write", side_effect=write), patch.object(b.os, "lseek"), \
             patch.object(b.os, "close", side_effect=close):
            if fault:
                with self.assertRaises((b.Refusal, OSError)):
                    root.map_creator(child)
            else:
                root.map_creator(child)
        self.assertEqual(closes, [20, 21, 22])
        self.assertEqual(root.fds, {})
        return writes, root.report.value()

    def test_exact_maps_partial_presence_readback_and_all_remaining_closes(self):
        writes, report = self.map_run()
        self.assertEqual(writes, [(20, b"deny"), (21, b"0 1001 1\n"), (22, b"0 1002 1\n")])
        self.assertTrue(report.complete)
        for fault in ("present", "partial", "readback", "close"):
            with self.subTest(fault=fault):
                writes, report = self.map_run(fault)
                if fault == "present":
                    self.assertEqual(writes, [])
                if fault == "partial":
                    self.assertEqual(len(writes), 2)
                if fault == "close":
                    self.assertFalse(report.complete)

    def worker_run(self, *, pre=None, post=None, fd_fault=None, root_fault=False, close_fault=None):
        root = self.supervisor()
        root.capture = (8, 40)
        root.fds = {"user": 10, "mount": 11, "gate.read": 12, "gate.write": 13,
                    "worker.read": 14, "worker.write": 15}
        table = {
            0: pipe_value(30, 0), 1: pipe_value(40, 1), 2: pipe_value(50, 1),
            10: (1, 20, stat.S_IFREG, 0), 11: (1, 21, stat.S_IFREG, 0),
            12: pipe_value(60, 0), 13: pipe_value(60, 1),
            14: pipe_value(70, 0), 15: pipe_value(70, 1),
        }
        root.expected_worker = {0: table[0], 1: table[15], 2: table[2],
                                **{fd: table[fd] for fd in (10, 11, 12)}}
        if fd_fault == "alias":
            table[16] = table[1]
        if fd_fault == "ancestor":
            table[16] = (1, 8, stat.S_IFDIR, 0)
        if fd_fault == "device":
            table[0] = (1, 8, stat.S_IFCHR, 0)
        events, writes = [], []

        def close(fd):
            events.append(("close", fd))
            if close_fault == (fd, "before"):
                raise OSError(errno.EIO, "inert before-close")
            table.pop(fd)
            if close_fault == (fd, "after"):
                raise OSError(errno.EIO, "inert after-close")

        def state(path):
            if str(path) in ("/", "."):
                return (3, 99, stat.S_IFDIR, 0, 99, 0) if root_fault else self.target.root
            return (3, 5, stat.S_IFDIR, 0, 30, 15)

        native = SimpleNamespace(setns=lambda fd, kind: events.append(("setns", fd, kind)) or 0)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, dict(os.environ), clear=True))
            for name in ("setresgid", "setresuid", "setgroups", "chdir"):
                stack.enter_context(patch.object(b.os, name, side_effect=lambda *args, name=name: events.append((name, *args))))
            stack.enter_context(patch.object(b.os, "dup2", side_effect=lambda source, target, **kw: table.__setitem__(target, table[source])))
            stack.enter_context(patch.object(b.os, "close", side_effect=close))
            stack.enter_context(patch.object(b.os, "write", side_effect=lambda fd, data: writes.append(data) or len(data)))
            stack.enter_context(patch.object(b, "fd_inventory", side_effect=lambda: dict(table)))
            stack.enter_context(patch.object(b, "self_state", side_effect=[pre or self.state(), post or self.setup_state()]))
            stack.enter_context(patch.object(b, "mount_state", side_effect=state))
            stack.enter_context(patch.object(b, "libc", return_value=native))
            for name in ("bounding", "capset", "prctl", "parent_guard", "send_token", "receive_token"):
                stack.enter_context(patch.object(b, name, side_effect=lambda *args, name=name: events.append((name, *args))))
            subject = stack.enter_context(patch.object(b, "mechanism", return_value=worker_value()))
            try:
                root.worker()
                failure = None
            except (b.Refusal, OSError) as error:
                failure = type(error)
                life._forget_error(error)
            return failure, subject.call_count, events, writes, root.report.value()

    def test_worker_real_control_flow_has_user_then_mount_entry_and_writer_barrier(self):
        failure, imports, events, writes, report = self.worker_run()
        self.assertIsNone(failure)
        self.assertEqual(imports, 1)
        self.assertEqual([event for event in events if event[0] == "setns"],
                         [("setns", 10, b.NEWUSER), ("setns", 11, b.NEWNS)])
        self.assertIn(("setresuid", 1001, 1001, 1001), events)
        self.assertIn(("setresgid", 1002, 1002, 1002), events)
        self.assertEqual(life._private_worker_record(writes[0]), life._private_worker_record(b.json_bytes(worker_value())))
        self.assertTrue(report.complete)

    def test_worker_refuses_root_caps_NNP_maps_rootcwd_and_descriptor_faults_before_import(self):
        faults = [
            {"pre": {**self.state(), "uid": (0,) * 4}},
            {"pre": {**self.state(), "caps": (0, 1, 1, 0, 0)}},
            {"pre": {**self.state(), "nnp": 0}},
            {"pre": {**self.state(), "gid_map": ((0, 1002, 1),)}},
            {"post": {**self.setup_state(), "user": self.outer["user"]}},
            {"post": {**self.setup_state(), "gid_map": ((0, 1001, 1),)}},
            {"post": self.state(True)},
            {"root_fault": True}, {"fd_fault": "alias"}, {"fd_fault": "ancestor"},
            {"fd_fault": "device"}, {"close_fault": (10, "before")}, {"close_fault": (10, "after")},
        ]
        for fault in faults:
            with self.subTest(fault=fault):
                failed, imports, _, writes, _ = self.worker_run(**fault)
                self.assertIsNotNone(failed)
                self.assertEqual((imports, writes), (0, []))

    def test_removing_actual_preentry_guard_breaks_the_behavioral_oracle(self):
        retained_root = {**self.state(), "uid": (0,) * 4}

        def oracle():
            failed, imports, _, _, _ = self.worker_run(pre=retained_root)
            self.assertIsNotNone(failed)
            self.assertEqual(imports, 0)

        oracle()
        with patch.object(b, "validate_preentry"):
            with self.assertRaises(AssertionError):
                oracle()
        oracle()

    def test_real_tokens_on_owned_pipes_and_partial_EOF_denial(self):
        reader, writer = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            b.send_token(writer, b"GO\n", 130.0)
            b.receive_token(reader, b"GO\n", 130.0)
            os.write(writer, b"G")
            with self.assertRaises(b.Refusal):
                b.receive_token(reader, b"GO\n", 130.0)
            os.close(writer)
            writer = None
            b.receive_eof(reader, 130.0)
            with self.assertRaises(b.Refusal):
                b.receive_token(reader, b"GO\n", 130.0)
            with self.assertRaises(b.Refusal):
                b.send_token(reader, b"ARBITRARY\n", 130.0)
        finally:
            os.close(reader)
            if writer is not None:
                os.close(writer)
        with self.assertRaises(TimeoutError):
            b.receive_token(100, b"GO\n", 100.0)

    def test_wait_status_is_owned_and_cleanup_cannot_invent_a_normal_observation(self):
        for status in (0, 7, -9, 125):
            child = b.Child(77, 10)
            value = SimpleNamespace(si_pid=77, si_uid=0, si_signo=int(signal.SIGCHLD),
                                    si_code=os.CLD_EXITED if status >= 0 else os.CLD_KILLED,
                                    si_status=status if status >= 0 else -status)
            with patch.object(b.os, "waitid", return_value=value):
                self.assertEqual(b.observe(child, 130, life), status)
            self.assertEqual(child.observed, status)
        child = b.Child(77, 10)
        with patch.object(b.os, "waitid", return_value=None), \
             patch.object(b.os, "waitpid", return_value=(77, 7 << 8)), \
             patch.object(b.signal, "pidfd_send_signal") as send:
            b.stop_child(child, 130)
            self.assertTrue(child.reaped)
            self.assertIsNone(child.observed)
            send.assert_called_once_with(10, signal.SIGKILL)
            b.stop_child(child, 130)
            send.assert_called_once()
            with self.assertRaises(b.Refusal):
                b.observe(child, 130, life)

    def test_R_latches_real_wait_before_all_cleanup_and_does_not_forward_worker_frames(self):
        for status, bad_bytes, cleanup_fault in ((7, False, True), (0, True, False), (0, False, False)):
            root, child = self.supervisor(), b.Child(77, 9)
            root.fds = {"worker.read": 8, "W.pidfd": 9}
            root.children["W"] = child
            root.stage = "worker"
            root.setup_status = 0
            data = b.json_bytes(self.records()[0] if bad_bytes else worker_value())
            source = iter([data, b""])
            frames, latches, closed = [], [], []

            def wait(*args):
                return SimpleNamespace(si_pid=77, si_uid=0, si_signo=int(signal.SIGCHLD),
                                       si_code=os.CLD_EXITED, si_status=status)

            def close(fd):
                latches.append(root.observation)
                closed.append(fd)
                if cleanup_fault and fd == 8:
                    raise OSError(errno.EIO, "inert cleanup")

            def stop(child, deadline):
                latches.append(root.observation)
                child.reaped = True

            with self.subTest(status=status, bad_bytes=bad_bytes, cleanup_fault=cleanup_fault), \
                 patch.object(b, "root_context", return_value=self.outer), \
                 patch.object(root, "create_fixture"), patch.object(root, "setup_creator", return_value=True), \
                 patch.object(root, "setup_worker", return_value=child), \
                 patch.object(b.select, "select", return_value=([8], [], [])), \
                 patch.object(b.os, "read", side_effect=lambda *args: next(source)), \
                 patch.object(b.os, "waitid", side_effect=wait), patch.object(b, "stop_child", side_effect=stop), \
                 patch.object(b.os, "write", side_effect=lambda fd, data: frames.append(data) or len(data)), \
                 patch.object(b.os, "close", side_effect=close):
                actual = root.run()
            self.assertEqual(actual, status if status else 125 if bad_bytes else 0)
            before = life._decode_frame(frames[0][4:], "a" * 32, self.binding)
            after = life._decode_frame(frames[1][4:], "a" * 32, self.binding, before)
            self.assertEqual(before.status, status)
            self.assertEqual(closed, [8, 9])
            self.assertTrue(all(item == ("worker", 77, status) for item in latches))
            self.assertEqual(after.cleanup.complete, not cleanup_fault and not bad_bytes)
            if bad_bytes:
                self.assertIsNone(before.result)
                self.assertEqual(after.disposition, "raise")

    def test_R_setup_exception_stays_first_when_cleanup_observes_nonzero(self):
        root = self.supervisor()
        child = b.Child(77, 9)
        root.children["N"], root.fds = child, {"N.pidfd": 9, "response.read": 8}
        primary = ValueError("bounded earlier setup")
        frames, closed = [], []

        def stop(child, deadline):
            child.reaped = True
            child.observed = None
            raise OSError(errno.EIO, "later cleanup")

        with patch.object(b, "root_context", side_effect=primary), patch.object(b, "stop_child", side_effect=stop), \
             patch.object(b.os, "close", side_effect=lambda fd: closed.append(fd)), \
             patch.object(b.os, "write", side_effect=lambda fd, data: frames.append(data) or len(data)):
            self.assertEqual(root.run(), 125)
        before = life._decode_frame(frames[0][4:], "a" * 32, self.binding)
        self.assertEqual((before.kind, before.status, before.error), ("exception", None, (2, None)))
        self.assertEqual(closed, [9, 8])
        self.assertIsNone(primary.__traceback__)

    def test_R_publication_short_write_deadline_and_duplicate_are_terminal(self):
        root = self.supervisor()
        value = self.records()[0]
        with patch.object(b.os, "write", return_value=1):
            with self.assertRaises(b.Refusal):
                root.publish(value)
        self.assertEqual(root.publications, 1)
        with self.assertRaises(b.Refusal):
            root.publish(value)
        root = self.supervisor()
        with patch.object(b.time, "monotonic", return_value=130), self.assertRaises(TimeoutError):
            root.publish(value)

    def test_first_error_and_uncertain_FD_do_not_skip_remaining_cleanup(self):
        for before_release in (False, True):
            root = self.supervisor()
            root.fds = {"user": 10, "mount": 11}
            primary = ValueError("earlier")
            attempted, released = [], []

            def close(fd):
                attempted.append(fd)
                if fd == 10 and before_release:
                    raise OSError(errno.EIO, "before")
                released.append(fd)
                if fd == 10:
                    raise OSError(errno.EIO, "after")

            with patch.object(b.os, "close", side_effect=close):
                root.closes(("user", "mount"), primary=primary)
                root.closes(("user", "mount"), primary=primary)
            self.assertEqual(attempted, [10, 11])
            self.assertEqual(released, [11] if before_release else [10, 11])
            self.assertFalse(root.report.value().complete)
            self.assertEqual(root.fds, {})
            self.assertFalse(hasattr(primary, "cleanup_errors"))

    def test_old_operation_requires_actual_EPERM_and_unchanged_flags(self):
        native = SimpleNamespace(mount=Mock(return_value=-1))
        with patch.object(b, "libc", return_value=native), patch.object(b.ctypes, "get_errno", return_value=errno.EPERM), \
             patch.object(b, "mount_state", return_value=mount_value()):
            b.old_negative(Path("/fixed/null"))
            self.assertEqual(native.mount.call_args.args[3], 0x102A)
            native.mount.return_value = 0
            with self.assertRaises(b.Refusal):
                b.old_negative(Path("/fixed/null"))
            native.mount.return_value = -1
            with patch.object(b.ctypes, "get_errno", return_value=errno.EINVAL), self.assertRaises(b.Refusal):
                b.old_negative(Path("/fixed/null"))
            with patch.object(b, "mount_state", side_effect=[mount_value(), mount_value(11)]), self.assertRaises(b.Refusal):
                b.old_negative(Path("/fixed/null"))

    def test_subject_adapter_records_one_actual_forwarded_call_without_a_fallback(self):
        setup = SimpleNamespace(ctypes=ctypes, MountAttributes=b.Attributes)
        native = SimpleNamespace(syscall=Mock(return_value=0))

        def actual_stub(root):
            c = setup.ctypes
            attributes = setup.MountAttributes(attr_clr=4)
            c.CDLL(None, use_errno=True).syscall(
                c.c_long(442), c.c_int(10), c.c_char_p(b""), c.c_uint(4096),
                c.byref(attributes), c.c_size_t(32),
            )

        setup._enable_toolchain_null = actual_stub
        with patch.object(b, "libc", return_value=native):
            self.assertEqual(b.helper_transition(setup, Path("/fixed")), [b.SELECTIVE_CALL])
        native.syscall.assert_called_once()
        self.assertIs(setup.ctypes, ctypes)

    def mechanism_run(self, *, old_success=False, wrong_target=False, retained_caps=False):
        events, changed = [], [False]
        native = SimpleNamespace(mount=lambda *args: events.append("old") or (0 if old_success else -1))
        setup = SimpleNamespace(bind=lambda *args, **kwargs: None)
        target_state = self.setup_state()
        if wrong_target:
            target_state["user"] = self.outer["user"]
        final_state = {**target_state, "caps": (0, 1, 1, 0, 0) if retained_caps else (0,) * 5}
        states = iter([target_state, final_state])

        def helper(*args):
            events.append("helper")
            changed[0] = True
            return [b.SELECTIVE_CALL]

        with ExitStack() as stack:
            stack.enter_context(patch.object(b, "self_state", side_effect=lambda: next(states)))
            stack.enter_context(patch.object(b, "fd_inventory", return_value={
                0: pipe_value(30, 0), 1: pipe_value(70, 1), 2: pipe_value(50, 1),
            }))
            imported = stack.enter_context(patch.object(b, "load_subject", return_value=setup))
            stack.enter_context(patch.object(Path, "mkdir"))
            stack.enter_context(patch.object(b.os, "open", side_effect=[20, 21]))
            stack.enter_context(patch.object(b.os, "close"))
            stack.enter_context(patch.object(b, "mount_state", side_effect=lambda path: mount_value(11 if changed[0] else 15)))
            stack.enter_context(patch.object(b, "libc", return_value=native))
            stack.enter_context(patch.object(b.ctypes, "get_errno", return_value=errno.EPERM))
            stack.enter_context(patch.object(b, "helper_transition", side_effect=helper))
            stack.enter_context(patch.object(b, "bounding", side_effect=lambda bits: events.append("bounding-zero")))
            stack.enter_context(patch.object(b, "capset", side_effect=lambda bits: events.append("caps-zero")))
            stack.enter_context(patch.object(b, "prctl", side_effect=lambda *args: events.append("NNP")))
            stack.enter_context(patch.object(b, "readonly_denial", side_effect=lambda path: events.append("readonly-io") or errno.EROFS))
            stack.enter_context(patch.object(b, "device_denial", side_effect=lambda path: events.append("device-io") or errno.EACCES))
            stack.enter_context(patch.object(b, "null_io", side_effect=lambda path: events.append("null-io")))
            try:
                result = b.mechanism(Path("/fixture/volume"), self.binding, self.outer, self.target, life)
                failed = False
            except b.Refusal as error:
                life._forget_error(error)
                result, failed = None, True
            return failed, result, events, imported.call_count

    def test_fixed_mechanism_old_negative_precedes_selective_and_dropped_IO(self):
        failed, value, events, imports = self.mechanism_run()
        self.assertFalse(failed)
        self.assertEqual(imports, 1)
        self.assertLess(events.index("old"), events.index("helper"))
        self.assertLess(events.index("helper"), events.index("caps-zero"))
        self.assertLess(events.index("NNP"), events.index("null-io"))
        self.assertTrue(value["null_io"])
        for fault in ({"old_success": True}, {"wrong_target": True}, {"retained_caps": True}):
            with self.subTest(fault=fault):
                failed, value, events, imports = self.mechanism_run(**fault)
                self.assertTrue(failed)
                self.assertIsNone(value)
                self.assertNotIn("null-io", events)
                if "wrong_target" in fault:
                    self.assertEqual(imports, 0)
                    self.assertNotIn("old", events)
                if "old_success" in fault:
                    self.assertNotIn("helper", events)

    def test_removing_old_negative_gate_breaks_the_behavioral_oracle(self):
        def oracle():
            failed, value, events, _ = self.mechanism_run(old_success=True)
            self.assertTrue(failed)
            self.assertIsNone(value)
            self.assertNotIn("helper", events)

        oracle()
        with patch.object(b, "old_negative"), self.assertRaises(AssertionError):
            oracle()
        oracle()

    def test_mode_semantics_and_order_neutral_records(self):
        value = worker_value()
        expected = life._private_worker_record(b.json_bytes(value))
        b.validate_mode(expected)
        self.assertEqual(life._private_worker_record(b.json_bytes(dict(reversed(list(value.items()))))), expected)
        for field, replacement in (
            ("mode", "old"), ("before", list(mount_value(14))), ("after", list(mount_value(10))),
            ("failure", [1, errno.EPERM]), ("calls", []), ("caps", [0, 1, 0, 0, 0, 1]),
            ("denied", [errno.EACCES, errno.EROFS, errno.EACCES]), ("null_io", False),
            ("fd_closed", False), ("local_nonzero_topology", False),
        ):
            with self.subTest(field=field), self.assertRaises(b.Refusal):
                b.validate_mode(life._private_worker_record(b.json_bytes({**value, field: replacement})))

    def test_neutral_local_renaming_preserves_credential_oracle(self):
        tree = ast.parse((self.harness / b.PROGRAM).read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "validate_credentials")

        class Rename(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id == "value":
                    node.id = "observed"
                return node

            def visit_arg(self, node):
                if node.arg == "value":
                    node.arg = "observed"
                return node

        changed = ast.fix_missing_locations(ast.Module(body=[Rename().visit(copy.deepcopy(function))], type_ignores=[]))
        scope = dict(vars(b))
        exec(compile(changed, "<pure-rename-control>", "exec"), scope)
        for function in (b.validate_credentials, scope["validate_credentials"]):
            function(self.state(), 1001, 1002, (0,) * 5)
            with self.assertRaises(b.Refusal):
                function({**self.state(), "uid": (0,) * 4}, 1001, 1002, (0,) * 5)

    def test_interface_gap_is_real_and_no_binding_or_mode_slot_is_overloaded(self):
        before = self.records()[0]
        life._decode_frame(life._encode_frame(before)[4:], "a" * 32, self.binding)
        for field in ("fixture_child", "backing_identity"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                life._decode_frame(life._encode_frame({**before, field: [3, 9, 0, 0]})[4:], "a" * 32, self.binding)
        replaced = copy.deepcopy(before)
        replaced["binding"][5] = 9
        with self.assertRaises(ValueError):
            life._decode_frame(life._encode_frame(replaced)[4:], "a" * 32, self.binding)

    def test_cleanup_preserves_unretained_children_and_replaced_parents(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=3, st_ino=4, st_uid=1001, st_gid=1002)
        with patch.object(b.os, "lstat", return_value=info), patch.object(b.os, "open", return_value=10), \
             patch.object(b.os, "fstat", return_value=info), patch.object(b.os, "close"), \
             patch.object(b.os, "rmdir") as remove:
            with patch.object(b.os, "listdir", return_value=["volume"]), self.assertRaisesRegex(b.Refusal, b.INTERFACE_HOLD):
                b.remove_empty_fixture(Path("/owned"), self.binding)
            remove.assert_not_called()
            with patch.object(b.os, "listdir", return_value=[]):
                b.remove_empty_fixture(Path("/owned"), self.binding)
            remove.assert_called_once_with(Path("/owned"))
            for name, wrong in (("st_ino", 9), ("st_uid", 0), ("st_gid", 0), ("st_mode", stat.S_IFLNK | 0o777)):
                with patch.object(info, name, wrong), self.assertRaises(b.Refusal):
                    b.remove_empty_fixture(Path("/owned"), self.binding)
            remove.assert_called_once()

    def test_interface_hold_refuses_without_acquiring_budget_or_attempting_launch(self):
        event, context = self.push()
        scope = {**b.identity(event, context), "preparation_hold": b.INTERFACE_HOLD}
        records = {}

        def read(path, *args):
            return b.json_bytes(event if str(path) == "/work/event" else scope)

        with patch.object(b, "paths", return_value=(Path("/work/harness"), Path("/work/candidate"), Path("/work/output"))), \
             patch.object(b, "read_file", side_effect=read), patch.object(b, "verify_checkout"), \
             patch.object(b, "verify_environment"), patch.object(b, "write_record", side_effect=lambda output, name, value: records.__setitem__(name, value)), \
             patch.object(b, "load_control", side_effect=AssertionError("no new owner under hold")):
            self.assertEqual(b.coordinate(context), 125)
        self.assertEqual(set(records), set(b.ARTIFACTS) - {"scope.json"})
        self.assertFalse(records["launch.json"]["attempted"])
        self.assertFalse(records["custody.json"]["qualified"])
        self.assertIsNone(records["mode.json"]["result"])
        self.assertFalse(records["cleanup.json"]["launch_owners_acquired"])
        with patch.dict(os.environ, {"ALLOW_BOOTSTRAP": "1"}), self.assertRaises(b.Refusal):
            b.execution_contract()


class Stream:
    def __init__(self, fd, closed, fault=None):
        self.fd, self.on_close, self.fault, self.closed = fd, closed, fault, False

    def fileno(self):
        return self.fd

    def close(self):
        self.on_close(self.fd)
        self.closed = True
        if self.fault is not None:
            raise self.fault


class Selector:
    def __init__(self):
        self.keys = {}

    def register(self, stream, events, data=None):
        fd = stream if type(stream) is int else stream.fileno()
        self.keys[fd] = selectors.SelectorKey(stream, fd, events, data)

    def unregister(self, stream):
        self.keys.pop(stream if type(stream) is int else stream.fileno())

    def get_map(self):
        return self.keys

    def select(self, timeout):
        if timeout == 0:
            return []
        return [(key, selectors.EVENT_READ) for key in tuple(self.keys.values())]

    def close(self):
        self.keys.clear()


class CapturedChild:
    def __init__(self):
        self.pid, self.returncode = 55, None


class Capture(Inert):
    def watchdog_case(self, status, close_error):
        child = CapturedChild()
        frames, events = [], []

        def wait(timeout=None):
            events.append(("wait", len(frames)))
            child.returncode = status
            return status

        child.wait = wait
        value = SimpleNamespace(si_pid=55, si_uid=0, si_signo=int(signal.SIGCHLD),
                                si_code=os.CLD_EXITED, si_status=status)

        def close(fd):
            events.append(("close", len(frames)))
            if close_error:
                raise OSError(errno.EIO, "inert completion close")

        argv = [
            "lifecycle.py", "130.0", "--outcome-v1", "a" * 32, "--",
            *budgeting.NAMESPACE_LAUNCHER, "/usr/bin/python3", "-I", "-S", "-B",
            str(self.harness / b.PROGRAM), "--reaper", "readonly",
            "1001", "1002", "130.0", "/work/issue180-null-bootstrap-1-42/fixture", "3", "4", "1001",
        ]
        with patch.object(life, "prctl"), patch.object(life, "require_pidfds"), \
             patch.object(life, "parent_death"), patch.object(life, "owned_children", return_value=[]), \
             patch.object(life, "ordinary_executable"), \
             patch.object(life.subprocess, "Popen", return_value=child), \
             patch.object(life.selectors, "DefaultSelector", side_effect=Selector), \
             patch.object(life.os, "pidfd_open", return_value=47), \
             patch.object(life.os, "waitid", return_value=value), patch.object(life.os, "killpg"), \
             patch.object(life.os, "close", side_effect=close), patch.object(life.os, "set_blocking"), \
             patch.object(life.os, "fstat", side_effect=lambda fd: SimpleNamespace(st_mode=stat.S_IFIFO | 0o600, st_dev=8, st_ino=fd + 100)), \
             patch.object(life.os, "fpathconf", return_value=4096), \
             patch.object(life.fcntl, "fcntl", return_value=os.O_WRONLY), \
             patch.object(life.os, "write", side_effect=lambda fd, data: frames.append(data) or len(data)), \
             patch.object(life.sys, "flags", SimpleNamespace(isolated=True, no_site=True)), \
             patch.object(life.sys, "argv", argv), patch.object(life.sys, "stderr", io.StringIO()):
            actual = life.main()
        self.assertEqual(child.returncode, status)
        self.assertTrue(all(count == 1 for _, count in events))
        return actual, [life._json_read(frame[4:]) for frame in frames]

    def run_capture_case(self, outer=0, inner=0, *, close_error=False, read_error=False, altered=None):
        owner = completed = snapshot = None
        budget = budgeting.ProbeBudget(budgeting.Limits(seconds=30))
        budget.started = 100.0
        events, offsets = [], [0, 0]
        records = self.records(inner) if altered is None else altered
        data = b"".join(life._encode_frame(record) for record in records)
        earlier, cleanup = ValueError("inert read"), OSError(errno.EIO, "inert close")
        child = CapturedChild()

        def close(fd):
            events.append((fd, owner._status, owner._streams is not None))

        child.stdin = Stream(12, close)
        child.stdout = Stream(10, close, cleanup if close_error else None)
        child.stderr = Stream(11, close)

        def wait(timeout=None):
            child.returncode = outer
            return outer

        def read(fd, count):
            if read_error and fd == 11:
                raise earlier
            index = fd - 10
            source = data if index == 0 else b"opaque-not-a-frame"
            chunk = source[offsets[index]:offsets[index] + count]
            offsets[index] += len(chunk)
            return chunk

        child.wait = wait
        raised = None
        with patch.object(budgeting.secrets, "token_hex", return_value="a" * 32), \
             patch.object(budgeting, "ordinary_executable"), patch.object(life, "ordinary_executable"), \
             patch.object(budgeting.subprocess, "Popen", return_value=child) as launch, \
             patch.object(budgeting.selectors, "DefaultSelector", side_effect=Selector), \
             patch.object(budgeting.os, "set_blocking"), patch.object(budgeting.os, "read", side_effect=read):
            try:
                owner = budget.reserve_outcome(output_limit=b.CAPTURE_BYTES)
                try:
                    completed = b.run_capture(budget, owner, self.binding, Path("/owned/fixture"), self.harness)
                except BaseException as error:
                    if error is not earlier and error is not cleanup:
                        raise
                    raised = "earlier" if error is earlier else "cleanup"
                    life._forget_error(error)
                budget.close(report=owner._cleanup)
                snapshot = owner.snapshot()
                semantic = b.summary(snapshot, life)
                accepted_capture = b.capture_complete(snapshot)
                actual_argv = launch.call_args.args[0]
                self.assertEqual(actual_argv[:7], [
                    "/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-S", "-B",
                ])
                delimiter = actual_argv.index("--", 7)
                self.assertEqual(tuple(actual_argv[delimiter + 1:delimiter + 1 + len(budgeting.NAMESPACE_LAUNCHER)]),
                                 budgeting.NAMESPACE_LAUNCHER)
                self.assertEqual(launch.call_args.kwargs["pass_fds"], ())
                self.assertNotIn("SUDO_UID", launch.call_args.kwargs["env"])
                self.assertEqual((budget.runs, budget.states, budget._outcome_entries), (1, 1, 52))
                self.assertEqual(budget.bytes["cache"], 552960)
                self.assertEqual(budget.deadline, 130.0)
                before_bytes = dict(budget.bytes)
                snapshot = completed = None
                owner.release()
                self.assertEqual(budget.bytes, before_bytes)
                with self.assertRaises(budgeting.MakeProbeError):
                    owner.snapshot()
                return raised, semantic, accepted_capture, events
            finally:
                snapshot = completed = data = records = None
                if owner is not None and not owner._released:
                    owner.release()

    def test_actual_c8_capture_uses_one_budget_original_deadline_and_no_FD_forwarding(self):
        raised, value, accepted, events = self.run_capture_case()
        self.assertIsNone(raised)
        self.assertEqual(value["outer_returncode"], 0)
        self.assertFalse(value["qualified"])
        self.assertTrue(accepted)  # necessary capture checks, not fixture acceptance
        self.assertTrue(all(status == 0 and frozen for _, status, frozen in events))
        with self.assertRaises(b.Refusal):
            b.execution_contract()

    def test_actual_L_normal_observation_survives_main125_then_actual_C_cleanup_failure(self):
        for inner, failed in ((0, False), (7, False), (0, True), (7, True)):
            with self.subTest(inner=inner, failed=failed):
                outer, records = self.watchdog_case(inner, failed)
                self.assertEqual(outer, 125 if failed else inner)
                self.assertEqual(records[0]["status"], inner)
                self.assertEqual(records[1]["disposition"], "raise" if failed else "return")
                raised, value, accepted, _ = self.run_capture_case(
                    outer, inner, close_error=failed, altered=[*self.records(inner)[:2], *records],
                )
                self.assertEqual(value["outer_returncode"], outer)
                self.assertEqual(value["observations"][2]["status"], inner)
                self.assertEqual(raised, "cleanup" if failed else None)
                self.assertEqual(accepted, inner == 0 and not failed)
                self.assertFalse(value["qualified"])

    def test_status_precedes_cleanup_outer125_and_prior_capture_errors_are_not_masked(self):
        for outer, inner, close_error, read_error in (
            (7, 7, True, False), (125, 7, True, False), (0, 0, True, False),
            (125, 7, True, True), (-9, -9, False, False),
        ):
            with self.subTest(outer=outer, inner=inner, read_error=read_error):
                raised, value, accepted, events = self.run_capture_case(
                    outer, inner, close_error=close_error, read_error=read_error,
                )
                self.assertFalse(accepted)
                self.assertFalse(value["qualified"])
                self.assertEqual(value["outer_returncode"], None if read_error else outer)
                if read_error:
                    self.assertEqual(raised, "earlier")
                    self.assertEqual(value["outer_stage"], "capture")
                else:
                    self.assertEqual(raised, "cleanup" if close_error else None)
                    self.assertTrue(all(status == outer and frozen for _, status, frozen in events))
                    self.assertEqual(value["observations"][0]["status"], inner)
                self.assertEqual({fd for fd, _, _ in events}, {10, 11, 12})

    def test_partial_duplicate_misbound_and_missing_custody_never_grants_acceptance(self):
        original = self.records()
        mutations = [original[:1], original[:3], original + [original[0]]]
        for role_field, replacement in (("binding", ["readonly", 1001, 1002, 130.0, 3, 9, 1001]), ("pid", True)):
            value = copy.deepcopy(original)
            value[0][role_field] = replacement
            mutations.append(value)
        wrong_token = copy.deepcopy(original)
        wrong_token[2]["token"] = "c" * 32
        mutations.append(wrong_token)
        for value in mutations:
            with self.subTest(records=len(value)):
                _, semantic, accepted, _ = self.run_capture_case(altered=value)
                self.assertFalse(accepted)
                self.assertFalse(semantic["custody_complete"])
        interleaved = [original[index] for index in (2, 0, 3, 1)]
        self.assertTrue(self.run_capture_case(altered=interleaved)[2])


if __name__ == "__main__":
    unittest.main()
