import base64
import copy
import hashlib
import os
import shutil
import signal
import socket
import ssl
import struct
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import jsonschema

from scripts.workflow_pilot import git_broker as broker
from scripts.workflow_pilot import git_broker_protocol as protocol
from scripts.workflow_pilot import git_broker_store as store_module
from scripts.workflow_pilot.signed_records import (
    RecordError, canonical_json, format_utc, parse_utc, signed_payload, strict_json, utc_now,
    verify_signature,
)
from scripts.workflow_pilot.tests.broker_test_support import Fixture, Keys, artifact_directory


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

    def test_plan_schema_accepts_real_signed_publication(self):
        plan, _pack, _current = self.fixture.make_plan()
        schema = __import__("json").loads(Path(protocol.__file__).with_name("git_broker.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(plan)

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
