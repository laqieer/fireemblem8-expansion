"""Benign parsed/API controls. Never collect live facts or launch a namespace."""

import ast
from contextlib import ExitStack
import copy
import errno
import io
import json
from pathlib import Path
import re
import shlex
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

from scripts.ci_startup_census import census


ROOT = Path(__file__).resolve().parents[2]


def fake_facts():
    available = {"available": True, "value": "fixture"}
    status = {key: "0" for key in census.STATUS_KEYS}
    status.update(Uid="1001 1001 1001 1001", Gid="1001 1001 1001 1001", Groups="")
    return {
        "pid": 200, "parent_pid": 100,
        "real_effective_saved_uid": [1001] * 3, "real_effective_saved_gid": [1001] * 3,
        "groups": [], "status": status,
        "uid_map": {"available": True, "value": "1001 1001 1\n"},
        "gid_map": {"available": True, "value": "1001 1001 1\n"},
        "setgroups": available.copy(),
        "map_ownership": {name: {"available": True, "uid": 1001, "gid": 1001}
                          for name in ("uid_map", "gid_map")},
        "dumpable": {"available": True, "value": 1},
        "namespaces": {name: {"available": True, "inode": 100 + index}
                       for index, name in enumerate(("user", "mnt"))},
        "lsm_label": available.copy(),
        "kernel": {"sysname": "Linux", "release": "fixture", "version": "fixture", "machine": "x86_64"},
        "python": {"version": [3, 12, 0], "isolated": 1, "no_site": 1, "optimize": 0},
        "restrictions": {path: {"available": False, "errno": errno.ENOENT, "error": "FileNotFoundError"}
                         for path in census.RESTRICTIONS},
    }


class FakeLifecycle:
    TERMINATING = (signal.SIGTERM,)

    @staticmethod
    def owned_children():
        return []

    @staticmethod
    def finish_cleanup(actions, *, primary=None, handlers=None):
        errors = []
        for action in actions:
            try:
                action()
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                errors.append(error)
        if handlers is not None:
            handlers.clear()
        if errors:
            failure = primary if primary is not None else errors[0]
            failure.cleanup_errors = tuple(str(error) for error in errors)
            if primary is None:
                raise failure


def expression_value(source, context):
    node = ast.parse(source.replace("&&", " and ").replace("||", " or ").replace("false", "False"), mode="eval")

    def visit(value):
        if isinstance(value, ast.Expression):
            return visit(value.body)
        if isinstance(value, ast.Constant):
            return value.value
        if isinstance(value, ast.Name) and value.id == "github":
            return context
        if isinstance(value, ast.Attribute):
            return visit(value.value)[value.attr]
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.And):
            return all(visit(item) for item in value.values)
        if isinstance(value, ast.Compare) and len(value.ops) == 1 and isinstance(value.ops[0], ast.Eq):
            return visit(value.left) == visit(value.comparators[0])
        if isinstance(value, ast.Name) and value.id == "true":
            return True
        raise AssertionError("unexpected workflow expression node")

    return visit(node)


class CensusPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="null-census-benign-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.harness, self.candidate = self.root / "harness", self.root / "candidate"
        self.harness.mkdir()
        self.candidate.mkdir()
        (self.root / "temp").mkdir()
        self.context = {
            "GITHUB_SHA": "a" * 40, "GITHUB_WORKFLOW_SHA": "a" * 40,
            "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/" + census.BRANCH,
            "GITHUB_REPOSITORY": census.REPOSITORY, "GITHUB_ACTOR": census.OWNER,
            "GITHUB_TRIGGERING_ACTOR": census.OWNER, "GITHUB_RUN_NUMBER": "1",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "100",
            "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
            "GITHUB_WORKFLOW_REF": census.REPOSITORY + "/" + census.WORKFLOW + "@refs/heads/" + census.BRANCH,
            "GITHUB_WORKSPACE": str(self.root), "RUNNER_TEMP": str(self.root / "temp"),
        }
        self.event = {
            "ref": self.context["GITHUB_REF"], "before": "0" * 40, "after": "a" * 40,
            "created": True, "deleted": False,
            "repository": {"full_name": census.REPOSITORY, "private": False},
            "sender": {"login": census.OWNER},
        }
        self.scope = census.identity(self.event, self.context)
        self.output = self.root / "temp/issue180-null-startup-census-1-100"

    def prepare(self):
        with patch.object(census, "verify_checkout"):
            census.plan(self.harness, self.candidate, self.output, self.event, self.context)

    def run_seams(self, stack, launcher):
        stack.enter_context(patch.object(census, "verify_checkout"))
        stack.enter_context(patch.object(census, "source_environment", return_value={"PATH": "/usr/bin:/bin"}))
        stack.enter_context(patch.object(census, "load_lifecycle", return_value=FakeLifecycle))
        facts = stack.enter_context(patch.object(census, "collect_facts", return_value=fake_facts()))
        stack.enter_context(patch.object(census, "bounded_command", return_value=subprocess.CompletedProcess(
            ["/usr/bin/unshare", "--version"], 0, b"unshare fixture\n", b"",
        )))
        stack.enter_context(patch.object(census.os, "getuid", return_value=1001))
        stack.enter_context(patch.object(census.os, "geteuid", return_value=1001))
        stack.enter_context(patch.object(census.os, "getgid", return_value=1001))
        stack.enter_context(patch.object(census.os, "listdir", return_value=["0", "1", "2", "3"]))
        stack.enter_context(patch.object(census.signal, "signal", return_value=signal.SIG_DFL))
        launch = stack.enter_context(patch.object(census, "launch_once", side_effect=launcher))
        stack.enter_context(patch.object(census.subprocess, "Popen", side_effect=AssertionError("real launch forbidden")))
        return facts, launch

    def child_records(self):
        return (
            census.encoded({"kind": "first-outer-entry", "identity": self.scope})
            + census.encoded({"kind": "outer-census", "identity": self.scope,
                              "facts": fake_facts(), "stopped_before_fixture_setup": True})
        )

    def test_first_owner_created_public_event_is_required(self):
        self.assertTrue(self.scope["never_merge"])
        self.assertFalse(self.scope["production_acceptance"])
        for key, value in (
            ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_REF", "refs/heads/master"),
            ("GITHUB_ACTOR", "someone-else"), ("GITHUB_TRIGGERING_ACTOR", "someone-else"),
            ("GITHUB_RUN_NUMBER", "2"), ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_REPOSITORY", "other/repo"), ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("RUNNER_OS", "Windows"), ("GITHUB_WORKFLOW_REF", "other"),
            ("GITHUB_WORKFLOW_SHA", "b" * 40), ("GITHUB_SHA", "A" * 40), ("GITHUB_RUN_ID", "../1"),
        ):
            with self.subTest(key=key), self.assertRaises(census.CensusError):
                census.identity(self.event, {**self.context, key: value})
        for path, value in (
            (("created",), False), (("created",), 1), (("deleted",), True),
            (("before",), "b" * 40), (("after",), "b" * 40), (("ref",), "refs/heads/master"),
            (("repository", "private"), True), (("repository", "private"), 0),
            (("sender", "login"), "someone-else"),
        ):
            event = copy.deepcopy(self.event)
            parent = event if len(path) == 1 else event[path[0]]
            parent[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(census.CensusError):
                census.identity(event, self.context)
        with self.assertRaises(census.CensusError):
            census.identity([], self.context)

    def test_real_workflow_event_gate_and_ordinary_step_are_closed(self):
        document = yaml.safe_load((ROOT / census.WORKFLOW).read_text())
        self.assertEqual(document.get("on", document.get(True)), {"push": {"branches": [census.BRANCH]}})
        self.assertEqual(document["permissions"], {"contents": "read"})
        self.assertEqual(document["concurrency"]["cancel-in-progress"], False)
        job, = document["jobs"].values()
        github = {
            "repository": census.REPOSITORY, "actor": census.OWNER, "triggering_actor": census.OWNER,
            "event": {"repository": {"private": False}, "sender": {"login": census.OWNER},
                      "created": True, "deleted": False},
            "run_number": 1, "run_attempt": 1,
        }
        self.assertTrue(expression_value(job["if"], github))
        for path, value in (
            (("actor",), "other"), (("triggering_actor",), "other"), (("repository",), "other/repo"),
            (("run_number",), 2), (("run_attempt",), 2), (("event", "created"), False),
            (("event", "deleted"), True), (("event", "repository", "private"), True),
            (("event", "sender", "login"), "other"),
        ):
            changed = copy.deepcopy(github)
            parent = changed
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value
            self.assertFalse(expression_value(job["if"], changed), path)
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["timeout-minutes"], 60)
        checkouts = [step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual([step["with"]["ref"] for step in checkouts], ["${{ github.sha }}", census.CANDIDATE])
        self.assertEqual([step["with"]["path"] for step in checkouts], ["harness", "candidate"])
        self.assertTrue(all(step["with"]["persist-credentials"] is False for step in checkouts))
        attempt = next(step for step in job["steps"]
                       if "run" in step and "--signal=KILL" in shlex.split(step["run"]))
        self.assertEqual(attempt["working-directory"], "candidate")
        self.assertIn("/usr/bin/timeout --signal=KILL 45s", attempt["run"])
        self.assertNotIn("sudo", attempt["run"])
        self.assertNotIn("continue-on-error", attempt)
        upload = next(step for step in job["steps"]
                      if step.get("uses", "").startswith("actions/upload-artifact@"))
        uploads = upload["with"]["path"].splitlines()
        self.assertEqual([Path(path).name for path in uploads],
                         ["scope.json", "attempt.json", "prelaunch.json", "launch.json", "stdout", "stderr", "result.json"])
        self.assertIsNone(re.search(r"\$\{\{[^}]*\bsecrets[.\[]", json.dumps(document)))

    def test_declared_native_setup_environment_and_outer_argv_match_selected_source(self):
        def source(path):
            return subprocess.check_output(
                ["/usr/bin/git", "-C", str(ROOT), "show", census.CANDIDATE + ":" + path], timeout=5,
            )
        selected = yaml.safe_load(source(".github/workflows/build.yml"))["jobs"]["extended-host-tests"]
        diagnostic = yaml.safe_load((ROOT / census.WORKFLOW).read_text())["jobs"]["startup-census"]
        self.assertEqual(selected["runs-on"], diagnostic["runs-on"])
        self.assertEqual(selected["timeout-minutes"], diagnostic["timeout-minutes"])
        def installation(job):
            commands = [shlex.split(step["run"]) for step in job["steps"] if "run" in step]
            return next(command for command in commands if command[:3] == ["sudo", "apt-get", "update"])
        self.assertEqual(installation(selected), installation(diagnostic))
        authority = self.candidate / "scripts/validation_ownership/authority.py"
        authority.parent.mkdir(parents=True)
        authority.write_bytes(source("scripts/validation_ownership/authority.py"))
        environment = census.source_environment(self.candidate)
        original = ast.parse(authority.read_text())
        value, = [ast.literal_eval(node.value) for node in original.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "ENVIRONMENT" for target in node.targets)]
        self.assertEqual(environment, value)
        tree = ast.parse(source("scripts/validation_ownership/tests/test_foundation.py"))
        helper = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "null_mount_kernel_control")
        launch = next(node for node in ast.walk(helper) if isinstance(node, ast.Call)
                      and ast.unparse(node.func) == "subprocess.Popen")
        elements = launch.args[0].elts
        start = next(index for index, node in enumerate(elements)
                     if isinstance(node, ast.Constant) and node.value == "/usr/bin/unshare")
        original_prefix = tuple(ast.literal_eval(node) for node in elements[start:start + len(census.NS_ARGUMENTS)])
        self.assertEqual(census.NS_ARGUMENTS, original_prefix)
        argv = census.fixed_argv(self.candidate, Path("/owned/null-mount-unsupported.py"),
                                 Path("/owned/null-mount-unsupported"), 1001, 1001, 123.5)
        self.assertEqual(argv[7:7 + len(original_prefix)], list(original_prefix))
        self.assertEqual(argv[-5:], [census.MODE, "outer", "/owned/null-mount-unsupported", "1001", "1001"])
        self.assertEqual(argv[0:4], ["/usr/bin/python3", "-I", "-S", "-B"])
        for uid, gid in ((0, 1001), (1001, 0), (True, 1001)):
            with self.assertRaises(census.CensusError):
                census.fixed_argv(self.candidate, Path("/p"), Path("/f"), uid, gid, 1)

    def test_plan_is_exclusive_and_does_not_collect_or_launch(self):
        with patch.object(census, "collect_facts", side_effect=AssertionError), patch.object(
            census, "launch_once", side_effect=AssertionError,
        ):
            self.prepare()
            with self.assertRaises(FileExistsError):
                self.prepare()
        planned = json.loads((self.output / "scope.json").read_text())
        self.assertEqual(planned["max_outer_attempts"], 1)
        self.assertEqual(planned["context_differences"], list(census.CONTEXT_DIFFERENCES))
        with self.assertRaises(census.CensusError):
            census.paths(self.candidate, self.harness, self.output, self.context)
        with self.assertRaises(census.CensusError):
            census.paths(self.harness, self.candidate, self.output / "child", self.context)

    def test_display_names_and_mapping_order_do_not_become_behavior_evidence(self):
        document = yaml.safe_load((ROOT / census.WORKFLOW).read_text())
        for index, step in enumerate(document["jobs"]["startup-census"]["steps"]):
            step["name"] = "Equivalent display label " + str(index)
        with patch.object(yaml, "safe_load", return_value=document):
            self.test_real_workflow_event_gate_and_ordinary_step_are_closed()
        self.assertEqual(
            census.identity(dict(reversed(list(self.event.items()))), dict(reversed(list(self.context.items())))),
            self.scope,
        )

    def test_source_binding_rejects_dirty_wrong_and_unrelated_trees(self):
        clean_diff = b"".join(b"A\0" + path.encode() + b"\0" for path in sorted(census.HARNESS_PATHS))
        for responses in (
            [b"b" * 40 + b"\n"],
            [b"a" * 40 + b"\n", b" M existing\n"],
            [b"a" * 40 + b"\n", b"", b"", b"M\0.github/workflows/build.yml\0"],
            [b"a" * 40 + b"\n", b"", b"", clean_diff + b"A\0unexpected\0"],
        ):
            with patch.object(census, "git", side_effect=responses), self.assertRaises(census.CensusError):
                census.verify_checkout(self.harness, "a" * 40, harness=True)
        with patch.object(census, "git", side_effect=[b"a" * 40 + b"\n", b"", b"", clean_diff]):
            census.verify_checkout(self.harness, "a" * 40, harness=True)

    def test_closed_fact_census_omits_unrelated_status_and_marks_unavailable(self):
        status = "".join(key + ":\t" + value + "\n" for key, value in fake_facts()["status"].items())
        status += "Secret:\tdo-not-publish\nName:\tprivate-name\n"
        with ExitStack() as stack:
            reader = stack.enter_context(patch.object(census, "read_bounded", return_value=status.encode()))
            stack.enter_context(patch.object(census, "text_fact", return_value={"available": False, "errno": 13}))
            stack.enter_context(patch.object(census, "stat_fact", return_value={"available": False, "errno": 13}))
            stack.enter_context(patch.object(census, "namespace_fact", return_value={"available": False, "errno": 1}))
            stack.enter_context(patch.object(census, "dumpable_fact", return_value={"available": True, "value": 1}))
            stack.enter_context(patch.object(census.os, "getresuid", return_value=(1001, 1001, 1001)))
            stack.enter_context(patch.object(census.os, "getresgid", return_value=(1001, 1001, 1001)))
            stack.enter_context(patch.object(census.os, "getgroups", return_value=[]))
            stack.enter_context(patch.object(census.os, "getpid", return_value=200))
            stack.enter_context(patch.object(census.os, "getppid", return_value=100))
            stack.enter_context(patch.object(census.os, "uname", return_value=SimpleNamespace(
                sysname="Linux", release="fixture", version="fixture", machine="x86_64", nodename="private-host",
            )))
            facts = census.collect_facts()
        self.assertEqual(reader.call_args.args, ("/proc/self/status", 64 * 1024))
        self.assertNotIn("private", census.encoded(facts).decode())
        self.assertNotIn("Secret", facts["status"])
        self.assertFalse(facts["uid_map"]["available"])
        self.assertEqual(facts["namespaces"]["user"]["errno"], 1)
        for changed in ({**facts, "environment": {}}, {**facts, "real_effective_saved_uid": [True] * 3}):
            with self.assertRaises(census.CensusError):
                census.validate_facts(changed)

    def test_bounded_records_and_optional_reads_never_truncate_into_facts(self):
        path = self.root / "record"
        path.write_bytes(b"x" * 8)
        self.assertEqual(census.read_bounded(path, 8), b"x" * 8)
        with self.assertRaises(census.CensusError):
            census.read_bounded(path, 7)
        self.assertFalse(census.text_fact(path, 7)["available"])
        absent = census.text_fact(self.root / "absent")
        self.assertFalse(absent["available"])
        self.assertEqual(absent["errno"], errno.ENOENT)
        with self.assertRaises(census.CensusError):
            census.write_record(self.root / "oversize", {"value": "x" * census.RECORD_BYTES})
        self.assertFalse((self.root / "oversize").exists())
        with self.assertRaises(census.CensusError):
            census.parse_json(b'{"a":1,"a":2}')
        with self.assertRaises(census.CensusError):
            census.parse_json(b'{"a":NaN}')

    def test_fixed_command_uses_bounded_files_and_original_size_limit(self):
        def invoke(argv, **kwargs):
            self.assertEqual(argv, ["/usr/bin/unshare", "--version"])
            self.assertEqual(kwargs["timeout"], 2)
            self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin"})
            self.assertIs(kwargs["preexec_fn"], census.child_limits)
            kwargs["stdout"].write(b"fixture version\n")
            kwargs["stderr"].write(b"")
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(census.subprocess, "run", side_effect=invoke) as run:
            value = census.bounded_command(["/usr/bin/unshare", "--version"], {"PATH": "/usr/bin:/bin"}, 2)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(value.stdout, b"fixture version\n")
        self.assertEqual(value.stderr, b"")

    def test_namespace_owner_descriptors_close_on_success_and_denial(self):
        for denied in (False, True):
            def ioctl(descriptor, request, *args):
                self.assertEqual(descriptor, 41)
                self.assertEqual(request, 0xB701)
                if denied:
                    raise PermissionError(errno.EPERM, "fixture denial")
                return 42
            with patch.object(census.os, "open", return_value=41), patch.object(
                census.os, "fstat", side_effect=lambda fd: SimpleNamespace(st_dev=1, st_ino=fd),
            ), patch.object(census.fcntl, "ioctl", side_effect=ioctl), patch.object(census.os, "close") as close:
                value = census.namespace_fact("mnt")
            self.assertEqual([call.args[0] for call in close.call_args_list], [41] if denied else [42, 41])
            self.assertEqual(value["owner_userns"]["available"], not denied)

    def test_dumpability_uses_only_the_read_api(self):
        library = SimpleNamespace(prctl=Mock(return_value=1))
        with patch.object(census.ctypes, "CDLL", return_value=library):
            self.assertEqual(census.dumpable_fact(), {"available": True, "value": 1})
        library.prctl.assert_called_once_with(3, 0, 0, 0, 0)

    def test_outer_payload_stops_after_two_census_records_without_workload_calls(self):
        parent = self.root / "owned"
        parent.mkdir()
        program = parent / "null-mount-unsupported.py"
        program.touch()
        fixture = parent / "null-mount-unsupported"
        fixture.mkdir()
        census.write_record(fixture / "authority.json", {
            "identity": self.scope, "candidate": str(self.candidate), "uid": 1001, "gid": 1001,
            "deadline": 200.0,
        })
        argv = [str(program), str(self.candidate / "scripts/validation_ownership"),
                census.MODE, "outer", str(fixture), "1001", "1001"]
        output = io.BytesIO()
        with patch.object(census, "collect_facts", return_value=fake_facts()) as collect, patch.object(
            census.sys, "stdout", SimpleNamespace(buffer=output),
        ), patch.object(census.time, "monotonic", return_value=100.0), patch.object(
            census.subprocess, "Popen", side_effect=AssertionError("nested launch forbidden"),
        ), patch.object(census.ctypes, "CDLL", side_effect=AssertionError("mount/drop API forbidden")):
            self.assertEqual(census.outer_entry(argv), 0)
            collect.assert_called_once_with()
            for index, replacement in ((2, "readonly"), (3, "inner"), (5, "0")):
                changed = list(argv)
                changed[index] = replacement
                with self.assertRaises(census.CensusError):
                    census.outer_entry(changed)
            self.assertEqual(collect.call_count, 1)
        marker, post = census.parse_outer(output.getvalue(), self.scope, 0)
        self.assertTrue(marker)
        self.assertEqual(post["value"], fake_facts())
        self.assertEqual(len(output.getvalue().splitlines()), 2)

    def test_post_mapping_facts_cannot_be_synthesized_from_success_or_stale_output(self):
        marker, post = census.parse_outer(b"", self.scope, 1)
        self.assertFalse(marker)
        self.assertFalse(post["available"])
        for value in (
            b"", census.encoded({"kind": "first-outer-entry", "identity": self.scope}),
            self.child_records() + self.child_records(),
            self.child_records().replace(b'"run_id": "100"', b'"run_id": "101"'),
        ):
            with self.assertRaises(census.CensusError):
                census.parse_outer(value, self.scope, 0)
        rows = [json.loads(line) for line in self.child_records().splitlines()]
        rows[1]["facts"] = {}
        with self.assertRaises(census.CensusError):
            census.parse_outer(b"".join(census.encoded(row) for row in rows), self.scope, 0)

    def test_failed_mapping_is_preserved_once_without_child_facts_or_retry(self):
        self.prepare()
        def denied(argv, environment, candidate, output, lifecycle, state):
            self.assertEqual(tuple(argv[7:16]), census.NS_ARGUMENTS)
            (output / "stdout").write_bytes(b"")
            (output / "stderr").write_bytes(b"unshare: fixture uid_map denial\n")
            state.update(watchdog_launched=True, watchdog_reaped=True, pidfd_closed=True, lifetime_closed=True)
            return 1
        with ExitStack() as stack:
            facts, launch = self.run_seams(stack, denied)
            self.assertEqual(census.run(self.harness, self.candidate, self.output, self.event, self.context), 1)
            with self.assertRaises(FileExistsError):
                census.run(self.harness, self.candidate, self.output, self.event, self.context)
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(facts.call_count, 1)
        value = json.loads((self.output / "result.json").read_text())
        self.assertEqual(value["first_error"]["type"], "outer-startup-exit")
        launch_record = json.loads((self.output / "launch.json").read_text())
        self.assertEqual(launch_record["argv"], value["argv"])
        self.assertFalse(value["post_unshare_census"]["available"])
        self.assertTrue(value["cleanup"]["cleanup_confirmed"])
        self.assertFalse((self.output / "owned-fixture").exists())

    def test_cold_success_is_census_only_not_historical_repair(self):
        self.prepare()
        def accepted(argv, environment, candidate, output, lifecycle, state):
            (output / "stdout").write_bytes(self.child_records())
            (output / "stderr").write_bytes(b"")
            return 0
        with ExitStack() as stack:
            _, launch = self.run_seams(stack, accepted)
            self.assertEqual(census.run(self.harness, self.candidate, self.output, self.event, self.context), 0)
            launch.assert_called_once()
        value = json.loads((self.output / "result.json").read_text())
        self.assertTrue(value["outer_entry_reached"])
        self.assertFalse(value["historical_failure_fixed"])
        self.assertFalse(value["production_acceptance"])
        self.assertEqual(value["context_differences"], list(census.CONTEXT_DIFFERENCES))

    def test_first_mapping_error_survives_secondary_output_and_cleanup_failures(self):
        self.prepare()
        def denied(argv, environment, candidate, output, lifecycle, state):
            (output / "stdout").write_bytes(b"not JSON\n")
            (output / "stderr").write_bytes(b"original uid_map denial\n")
            return 1
        with ExitStack() as stack:
            self.run_seams(stack, denied)
            stack.enter_context(patch.object(census.shutil, "rmtree", side_effect=PermissionError("retained fixture")))
            self.assertEqual(census.run(self.harness, self.candidate, self.output, self.event, self.context), 1)
        value = json.loads((self.output / "result.json").read_text())
        self.assertEqual(value["first_error"]["type"], "outer-startup-exit")
        self.assertIn("secondary_error", value)
        self.assertFalse(value["cleanup"]["cleanup_confirmed"])
        self.assertTrue((self.output / "owned-fixture").exists())
        self.assertEqual((self.output / "stderr").read_bytes(), b"original uid_map denial\n")

    def test_preexisting_fixture_is_not_owned_or_deleted_after_setup_refusal(self):
        self.prepare()
        foreign = self.output / "owned-fixture"
        foreign.mkdir()
        marker = foreign / "not-created-by-this-attempt"
        marker.write_bytes(b"preserve the existing object\n")
        with ExitStack() as stack:
            _, launch = self.run_seams(stack, lambda *args: self.fail("namespace launch forbidden"))
            self.assertEqual(census.run(self.harness, self.candidate, self.output, self.event, self.context), 1)
            launch.assert_not_called()
        value = json.loads((self.output / "result.json").read_text())
        self.assertEqual(value["first_error"]["type"], "FileExistsError")
        self.assertTrue(marker.is_file(), "setup refusal deleted an unowned fixture")
        self.assertEqual(marker.read_bytes(), b"preserve the existing object\n")
        self.assertFalse(value["cleanup"]["cleanup_confirmed"])

    def test_changed_source_refuses_before_any_census_or_launch(self):
        self.prepare()
        with patch.object(census, "verify_checkout", side_effect=census.CensusError("changed source")), patch.object(
            census, "collect_facts", side_effect=AssertionError("census forbidden"),
        ), patch.object(census, "launch_once", side_effect=AssertionError("launch forbidden")):
            self.assertEqual(census.run(self.harness, self.candidate, self.output, self.event, self.context), 1)
        self.assertTrue((self.output / "attempt.json").exists())
        self.assertFalse(json.loads((self.output / "result.json").read_text())["cleanup"]["source_unchanged"])

    def test_actual_launch_api_keeps_exact_argv_and_closes_owned_handles(self):
        self.output.mkdir()
        argv = census.fixed_argv(self.candidate, Path("/owned/null-mount-unsupported.py"),
                                 Path("/owned/null-mount-unsupported"), 1001, 1001, 500.0)
        child = SimpleNamespace(pid=12345, returncode=None)
        def wait(timeout):
            self.assertEqual(timeout, census.WAIT_SECONDS)
            child.returncode = 1
        child.wait = Mock(side_effect=wait)
        child.poll = Mock(side_effect=lambda: child.returncode)
        state = {"watchdog_launched": False, "watchdog_reaped": True, "pidfd_closed": True, "lifetime_closed": True}
        with patch.object(census.os, "pipe2", return_value=(41, 42)), patch.object(
            census.os, "pidfd_open", return_value=43,
        ), patch.object(census.os, "close") as close, patch.object(
            census.subprocess, "Popen", return_value=child,
        ) as launch, patch.object(census.signal, "pthread_sigmask", return_value={signal.SIGUSR1}) as mask:
            result = census.launch_once(argv, {"PATH": "/usr/bin:/bin"}, self.candidate,
                                         self.output, FakeLifecycle, state)
            with patch.object(census, "child_limits") as limit:
                launch.call_args.kwargs["preexec_fn"]()
            limit.assert_called_once_with()
            self.assertEqual(mask.call_args.args, (signal.SIG_SETMASK, {signal.SIGUSR1}))
        self.assertEqual(result, 1)
        self.assertEqual(launch.call_args.args[0], argv)
        self.assertEqual(launch.call_args.kwargs["stdin"], 41)
        self.assertTrue(launch.call_args.kwargs["close_fds"])
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.assertEqual([call.args[0] for call in close.call_args_list], [41, 42, 43])
        self.assertTrue(all(state[name] for name in ("watchdog_reaped", "pidfd_closed", "lifetime_closed")))

    def test_launch_failure_still_closes_lifetime_without_signaling_unowned_pid(self):
        self.output.mkdir()
        state = {"watchdog_launched": False, "watchdog_reaped": True, "pidfd_closed": True, "lifetime_closed": True}
        with patch.object(census.os, "pipe2", return_value=(41, 42)), patch.object(
            census.os, "close",
        ) as close, patch.object(census.subprocess, "Popen", side_effect=OSError("launch failed")), patch.object(
            census.signal, "pidfd_send_signal", side_effect=AssertionError("no owned process"),
        ), patch.object(census.signal, "pthread_sigmask", return_value=set()):
            with self.assertRaisesRegex(OSError, "launch failed"):
                census.launch_once(["fixed"], {}, self.candidate, self.output, FakeLifecycle, state)
        self.assertEqual([call.args[0] for call in close.call_args_list], [42, 41])
        self.assertFalse(state["watchdog_launched"])
        self.assertTrue(state["lifetime_closed"])

    def test_timeout_closes_lifetime_then_signals_only_owned_pidfd_and_reaps(self):
        self.output.mkdir()
        child = SimpleNamespace(pid=12345, returncode=None)
        waits = []
        events = []
        def wait(timeout):
            waits.append(timeout)
            if len(waits) < 3:
                raise subprocess.TimeoutExpired("fixed fixture", timeout)
            child.returncode = 1
        child.wait, child.poll = Mock(side_effect=wait), Mock(return_value=None)
        state = {"watchdog_launched": False, "watchdog_reaped": True, "pidfd_closed": True, "lifetime_closed": True}
        with patch.object(census.os, "pipe2", return_value=(41, 42)), patch.object(
            census.os, "pidfd_open", return_value=43,
        ), patch.object(census.os, "close", side_effect=lambda fd: events.append(("close", fd))), patch.object(
            census.subprocess, "Popen", return_value=child,
        ), patch.object(census.signal, "pthread_sigmask", return_value=set()), patch.object(
            census.signal, "pidfd_send_signal", side_effect=lambda *args: events.append(("signal", args)),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                census.launch_once(["fixed"], {}, self.candidate, self.output, FakeLifecycle, state)
        self.assertEqual(waits, [35, 5, 5])
        self.assertEqual(events, [
            ("close", 41), ("close", 42), ("signal", (43, signal.SIGTERM)), ("close", 43),
        ])
        self.assertTrue(all(state[name] for name in ("watchdog_reaped", "pidfd_closed", "lifetime_closed")))

    def test_uncertain_cleanup_never_becomes_success_when_returncode_is_missing(self):
        self.prepare()
        def timeout(argv, environment, candidate, output, lifecycle, state):
            state.update(watchdog_launched=True, watchdog_reaped=False, lifetime_closed=False)
            raise subprocess.TimeoutExpired("fixed", 35)
        with ExitStack() as stack:
            _, launch = self.run_seams(stack, timeout)
            self.assertEqual(census.run(self.harness, self.candidate, self.output, self.event, self.context), 1)
            self.assertEqual(launch.call_count, 1)
        value = json.loads((self.output / "result.json").read_text())
        self.assertIsNone(value["outer_returncode"])
        self.assertEqual(value["first_error"]["type"], "TimeoutExpired")
        self.assertFalse(value["cleanup"]["cleanup_confirmed"])

    def test_output_limits_are_the_original_finite_file_limits(self):
        with patch.object(census.resource, "setrlimit") as limit:
            census.child_limits()
        limit.assert_called_once_with(census.resource.RLIMIT_FSIZE, (256 * 1024, 256 * 1024))
        self.assertEqual((census.WATCHDOG_SECONDS, census.WAIT_SECONDS, census.OUTER_SECONDS), (30, 35, 45))
        self.assertEqual(census.FIXTURE_BYTES, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
