import base64
import configparser
import copy
import hashlib
import importlib
import io
import json
import os
import shlex
import shutil
import signal
import socket
import sqlite3
import ssl
import stat
import struct
import subprocess
import sys
import threading
import time
import unittest
import zlib
from contextlib import closing, contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import jsonschema

from scripts.workflow_pilot import git_broker as broker
from scripts.workflow_pilot import git_broker_protocol as protocol
from scripts.workflow_pilot import git_broker_store as store_module
from scripts.workflow_pilot.signed_records import (
    RecordError, canonical_json, format_utc, parse_utc, signed_payload, strict_json, utc_now,
    verify_signature,
)
from scripts.workflow_pilot.tests.broker_test_support import (
    Fixture, Keys, artifact_directory, installed_copy, poison_bytecode,
)
from scripts.workflow_pilot.tests import protected_broker_fixture as protected


class BrokerBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.root = artifact_directory("broker-loader")
        self.entry = installed_copy(self.root / "installed")
        self.marker = self.root / "cache-executed"

    def tearDown(self):
        shutil.rmtree(self.root)

    @contextmanager
    def synthetic_root_ownership(self):
        lstat, fstat = Path.lstat, os.fstat

        def owned(metadata):
            values = list(metadata)
            values[4] = 0
            return os.stat_result(values)

        # Only ownership is simulated. Real modes, links, types, opened bytes
        # and loader behavior are tested; this is not protected-deployment proof.
        with mock.patch.object(Path, "lstat", lambda path: owned(lstat(path))), mock.patch.object(
            broker.os, "fstat", side_effect=lambda fd: owned(fstat(fd)),
        ):
            yield

    def test_unprotected_bootstrap_rejects_before_any_local_source_or_cache_executes(self):
        poison_bytecode(self.entry, self.marker)
        control = subprocess.run([
            "/usr/bin/python3", "-I", "-B", "-c",
            f"import sys;sys.path.insert(0,{str(self.entry.parents[2])!r});"
            "from scripts.workflow_pilot import signed_records",
        ], cwd=self.root, capture_output=True, timeout=5, check=True)
        self.assertEqual(control.stdout, b"")
        self.assertTrue(self.marker.exists(), "-B unexpectedly prevented the negative control")
        self.marker.unlink()
        self.entry.with_name("git_broker_store.py").chmod(0o666)
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", "-B", str(self.entry), "--help"],
            cwd=self.root, capture_output=True, timeout=5,
        )
        self.assertFalse(self.marker.exists(), "local bytecode ran before source preflight")
        self.assertEqual(completed.returncode, 2)

    def test_every_source_is_checked_before_package_code_executes(self):
        initializer = self.entry.with_name("__init__.py")
        initializer.write_text(
            f"from pathlib import Path\nPath({str(self.marker)!r}).write_text('source executed')\n"
        )
        source = self.entry.with_name("git_broker_store.py")
        original = source.read_bytes()
        with self.synthetic_root_ownership():
            for defect in ("writable", "symlink", "hardlink", "oversized", "directory"):
                with self.subTest(defect=defect):
                    if defect == "writable":
                        source.chmod(0o666)
                    elif defect == "symlink":
                        source.unlink()
                        source.symlink_to(initializer)
                    elif defect == "hardlink":
                        source.unlink()
                        os.link(initializer, source)
                    elif defect == "oversized":
                        source.write_bytes(b" " * (1024 * 1024 + 1))
                    else:
                        source.unlink()
                        source.mkdir()
                    try:
                        with self.assertRaises((OSError, ValueError)):
                            with broker._source_only_broker(self.entry):
                                self.fail("unprotected source reached local import")
                        self.assertFalse(self.marker.exists())
                    finally:
                        if source.is_dir():
                            source.rmdir()
                        else:
                            source.unlink()
                        source.write_bytes(original)
                        source.chmod(0o644)
            self.entry.parent.chmod(0o777)
            try:
                with self.assertRaises(ValueError):
                    broker._capture_installed_sources(self.entry)
            finally:
                self.entry.parent.chmod(0o755)

    def test_source_only_loader_ignores_all_caches_and_never_reopens_captured_paths(self):
        for name in broker.INSTALLED_MODULES:
            poison_bytecode(self.entry, self.marker, name)
        with self.synthetic_root_ownership():
            captured = broker._capture_installed_sources(self.entry)
        self.entry.with_name("signed_records.py").write_text("raise RuntimeError('path reopened')\n")
        self.entry.with_name("uncaptured.py").write_text("raise RuntimeError('import fallback')\n")
        (self.entry.parent.parent / "__init__.py").write_text("raise RuntimeError('package fallback')\n")
        previous = {name: module for name, module in sys.modules.items() if name.startswith("scripts.")}
        with mock.patch.object(broker, "_capture_installed_sources", return_value=captured) as capture, \
             mock.patch.object(broker.os, "open", side_effect=AssertionError("source pathname reopened")), \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("source pathname reopened")):
            with broker._source_only_broker(self.entry) as installed:
                self.assertIsNot(installed, broker)
                records = importlib.import_module("scripts.workflow_pilot.signed_records")
                self.assertIs(installed.RecordError, records.RecordError)
                self.assertEqual(records.parse_utc("2026-09-05T23:00:00Z").hour, 23)
                with self.assertRaises(records.RecordError):
                    records.parse_utc("2026-09-05T24:00:00Z")
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module("scripts.workflow_pilot.uncaptured")
                self.assertEqual(sys.modules["scripts"].__path__, [])
                self.assertEqual(sys.modules["scripts.workflow_pilot"].__path__, [])
        capture.assert_called_once_with(self.entry)
        self.assertFalse(self.marker.exists())
        for name, module in previous.items():
            self.assertIs(sys.modules[name], module)

    def test_deployed_unit_keeps_private_state_explicit_socket_group_and_failure_statuses(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.optionxform = str
        unit.read(Path(broker.__file__).parent / "deployment" / "workflow-pilot-git-broker@.service")
        service = unit["Service"]
        self.assertEqual(service["User"], "fe8-git-broker")
        self.assertEqual(service["Group"], "fe8-git-broker")
        self.assertEqual(shlex.split(service["SupplementaryGroups"]), ["fe8-git-coordinator"])
        self.assertEqual(int(service["UMask"], 8), 0o077)
        self.assertEqual(int(service["StateDirectoryMode"], 8), 0o700)
        self.assertEqual(int(service["RuntimeDirectoryMode"], 8) & 0o022, 0)
        self.assertEqual(service["KillMode"], "control-group")
        self.assertEqual(service["SendSIGKILL"], "yes")
        self.assertEqual(service["TimeoutStopSec"], "2")
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertNotIn("2", shlex.split(service.get("SuccessExitStatus", "")))
        for directive, command in (("ExecStartPre", "preflight-server"), ("ExecStart", "serve")):
            arguments = shlex.split(service[directive])
            self.assertEqual(arguments[:2], ["/usr/bin/python3", "-I"])
            self.assertEqual(Path(arguments[2]).name, "git_broker.py")
            self.assertEqual(arguments[3:], [command, "--installation", "/etc/fe8-git-broker/%i.json"])


class BrokerSocketPermissionTests(unittest.TestCase):
    def setUp(self):
        self.root = artifact_directory("broker-socket")
        self.endpoint = self.root / "broker.sock"
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.listener.bind(f"/proc/self/fd/{descriptor}/broker.sock")
        finally:
            os.close(descriptor)
        self.endpoint.chmod(0o660)
        self.manifest = {"broker_uid": os.geteuid(), "socket_gid": self.endpoint.stat().st_gid}

    def tearDown(self):
        self.listener.close()
        shutil.rmtree(self.root)

    def test_client_requires_exact_socket_owner_group_type_and_permissions(self):
        broker.socket_permissions(self.endpoint, self.manifest)
        for mode in (0o666, 0o600, 0o770, 0o777):
            self.endpoint.chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaises(RecordError):
                broker.socket_permissions(self.endpoint, self.manifest)
        self.endpoint.chmod(0o660)
        for field in ("broker_uid", "socket_gid"):
            with self.subTest(field=field), self.assertRaises(RecordError):
                broker.socket_permissions(self.endpoint, {**self.manifest, field: self.manifest[field] + 1})
        self.endpoint.unlink()
        self.endpoint.write_bytes(b"not a socket")
        self.endpoint.chmod(0o660)
        with self.assertRaises(RecordError):
            broker.socket_permissions(self.endpoint, self.manifest)

    def test_group_mode_does_not_authorize_inherited_named_user_acl(self):
        # Linux POSIX ACL v2: a named user can otherwise inherit socket write
        # access even while chmod reports exactly 0660.
        acl = struct.pack("<I", 2) + b"".join(
            struct.pack("<HHI", tag, permissions, identity)
            for tag, permissions, identity in (
                (1, 6, 0xffffffff), (2, 6, os.geteuid() + 1),
                (4, 6, 0xffffffff), (16, 6, 0xffffffff), (32, 0, 0xffffffff),
            )
        )
        os.setxattr(self.endpoint, "system.posix_acl_access", acl)
        self.assertEqual(stat.S_IMODE(self.endpoint.stat().st_mode), 0o660)
        with self.assertRaises(RecordError):
            broker.socket_permissions(self.endpoint, self.manifest)
        os.removexattr(self.endpoint, "system.posix_acl_access")
        broker.socket_permissions(self.endpoint, self.manifest)

    def test_protected_probe_does_not_count_post_connect_rejection_or_unavailability_as_denial(self):
        for result in (
            {"direct_protocol": "denied"},
            {"direct_connect": "connected", "direct_protocol": "denied"},
            {"direct_connect": "unavailable"},
        ):
            with self.subTest(result=result), mock.patch.object(
                protected.subprocess, "run", return_value=SimpleNamespace(stdout=canonical_json(result)),
            ), self.assertRaises(RecordError):
                protected.candidate_probe(65533, [], self.endpoint, 1)


class BrokerFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = artifact_directory("broker-behavior")
        cls.keys = Keys(cls.root / "keys")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    def setUp(self):
        self.directory = artifact_directory("broker-case")
        self.fixture = Fixture(self.directory, self.keys)

    def tearDown(self):
        self.fixture.close()
        shutil.rmtree(self.directory)

    def assert_absent(self):
        self.assertEqual(
            self.fixture.store.remote_refs(utc_now() + timedelta(seconds=3)),
            dict.fromkeys(self.fixture.policy.refs),
        )


class BrokerTests(BrokerFixtureTests):
    def test_real_atomic_bootstrap_advance_bind_and_fresh_readback(self):
        first = self.fixture.bootstrap()
        with self.assertRaises(RecordError):
            self.fixture.store.reserve(first, self.keys.client_fingerprint)
        for operation in ("advance", "bind", "advance"):
            plan, pack, current = self.fixture.make_plan(operation)
            with self.subTest(operation=operation):
                result = self.fixture.publish(plan, pack)
                self.assertEqual(result[0], "published")
                self.assertEqual(result[1], protocol.expected_refs(plan, "new"))
                self.assertIsNotNone(result[2])
                self.assertEqual(
                    self.fixture.store.readback(
                        plan, self.keys.client_fingerprint, utc_now() + timedelta(seconds=3),
                    ), result,
                )
                self.fixture.current = current
        self.assertEqual(list(self.fixture.store.work.iterdir()), [])
        self.assertEqual(
            self.fixture.git(self.fixture.remote, "for-each-ref", "--format=%(refname)").decode().splitlines(),
            sorted(self.fixture.policy.refs),
        )

    def test_real_deltified_atomic_publication(self):
        self.fixture.bootstrap()
        plan, _pack, _current = self.fixture.make_plan()
        pack = self.fixture.git(
            self.fixture.source, "pack-objects", "--stdout", "--delta-base-offset",
            "--no-reuse-delta", "--no-reuse-object", "--window=10", "--depth=10",
            data=("\n".join(plan["pack"]["objects"]) + "\n").encode("ascii"),
        )
        result = self.fixture.git(self.fixture.source, "index-pack", "--stdin", "--strict", data=pack)
        index = self.fixture.source / "objects" / "pack" / f"pack-{result[5:-1].decode('ascii')}.idx"
        description = self.fixture.git(self.fixture.source, "verify-pack", "-v", str(index))
        self.assertTrue(any(len(line.split()) == 7 for line in description.splitlines()))
        plan["pack"].update(size=len(pack), sha256=hashlib.sha256(pack).hexdigest())
        self.keys.sign(protocol.PLAN_DOMAIN, plan)
        result = self.fixture.publish(plan, pack)
        self.assertEqual(result[0], "published")
        self.assertEqual(result[1], protocol.expected_refs(plan, "new"))
        self.assertEqual(
            self.fixture.store.remote_refs(utc_now() + timedelta(seconds=3)), result[1],
        )
        self.assertEqual(list(self.fixture.store.work.iterdir()), [])

    def test_plan_schema_accepts_real_signed_publication(self):
        plan, _pack, _current = self.fixture.make_plan()
        schema = __import__("json").loads(Path(protocol.__file__).with_name("git_broker.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(plan)

    def test_repository_schema_and_runtime_reject_reserved_names_and_trailing_input(self):
        plan, _pack, _current = self.fixture.make_plan()
        schema = json.loads(Path(protocol.__file__).with_name("git_broker.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        cases = [
            ("a/b", True), ("example/...", True), ("example/.git", True),
            ("example/_", True), ("a" * 39 + "/" + "b" * 100, True),
            ("example/.", False), ("example/..", False), ("./repo", False),
            ("../repo", False), ("example/", False), ("example/repo/extra", False),
            ("a" * 40 + "/repo", False), ("example/" + "b" * 101, False),
        ]
        cases.extend(("example/repo" + suffix, False) for suffix in (
            "\n", "\r", "\r\n", " ", "\0", "\u2028", "\u2029",
        ))
        for repository, expected in cases:
            with self.subTest(repository=repository):
                changed = copy.deepcopy(plan)
                changed["repository"] = repository
                self.keys.sign(protocol.PLAN_DOMAIN, changed)
                try:
                    policy = protocol.Policy.parse({**self.fixture.policy.__dict__, "repository": repository})
                    protocol.validate_plan(changed, policy, self.keys.client_fingerprint, utc_now())
                    runtime_accepts = True
                except RecordError:
                    runtime_accepts = False
                self.assertEqual(runtime_accepts, expected)
                self.assertEqual(validator.is_valid(changed), runtime_accepts)

    def test_ref_schema_and_runtime_require_strict_end_of_input(self):
        plan, _pack, _current = self.fixture.make_plan()
        schema = json.loads(Path(protocol.__file__).with_name("git_broker.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        for index in (0, 1):
            for suffix in ("", "\n", "\r", "\r\n", " ", "\0", "\u2028", "\u2029"):
                with self.subTest(index=index, suffix=suffix):
                    changed = copy.deepcopy(plan)
                    changed["updates"][index]["ref"] += suffix
                    self.keys.sign(protocol.PLAN_DOMAIN, changed)
                    try:
                        protocol.validate_plan(
                            changed, self.fixture.policy, self.keys.client_fingerprint, utc_now(),
                        )
                        runtime_accepts = True
                    except RecordError:
                        runtime_accepts = False
                    self.assertEqual(runtime_accepts, not suffix)
                    self.assertEqual(validator.is_valid(changed), runtime_accepts)
        plan["updates"].reverse()
        self.keys.sign(protocol.PLAN_DOMAIN, plan)
        validator.validate(plan)
        protocol.validate_plan(plan, self.fixture.policy, self.keys.client_fingerprint, utc_now())

    def test_local_preflight_requires_remote_strictly_below_state(self):
        actual_uid = os.geteuid()
        manifest = self.fixture.manifest()
        manifest.update(broker_uid=actual_uid or 65534, socket="/run/workflow-pilot-fixture.sock")
        installation = self.directory / "server.json"
        inside = self.fixture.state / "remotes" / "authority.git"
        outside = self.directory / "outside.git"
        prefix = self.directory / "state-other" / "authority.git"
        for path in (inside, outside, prefix):
            path.mkdir(mode=0o700, parents=True)
        real_protected = broker.protected_path

        def fixture_ownership(path, _owners, **options):
            # Exercise real paths/modes/links, not a claimed root installation.
            return real_protected(path, {0, actual_uid}, **options)

        def preflight(remote):
            manifest["policy"]["endpoint"] = remote.as_uri()
            installation.write_bytes(canonical_json(manifest))
            return broker.load_installation(installation, "server")

        with mock.patch.object(
            broker.os, "getgroups", return_value=[manifest["socket_gid"]],
        ), mock.patch.object(broker, "protected_path", side_effect=fixture_ownership), mock.patch.object(
            broker.os, "geteuid", return_value=manifest["broker_uid"],
        ):
            self.assertEqual(preflight(inside)[1].endpoint, inside.as_uri())
            for path in (outside, prefix, self.fixture.state, self.directory):
                with self.subTest(remote=path), self.assertRaises(RecordError):
                    preflight(path)
            link = self.fixture.state / "linked.git"
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(RecordError):
                preflight(link)

    def test_preflight_requires_explicit_socket_group_and_actual_process_membership(self):
        actual_uid, protected_path = os.geteuid(), broker.protected_path
        installation = self.directory / "group-installation.json"

        def fixture_ownership(path, _owners, **options):
            return protected_path(path, {0, actual_uid}, **options)

        for client in (False, True):
            manifest = self.fixture.manifest(client=client)
            manifest["socket"] = "/run/workflow-pilot-fixture.sock"
            role = "client" if client else "server"
            actor = manifest["coordinator_uid" if client else "broker_uid"]
            group = manifest["socket_gid"]
            installation.write_bytes(canonical_json(manifest))
            with mock.patch.object(broker, "protected_path", side_effect=fixture_ownership), \
                 mock.patch.object(broker.os, "geteuid", return_value=actor), \
                 mock.patch.object(broker.os, "getegid", return_value=group + 1), \
                 mock.patch.object(broker.os, "getgroups", return_value=[]) as groups:
                with self.subTest(role=role), self.assertRaises(RecordError):
                    broker.load_installation(installation, role)
                groups.return_value = [group]
                self.assertEqual(broker.load_installation(installation, role)[0]["socket_gid"], group)
                for invalid in (None, True, 0, -1, 2**31, str(group)):
                    with self.subTest(role=role, invalid=invalid):
                        installation.write_bytes(canonical_json({**manifest, "socket_gid": invalid}))
                        with self.assertRaises(RecordError):
                            broker.load_installation(installation, role)
                del manifest["socket_gid"]
                installation.write_bytes(canonical_json(manifest))
                with self.assertRaises(RecordError):
                    broker.load_installation(installation, role)

    def test_every_identity_and_signed_plan_boundary_rejects_before_reservation(self):
        plan, _pack, _current = self.fixture.make_plan()
        mutations = {
            "expired": {"issued_at": format_utc(utc_now() - timedelta(seconds=30)), "expires_at": format_utc(utc_now() - timedelta(seconds=1))},
            "future": {"issued_at": format_utc(utc_now() + timedelta(seconds=5)), "expires_at": format_utc(utc_now() + timedelta(seconds=10))},
            "long": {"expires_at": format_utc(utc_now() + timedelta(minutes=5))},
            "copied-deployment": {"deployment_id": "e" * 64},
            "wrong-repository": {"repository": "other/repository"},
            "wrong-repository-id": {"repository_id": 9},
            "bool-repository-id": {"repository_id": True},
            "wrong-issue": {"issue": 178},
            "wrong-actor": {"actor_id": 8},
            "wrong-key": {"signer_key_id": "b" * 64},
            "wrong-endpoint": {"endpoint": "https://github.com/other/repo.git"},
            "copied-client": {"client_certificate_sha256": "c" * 64},
            "generic-push": {"operation": "push"},
            "wrong-sequence": {"sequence": 1},
            "hour-24": {"issued_at": "2026-09-05T24:00:00Z"},
            "precision": {"issued_at": "2026-09-05T00:00:00.1234567Z"},
            "unknown-field": {"argv": ["git", "push", "origin", "master"]},
        }
        for label, changes in mutations.items():
            with self.subTest(label=label):
                changed = {**copy.deepcopy(plan), **changes}
                self.keys.sign(protocol.PLAN_DOMAIN, changed)
                with self.assertRaises(RecordError):
                    self.fixture.store.reserve(changed, self.keys.client_fingerprint)
        for target in ("refs/heads/master", "refs/tags/v1", "refs/heads/workflow-pilot/authority/issue-178"):
            changed = copy.deepcopy(plan)
            changed["updates"][0]["ref"] = target
            self.keys.sign(protocol.PLAN_DOMAIN, changed)
            with self.subTest(target=target), self.assertRaises(RecordError):
                self.fixture.store.reserve(changed, self.keys.client_fingerprint)
        for change in ("signature", "nonce", "pack"):
            changed = copy.deepcopy(plan)
            if change == "signature":
                changed["signature"] = changed["signature"][:-4] + "AAAA"
            elif change == "nonce":
                changed["nonce"] = "a" * 64
            else:
                changed["pack"]["sha256"] = "f" * 64
            with self.subTest(change=change), self.assertRaises(RecordError):
                self.fixture.store.reserve(changed, self.keys.client_fingerprint)
        with self.assertRaises(RecordError):
            self.fixture.store.reserve(plan, "e" * 64)
        self.assertEqual(self.fixture.store.db.execute("SELECT count(*) FROM operations").fetchone()[0], 0)
        self.assert_absent()

    def test_pack_substitution_is_spent_and_never_uses_remote_credentials(self):
        plan, pack, _current = self.fixture.make_plan()
        self.fixture.store.reserve(plan, self.keys.client_fingerprint)
        # Remote entry is a guard only: real index-pack runs in all valid-pack cases.
        with mock.patch.object(self.fixture.store, "remote_refs", side_effect=AssertionError("remote used")):
            result = self.fixture.store.publish_reserved(
                plan, pack[:-1] + bytes([pack[-1] ^ 1]), utc_now() + timedelta(seconds=5),
            )
        self.assertEqual(result[0], "rejected")
        with self.assertRaises(RecordError):
            self.fixture.store.reserve(plan, self.keys.client_fingerprint)
        self.assert_absent()

    def test_exact_object_closure_refuses_unused_objects(self):
        plan, _pack, _current = self.fixture.make_plan()
        extra = self.fixture.git(
            self.fixture.source, "hash-object", "-w", "--stdin", data=b"unused malicious payload",
        ).decode().strip()
        objects = sorted(plan["pack"]["objects"] + [extra])
        pack = self.fixture.git(
            self.fixture.source, "pack-objects", "--stdout", "--window=0",
            data=("\n".join(objects) + "\n").encode(),
        )
        plan["pack"] = {"sha256": hashlib.sha256(pack).hexdigest(), "size": len(pack), "objects": objects}
        self.keys.sign(protocol.PLAN_DOMAIN, plan)
        self.assertEqual(self.fixture.publish(plan, pack)[0], "rejected")
        self.assert_absent()

    def test_pack_header_and_missing_object_closure_reject(self):
        for kind in ("count", "truncated", "thin"):
            plan, pack, _current = self.fixture.make_plan()
            if kind == "count":
                pack = pack[:8] + struct.pack(">I", protocol.MAX_OBJECTS + 1) + pack[12:]
            elif kind == "truncated":
                pack = pack[:64]
            else:
                objects = plan["pack"]["objects"][1:]
                pack = self.fixture.git(
                    self.fixture.source, "pack-objects", "--stdout", "--window=0",
                    data=("\n".join(objects) + "\n").encode(),
                )
                plan["pack"]["objects"] = objects
                # If an omitted tip makes structural validation reject, this is
                # already closed before index-pack; otherwise fsck must reject.
            plan["pack"]["sha256"], plan["pack"]["size"] = hashlib.sha256(pack).hexdigest(), len(pack)
            self.keys.sign(protocol.PLAN_DOMAIN, plan)
            with self.subTest(kind=kind):
                try:
                    result = self.fixture.publish(plan, pack)
                except RecordError:
                    result = ("rejected",)
                self.assertEqual(result[0], "rejected")
        self.assert_absent()

    def test_full_signed_objects_cannot_change_issue_sequence_policy_or_binding(self):
        mutations = {
            "authority-issue": lambda a, b: a.update(issue=178),
            "anchor-issue": lambda a, b: b.update(issue=178),
            "authority-sequence": lambda a, b: a.update(sequence=1),
            "anchor-sequence": lambda a, b: b.update(sequence=1),
            "new-oid-binding": lambda a, b: b.update(authority_object_id="f" * 40),
            "self-selected-key": lambda a, b: a.update(signer=self.keys.public("other")),
            "wrong-ruleset": lambda a, b: a.update(ruleset_id=8),
            "unsigned-attestation-change": lambda a, b: a["publication_attestation"].update(operation_nonce="a" * 64),
            "missing-field": lambda a, b: a.pop("event"),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                plan, pack, _current = self.fixture.make_plan(mutation=mutation)
                self.assertEqual(self.fixture.publish(plan, pack)[0], "rejected")
        self.assert_absent()

    def test_server_hook_rejects_both_refs_and_cannot_be_disabled_by_client_config(self):
        hooks = self.fixture.remote / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-receive"
        marker = self.directory / "hook-ran"
        hook.write_text(f"#!/bin/sh\nprintf authoritative > '{marker}'\nexit 1\n")
        hook.chmod(0o700)
        plan, pack, _current = self.fixture.make_plan()
        self.assertEqual(self.fixture.publish(plan, pack)[0], "rejected")
        self.assertEqual(marker.read_text(), "authoritative")
        self.assert_absent()

    def test_nonfastforward_delete_flags_fail_closed(self):
        for flag in ("receive.denyNonFastForwards", "receive.denyDeletes"):
            self.fixture.git(self.fixture.remote, "config", flag, "false")
            plan, pack, _current = self.fixture.make_plan()
            with self.subTest(flag=flag):
                self.assertEqual(self.fixture.publish(plan, pack)[0], "rejected")
            self.fixture.git(self.fixture.remote, "config", flag, "true")
        self.assert_absent()

    def test_external_hook_path_include_and_duplicate_config_are_not_protection(self):
        for key, value in (
            ("core.hooksPath", str(self.directory)),
            ("include.path", str(self.directory / "candidate.config")),
            ("uploadpack.packObjectsHook", "/bin/false"),
            ("core.bare", "true"),
        ):
            self.fixture.git(self.fixture.remote, "config", "--add", key, value)
            plan, pack, _current = self.fixture.make_plan()
            with self.subTest(key=key):
                self.assertEqual(self.fixture.publish(plan, pack)[0], "rejected")
            self.fixture.git(self.fixture.remote, "config", "--unset-all", key)
            if key == "core.bare":
                self.fixture.git(self.fixture.remote, "config", key, "true")
        self.assert_absent()

    def test_reported_abstract_socket_expired_plan_negative_control(self):
        plan, _pack, _current = self.fixture.make_plan()
        plan["issued_at"] = format_utc(utc_now() - timedelta(seconds=20))
        plan["expires_at"] = format_utc(utc_now() - timedelta(seconds=1))
        self.keys.sign(protocol.PLAN_DOMAIN, plan)
        with self.assertRaises(RecordError):
            self.fixture.store.reserve(plan, self.keys.client_fingerprint)
        self.assert_absent()
        address = "\0fe8-negative-control-" + os.urandom(12).hex()
        errors = []

        def unsafe_control(listener):
            # Reproduce the reported defect, not a production implementation:
            # this isolated test-only transport deliberately omits both checks.
            try:
                raw, _address = listener.accept()
                with raw:
                    channel = broker.Channel(raw, utc_now() + timedelta(seconds=3))
                    submitted = strict_json(channel.read_frame(protocol.MAX_JSON), protocol.MAX_JSON)
                    self.fixture.git(
                        self.fixture.source, "push", "--atomic", self.fixture.policy.endpoint,
                        *(f"{item['new']}:{item['ref']}" for item in submitted["updates"]),
                    )
                    channel.send_frame(b'{"moved":true}\n', protocol.MAX_RESPONSE)
            except BaseException as error:
                errors.append(error)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(address)
            listener.listen(1)
            listener.settimeout(3)
            thread = threading.Thread(target=unsafe_control, args=(listener,))
            thread.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
                    raw.settimeout(3)
                    raw.connect(address)
                    channel = broker.Channel(raw, utc_now() + timedelta(seconds=3))
                    channel.send_frame(canonical_json(plan), protocol.MAX_JSON)
                    self.assertEqual(channel.read_frame(protocol.MAX_RESPONSE), b'{"moved":true}\n')
            finally:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            self.fixture.store.remote_refs(utc_now() + timedelta(seconds=3)),
            protocol.expected_refs(plan, "new"),
        )

    def test_signature_encoding_is_canonical_and_representative_is_bounded(self):
        record = self.keys.sign(b"broker-test\0", {"nonce": "a" * 64})
        payload = signed_payload(b"broker-test\0", record)
        verify_signature(self.keys.signer, payload, record["signature"])
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        signature = record["signature"]
        # RSA-2048 emits 256 bytes, hence two '=' and four unused pad bits.
        alias = signature[:-3] + alphabet[alphabet.index(signature[-3]) + 1] + "=="
        self.assertEqual(base64.b64decode(alias), base64.b64decode(signature))
        with self.assertRaises(RecordError):
            verify_signature(self.keys.signer, payload, alias)
        modulus = int(self.keys.signer["modulus_hex"], 16)
        alias = base64.b64encode(modulus.to_bytes(256, "big")).decode("ascii")
        with self.assertRaises(RecordError):
            verify_signature(self.keys.signer, payload, alias)

    def test_compare_and_swap_and_aba_nonce_protection(self):
        first = self.fixture.bootstrap()
        plan, pack, _current = self.fixture.make_plan()
        plan["updates"][0]["old"] = "e" * 40
        self.keys.sign(protocol.PLAN_DOMAIN, plan)
        with self.assertRaises(RecordError):
            self.fixture.publish(plan, pack)
        # Simulate an external admin rollback, not a candidate capability.
        for update in first["updates"]:
            self.fixture.git(self.fixture.remote, "update-ref", "-d", update["ref"])
        with self.assertRaises(RecordError):
            self.fixture.store.reserve(first, self.keys.client_fingerprint)
        changed = copy.deepcopy(first)
        changed["nonce"] = "f" * 64
        self.keys.sign(protocol.PLAN_DOMAIN, changed)
        with self.assertRaises(RecordError):
            self.fixture.store.reserve(changed, self.keys.client_fingerprint)
        self.assert_absent()

    def test_second_authority_and_journal_restarts_reject_replay_and_pending_work(self):
        with self.assertRaises((RecordError, BlockingIOError)):
            store_module.PublicationStore(self.fixture.policy, self.fixture.state)
        plan, _pack, _current = self.fixture.make_plan()
        self.fixture.store.reserve(plan, self.keys.client_fingerprint)
        self.fixture.store.close()
        self.fixture.store = store_module.PublicationStore(self.fixture.policy, self.fixture.state)
        self.assertEqual(
            self.fixture.store.db.execute("SELECT status FROM operations").fetchone()[0], "uncertain",
        )
        with self.assertRaises(RecordError):
            self.fixture.store.reserve(plan, self.keys.client_fingerprint)
        self.assert_absent()

    def test_concurrent_reservation_claim_is_atomic(self):
        plan, pack, _current = self.fixture.make_plan()
        self.fixture.store.reserve(plan, self.keys.client_fingerprint)
        other, _pack, _current = self.fixture.make_plan()
        with self.assertRaises(RecordError):
            self.fixture.store.reserve(other, self.keys.client_fingerprint)
        self.assertEqual(
            self.fixture.store.publish_reserved(plan, pack, utc_now() + timedelta(seconds=5))[0], "published",
        )
        with self.assertRaises(RecordError):
            self.fixture.store.publish_reserved(plan, pack, utc_now() + timedelta(seconds=5))

    def test_candidate_git_config_and_environment_never_execute(self):
        marker = self.directory / "candidate-hook"
        command = f"!printf owned > '{marker}'"
        self.fixture.git(self.fixture.source, "config", "core.fsmonitor", command)
        self.fixture.git(self.fixture.source, "config", "credential.helper", command)
        self.fixture.git(self.fixture.source, "config", "url.ext::evil.insteadOf", self.fixture.policy.endpoint)
        poison = {
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": command,
            "GIT_SSH_COMMAND": command, "GIT_ASKPASS": command, "SSH_AUTH_SOCK": str(marker),
            "GIT_OBJECT_DIRECTORY": str(self.fixture.source / "objects"), "GIT_CONFIG_GLOBAL": str(marker),
        }
        plan, pack, _current = self.fixture.make_plan()
        with mock.patch.dict(os.environ, poison):
            self.assertEqual(self.fixture.publish(plan, pack)[0], "published")
        self.assertFalse(marker.exists())
        self.assertEqual(list(self.fixture.store.work.iterdir()), [])

    def test_process_output_and_timeout_group_are_bounded(self):
        for command, seconds in (
            (["/usr/bin/python3", "-I", "-c", "print('x'*100000)"], 3),
            (["/bin/sh", "-c", "sleep 30 & wait"], 0.15),
        ):
            with self.subTest(command=command[0]), self.assertRaises(RecordError):
                store_module.run_bounded(
                    command, cwd=self.directory, environment=store_module.clean_environment(self.directory),
                    deadline=utc_now() + timedelta(seconds=seconds), maximum_output=4096,
                )

    def test_sigkill_of_process_owner_does_not_remove_independent_deadline(self):
        import subprocess

        marker = self.directory / "bounded-child.json"
        child_code = (
            "import json,os,subprocess,time;from pathlib import Path;"
            "p=subprocess.Popen(['/bin/sleep','30']);"
            f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),p.pid,os.getpgrp()]));"
            "time.sleep(30)"
        )
        program = (
            "import sys;from pathlib import Path;from datetime import timedelta;"
            f"sys.path.insert(0,{str(Path(broker.__file__).resolve().parents[2])!r});"
            "from scripts.workflow_pilot.git_broker_store import run_bounded,clean_environment;"
            "from scripts.workflow_pilot.signed_records import utc_now;"
            f"root=Path({str(self.directory)!r});"
            f"run_bounded(['/usr/bin/python3','-I','-c',{child_code!r}],cwd=root,"
            "environment=clean_environment(root),deadline=utc_now()+timedelta(seconds=1.5))"
        )
        process = subprocess.Popen(
            ["/usr/bin/python3", "-I", "-c", program], cwd=self.directory,
            env=store_module.clean_environment(self.directory), start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
        identities = None

        def running(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0] != "Z"
            except FileNotFoundError:
                return False

        try:
            end = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < end and process.poll() is None:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "bounded child did not start")
            identities = __import__("json").loads(marker.read_text())
            process.kill()
            process.wait(timeout=2)
            end = time.monotonic() + 4
            while any(running(pid) for pid in identities[:2]) and time.monotonic() < end:
                time.sleep(0.02)
            self.assertFalse(any(running(pid) for pid in identities[:2]))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            if identities and any(running(pid) for pid in identities[:2]):
                try:
                    os.killpg(identities[2], signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_same_uid_peer_and_unprotected_installation_are_not_authority(self):
        left, right = socket.socketpair()
        with left, right, self.assertRaises(RecordError):
            broker.peer_uid(left, os.geteuid())
        manifest = self.fixture.manifest(client=True)
        path = self.directory / "client.json"
        path.write_bytes(canonical_json(manifest))
        with self.assertRaises(RecordError):
            broker.BrokerClient(path)
        for path in (self.directory / "pipe", self.directory / "link"):
            if path.name == "pipe":
                os.mkfifo(path)
            else:
                path.symlink_to(self.directory / "client.json")
            with self.subTest(path=path.name), self.assertRaises((RecordError, OSError)):
                broker.read_regular(path, protocol.MAX_JSON)

    def test_protected_attack_sends_pack_and_never_counts_transport_errors_as_rejection(self):
        plan, pack, _current = self.fixture.make_plan()
        plan_path, pack_path = self.directory / "plan.json", self.directory / "objects.pack"
        plan_path.write_bytes(canonical_json(plan))
        pack_path.write_bytes(pack)
        arguments = SimpleNamespace(
            client_installation=self.directory / "client.json", client_action="attack",
            plan=plan_path, pack=pack_path,
        )
        consumer = object()
        for error in (TimeoutError, ConnectionResetError, OSError, RecordError):
            with self.subTest(error=error.__name__), mock.patch.object(
                broker, "BrokerClient", return_value=consumer,
            ), mock.patch.object(
                protected, "direct_authenticated_request", side_effect=error("synthetic failure"),
            ) as request:
                with self.assertRaises(error):
                    protected.client_action(arguments)
                request.assert_called_once_with(consumer, plan, pack)


class BrokerServiceTests(BrokerFixtureTests):
    """Real listener/process cleanup with synthetic installation ownership only."""

    def setUp(self):
        super().setUp()
        self.fixture.close()
        self.fixture.store = None
        self.process = None

    def tearDown(self):
        protected.stop_owned(self.process)
        if self.process is not None:
            self.process.communicate(timeout=3)
        if self.fixture.store is not None:
            self.fixture.close()
        shutil.rmtree(self.directory)

    def start(self, *, exchange=False, cleanup_error=False):
        manifest = self.fixture.manifest()
        # Short relative address avoids Linux's sun_path bound in long CI paths.
        # Only the unavailable protected installation/peer boundary is replaced.
        manifest.update(socket="broker.sock", socket_gid=os.getegid(), broker_uid=os.geteuid())
        script = (
            "import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0,{str(Path(broker.__file__).resolve().parents[2])!r})\n"
            "from scripts.workflow_pilot import git_broker as broker\n"
            f"manifest={manifest!r}\n"
            "broker.load_installation=lambda path,role:(manifest,broker.Policy.parse(manifest['policy']))\n"
        )
        if exchange:
            script += "broker.peer_uid=lambda connection,expected:None\n"
        if cleanup_error:
            script += (
                "unlink=Path.unlink\n"
                "def fail_unlink(path,*args,**kwargs):\n"
                "    if str(path)=='broker.sock': raise OSError('synthetic cleanup failure')\n"
                "    return unlink(path,*args,**kwargs)\n"
                "Path.unlink=fail_unlink\n"
            )
        script += "raise SystemExit(broker.main(['serve','--installation','synthetic.json']))\n"
        self.process = subprocess.Popen(
            ["/usr/bin/python3", "-I", "-c", script], cwd=self.directory,
            env=store_module.clean_environment(self.directory), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
        )
        protected.wait_for_socket(self.process, self.directory / "broker.sock")

    def stop(self, signum=signal.SIGTERM):
        self.process.send_signal(signum)
        output, errors = self.process.communicate(timeout=5)
        code = self.process.returncode
        self.process = None
        self.assertEqual(output, b"")
        return code, errors

    def reopen(self):
        self.fixture.store = store_module.PublicationStore(self.fixture.policy, self.fixture.state)
        return self.fixture.store

    @contextmanager
    def connection(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.settimeout(3)
            address = f"/proc/{self.process.pid}/cwd/broker.sock"
            end = time.monotonic() + 3
            while True:
                try:
                    raw.connect(address)
                    break
                except ConnectionRefusedError:
                    if time.monotonic() >= end:
                        raise
                    time.sleep(0.01)
            with broker.tls_context(self.fixture.manifest(client=True), False).wrap_socket(
                raw, server_hostname="workflow-pilot-git-broker",
            ) as connection:
                yield connection

    def begin_plan(self, connection, plan, pack=None):
        channel = broker.Channel(connection, utc_now() + timedelta(seconds=5))
        hello = protocol.validate_hello(
            strict_json(channel.read_frame(protocol.MAX_RESPONSE), protocol.MAX_RESPONSE),
            self.fixture.policy.deployment_id, self.keys.response_key, utc_now(),
        )
        channel.send_frame(canonical_json({
            "protocol": protocol.PROTOCOL, "session_nonce": hello["session_nonce"],
            "operation": "publish", "plan": plan,
        }), protocol.MAX_JSON)
        if pack is not None:
            channel.send_frame(pack, protocol.MAX_PACK)

    def await_status(self, plan, expected):
        end, actual = time.monotonic() + 5, None
        while time.monotonic() < end:
            with closing(sqlite3.connect(self.fixture.state / "nonces.sqlite3", timeout=1)) as journal:
                actual = journal.execute(
                    "SELECT status FROM operations WHERE nonce=?", (plan["nonce"],),
                ).fetchone()
            if actual == (expected,):
                return
            time.sleep(0.01)
        self.fail(f"request never reached {expected}: {actual}")

    def test_socket_is_coordinator_group_only_before_accept(self):
        self.start()
        endpoint = (self.directory / "broker.sock").lstat()
        self.assertTrue(stat.S_ISSOCK(endpoint.st_mode))
        self.assertEqual(stat.S_IMODE(endpoint.st_mode), 0o660)
        self.assertEqual(endpoint.st_gid, os.getegid())

    def test_sigterm_cleanly_unwinds_listener_and_journal(self):
        self.start()
        self.assertEqual(self.stop(), (0, b""))
        self.assertFalse((self.directory / "broker.sock").exists())
        self.assertEqual(self.reopen().db.execute("SELECT count(*) FROM operations").fetchone(), (0,))

    def test_sigint_remains_failure_and_unwinds_listener(self):
        self.start()
        code, errors = self.stop(signal.SIGINT)
        self.assertEqual(code, 2)
        self.assertTrue(errors)
        self.assertFalse((self.directory / "broker.sock").exists())
        self.reopen()

    def test_sigterm_does_not_swallow_cleanup_failure(self):
        self.start(cleanup_error=True)
        code, errors = self.stop()
        self.assertEqual(code, 2)
        self.assertTrue(errors)
        self.reopen()

    def test_sigterm_during_incomplete_pack_keeps_nonce_consumed(self):
        plan, _pack, _current = self.fixture.make_plan()
        self.start(exchange=True)
        with self.connection() as connection:
            self.begin_plan(connection, plan)
            self.await_status(plan, "reserved")
            self.assertEqual(self.stop(), (0, b""))
        store = self.reopen()
        self.assertEqual(store.db.execute("SELECT status FROM operations").fetchone(), ("rejected",))
        with self.assertRaises(RecordError):
            store.reserve(plan, self.keys.client_fingerprint)
        self.assertEqual(store.remote_refs(utc_now() + timedelta(seconds=3)), dict.fromkeys(self.fixture.policy.refs))
        self.assertFalse((self.directory / "broker.sock").exists())

    def test_sigterm_during_receive_pack_kills_children_and_preserves_uncertainty(self):
        marker = self.directory / "hook-pids"
        hook = self.fixture.remote / "hooks" / "pre-receive"
        hook.parent.mkdir(mode=0o700)
        hook.write_text(
            "#!/bin/sh\nsleep 30 &\nprintf '%s %s\\n' \"$$\" \"$!\" > "
            + shlex.quote(str(marker)) + "\nwait\n"
        )
        hook.chmod(0o700)
        plan, pack, _current = self.fixture.make_plan()
        self.start(exchange=True)
        with self.connection() as connection:
            self.begin_plan(connection, plan, pack)
            self.await_status(plan, "executing")
            end = time.monotonic() + 5
            identities = []
            while time.monotonic() < end:
                if marker.exists():
                    identities = [int(value) for value in marker.read_text().split()]
                    if len(identities) == 2:
                        break
                time.sleep(0.01)
            self.assertEqual(len(identities), 2, "real receive-pack hook never started")
            self.assertEqual(self.stop(), (0, b""))

        def running(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0] != "Z"
            except FileNotFoundError:
                return False

        end = time.monotonic() + 3
        while any(running(pid) for pid in identities) and time.monotonic() < end:
            time.sleep(0.01)
        self.assertFalse(any(running(pid) for pid in identities))
        store = self.reopen()
        self.assertEqual(
            store.db.execute("SELECT status,completed_at FROM operations").fetchone(), ("uncertain", None),
        )
        self.assertEqual(
            store.readback(plan, self.keys.client_fingerprint, utc_now() + timedelta(seconds=3))[0],
            "uncertain",
        )
        with self.assertRaises(RecordError):
            store.reserve(plan, self.keys.client_fingerprint)
        self.assertEqual(list(store.work.iterdir()), [])
        self.assertFalse((self.directory / "broker.sock").exists())

    def test_clean_stop_is_scoped_to_serve_and_does_not_mask_interruptions_or_errors(self):
        previous = signal.getsignal(signal.SIGTERM)
        mask = os.umask(0o077)
        try:
            with mock.patch.object(broker, "serve", side_effect=lambda *a, **k: os.kill(os.getpid(), signal.SIGTERM)):
                self.assertEqual(broker.main(["serve", "--installation", "synthetic.json"]), 0)
            self.assertIs(signal.getsignal(signal.SIGTERM), previous)
            for command, target, failure in (
                ("preflight-server", "load_installation", KeyboardInterrupt),
                ("serve", "serve", KeyboardInterrupt),
                ("serve", "serve", RecordError("synthetic service failure")),
            ):
                with self.subTest(command=command, failure=failure), mock.patch.object(
                    broker, target, side_effect=failure,
                ), mock.patch.object(broker.sys, "stderr", io.StringIO()):
                    self.assertEqual(broker.main([command, "--installation", "synthetic.json"]), 2)
                self.assertIs(signal.getsignal(signal.SIGTERM), previous)
        finally:
            os.umask(mask)


class PackBoundsTests(BrokerFixtureTests):
    """Real full Git packs at the store's object-loading boundary."""

    def delta_pack(self):
        def variable_size(size):
            encoded = bytearray()
            while size >= 128:
                encoded.append((size & 127) | 128)
                size >>= 7
            encoded.append(size)
            return bytes(encoded)

        def entry(kind, data, base=b""):
            size = len(data)
            header = bytearray([(kind << 4) | (size & 15)])
            size >>= 4
            while size:
                header[-1] |= 128
                header.append(size & 127)
                size >>= 7
            return bytes(header) + base + zlib.compress(data)

        base = bytes(range(256)) * 256
        base_oid = self.fixture.git(
            self.fixture.source, "hash-object", "-w", "--stdin", data=base,
        ).decode("ascii").strip()
        sizes = {base_oid: len(base)}
        entries = [entry(3, base)]
        for suffix in (b"a", b"b", b"c"):
            contents = base * 2 + suffix
            object_id = self.fixture.git(
                self.fixture.source, "hash-object", "-w", "--stdin", data=contents,
            ).decode("ascii").strip()
            sizes[object_id] = len(contents)
            # Each 0x80 copies 64 KiB at offset zero; the final instruction
            # inserts one byte. These REF_DELTA objects exceed their base size.
            delta = variable_size(len(base)) + variable_size(len(contents)) + b"\x80\x80\x01" + suffix
            entries.append(entry(7, delta, bytes.fromhex(base_oid)))
        tree_oid = self.fixture.git(
            self.fixture.source, "mktree",
            data=b"".join(
                f"100644 blob {object_id}\t{index}.bin\n".encode("ascii")
                for index, object_id in enumerate(sizes)
            ),
        ).decode("ascii").strip()
        commit_oid = self.fixture.git(
            self.fixture.source, "commit-tree", tree_oid, data=b"Resolved delta bounds fixture\n",
        ).decode("ascii").strip()
        for object_id, kind, code in ((tree_oid, "tree", 2), (commit_oid, "commit", 1)):
            contents = self.fixture.git(self.fixture.source, "cat-file", kind, object_id)
            sizes[object_id] = len(contents)
            entries.append(entry(code, contents))
        pack = b"PACK" + struct.pack(">II", 2, len(entries)) + b"".join(entries)
        pack += hashlib.sha1(pack).digest()
        result = self.fixture.git(self.fixture.source, "index-pack", "--stdin", "--strict", data=pack)
        index = self.fixture.source / "objects" / "pack" / f"pack-{result[5:-1].decode('ascii')}.idx"
        description = self.fixture.git(self.fixture.source, "verify-pack", "-v", str(index))
        reported, deltas = {}, []
        for line in description.decode("ascii").splitlines():
            parts = line.split()
            if parts and parts[0] in sizes:
                reported[parts[0]] = int(parts[2])
                if len(parts) == 7:
                    deltas.append(parts[0])
        self.assertEqual(set(reported), set(sizes))
        self.assertEqual(len(deltas), 3)
        self.assertTrue(all(reported[object_id] < sizes[object_id] for object_id in deltas))
        plan = {
            "pack": {
                "size": len(pack), "sha256": hashlib.sha256(pack).hexdigest(),
                "objects": sorted(sizes),
            },
            "updates": [{"new": commit_oid}],
        }
        return plan, pack, sizes, reported

    def test_delta_pack_accepts_resolved_size_boundaries(self):
        plan, pack, sizes, _reported = self.delta_pack()
        repository = self.fixture.store.work / "accepted.git"
        with mock.patch.object(store_module, "MAX_OBJECT", max(sizes.values())), mock.patch.object(
            store_module, "MAX_EXPANDED", sum(sizes.values()),
        ):
            self.fixture.store._load_pack(repository, pack, plan, utc_now() + timedelta(seconds=5))
        for object_id, size in sizes.items():
            self.assertEqual(
                int(self.fixture.git(repository, "cat-file", "-s", object_id)), size,
            )
        self.assert_absent()

    def test_delta_pack_rejects_individual_resolved_size_overflow(self):
        plan, pack, sizes, reported = self.delta_pack()
        limit = 96 * 1024
        self.assertLess(max(reported.values()), limit)
        self.assertGreater(max(sizes.values()), limit)
        with mock.patch.object(store_module, "MAX_OBJECT", limit), self.assertRaisesRegex(
            RecordError, "individual expanded object bound",
            msg=f"reported maximum {max(reported.values())}; resolved maximum {max(sizes.values())}",
        ):
            self.fixture.store._load_pack(
                self.fixture.store.work / "individual.git", pack, plan, utc_now() + timedelta(seconds=5),
            )
        self.assert_absent()

    def test_delta_pack_rejects_aggregate_resolved_size_overflow(self):
        plan, pack, sizes, reported = self.delta_pack()
        limit = 256 * 1024
        self.assertLess(sum(reported.values()), limit)
        self.assertGreater(sum(sizes.values()), limit)
        self.assertLess(max(sizes.values()), store_module.MAX_OBJECT)
        with mock.patch.object(store_module, "MAX_EXPANDED", limit), self.assertRaisesRegex(
            RecordError, "expanded",
            msg=f"reported total {sum(reported.values())}; resolved total {sum(sizes.values())}",
        ):
            self.fixture.store._load_pack(
                self.fixture.store.work / "aggregate.git", pack, plan, utc_now() + timedelta(seconds=5),
            )
        self.assert_absent()


class AuthenticatedChannelTests(BrokerFixtureTests):
    """Real TLS/protocol tests, explicitly not a same-UID deployment substitute."""

    def tls_pair(self, server_action, client_action, *, certificate="client"):
        left, right = socket.socketpair()
        errors = []
        server_manifest = self.fixture.manifest()
        client_manifest = self.fixture.manifest(client=True)
        client_manifest["certificate"] = str(self.keys.root / (certificate + ".crt"))
        client_manifest["private_key"] = str(self.keys.root / (certificate + ".key"))

        def run():
            try:
                with left:
                    left.settimeout(5)
                    with broker.tls_context(server_manifest, True).wrap_socket(left, server_side=True) as connection:
                        server_action(connection, server_manifest)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            with right:
                right.settimeout(5)
                with broker.tls_context(client_manifest, False).wrap_socket(
                    right, server_hostname="workflow-pilot-git-broker",
                ) as connection:
                    self.assertEqual(
                        hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest(),
                        self.keys.server_fingerprint,
                    )
                    return client_action(connection, client_manifest)
        finally:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "bounded protocol thread did not exit")
            # A new connection uses the same durable journal. SQLite normally
            # belongs to the single service thread; these tests create it there.
            self.channel_errors = errors

    def protocol_store(self):
        self.fixture.store.close()
        # Construction must happen in the service thread for SQLite ownership.
        self.fixture.store = None

    def tearDown(self):
        if self.fixture.store is not None:
            self.fixture.close()
        shutil.rmtree(self.directory)

    def exchange(self, connection, manifest):
        store = store_module.PublicationStore(self.fixture.policy, self.fixture.state)
        try:
            broker.exchange(connection, store, manifest)
        finally:
            store.close()

    def client(self, connection, manifest, plan, pack, readback=False):
        # The transport-independent production consumer is invoked after a real
        # TLS handshake; only the unavailable distinct-UID installation is not
        # claimed by this deterministic test.
        client = object.__new__(broker.BrokerClient)
        client.manifest, client.policy = manifest, self.fixture.policy
        return client._request_authenticated(connection, plan, pack, readback=readback)

    def fixture_submit(self, action, plan, pack):
        def request(connection, manifest):
            if action == "publish":
                return self.client(connection, manifest, plan, pack)
            self.assertEqual(action, "attack")
            client = object.__new__(broker.BrokerClient)
            client.manifest, client.policy = manifest, self.fixture.policy
            return protected.direct_authenticated_exchange(client, connection, plan, pack)
        return self.tls_pair(self.exchange, request)

    def test_protected_fixture_observes_each_fresh_signed_adversary_and_actual_replay(self):
        self.fixture.bootstrap()
        self.protocol_store()
        events, nonces = [], set()
        with protected.observe_validation(events.append):
            for kind in protected.PLAN_ATTACKS:
                with self.subTest(kind=kind):
                    plan, pack = protected.adversarial_plan(self.fixture, kind)
                    self.assertNotIn(plan["nonce"], nonces)
                    nonces.add(plan["nonce"])
                    self.assertEqual(len(pack), plan["pack"]["size"])
                    self.assertEqual(hashlib.sha256(pack).hexdigest(), plan["pack"]["sha256"])
                    verify_signature(
                        self.fixture.policy.signing_key,
                        signed_payload(protocol.PLAN_DOMAIN, plan), plan["signature"],
                    )
                    protected.check_rejection(
                        self.fixture, self.fixture_submit, lambda: events, plan, pack,
                    )
            valid, pack, current = self.fixture.make_plan()
            response = self.fixture_submit("publish", valid, pack)
            self.assertEqual(response["status"], "published")
            self.assertEqual(response["refs"], protocol.expected_refs(valid, "new"))
            self.fixture.current = current
            protected.check_rejection(
                self.fixture, self.fixture_submit, lambda: events, valid, pack, replay=True,
            )

    def test_protected_fixture_detects_each_skipped_plan_field_check(self):
        real_validate = store_module.validate_plan
        self.fixture.close()
        for kind in protected.PLAN_ATTACKS:
            with self.subTest(kind=kind):
                self.fixture = Fixture(self.directory / kind, self.keys)
                plan, pack = protected.adversarial_plan(self.fixture, kind)
                self.protocol_store()
                events = []

                def skip_field(submitted, policy, peer, now):
                    # Keep every other check, including the original signature.
                    # Only the selected semantic check sees a repaired control.
                    verify_signature(
                        policy.signing_key, signed_payload(protocol.PLAN_DOMAIN, submitted),
                        submitted["signature"],
                    )
                    control = copy.deepcopy(submitted)
                    if kind in ("expired", "future"):
                        now = parse_utc(control["issued_at"])
                    elif kind in ("issue", "endpoint"):
                        control[kind] = getattr(policy, kind)
                    else:
                        control["updates"][0]["ref"] = policy.refs[0]
                    self.keys.sign(protocol.PLAN_DOMAIN, control)
                    real_validate(control, policy, peer, now)
                    return submitted

                with mock.patch.object(store_module, "validate_plan", side_effect=skip_field), \
                     protected.observe_validation(events.append):
                    with self.assertRaisesRegex(RecordError, "observed broker validation rejection"):
                        protected.check_rejection(
                            self.fixture, self.fixture_submit, lambda: events, plan, pack,
                        )
                self.assertEqual(events[0], {
                    "request_digest": protocol.plan_digest(plan), "stage": "plan", "outcome": "passed",
                })
                self.assertEqual(events[1]["outcome"], "passed")

    def test_protected_fixture_refuses_timeout_disconnect_response_and_missing_trace_evidence(self):
        plan, pack = protected.adversarial_plan(self.fixture, "issue")
        before = protected.journal_snapshot(self.fixture.state)
        self.protocol_store()
        events = []
        with protected.observe_validation(events.append):
            protected.check_rejection(self.fixture, self.fixture_submit, lambda: events, plan, pack)
        after = protected.journal_snapshot(self.fixture.state)
        wrong_digest = [{**event, "request_digest": "0" * 64} for event in events]
        for result, observed in (
            ({"transport": "timeout"}, events), ({"transport": "closed"}, []),
            ({"transport": "disconnected"}, []), ({"transport": "closed"}, wrong_digest),
            ({"transport": "response", "status": "rejected"}, events),
            ({"transport": "response", "status": "published"}, events),
        ):
            with self.subTest(result=result, trace=observed), self.assertRaises(RecordError):
                protected.require_validation_rejection(plan, result, observed, before, after)

    def test_protected_fixture_refuses_reused_nonce_for_nonreplay_case(self):
        consumed = self.fixture.bootstrap()
        plan, pack = protected.adversarial_plan(self.fixture, "issue")
        plan["nonce"] = consumed["nonce"]
        self.keys.sign(protocol.PLAN_DOMAIN, plan)
        self.protocol_store()
        events = []
        with protected.observe_validation(events.append), self.assertRaisesRegex(
            RecordError, "non-replay attack did not use a fresh nonce",
        ):
            protected.check_rejection(self.fixture, self.fixture_submit, lambda: events, plan, pack)

    def test_protected_hook_rejection_is_not_validation_evidence_and_cleanup_restores_publication(self):
        self.fixture.bootstrap()
        hook = self.fixture.remote / "hooks" / "pre-receive"
        hook.parent.mkdir()
        marker = self.directory / "protected-hook-ran"
        hook.write_text(f"#!/bin/sh\nprintf authoritative > '{marker}'\nexit 1\n")
        hook.chmod(0o700)
        plan, pack, _current = self.fixture.make_plan()
        self.protocol_store()
        events = []
        try:
            with protected.observe_validation(events.append):
                with self.assertRaisesRegex(RecordError, "observed broker validation rejection"):
                    protected.check_rejection(
                        self.fixture, self.fixture_submit, lambda: events, plan, pack,
                    )
                self.assertEqual(self.channel_errors, [])
                self.assertEqual(marker.read_text(), "authoritative")
                self.assertEqual(events[0]["outcome"], "passed")
                protected.check_hook_rejection(self.fixture, self.fixture_submit, hook)
            self.assertFalse(hook.exists())
            expected = dict(zip(self.fixture.policy.refs, self.fixture.current[2:4]))
            self.assertEqual(
                self.fixture.git(
                    self.fixture.remote, "for-each-ref", "--format=%(refname) %(objectname)",
                ).decode().splitlines(),
                [f"{ref} {expected[ref]}" for ref in sorted(expected)],
            )
        finally:
            hook.unlink(missing_ok=True)

    def test_production_exchange_authenticates_plan_result_and_readback(self):
        plan, pack, _current = self.fixture.make_plan()
        self.protocol_store()
        response = self.tls_pair(
            self.exchange, lambda connection, manifest: self.client(connection, manifest, plan, pack),
        )
        self.assertEqual(self.channel_errors, [])
        self.assertEqual(response["status"], "published")
        self.assertEqual(response["refs"], protocol.expected_refs(plan, "new"))
        for secret in (
            b"PRIVATE KEY", (self.keys.root / "server.key").read_bytes(),
            (self.keys.root / "client.key").read_bytes(),
        ):
            self.assertNotIn(secret, canonical_json(response))
        result = self.tls_pair(
            self.exchange,
            lambda connection, manifest: self.client(connection, manifest, plan, None, readback=True),
        )
        self.assertEqual(self.channel_errors, [])
        self.assertEqual(result["status"], "published")
        self.assertNotEqual(response["session_nonce"], result["session_nonce"])
        self.assertEqual(result["request_digest"], response["request_digest"])

    def test_valid_ca_but_wrong_coordinator_certificate_rejects(self):
        self.protocol_store()
        with self.assertRaises((RecordError, ssl.SSLError, OSError)):
            self.tls_pair(
                self.exchange,
                lambda connection, manifest: broker.Channel(
                    connection, utc_now() + timedelta(seconds=2),
                ).read_frame(protocol.MAX_RESPONSE),
                certificate="other",
            )
        self.assertTrue(any(isinstance(error, RecordError) for error in self.channel_errors))

    def test_disconnect_after_reservation_consumes_capability(self):
        plan, _pack, _current = self.fixture.make_plan()
        self.protocol_store()

        def abandon(connection, _manifest):
            channel = broker.Channel(connection, utc_now() + timedelta(seconds=3))
            hello = strict_json(channel.read_frame(protocol.MAX_RESPONSE), protocol.MAX_RESPONSE)
            channel.send_frame(canonical_json({
                "protocol": protocol.PROTOCOL, "session_nonce": hello["session_nonce"],
                "operation": "publish", "plan": plan,
            }), protocol.MAX_JSON)

        self.tls_pair(self.exchange, abandon)
        store = store_module.PublicationStore(self.fixture.policy, self.fixture.state)
        try:
            with self.assertRaises(RecordError):
                store.reserve(plan, self.keys.client_fingerprint)
            self.assertEqual(store.remote_refs(utc_now() + timedelta(seconds=3)), dict.fromkeys(self.fixture.policy.refs))
        finally:
            store.close()

    def test_signed_broker_response_cannot_be_copied_forged_or_extended(self):
        plan, pack, _current = self.fixture.make_plan()
        self.protocol_store()
        captured = {}

        def client(connection, manifest):
            channel = broker.Channel(connection, utc_now() + timedelta(seconds=3))
            hello = strict_json(channel.read_frame(protocol.MAX_RESPONSE), protocol.MAX_RESPONSE)
            channel.send_frame(canonical_json({
                "protocol": protocol.PROTOCOL, "session_nonce": hello["session_nonce"],
                "operation": "publish", "plan": plan,
            }), protocol.MAX_JSON)
            channel.send_frame(pack, protocol.MAX_PACK)
            captured["hello"] = hello
            return strict_json(channel.read_frame(protocol.MAX_RESPONSE), protocol.MAX_RESPONSE)

        response = self.tls_pair(self.exchange, client)
        self.assertEqual(response["status"], "published")
        mutations = (
            {"request_digest": "a" * 64}, {"session_nonce": "b" * 64},
            {"deadline": format_utc(utc_now() + timedelta(hours=1))},
            {"refs": dict.fromkeys(self.fixture.policy.refs, "e" * 40)},
            {"completed_at": format_utc(parse_utc(plan["expires_at"]) + timedelta(seconds=1))},
        )
        for changes in mutations:
            changed = {**response, **changes}
            # Even a properly signed response cannot extend the request or
            # certify different refs; the pinned client checks both.
            self.keys.sign(protocol.RESPONSE_DOMAIN, changed, name="server")
            with self.subTest(changes=changes), self.assertRaises(RecordError):
                protocol.validate_response(
                    changed, self.fixture.policy, self.keys.response_key, plan,
                    captured["hello"], utc_now(),
                )
        changed = dict(response)
        self.keys.sign(protocol.RESPONSE_DOMAIN, changed, name="other")
        with self.assertRaises(RecordError):
            protocol.validate_response(
                changed, self.fixture.policy, self.keys.response_key, plan, captured["hello"], utc_now(),
            )


if __name__ == "__main__":
    unittest.main()
