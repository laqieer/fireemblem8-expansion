"""Synthetic GitHub identity responses, never credentialed deployment evidence."""

import base64
import copy
import fcntl
import hashlib
import io
import os
import shlex
import socket
import ssl
import threading
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import git_broker as broker
from scripts.workflow_pilot import git_broker_store as store_module
from scripts.workflow_pilot.signed_records import RecordError, canonical_json, format_utc, utc_now
from scripts.workflow_pilot.tests.broker_test_support import command
from scripts.workflow_pilot.tests.test_git_broker import BrokerFixtureTests


class IdentityResponse:
    def __init__(self, raw, *, status=200, headers=None):
        self.status = status
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.body = io.BytesIO(raw)

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size):
        return self.body.read(size)


class CredentialTests(BrokerFixtureTests):
    def setUp(self):
        super().setUp()
        self.token = b"github_pat_SYNTHETIC_fixture_not_a_real_credential"
        self.token_file = self.directory / "token"
        self.token_file.write_bytes(self.token + b"\n")
        self.token_file.chmod(0o600)
        for name in ("key", "wrong-key"):
            command([
                "/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                "-C", "synthetic-only", "-f", str(self.directory / name),
            ], self.directory)
        self.key_file = self.directory / "key"
        self.hosts_file = self.directory / "known_hosts"
        self.hosts_file.write_bytes(b"github.com " + self.key_file.with_suffix(".pub").read_bytes())
        self.hosts_file.chmod(0o600)
        public = base64.b64decode(self.key_file.with_suffix(".pub").read_bytes().split()[1])
        self.fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(public).digest()).decode().rstrip("=")
        self.user = {"id": 7, "type": "User", "login": "fixture-user"}
        self.greeting = b"Hi fixture-user! You've successfully authenticated, but GitHub does not provide shell access.\n"
        self.api_requests, self.ssh_probes, self.remote_calls = [], [], []
        self.worker_output = None
        self.after_validation = lambda: None
        self.use_transport("https")

    def use_transport(self, kind):
        endpoint = (
            f"https://github.com/{self.fixture.policy.repository}.git"
            if kind == "https" else f"ssh://git@github.com/{self.fixture.policy.repository}.git"
        )
        self.fixture.policy = replace(self.fixture.policy, endpoint=endpoint)
        self.fixture.store.policy = self.fixture.policy
        self.manifest = self.fixture.manifest()
        self.manifest["broker_uid"] = os.geteuid()
        self.manifest["transport"] = (
            {
                "kind": "https", "credential_kind": "github-fine-grained-user-pat",
                "token_file": str(self.token_file), "helper": str(Path(broker.__file__).resolve()),
            }
            if kind == "https" else {
                "kind": "ssh", "credential_kind": "github-user-ed25519",
                "key": str(self.key_file), "known_hosts": str(self.hosts_file),
                "public_key_fingerprint": self.fingerprint,
            }
        )
        self.fixture.store.transport = self.manifest["transport"]
        self.fixture.store.installation = self.directory / "installation.json"

    @contextmanager
    def identity_fixture(self, response=None):
        """Substitute only OS installation and remote observations, not validators."""
        response = response or IdentityResponse(canonical_json(self.user))
        real_run, real_read = store_module.run_bounded, broker.read_regular

        def https_connection(host, port, *, timeout, context):
            self.assertEqual((host, port), ("api.github.com", 443))
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 5)
            connection = mock.Mock()
            connection.getresponse.return_value = response
            connection.request.side_effect = lambda *args, **kwargs: self.api_requests.append((args, kwargs))
            return connection

        def fixture_read(path, maximum, **kwargs):
            # This fixture is deliberately not the root-owned deployment test.
            if path == self.hosts_file:
                kwargs["owners"] = {os.geteuid()}
            return real_read(path, maximum, **kwargs)

        def worker(arguments, **kwargs):
            if arguments[0] == "/usr/bin/ssh":
                self.ssh_probes.append(arguments)
                self.assertEqual(kwargs["allowed_codes"], (1,))
                self.assertTrue(kwargs["capture_stderr"])
                return self.greeting
            if arguments[-1] != "credential-check":
                return real_run(arguments, **kwargs)
            self.assertEqual(arguments[:2], ["/usr/bin/python3", "-I"])
            self.assertNotIn(self.token, repr(arguments).encode())
            self.assertNotIn(self.token, repr(kwargs["environment"]).encode())
            output = io.BytesIO()
            errors = io.StringIO()
            with mock.patch.dict(os.environ, kwargs["environment"], clear=True), mock.patch.object(
                broker.sys, "stdout", mock.Mock(buffer=output),
            ), mock.patch.object(broker.sys, "stderr", errors):
                mask = os.umask(0o077)
                try:
                    result = broker.main(["credential-check"])
                finally:
                    os.umask(mask)
            self.assertNotIn(self.token, errors.getvalue().encode())
            self.assertNotIn(self.key_file.read_bytes(), errors.getvalue().encode())
            if result:
                raise RecordError("synthetic identity worker rejected")
            self.after_validation()
            return output.getvalue() if self.worker_output is None else self.worker_output

        def remote_git(arguments, **kwargs):
            environment = kwargs["environment"]
            self.remote_calls.append((arguments, environment.copy()))
            self.assertNotIn(self.token, repr(arguments).encode())
            self.assertNotIn(self.token, repr(environment).encode())
            self.assertNotIn(self.key_file.read_bytes(), repr(environment).encode())
            return b""

        with mock.patch.object(broker, "load_installation", return_value=(self.manifest, self.fixture.policy)), \
             mock.patch.object(broker, "read_regular", side_effect=fixture_read), \
             mock.patch.object(broker.http.client, "HTTPSConnection", side_effect=https_connection), \
             mock.patch.object(broker, "run_bounded", side_effect=worker), \
             mock.patch.object(store_module, "run_bounded", side_effect=remote_git):
            yield

    def remote_refs(self):
        return self.fixture.store.remote_refs(utc_now() + timedelta(seconds=5))

    def test_https_identity_is_checked_before_every_remote_git_operation(self):
        with self.identity_fixture():
            self.assertEqual(self.remote_refs(), dict.fromkeys(self.fixture.policy.refs))
        self.assertEqual(len(self.remote_calls), 1)
        self.assertEqual(self.api_requests[0][0], ("GET", "/user"))
        self.assertEqual(self.api_requests[0][1]["headers"]["Authorization"], "Bearer " + self.token.decode())
        self.user["id"] = 8
        with self.identity_fixture(), self.assertRaises(RecordError):
            self.remote_refs()
        self.assertEqual(len(self.remote_calls), 1)

    def test_wrong_user_bot_missing_and_boolean_principals_never_reach_remote_git(self):
        for identity in (
            {"id": 8}, {"id": True}, {"type": "Bot"}, {"type": "Organization"},
            {"id": None}, {"login": None}, {"login": "owner/repository"},
        ):
            with self.subTest(identity=identity):
                response = IdentityResponse(canonical_json({**self.user, **identity}))
                with self.identity_fixture(response), self.assertRaises(RecordError):
                    self.remote_refs()
        self.assertEqual(self.remote_calls, [])

    def test_unsupported_credential_kinds_and_caller_authority_reject(self):
        for kind in ("https", "ssh"):
            self.use_transport(kind)
            original = copy.deepcopy(self.manifest["transport"])
            for changes in (
                {"credential_kind": "github-app-installation"},
                {"credential_kind": "github-deploy-key"},
                {"credential_kind": "github-classic-pat"},
                {"api_url": "https://attacker.invalid/user"},
                {"ca_certificate": str(self.keys.root / "ca.crt")},
                {"actor_id": 8},
            ):
                self.manifest["transport"] = {**original, **changes}
                self.fixture.store.transport = self.manifest["transport"]
                with self.subTest(kind=kind, changes=changes), self.identity_fixture(), self.assertRaises(RecordError):
                    self.remote_refs()
            self.manifest["transport"] = {key: value for key, value in original.items() if key != "credential_kind"}
            self.fixture.store.transport = self.manifest["transport"]
            with self.identity_fixture(), self.assertRaises(RecordError):
                self.remote_refs()
        self.assertEqual(self.remote_calls, [])
        self.assertEqual(self.api_requests, [])
        self.assertEqual(self.ssh_probes, [])

    def test_token_prefix_or_file_protection_is_not_identity_evidence(self):
        for token in (
            b"ghp_classic", b"ghs_installation", b"ghu_app_user", b"gho_oauth", b"",
            b"github_pat_newline\n\n", b"github_pat_crlf\r\n", b"github_pat_bad\0value",
        ):
            self.token_file.write_bytes(token)
            with self.subTest(token=token), self.identity_fixture(), self.assertRaises(RecordError):
                self.remote_refs()
        self.assertEqual(self.api_requests, [])
        self.assertEqual(self.remote_calls, [])
        self.token_file.write_bytes(self.token)
        self.token_file.chmod(0o644)
        with self.identity_fixture(), self.assertRaises(RecordError):
            self.remote_refs()

    def test_https_helper_uses_verified_snapshot_after_original_file_replacement(self):
        original = self.token_file.read_bytes()
        snapshot = []

        def substitute():
            self.token_file.unlink()
            self.token_file.write_bytes(b"github_pat_DIFFERENT_principal")
            self.token_file.chmod(0o600)

        self.after_validation = substitute
        with self.identity_fixture():
            def consume(arguments, **kwargs):
                environment = kwargs["environment"]
                snapshot.append(environment["FE8_BROKER_CREDENTIAL"])
                self.assertEqual(broker.read_snapshot(snapshot[0], broker.TOKEN_MAX), original)
                self.assertNotIn(self.token, repr(arguments).encode())
                self.assertNotIn(self.token, repr(environment).encode())
                output = io.BytesIO()
                request = f"protocol=https\nhost=github.com\npath={self.fixture.policy.repository}.git\n\n".encode()
                with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
                    broker.sys, "stdin", mock.Mock(buffer=io.BytesIO(request)),
                ), mock.patch.object(broker.sys, "stdout", mock.Mock(buffer=output)):
                    broker.credential_helper("get")
                self.assertEqual(output.getvalue(), b"username=x-access-token\npassword=" + self.token + b"\n\n")
                return b""
            with mock.patch.object(store_module, "run_bounded", side_effect=consume):
                self.remote_refs()
        self.assertEqual(self.api_requests[0][1]["headers"]["Authorization"], "Bearer " + self.token.decode())
        with self.assertRaises(OSError):
            broker.read_snapshot(snapshot[0], broker.TOKEN_MAX)

    def test_real_git_credential_helper_child_reads_the_same_sealed_snapshot(self):
        real_run = store_module.run_bounded
        manifest = self.directory / "synthetic-helper.json"
        manifest.write_bytes(canonical_json(self.manifest))
        program = (
            "import json,sys;from pathlib import Path;"
            f"sys.path.insert(0,{str(Path(broker.__file__).resolve().parents[2])!r});"
            "from scripts.workflow_pilot import git_broker as broker;"
            f"manifest=json.loads(Path({str(manifest)!r}).read_text());"
            "broker.load_installation=lambda path,role:(manifest,broker.Policy.parse(manifest['policy']));"
            "raise SystemExit(broker.main(['credential',sys.argv[1]]))"
        )
        self.after_validation = lambda: self.token_file.write_bytes(b"github_pat_REPLACED")
        deadline = utc_now() + timedelta(seconds=5)
        with self.identity_fixture(), broker.verified_credentials(
            self.fixture.store.installation, self.fixture.policy, self.manifest["transport"],
            self.fixture.state, deadline,
        ) as environment:
            output = real_run([
                "/usr/bin/git", "-c", "credential.helper=",
                "-c", "credential.helper=!/usr/bin/python3 -I -c " + shlex.quote(program),
                "-c", "credential.useHttpPath=true", "credential", "fill",
            ], cwd=self.directory,
                environment={**store_module.clean_environment(self.directory), **environment},
                input_bytes=f"protocol=https\nhost=github.com\npath={self.fixture.policy.repository}.git\n\n".encode(),
                deadline=deadline,
            )
        fields = dict(line.split(b"=", 1) for line in output.splitlines())
        self.assertEqual(fields[b"password"], self.token)
        self.assertEqual(fields[b"username"], b"x-access-token")

    def test_helper_rejects_other_hosts_paths_usernames_and_duplicate_fields(self):
        with self.identity_fixture(), broker.verified_credentials(
            self.fixture.store.installation, self.fixture.policy, self.manifest["transport"],
            self.fixture.state, utc_now() + timedelta(seconds=5),
        ) as environment:
            valid = f"protocol=https\nhost=github.com\npath={self.fixture.policy.repository}.git\n".encode()
            for request in (
                valid.replace(b"https", b"http"), valid.replace(b"github.com", b"attacker.invalid"),
                valid.replace(self.fixture.policy.repository.encode(), b"other/repository"),
                valid + b"username=another-user\n", valid + b"host=github.com\n", b"x" * 4097,
            ):
                output = io.BytesIO()
                with self.subTest(request=request[:100]), mock.patch.dict(os.environ, environment, clear=True), \
                     mock.patch.object(broker.sys, "stdin", mock.Mock(buffer=io.BytesIO(request))), \
                     mock.patch.object(broker.sys, "stdout", mock.Mock(buffer=output)), self.assertRaises(RecordError):
                    broker.credential_helper("get")
                self.assertEqual(output.getvalue(), b"")

    def test_ssh_checks_real_derived_fingerprint_and_authenticated_user_mapping(self):
        self.use_transport("ssh")
        with self.identity_fixture():
            self.remote_refs()
        self.assertEqual(len(self.ssh_probes), 1)
        self.assertEqual(len(self.remote_calls), 1)
        self.assertEqual(self.api_requests[0][0], ("GET", "/users/fixture-user"))
        self.assertNotIn("Authorization", self.api_requests[0][1]["headers"])

    def test_wrong_ssh_key_rejects_before_authentication_or_remote_git(self):
        self.use_transport("ssh")
        self.key_file.write_bytes((self.directory / "wrong-key").read_bytes())
        with self.identity_fixture(), self.assertRaises(RecordError):
            self.remote_refs()
        self.assertEqual(self.ssh_probes, [])
        self.assertEqual(self.api_requests, [])
        self.assertEqual(self.remote_calls, [])

    def test_rsa_encrypted_and_nonprivate_ssh_keys_are_not_supported(self):
        self.use_transport("ssh")
        original = self.key_file.read_bytes()
        for raw in (
            (self.keys.root / "server.key").read_bytes(),
            self.key_file.with_suffix(".pub").read_bytes(),
        ):
            self.key_file.write_bytes(raw)
            with self.identity_fixture(), self.assertRaises(RecordError):
                self.remote_refs()
        self.key_file.write_bytes(original)
        command([
            "/usr/bin/ssh-keygen", "-p", "-P", "", "-N", "synthetic-passphrase",
            "-f", str(self.key_file),
        ], self.directory)
        with self.identity_fixture(), self.assertRaises(RecordError):
            self.remote_refs()
        self.assertEqual(self.remote_calls, [])
        self.assertEqual(self.ssh_probes, [])
        self.assertEqual(self.api_requests, [])

    def test_ssh_deploy_key_greeting_and_wrong_numeric_user_reject(self):
        self.use_transport("ssh")
        for greeting, user in (
            (self.greeting.replace(b"fixture-user!", b"owner/repository!"), self.user),
            (self.greeting + b"extra untrusted output\n", self.user),
            (self.greeting, {**self.user, "id": 8}),
            (self.greeting, {**self.user, "type": "Bot"}),
            (self.greeting, {**self.user, "login": "different-user"}),
        ):
            self.greeting = greeting
            with self.subTest(greeting=greeting, user=user), self.identity_fixture(
                IdentityResponse(canonical_json(user)),
            ), self.assertRaises(RecordError):
                self.remote_refs()
        self.assertEqual(self.remote_calls, [])

    def test_ssh_git_uses_the_verified_key_and_host_snapshots_after_replacement(self):
        self.use_transport("ssh")
        original_key, original_hosts = self.key_file.read_bytes(), self.hosts_file.read_bytes()
        snapshots = []

        def substitute():
            self.key_file.write_bytes((self.directory / "wrong-key").read_bytes())
            self.hosts_file.write_bytes(b"github.com DIFFERENT_untrusted_host\n")

        self.after_validation = substitute
        with self.identity_fixture():
            def consume(arguments, **kwargs):
                environment = kwargs["environment"]
                snapshots.extend(environment[name] for name in ("FE8_BROKER_CREDENTIAL", "FE8_BROKER_KNOWN_HOSTS"))
                self.assertEqual(broker.read_snapshot(snapshots[0], broker.KEY_MAX), original_key)
                self.assertEqual(broker.read_snapshot(snapshots[1], broker.HOSTS_MAX), original_hosts)
                ssh = shlex.split(environment["GIT_SSH_COMMAND"])
                self.assertEqual(ssh[ssh.index("-i") + 1], snapshots[0])
                self.assertIn("UserKnownHostsFile=" + snapshots[1], ssh)
                self.assertEqual(self.ssh_probes[0][self.ssh_probes[0].index("-i") + 1], snapshots[0])
                self.assertNotIn(original_key, repr((arguments, environment)).encode())
                return b""
            with mock.patch.object(store_module, "run_bounded", side_effect=consume):
                self.remote_refs()
        for snapshot in snapshots:
            with self.assertRaises(OSError):
                broker.read_snapshot(snapshot, broker.HOSTS_MAX)

    def test_sealed_snapshot_rejects_mutation_unsealed_handles_and_regular_files(self):
        with broker.sealed_snapshot(self.token) as path:
            self.assertEqual(broker.read_snapshot(path, broker.TOKEN_MAX), self.token)
            descriptor = os.open(path, os.O_RDWR)
            try:
                for operation in (
                    lambda: os.write(descriptor, b"substitute"),
                    lambda: os.ftruncate(descriptor, 1),
                    lambda: fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE),
                ):
                    with self.assertRaises(OSError):
                        operation()
            finally:
                os.close(descriptor)
        for descriptor, unsealed in (
            (os.open(self.token_file, os.O_RDONLY), False),
            (os.memfd_create("unsealed-fixture", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING), True),
        ):
            try:
                if unsealed:
                    os.fchmod(descriptor, 0o600)
                    os.write(descriptor, self.token)
                with self.assertRaises(RecordError):
                    broker.read_snapshot(f"/proc/{os.getpid()}/fd/{descriptor}", broker.TOKEN_MAX)
            finally:
                os.close(descriptor)

    def test_real_ssh_configuration_has_no_agent_certificate_host_or_password_fallback(self):
        with broker.sealed_snapshot(self.key_file.read_bytes()) as key, broker.sealed_snapshot(
            self.hosts_file.read_bytes(),
        ) as hosts:
            output = store_module.run_bounded(
                [*broker.ssh_arguments(key, hosts), "-G", "git@github.com"],
                cwd=self.directory, environment=store_module.clean_environment(self.directory),
                deadline=utc_now() + timedelta(seconds=3),
            ).decode()
            config = {}
            for line in output.splitlines():
                name, value = line.split(" ", 1)
                config.setdefault(name, []).append(value)
            for name, value in {
                "hostname": "github.com", "hostkeyalias": "github.com", "port": "22",
                "identityfile": key, "userknownhostsfile": hosts, "globalknownhostsfile": "/dev/null",
                "identitiesonly": "yes", "identityagent": "none", "certificatefile": "none",
                "pubkeyacceptedalgorithms": "ssh-ed25519", "stricthostkeychecking": "true",
                "batchmode": "yes", "passwordauthentication": "no", "kbdinteractiveauthentication": "no",
                "verifyhostkeydns": "false", "updatehostkeys": "false", "forwardagent": "no",
                "preferredauthentications": "publickey",
            }.items():
                self.assertEqual(config[name], [value])
            self.assertNotIn("proxycommand", config)

    def test_api_failures_bounds_and_duplicate_identity_fields_fail_closed(self):
        for response in (
            IdentityResponse(canonical_json(self.user), status=302, headers={"Location": "https://attacker.invalid"}),
            IdentityResponse(canonical_json(self.user), status=401),
            IdentityResponse(canonical_json(self.user), headers={"Content-Encoding": "gzip"}),
            IdentityResponse(canonical_json(self.user), headers={"Content-Type": "text/html"}),
            IdentityResponse(canonical_json(self.user), headers={"Content-Length": str(broker.IDENTITY_MAX + 1)}),
            IdentityResponse(canonical_json(self.user), headers={"Content-Length": "1, 2"}),
            IdentityResponse(b"x" * (broker.IDENTITY_MAX + 1)),
            IdentityResponse(b'{"id":8,"id":7,"type":"User","login":"fixture-user"}'),
            IdentityResponse(b'{"id":NaN,"type":"User","login":"fixture-user"}'),
            IdentityResponse(b"\xff"),
            IdentityResponse(b"[]"),
        ):
            with self.subTest(response=response.body.getvalue()[:80]), self.identity_fixture(response), self.assertRaises(RecordError):
                self.remote_refs()
        self.assertEqual(self.remote_calls, [])
        self.assertEqual(len(self.api_requests), 11)

    def test_changed_installation_expired_worker_and_unrecognized_success_reject(self):
        with self.identity_fixture():
            changed = replace(self.fixture.policy, actor_id=8)
            with self.assertRaises(RecordError):
                with broker.verified_credentials(
                    self.fixture.store.installation, changed, self.manifest["transport"],
                    self.fixture.state, utc_now() + timedelta(seconds=3),
                ):
                    self.fail("changed policy accepted")
            with mock.patch.dict(os.environ, {
                "FE8_BROKER_INSTALLATION": str(self.fixture.store.installation),
                "FE8_BROKER_CREDENTIAL_DEADLINE": format_utc(utc_now() - timedelta(seconds=1)),
            }), self.assertRaises(RecordError):
                broker.credential_worker_context()
        self.worker_output = b"other result\n"
        with self.identity_fixture(), self.assertRaises(RecordError):
            self.remote_refs()
        self.assertEqual(self.remote_calls, [])

    def test_identity_tls_rejects_fixture_ca_even_with_poisoned_environment(self):
        errors = []
        real_connect = socket.create_connection
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.keys.root / "server.crt", self.keys.root / "server.key")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(3)

            def server():
                try:
                    raw, _ = listener.accept()
                    with raw:
                        raw.settimeout(3)
                        with context.wrap_socket(raw, server_side=True):
                            errors.append("unexpected trusted handshake")
                except ssl.SSLError:
                    pass
                except BaseException as error:
                    errors.append(error)

            def loopback(address, timeout, *args, **kwargs):
                self.assertEqual(address, ("api.github.com", 443))
                return real_connect(listener.getsockname(), timeout, *args, **kwargs)

            thread = threading.Thread(target=server)
            thread.start()
            try:
                with mock.patch.dict(os.environ, {
                    "SSL_CERT_FILE": str(self.keys.root / "ca.crt"),
                    "SSL_CERT_DIR": str(self.keys.root),
                    "HTTPS_PROXY": "https://attacker.invalid", "ALL_PROXY": "http://attacker.invalid",
                }), mock.patch.object(socket, "create_connection", side_effect=loopback), self.assertRaises(RecordError):
                    broker.github_user(token=self.token, login=None, deadline=utc_now() + timedelta(seconds=3))
            finally:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
