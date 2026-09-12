"""Focused local controls; never run the candidate graph or privileged setup."""

import ast
import builtins
import copy
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import yaml

from scripts.ci_calibration import kernel, policy, supervisor, volume_mount, worker


REPO = Path(__file__).resolve().parents[2]


class CalibrationControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ci180-benign-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def event(self):
        return {
            "ref": "refs/heads/" + policy.BRANCH, "before": "0" * 40, "after": "a" * 40,
            "created": True, "deleted": False,
            "repository": {"full_name": policy.REPOSITORY, "private": False},
            "sender": {"login": "laqieer"},
        }

    def authorization(self, **changes):
        return {
            "sha": "a" * 40, "run_id": "12345", "attempt": "1", "run_number": "1",
            "environment": "github-hosted", "operating_system": "Linux", "event_name": "push",
            **changes,
        }

    def facts(self):
        return {
            "memory_total": 16 * policy.GIB, "memory_available": 12 * policy.GIB,
            "disk_available": 24 * policy.GIB, "cpus": 4,
            "threads_max": 100000, "threads_current": 500,
            "cgroup_ancestors": [{
                "path": "/", "memory_max": None, "memory_current": 0,
                "pids_max": None, "pids_current": 0,
            }],
        }

    def test_only_first_owner_branch_creation_can_authorize_the_experiment(self):
        result = policy.validate_event(self.event(), **self.authorization())
        self.assertTrue(result["diagnostic_only"])
        self.assertFalse(result["production_acceptance"])
        for change in (
            {"attempt": "2"}, {"run_number": "2"}, {"environment": "self-hosted"},
            {"operating_system": "Windows"}, {"event_name": "workflow_dispatch"},
            {"run_id": "../unsafe"}, {"sha": "B" * 40},
        ):
            with self.subTest(change=change), self.assertRaises(policy.GuardError):
                policy.validate_event(self.event(), **self.authorization(**change))
        for path, value in (
            (("created",), False), (("deleted",), True), (("before",), "b" * 40),
            (("ref",), "refs/heads/master"), (("after",), "b" * 40),
            (("repository", "private"), True), (("sender", "login"), "someone-else"),
        ):
            event = self.event()
            owner = event
            for key in path[:-1]:
                owner = owner[key]
            owner[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(policy.GuardError):
                policy.validate_event(event, **self.authorization())

    def test_envelope_uses_effective_capacity_and_reserved_headroom(self):
        envelope = policy.choose_envelope(self.facts())
        self.assertEqual(envelope["memory_max"], 8 * policy.GIB)
        self.assertEqual(envelope["disk_bytes"], 8 * policy.GIB)
        self.assertEqual(envelope["pids_max"], 128)
        self.assertEqual(envelope["memory_swap_max"], 0)
        limited = self.facts()
        limited["cgroup_ancestors"].append({
            "path": "/runner", "memory_max": 8 * policy.GIB,
            "memory_current": 9 * policy.GIB // 2, "pids_max": 220, "pids_current": 80,
        })
        envelope = policy.choose_envelope(limited)
        self.assertEqual(envelope["memory_max"], 3 * policy.GIB // 2)
        self.assertEqual(envelope["pids_max"], 76)
        for key, value in (
            ("memory_available", 2 * policy.GIB), ("disk_available", 4 * policy.GIB),
            ("cpus", 1), ("threads_max", 600), ("cpus", True),
        ):
            facts = self.facts()
            facts[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(policy.GuardError):
                policy.choose_envelope(facts)

    def test_every_pinned_limit_is_classified_without_importing_candidate(self):
        raw = subprocess.run(
            ["/usr/bin/git", "-C", str(REPO), "show",
             policy.GRAPH + ":scripts/validation_ownership/budget.py"],
            capture_output=True, check=True, timeout=10,
        ).stdout
        tree = ast.parse(raw)

        def value(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name) and node.id == "MAX_PROBE_SECONDS":
                return 3600
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                return value(node.left) * value(node.right)
            self.fail("unexpected limit default expression")

        definition, = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Limits"]
        defaults = {
            node.target.id: value(node.value)
            for node in definition.body if isinstance(node, ast.AnnAssign)
        }
        manifest = policy.profile_manifest(defaults)
        self.assertEqual(set(manifest), set(defaults))
        for name in policy.RETAINED:
            self.assertEqual(manifest[name]["diagnostic"], defaults[name])
        for name in policy.RELAXED:
            self.assertEqual(manifest[name]["diagnostic"], (1 << 63) - 1)
        self.assertIn("total_bytes", policy.RELAXED)
        self.assertIn("file_bytes", policy.RETAINED)
        self.assertIn("created_files", policy.RETAINED)
        with self.assertRaises(policy.GuardError):
            policy.profile_manifest({**defaults, "unknown_limit": 1})

    def test_direct_graph_call_rejects_before_any_candidate_import(self):
        original = builtins.__import__
        imported = []

        def observe(name, *args, **kwargs):
            if name.startswith("scripts.validation_ownership"):
                imported.append(name)
                raise AssertionError("candidate import occurred before containment")
            return original(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=observe):
            with self.assertRaises(policy.GuardError):
                worker.graph({"guarded": True})
        self.assertEqual(imported, [])

    def test_profile_construction_is_immutable_and_preserves_one_clock_and_inherited_methods(self):
        limits_type = dataclasses.make_dataclass(
            "LocalLimitShape",
            [(key, int, dataclasses.field(default=value)) for key, value in policy.ORIGINAL_LIMITS.items()],
            frozen=True,
        )

        @dataclasses.dataclass
        class BudgetShape:
            limits: object
            started: float = dataclasses.field(default_factory=time.monotonic, init=False)
            calls: int = dataclasses.field(default=0, init=False)

            @property
            def deadline(self):
                return self.started + self.limits.seconds

            def charge(self, _category, _size):
                self.calls += 1

        deadline = time.monotonic() + 3600
        budget, limits, original, manifest = worker.calibration_budget(limits_type, BudgetShape, deadline)
        self.assertEqual(budget.deadline, deadline)
        self.assertIs(budget.charge.__func__, BudgetShape.charge)
        budget.charge("control", 1)
        self.assertEqual(budget.calls, 1)
        self.assertEqual(dataclasses.asdict(limits_type()), original)
        self.assertEqual(limits.total_bytes, policy.POLICY_SENTINEL)
        self.assertEqual(limits.file_bytes, 16 * policy.MIB)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            limits.pending_bytes = 1
        self.assertEqual(set(manifest), set(original))

    def test_real_unguarded_worker_entry_fails_without_candidate_imports(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"scope": "local-negative", "mode": "graph"}))
        path.chmod(0o444)
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-B", str(worker.__file__), str(path)],
            capture_output=True, timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(len(result.stdout) + len(result.stderr), 65536)
        self.assertNotIn(b"graph-start", result.stdout)
        self.assertNotIn(b"completed-diagnostic", result.stdout)

    def test_protocol_requires_real_order_scope_and_a_complete_bounded_record(self):
        parser = supervisor.Protocol("scope", 4096)
        ready = policy.encoded({"scope": "scope", "kind": "ready", "data": {"uid": 999}}) + b"\n"
        self.assertEqual(parser.feed(ready[:8]), [])
        record, = parser.feed(ready[8:])
        self.assertEqual(record["data"]["uid"], 999)
        with self.assertRaises(policy.GuardError):
            parser.feed(ready)
        for bad in (
            b'{"scope":"scope","scope":"other","kind":"ready","data":{}}\n',
            policy.encoded({"scope": "other", "kind": "ready", "data": {}}) + b"\n",
            policy.encoded({"scope": "scope", "kind": "result", "data": {}}) + b"\n",
            b"not-json\n",
        ):
            with self.subTest(bad=bad), self.assertRaises(policy.GuardError):
                supervisor.Protocol("scope", 4096).feed(bad)
        with self.assertRaises(policy.GuardError):
            supervisor.Protocol("scope", 64).feed(b"x" * 65)

    def test_output_probe_does_not_parse_or_retain_raw_payload_after_readiness(self):
        parser = supervisor.Protocol("scope", 128, raw_after_ready=True)
        ready = policy.encoded({"scope": "scope", "kind": "ready", "data": {}}) + b"\n"
        self.assertEqual(len(parser.feed(ready)), 1)
        self.assertEqual(parser.feed(b"raw owned output"), [])
        self.assertEqual(parser.buffer, b"")
        with self.assertRaises(policy.GuardError):
            parser.feed(b"x" * 129)

    def test_artifact_allowlist_and_capacity_fail_without_truncation(self):
        artifacts = supervisor.Artifacts(self.root / "artifacts")
        artifacts.write("scope.json", {"diagnostic_only": True})
        before = (artifacts.root / "scope.json").read_bytes()
        with mock.patch.object(policy, "OUTPUT_BYTES", len(before)):
            with self.assertRaises(policy.GuardError):
                artifacts.write("scope.json", {"larger": "x" * len(before)})
        self.assertEqual((artifacts.root / "scope.json").read_bytes(), before)
        with self.assertRaises(policy.GuardError):
            artifacts.write("repository.tar", {"source": "forbidden"})
        secret = self.root / "untouched"
        secret.write_bytes(b"owned sentinel")
        (artifacts.root / "progress.jsonl").symlink_to(secret)
        with self.assertRaises(policy.GuardError):
            artifacts.append("progress.jsonl", {"unsafe": True})
        self.assertEqual(secret.read_bytes(), b"owned sentinel")

    def test_runtime_report_shape_cannot_be_a_production_or_partial_pass(self):
        report = {
            "coverage": {"tracked_paths": 2, "owned_paths": 1, "fail_closed_exclusions": 1},
            "measurement": {"false_positive_selections": 0, "false_negative_selections": 0},
            "artifact": {"executable_lifecycle": [
                {"removal": "fail", "restoration": "pass", "proof_id": f"proof-{index}",
                 "trigger_event_id": f"event-{index}", "trigger_type": "owned-test-trigger"}
                for index in range(3)
            ]},
            "execution": {"revision": policy.GRAPH, "base_revision": policy.BASE, "runs": 2, "states": 1},
        }
        self.assertFalse(policy.validate_report(report)["production_acceptance"])
        for path, value in (
            (("coverage", "owned_paths"), 0), (("measurement", "false_negative_selections"), 1),
            (("execution", "revision"), "b" * 40), (("artifact", "executable_lifecycle"), []),
        ):
            bad = copy.deepcopy(report)
            bad[path[0]][path[1]] = value
            with self.subTest(path=path), self.assertRaises(policy.GuardError):
                policy.validate_report(bad)

    def test_lifetime_probe_cannot_credit_outer_cleanup_for_a_broken_reaper(self):
        result = {
            "mode": "lifetime", "identity": {"uid": 999}, "empty": True,
            "watchdog_reaped": True, "escaped": {"sid": 42},
            "held_descendants_terminal": 2, "caller_lifetime_control_exercised": True,
            "empty_before_outer_cleanup": True, "first_cause": {"type": "lifetime-eof"},
        }
        supervisor.validate_probe(result)
        bad = {**result, "empty_before_outer_cleanup": False}
        with self.assertRaises(policy.GuardError):
            supervisor.validate_probe(bad)
        with self.assertRaises(policy.GuardError):
            supervisor.validate_probe({**result, "first_cause": {"type": "deadline"}})

    def test_nested_namespace_cannot_omit_control_write_qualification(self):
        proof = {
            "uid": 0, "uid_map": ["0", "999", "1"], "gid_map": ["0", "998", "1"],
            "no_new_privs": "1", "cgroup": "/owned", "namespaces": {"user": 2},
            "cgroup_control_probe": {"same_value_writes_denied": {"memory.max": 13, "pids.max": 1}},
        }
        arguments = {"uid": 999, "gid": 998, "group": "/owned", "parent_user_namespace": 1}
        policy.validate_nested_probe(proof, **arguments)
        for replacement in (None, {}, {"same_value_writes_denied": {"memory.max": 13}},
                            {"same_value_writes_denied": {"memory.max": 0, "pids.max": 1}},
                            {"namespace_denied": 2}):
            with self.subTest(replacement=replacement), self.assertRaises(policy.GuardError):
                policy.validate_nested_probe({**proof, "cgroup_control_probe": replacement}, **arguments)
        with self.assertRaises(policy.GuardError):
            policy.validate_nested_probe({**proof, "uid_map": ["0", "0", "1"]}, **arguments)

    def test_actual_scoped_apparmor_profile_parses_without_loading(self):
        path = self.root / "apparmor.profile"
        path.write_text(supervisor.apparmor_text("vo-ci180-12345"))
        result = subprocess.run(
            ["/usr/sbin/apparmor_parser", "--skip-kernel-load", "--skip-cache", "--jobs", "1",
             "--max-jobs", "1", "--abort-on-error", str(path)],
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        for bad in ("unconfined", "../other", "vo-ci180-1 {\n capability,"):
            with self.assertRaises(policy.GuardError):
                supervisor.apparmor_text(bad)

    def test_existing_watchdog_reaps_actual_double_fork_escape_on_deadline_and_lifetime(self):
        code = (
            "import json,os,signal\n"
            "r,w=os.pipe()\n"
            "first=os.fork()\n"
            "if first==0:\n"
            " os.close(r);os.setsid();second=os.fork()\n"
            " if second: os._exit(0)\n"
            " os.write(w,str(os.getpid()).encode());os.close(w)\n"
            " while True: signal.pause()\n"
            "os.close(w);grand=int(os.read(r,100));os.close(r);os.waitpid(first,0)\n"
            "print(json.dumps([os.getpid(),grand,os.getsid(grand),os.getsid(0)]),flush=True)\n"
            "while True: signal.pause()\n"
        )
        for reason in ("deadline", "lifetime"):
            with self.subTest(reason=reason):
                reader, writer = os.pipe2(os.O_CLOEXEC)
                child = None
                descriptors = []
                started = time.monotonic()
                deadline = started + (1 if reason == "deadline" else 5)
                try:
                    child = subprocess.Popen(
                        ["/usr/bin/python3", "-I", "-S", "-B", str(supervisor.LIFECYCLE), str(deadline),
                         "--", "/usr/bin/python3", "-I", "-S", "-B", "-c", code],
                        stdin=reader, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        close_fds=True, env=policy.CLEAN_ENV,
                    )
                    os.close(reader)
                    reader = None
                    self.assertIsNone(child.stdin)
                    with selectors.DefaultSelector() as selector:
                        selector.register(child.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(0.75))
                    leader, escaped, escaped_sid, leader_sid = json.loads(child.stdout.readline())
                    self.assertNotEqual(escaped_sid, leader_sid)
                    descriptors = [os.pidfd_open(pid) for pid in (leader, escaped)]
                    if reason == "lifetime":
                        os.close(writer)
                        writer = None
                    self.assertNotEqual(child.wait(timeout=4), 0)
                    with selectors.DefaultSelector() as selector:
                        for descriptor in descriptors:
                            selector.register(descriptor, selectors.EVENT_READ)
                        self.assertEqual(len(selector.select(0)), 2)
                    self.assertLess(time.monotonic() - started, 4)
                finally:
                    if writer is not None:
                        os.close(writer)
                    if reader is not None:
                        os.close(reader)
                    if child is not None:
                        child.wait(timeout=5)
                        child.stdout.close()
                        child.stderr.close()
                    for descriptor in descriptors:
                        os.close(descriptor)

    def test_setup_tool_has_actual_stream_and_deadline_guards(self):
        code, data = supervisor.tool(["/usr/bin/python3", "-I", "-S", "-B", "-c", "print('owned')"], timeout=2)
        self.assertEqual((code, data), (0, b"owned\n"))
        with self.assertRaises(policy.GuardError):
            supervisor.tool(
                ["/usr/bin/python3", "-I", "-S", "-B", "-c", "import os;os.write(1,b'x'*4096)"],
                timeout=2, maximum=64,
            )
        with self.assertRaises(policy.GuardError):
            supervisor.tool(
                ["/usr/bin/python3", "-I", "-S", "-B", "-c", "import time;time.sleep(5)"],
                timeout=0.2,
            )

    def test_volume_mount_dispatch_preserves_the_real_ordinary_executable_guard(self):
        spec = importlib.util.spec_from_file_location("owned_lifecycle", supervisor.LIFECYCLE)
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        for path in ("/usr/bin/mount", "/usr/bin/umount"):
            with self.assertRaises(ValueError):
                lifecycle.ordinary_executable(path)
            with self.assertRaisesRegex(policy.GuardError, "ordinary system executables"):
                supervisor.tool([path, "--version"], timeout=2)

        class OrdinaryHelperReached(Exception):
            pass

        def dispatch(argv, **_kwargs):
            lifecycle.ordinary_executable(argv[0])
            if argv[0] == "/usr/sbin/losetup":
                return 0, b"/dev/loop7\n"
            if argv[0] == "/usr/bin/python3":
                self.assertEqual(argv[1:4], ["-I", "-S", "-B"])
                self.assertEqual(argv[4], str(Path(volume_mount.__file__)))
                self.assertEqual(argv[5], "mount")
                raise OrdinaryHelperReached()
            return 0, b""

        volume = supervisor.Volume(self.root, 64 * policy.MIB, os.getuid(), os.getgid())
        with mock.patch.object(supervisor, "tool", side_effect=dispatch), mock.patch.object(
            volume_mount, "make_spec", return_value={"owned": "dispatch-only fixture"},
        ):
            with self.assertRaises(OrdinaryHelperReached):
                volume.prepare()
        self.assertIsNone(volume.mounted)
        self.assertFalse(volume.closed)

    def test_watched_volume_helper_executes_and_rejects_unowned_request(self):
        request = self.root / "mount.json"
        request.write_text(json.dumps({"target": str(self.root / "volume")}))
        request.chmod(0o444)
        with self.assertRaisesRegex(policy.GuardError, "owned volume setup:"):
            supervisor.tool([
                "/usr/bin/python3", "-I", "-S", "-B", str(Path(volume_mount.__file__)),
                "inspect", str(request), "0",
            ], timeout=2)

    def volume_fixture(self):
        volume = supervisor.Volume(self.root, 64 * policy.MIB, os.getuid(), os.getgid())
        volume.image.touch()
        volume.mountpoint.mkdir()
        volume.mount_request.write_text("{}")
        volume.device = "/dev/loop7"
        volume.mounted = None
        return volume

    def test_volume_cleanup_uses_watched_helper_and_confirms_mount_absence(self):
        for mounted in (False, True):
            with self.subTest(mounted=mounted):
                directory = self.root / str(mounted)
                directory.mkdir()
                volume = supervisor.Volume(directory, 64 * policy.MIB, os.getuid(), os.getgid())
                volume.image.touch()
                volume.mountpoint.mkdir()
                volume.mount_request.write_text("{}")
                volume.device = "/dev/loop7"
                volume.mounted = None
                calls = []

                def dispatch(argv, **_kwargs):
                    calls.append(argv)
                    if argv[0] == "/usr/sbin/losetup":
                        self.assertEqual(argv[1:], ["--detach", "/dev/loop7"])
                        return 0, b""
                    self.assertEqual(argv[:4], ["/usr/bin/python3", "-I", "-S", "-B"])
                    self.assertEqual(argv[4], str(Path(volume_mount.__file__)))
                    state = "mounted" if mounted and argv[5] == "inspect" else "absent"
                    return 0, policy.encoded({
                        "state": state, "mount_id": 42 if state == "mounted" else None,
                        "filesystem_device": 7,
                    })

                with mock.patch.object(supervisor, "tool", side_effect=dispatch):
                    volume.close()
                actions = [argv[5] for argv in calls if argv[0] == "/usr/bin/python3"]
                self.assertEqual(actions, ["inspect", "unmount"] if mounted else ["inspect"])
                self.assertTrue(volume.closed)
                self.assertFalse(volume.image.exists())
                self.assertFalse(volume.mountpoint.exists())

    def test_uncertain_volume_cleanup_preserves_image_and_device(self):
        volume = self.volume_fixture()
        with mock.patch.object(supervisor, "tool", side_effect=policy.GuardError("mount ownership uncertain")) as invoked:
            with self.assertRaisesRegex(policy.GuardError, "ownership uncertain"):
                volume.close()
        self.assertEqual(invoked.call_count, 1)
        self.assertEqual(volume.device, "/dev/loop7")
        self.assertFalse(volume.closed)
        self.assertTrue(volume.image.exists())
        self.assertTrue(volume.mountpoint.exists())

    def test_volume_syscalls_are_fixed_ext4_and_non_lazy_unmount(self):
        with mock.patch.object(kernel.LIBC, "mount", return_value=0) as mount:
            volume_mount.mount_syscall("/dev/loop7", "/owned/volume")
        mount.assert_called_once_with(b"/dev/loop7", b"/owned/volume", b"ext4", 6, None)
        with mock.patch.object(kernel.LIBC, "umount2", return_value=0) as unmount:
            volume_mount.unmount_syscall("/owned/volume")
        unmount.assert_called_once_with(b"/owned/volume", 0)
        with self.assertRaises(policy.GuardError):
            volume_mount.operate("arbitrary-command", {})

    def test_volume_ownership_backing_and_foreign_mounts_fail_closed(self):
        parent = Path("/tmp/vo-ci180-12345-owned/probe-volume")
        target, image, device = parent / "volume", parent / "workspace.img", Path("/dev/loop7")
        directory = lambda inode: [10, inode, stat.S_IFDIR | 0o755, 0, 0, 4096, 2, 0]
        identities = {
            parent: directory(20), target: directory(21),
            parent.parent: [10, 19, stat.S_IFDIR | 0o700, 0, 0, 4096, 2, 0],
            image: [10, 22, stat.S_IFREG | 0o600, 0, 0, 64 * policy.MIB, 1, 0],
            device: [11, 23, stat.S_IFBLK | 0o660, 0, 6, 0, 1, os.makedev(7, 7)],
        }
        spec = {
            "image": str(image), "target": str(target), "device": str(device),
            "image_identity": identities[image], "target_identity": identities[target],
            "device_identity": identities[device], "parent_identity": identities[parent],
            "worker_uid": 999, "worker_gid": 998,
        }
        with mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(
            volume_mount, "identity", side_effect=lambda path: identities[Path(path)],
        ), mock.patch.object(kernel, "read", side_effect=lambda path, *_: b"" if str(path) == "/proc/self/mountinfo" else str(image).encode()):
            self.assertEqual(volume_mount.inspect_mount(spec)["state"], "absent")
        mounted = copy.deepcopy(identities)
        mounted[target] = [os.makedev(7, 7), 2, stat.S_IFDIR | 0o755, 999, 998, 4096, 2, 0]
        record = f"90 1 7:7 / {target} rw,nosuid,nodev - ext4 /dev/loop7 rw\n".encode()
        with mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(
            volume_mount, "identity", side_effect=lambda path: mounted[Path(path)],
        ), mock.patch.object(kernel, "read", side_effect=lambda path, *_: record if str(path) == "/proc/self/mountinfo" else str(image).encode()):
            self.assertEqual(volume_mount.inspect_mount(spec, 90)["state"], "mounted")
            with self.assertRaises(policy.GuardError):
                volume_mount.inspect_mount(spec, 91)
        for defect in ("image-owner", "device-identity", "mountpoint-identity", "backing", "foreign-mount"):
            observed = copy.deepcopy(identities)
            if defect == "image-owner":
                observed[image][3] = 999
            elif defect == "device-identity":
                observed[device][1] += 1
            elif defect == "mountpoint-identity":
                observed[target][1] += 1

            def read(path, *_args):
                if str(path) == "/proc/self/mountinfo":
                    return f"90 1 7:8 / {target} rw,nosuid,nodev - ext4 /dev/loop8 rw\n".encode() if defect == "foreign-mount" else b""
                return b"/other/workspace.img" if defect == "backing" else str(image).encode()

            with self.subTest(defect=defect), mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(
                volume_mount, "identity", side_effect=lambda path: observed[Path(path)],
            ), mock.patch.object(kernel, "read", side_effect=read), mock.patch.object(
                volume_mount, "unmount_syscall",
            ) as unmount:
                with self.assertRaises(policy.GuardError):
                    volume_mount.operate("unmount", spec)
                unmount.assert_not_called()

    def test_real_non_lazy_unmount_syscall_in_an_owned_user_namespace(self):
        point = self.root / "owned-mount"
        point.mkdir()
        code = (
            "import os,sys\nfrom pathlib import Path\n"
            "sys.path.insert(0,sys.argv[1])\nimport kernel,volume_mount\n"
            "point=Path(sys.argv[2]);before=point.stat().st_dev\n"
            "kernel.mount_tmpfs(point,size=1048576,flags=6)\n"
            "(point/'owned').write_bytes(b'benign')\n"
            "assert point.stat().st_dev!=before\n"
            "volume_mount.unmount_syscall(point)\n"
            "assert point.stat().st_dev==before and not (point/'owned').exists()\n"
            "try: volume_mount.unmount_syscall(point)\n"
            "except OSError as error: assert error.errno==22\n"
            "else: raise AssertionError('absent mount was reported as unmounted')\n"
            "print('owned non-lazy unmount confirmed')\n"
        )
        status, output = supervisor.tool([
            "/usr/bin/unshare", "--user", "--map-root-user", "--mount", "--propagation", "private",
            "/usr/bin/python3", "-I", "-S", "-B", "-c", code,
            str(Path(volume_mount.__file__).parent), str(point),
        ], timeout=5)
        self.assertEqual((status, output), (0, b"owned non-lazy unmount confirmed\n"))
        self.assertEqual(list(point.iterdir()), [])

    def test_real_private_pivot_preserves_nested_userns_without_exposing_old_root(self):
        root = self.root / "pivot"
        root.mkdir()
        code = (
            "import importlib.util,os,sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0,sys.argv[1])\n"
            "import kernel\n"
            "spec=importlib.util.spec_from_file_location('ns',sys.argv[2])\n"
            "ns=importlib.util.module_from_spec(spec);spec.loader.exec_module(ns)\n"
            "root=Path(sys.argv[3])\n"
            "for name in ('usr','proc'): (root/name).mkdir()\n"
            "for name in ('bin','lib','lib64'): (root/name).symlink_to('usr/'+name)\n"
            "ns.bind(root,root,executable=True);ns.bind('/usr',root/'usr',executable=True)\n"
            "ns.mount('proc',root/'proc',2|4|8,'proc')\n"
            "kernel.pivot_root(root)\n"
            "assert not Path(sys.argv[3]).exists()\n"
            "os.execve('/usr/bin/unshare',['unshare','--user','--map-root-user',"
            "'/usr/bin/python3','-I','-S','-B','-c',"
            "\"import os;print('nested-uid='+str(os.getuid()))\"],{'PATH':'/usr/bin:/bin','LC_ALL':'C'})\n"
        )
        code_status, output = supervisor.tool([
            "/usr/bin/unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork",
            "--kill-child", "--propagation", "private", "/usr/bin/python3", "-I", "-S", "-B",
            "-c", code, str(Path(kernel.__file__).parent),
            str(REPO / "scripts/validation_ownership/sandbox_exec.py"), str(root),
        ], timeout=5)
        self.assertEqual((code_status, output), (0, b"nested-uid=0\n"))
        self.assertCountEqual(list(root.iterdir()), [root / "usr", root / "proc", root / "bin", root / "lib", root / "lib64"])

    def test_io_peak_rates_remain_observed_and_counter_monotonic(self):
        peaks = {}
        supervisor.io_peaks({}, {"7:1": {"rbytes": 100, "wbytes": 60}}, 2, peaks)
        self.assertEqual(peaks["7:1/rbytes_per_second"], 50)
        supervisor.io_peaks(
            {"7:1": {"rbytes": 100, "wbytes": 60}},
            {"7:1": {"rbytes": 130, "wbytes": 180}}, 2, peaks,
        )
        self.assertEqual(peaks["7:1/rbytes_per_second"], 50)
        self.assertEqual(peaks["7:1/wbytes_per_second"], 60)
        with self.assertRaises(policy.GuardError):
            supervisor.io_peaks({"7:1": {"wbytes": 100}}, {"7:1": {"wbytes": 1}}, 1, {})

    def test_workflow_is_push_only_separate_pinned_and_least_privilege(self):
        data = yaml.load((REPO / policy.WORKFLOW).read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(data["on"], {"push": {"branches": [policy.BRANCH]}})
        self.assertEqual(data["permissions"], {"contents": "read"})
        self.assertEqual(data["concurrency"]["group"], "issue180-disposable-diagnostic-baseline-3")
        self.assertEqual(set(data["jobs"]), {"disposable-graph-calibration-3"})
        job = data["jobs"]["disposable-graph-calibration-3"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("container", job)
        self.assertNotIn("strategy", job)
        self.assertEqual(job["timeout-minutes"], "90")
        for condition in ("github.run_number == 1", "github.run_attempt == 1", "github.event.created == true"):
            self.assertIn(condition, job["if"])
        steps = job["steps"]
        checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 2)
        self.assertEqual({step["uses"] for step in checkouts}, {
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        })
        self.assertEqual([step["with"]["path"] for step in checkouts], ["harness", "candidate"])
        self.assertEqual([step["with"]["ref"] for step in checkouts], ["${{ github.sha }}", policy.GRAPH])
        self.assertTrue(all(step["with"]["persist-credentials"] == "false" for step in checkouts))
        self.assertEqual(checkouts[1]["with"]["submodules"], "recursive")
        self.assertTrue(all(step["with"]["fetch-depth"] == "0" for step in checkouts))
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]["uses"], "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a")
        uploaded = uploads[0]["with"]["path"].splitlines()
        self.assertEqual([path.rsplit("/", 1)[1] for path in uploaded], list(policy.ARTIFACT_NAMES))
        self.assertTrue(all("*" not in path and "/candidate/" not in path and "/harness/" not in path for path in uploaded))
        for step in steps:
            self.assertNotIn("continue-on-error", step)
        self.assertNotIn("secrets.", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
