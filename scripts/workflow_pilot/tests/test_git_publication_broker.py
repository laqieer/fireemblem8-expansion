import base64
import copy
import datetime
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
import uuid
from contextlib import redirect_stderr
from pathlib import Path

from scripts.workflow_pilot import git_publication_broker as broker
from scripts.workflow_pilot import reporter


ROOT = Path(__file__).resolve().parents[3]
TEST_ROOT = ROOT / "build" / "test-artifacts" / "git-publication-broker"


def run(arguments, *, cwd=None, input_bytes=None, check=True, env=None):
    completed = subprocess.run(
        [os.fspath(argument) for argument in arguments],
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
        check=False,
        env=env,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"{arguments!r} failed:\n{completed.stdout!r}\n{completed.stderr!r}"
        )
    return completed


def git(cwd, *arguments, input_bytes=None, check=True):
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": os.fspath(cwd),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Broker Test",
        "GIT_AUTHOR_EMAIL": "broker@example.invalid",
        "GIT_COMMITTER_NAME": "Broker Test",
        "GIT_COMMITTER_EMAIL": "broker@example.invalid",
    }
    return run(
        [broker.GIT, "--no-pager", *arguments],
        cwd=cwd,
        input_bytes=input_bytes,
        check=check,
        env=environment,
    )


def key_pair(root, name):
    private = root / f"{name}.private.pem"
    public = root / f"{name}.public.pem"
    run([broker.OPENSSL, "genpkey", "-algorithm", "ED25519", "-out", private])
    run([broker.OPENSSL, "pkey", "-in", private, "-pubout", "-out", public])
    private.chmod(0o600)
    public.chmod(0o644)
    return private, public


def sign(private_key, payload):
    fd = os.memfd_create("workflow-pilot-test-signature")
    try:
        os.write(fd, payload)
        os.lseek(fd, 0, os.SEEK_SET)
        completed = subprocess.run(
            [
                broker.OPENSSL,
                "pkeyutl",
                "-sign",
                "-inkey",
                private_key,
                "-rawin",
                "-in",
                f"/proc/self/fd/{fd}",
            ],
            capture_output=True,
            check=False,
            pass_fds=(fd,),
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return completed.stdout
    finally:
        os.close(fd)


def directory_identity(path):
    metadata = os.stat(path, follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino


class AuthenticatedGitHandler(http.server.BaseHTTPRequestHandler):
    server_version = "WorkflowPilotGitFixture/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        self.server.fixture_log.append(format % args)

    def do_GET(self):
        self._serve_git()

    def do_POST(self):
        self._serve_git()

    def _serve_git(self):
        expected = "Basic " + base64.b64encode(
            f"workflow-broker:{self.server.fixture_secret}".encode("ascii")
        ).decode("ascii")
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="workflow-pilot"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        parsed = urllib.parse.urlsplit(self.path)
        environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_PROJECT_ROOT": os.fspath(self.server.fixture_project_root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": parsed.path,
            "QUERY_STRING": parsed.query,
            "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(length),
            "REMOTE_ADDR": self.client_address[0],
            "REMOTE_USER": "workflow-broker",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": str(self.server.server_port),
            "SERVER_PROTOCOL": self.request_version,
        }
        if self.headers.get("Git-Protocol"):
            environment["HTTP_GIT_PROTOCOL"] = self.headers["Git-Protocol"]
        completed = subprocess.run(
            [broker.GIT, "http-backend"],
            input=body,
            capture_output=True,
            check=False,
            env=environment,
        )
        headers, separator, payload = completed.stdout.partition(b"\r\n\r\n")
        if not separator:
            headers, separator, payload = completed.stdout.partition(b"\n\n")
        if not separator:
            self.send_error(500)
            return
        status = 200
        response_headers = []
        for raw_line in headers.decode("latin-1").splitlines():
            name, value = raw_line.split(":", 1)
            if name.lower() == "status":
                status = int(value.strip().split()[0])
            else:
                response_headers.append((name.strip(), value.strip()))
        self.send_response(status)
        for name, value in response_headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class AuthenticatedGitServer:
    def __init__(self, fixture):
        self.fixture = fixture
        self.secret = "fixture-" + os.urandom(24).hex()
        self.log = []
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), AuthenticatedGitHandler
        )
        self.server.fixture_secret = self.secret
        self.server.fixture_log = self.log
        self.server.fixture_project_root = fixture.root
        self.cert = fixture.root / "https-cert.pem"
        self.key = fixture.root / "https-key.pem"
        run(
            [
                broker.OPENSSL,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
                "-keyout",
                self.key,
                "-out",
                self.cert,
            ]
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever)

    def start(self):
        self.thread.start()

    @property
    def endpoint(self):
        return f"https://127.0.0.1:{self.server.server_port}/{self.fixture.remote.name}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)


class BrokerFixture:
    def __init__(self, root):
        self.root = root
        self.root.mkdir(mode=0o700, parents=True)
        self.source = root / "source"
        self.remote = root / "protected-remote.git"
        self.plan_store = root / "plans"
        self.state = root / "state"
        self.plan_store.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        git(root, "init", "--quiet", self.source)
        (self.source / "payload.txt").write_text("authority\n", encoding="ascii")
        git(self.source, "add", "payload.txt")
        git(self.source, "commit", "--quiet", "-m", "authority")
        self.commit = git(self.source, "rev-parse", "HEAD").stdout.decode().strip()
        git(root, "init", "--bare", "--quiet", self.remote)
        git(self.remote, "config", "receive.denyNonFastForwards", "true")
        self.hook_log = root / "hook.log"
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "count=0\n"
            "while read old new ref; do\n"
            "  case \"$ref\" in\n"
            "    refs/heads/workflow-pilot/issue-205/authority|"
            "refs/tags/workflow-pilot/issue-205/anchor) ;;\n"
            "    *) exit 41 ;;\n"
            "  esac\n"
            "  count=$((count + 1))\n"
            "done\n"
            "[ \"$count\" -eq 2 ]\n"
            f"printf invoked > {self.hook_log}\n",
            encoding="ascii",
        )
        hook.chmod(0o700)
        self.authority_private, self.authority_public = key_pair(root, "authority")
        self.broker_private, self.broker_public = key_pair(root, "broker")
        self.pack = root / "objects.pack"
        self.pack.write_bytes(
            git(
                self.source,
                "pack-objects",
                "--stdout",
                "--revs",
                input_bytes=f"{self.commit}\n".encode("ascii"),
            ).stdout
        )
        self.object_ids = sorted(
            git(
                self.source,
                "rev-list",
                "--objects",
                "--no-object-names",
                self.commit,
            )
            .stdout.decode("ascii")
            .splitlines()
        )
        git_identity = directory_identity(self.remote)
        object_identity = directory_identity(self.remote / "objects")
        installation_id = hashlib.sha256(
            f"installation:{root}".encode("utf-8")
        ).hexdigest()
        endpoint = self.remote.as_uri()
        self.installation = {
            "installation_id": installation_id,
            "repository": "laqieer/fireemblem8-expansion",
            "endpoint": endpoint,
            "expected_capability_uid": os.geteuid(),
            "broker_key_id": "broker-v1",
            "broker_private_key": self.broker_private,
            "plan_signers": {
                "authority-v1": {
                    "public_key": self.authority_public,
                    "signer": "external-installation",
                    "actor": "workflow-coordinator",
                }
            },
            "plan_store": self.plan_store,
            "state_directory": self.state,
            "authentication": {"mode": "local-test"},
            "protected_remote": {
                "git_dir": self.remote,
                "git_dir_device": git_identity[0],
                "git_dir_inode": git_identity[1],
                "objects_device": object_identity[0],
                "objects_inode": object_identity[1],
                "config_sha256": hashlib.sha256(
                    (self.remote / "config").read_bytes()
                ).hexdigest(),
                "hooks_sha256": broker._tree_digest(self.remote / "hooks"),
            },
            "pack_max_bytes": 8 * 1024 * 1024,
            "operation_timeout_seconds": 10,
            "plan_lifetime_seconds": 300,
            "test_only": True,
        }
        self.client_installation = {
            "installation_id": installation_id,
            "repository": self.installation["repository"],
            "endpoint": endpoint,
            "expected_broker_uid": os.geteuid(),
            "expected_capability_uid": os.geteuid(),
            "broker_key_id": "broker-v1",
            "broker_public_key": self.broker_public,
            "pack_max_bytes": self.installation["pack_max_bytes"],
            "operation_timeout_seconds": 10,
        }

    def make_plan(self, **changes):
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        plan = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "installation_id": self.installation["installation_id"],
            "repository": self.installation["repository"],
            "issue": 205,
            "endpoint": self.installation["endpoint"],
            "operation": "publish-authority-anchor",
            "authority_ref": broker._plan_ref(205, "authority"),
            "anchor_ref": broker._plan_ref(205, "anchor"),
            "expected_authority_oid": None,
            "expected_anchor_oid": None,
            "new_authority_oid": self.commit,
            "new_anchor_oid": self.commit,
            "object_ids": self.object_ids,
            "pack_sha256": hashlib.sha256(self.pack.read_bytes()).hexdigest(),
            "pack_size": self.pack.stat().st_size,
            "nonce": os.urandom(32).hex(),
            "sequence": 1,
            "issued_at": (now - datetime.timedelta(seconds=5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "expires_at": (now + datetime.timedelta(seconds=120)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "signer": "external-installation",
            "actor": "workflow-coordinator",
        }
        plan.update(changes)
        signature = sign(
            self.authority_private,
            broker.PLAN_DOMAIN + reporter.normalized_json(plan),
        )
        plan["signature"] = {
            "algorithm": "ed25519",
            "key_id": "authority-v1",
            "value": base64.b64encode(signature).decode("ascii"),
        }
        identity = hashlib.sha256(reporter.normalized_json(plan)).hexdigest()
        (self.plan_store / f"{identity}.json").write_bytes(
            reporter.normalized_json(plan)
        )
        return identity, plan

    def spawn(self, installation=None):
        parent, child = socket.socketpair()
        pid = os.fork()
        if pid == 0:
            parent.close()
            status = 0
            try:
                broker.serve_connection(
                    child,
                    installation or self.installation,
                    enforce_peer=False,
                )
            except BaseException:
                status = 3
            finally:
                child.close()
            os._exit(status)
        child.close()
        return pid, parent

    def publish(self, identity, *, installation=None, client_installation=None):
        pid, connection = self.spawn(installation)
        try:
            return broker.publish_via_connection(
                connection,
                client_installation or self.client_installation,
                identity,
                self.pack,
                enforce_peer=False,
            )
        finally:
            connection.close()
            waited, status = os.waitpid(pid, 0)
            if waited != pid:
                raise AssertionError("wrong broker process reaped")
            if not os.WIFEXITED(status):
                raise AssertionError(f"broker process terminated abnormally: {status}")

    def remote_refs(self):
        output = git(
            self.root,
            "ls-remote",
            "--refs",
            self.installation["endpoint"],
            broker._plan_ref(205, "authority"),
            broker._plan_ref(205, "anchor"),
        ).stdout.decode("ascii")
        return {
            line.split("\t", 1)[1]: line.split("\t", 1)[0]
            for line in output.splitlines()
        }

    def configure_https_authentication(self, server):
        endpoint = server.endpoint
        git(self.remote, "config", "http.receivepack", "true")
        askpass = self.root / "askpass"
        credential = self.root / "credential"
        askpass.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' workflow-broker ;;\n"
            "  *Password*) exec /bin/cat \"$WORKFLOW_PILOT_BROKER_CREDENTIAL_FILE\" ;;\n"
            "  *) exit 64 ;;\n"
            "esac\n",
            encoding="ascii",
        )
        askpass.chmod(0o700)
        credential.write_text(server.secret, encoding="ascii")
        credential.chmod(0o600)
        self.installation["endpoint"] = endpoint
        self.installation["authentication"] = {
            "mode": "https-askpass",
            "askpass": askpass,
            "credential_file": credential,
            "ca_file": server.cert,
        }
        self.installation["protected_remote"]["config_sha256"] = hashlib.sha256(
            (self.remote / "config").read_bytes()
        ).hexdigest()
        self.client_installation["endpoint"] = endpoint
        return credential

    def install_stalling_hook(self, seconds=30):
        ready = self.root / "stall-ready"
        pid_file = self.root / "stall-pid"
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            f"printf '%s' \"$$\" > {pid_file}\n"
            f"printf ready > {ready}\n"
            f"/bin/sleep {seconds}\n"
            "exit 1\n",
            encoding="ascii",
        )
        hook.chmod(0o700)
        self.installation["protected_remote"]["hooks_sha256"] = broker._tree_digest(
            self.remote / "hooks"
        )
        return ready, pid_file


class GitPublicationBrokerTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TEST_ROOT / uuid.uuid4().hex
        self.fixture = BrokerFixture(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_valid_plan_publishes_exact_pair_atomically_with_signed_response(self):
        identity, _plan = self.fixture.make_plan()
        refs = self.fixture.publish(identity)
        expected = {
            broker._plan_ref(205, "authority"): self.fixture.commit,
            broker._plan_ref(205, "anchor"): self.fixture.commit,
        }
        self.assertEqual(refs, expected)
        self.assertEqual(self.fixture.remote_refs(), expected)
        self.assertEqual(self.fixture.hook_log.read_text(encoding="ascii"), "invoked")
        journal = (self.fixture.state / "journal.jsonl").read_text(encoding="ascii")
        self.assertIn('"result":"published"', journal)
        self.assertNotIn(self.fixture.authority_private.read_text(encoding="ascii"), journal)
        self.assertNotIn(self.fixture.broker_private.read_text(encoding="ascii"), journal)

    def test_abstract_named_and_same_uid_connections_reject(self):
        name = "\0workflow-pilot-" + uuid.uuid4().hex
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(name)
            listener.listen(1)
            client.connect(name)
            accepted, _ = listener.accept()
            with accepted:
                with self.assertRaisesRegex(broker.BrokerError, "named and abstract"):
                    broker._require_unnamed_socket(accepted)
        finally:
            client.close()
            listener.close()
        server, candidate = socket.socketpair()
        try:
            with self.assertRaisesRegex(broker.BrokerError, "capability issuer principal"):
                broker.serve_connection(server, self.fixture.installation, enforce_peer=True)
        finally:
            server.close()
            candidate.close()

    def test_plan_lifetime_and_closed_destination_fields_reject(self):
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        cases = {
            "expired": {
                "issued_at": (now - datetime.timedelta(seconds=60)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "expires_at": (now - datetime.timedelta(seconds=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            },
            "future": {
                "issued_at": (now + datetime.timedelta(seconds=20)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "expires_at": (now + datetime.timedelta(seconds=40)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            },
            "repository": {"repository": "attacker/other"},
            "issue": {
                "issue": 206,
                "authority_ref": broker._plan_ref(205, "authority"),
                "anchor_ref": broker._plan_ref(205, "anchor"),
            },
            "endpoint": {"endpoint": self.root.as_uri() + "/other.git"},
            "operation": {"operation": "push"},
            "master": {"authority_ref": "refs/heads/master"},
            "tag": {"anchor_ref": "refs/tags/v1.0"},
            "installation": {"installation_id": "f" * 64},
            "signer": {"actor": "candidate"},
            "sequence": {"sequence": 0},
            "nonce": {"nonce": "not-a-nonce"},
            "new-oid": {"new_authority_oid": "1" * 40},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                identity, _ = self.fixture.make_plan(**changes)
                with self.assertRaises(broker.BrokerError):
                    self.fixture.publish(identity)
                self.assertEqual(self.fixture.remote_refs(), {})

    def test_wrong_old_oid_and_wrong_object_closure_reject_without_partial_ref(self):
        identity, _ = self.fixture.make_plan(expected_authority_oid="1" * 40)
        with self.assertRaisesRegex(broker.BrokerError, "stale-remote"):
            self.fixture.publish(identity)
        self.assertEqual(self.fixture.remote_refs(), {})

        other_root = TEST_ROOT / uuid.uuid4().hex
        other = BrokerFixture(other_root)
        try:
            identity, _ = other.make_plan(object_ids=[other.commit])
            with self.assertRaisesRegex(broker.BrokerError, "wrong-objects"):
                other.publish(identity)
            self.assertEqual(other.remote_refs(), {})
        finally:
            shutil.rmtree(other_root, ignore_errors=True)

    def test_replay_restart_sequence_and_journal_rollback_reject(self):
        first_identity, _ = self.fixture.make_plan()
        self.fixture.publish(first_identity)
        with self.assertRaisesRegex(broker.BrokerError, "replay"):
            self.fixture.publish(first_identity)
        lower_identity, _ = self.fixture.make_plan(
            expected_authority_oid=self.fixture.commit,
            expected_anchor_oid=self.fixture.commit,
            sequence=1,
        )
        with self.assertRaisesRegex(broker.BrokerError, "replay"):
            self.fixture.publish(lower_identity)

        journal_before = (self.fixture.state / "journal.jsonl").read_bytes()
        second_identity, _ = self.fixture.make_plan(
            expected_authority_oid=self.fixture.commit,
            expected_anchor_oid=self.fixture.commit,
            sequence=2,
        )
        self.fixture.publish(second_identity)
        (self.fixture.state / "journal.jsonl").write_bytes(journal_before)
        third_identity, _ = self.fixture.make_plan(
            expected_authority_oid=self.fixture.commit,
            expected_anchor_oid=self.fixture.commit,
            sequence=3,
        )
        with self.assertRaisesRegex(broker.BrokerError, "journal-rollback"):
            self.fixture.publish(third_identity)

    def test_concurrent_nonce_only_one_request_succeeds(self):
        identity, _ = self.fixture.make_plan()
        outcomes = []
        barrier = threading.Barrier(3)
        services = [self.fixture.spawn() for _ in range(2)]

        def worker(service):
            pid, connection = service
            barrier.wait()
            try:
                broker.publish_via_connection(
                    connection,
                    self.fixture.client_installation,
                    identity,
                    self.fixture.pack,
                    enforce_peer=False,
                )
                outcomes.append("published")
            except broker.BrokerError as error:
                outcomes.append(error.args[0])
            finally:
                connection.close()
                os.waitpid(pid, 0)

        threads = [
            threading.Thread(target=worker, args=(service,))
            for service in services
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(20)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes.count("published"), 1)
        self.assertEqual(
            self.fixture.remote_refs()[broker._plan_ref(205, "authority")],
            self.fixture.commit,
        )

    def test_wrong_response_key_and_request_binding_reject(self):
        identity, _ = self.fixture.make_plan()
        _wrong_private, wrong_public = key_pair(self.root, "wrong")
        client = dict(self.fixture.client_installation)
        client["broker_public_key"] = wrong_public
        with self.assertRaisesRegex(broker.BrokerError, "signature"):
            self.fixture.publish(identity, client_installation=client)

        request = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "request_nonce": "1" * 64,
            "plan_identity": "2" * 64,
            "pack_sha256": "3" * 64,
            "pack_size": 1,
            "deadline": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        response = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "request_digest": "0" * 64,
            "request_nonce": request["request_nonce"],
            "plan_identity": request["plan_identity"],
            "installation_id": self.fixture.installation["installation_id"],
            "broker_key_id": "broker-v1",
            "status": "ok",
            "code": "published",
            "refs": {"refs/a": self.fixture.commit, "refs/b": self.fixture.commit},
            "completed_at": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "signature": {
                "algorithm": "ed25519",
                "key_id": "broker-v1",
                "value": base64.b64encode(b"x" * 64).decode("ascii"),
            },
        }
        with self.assertRaisesRegex(broker.BrokerError, "does not bind"):
            broker._verify_response(
                response,
                request,
                self.fixture.client_installation,
                now=datetime.datetime.now(datetime.timezone.utc),
            )
        missing_signature = dict(response)
        del missing_signature["signature"]
        with self.assertRaisesRegex(broker.BrokerError, "fields differ"):
            broker._verify_response(
                missing_signature,
                request,
                self.fixture.client_installation,
                now=datetime.datetime.now(datetime.timezone.utc),
            )

    def test_remote_hook_config_and_directory_swap_reject(self):
        cases = ("hook", "config", "directory")
        for case in cases:
            other_root = TEST_ROOT / uuid.uuid4().hex
            other = BrokerFixture(other_root)
            try:
                identity, _ = other.make_plan()
                if case == "hook":
                    (other.remote / "hooks" / "pre-receive").write_text(
                        "#!/bin/sh\nexit 0\n", encoding="ascii"
                    )
                elif case == "config":
                    with (other.remote / "config").open("a", encoding="ascii") as stream:
                        stream.write("\n[receive]\n\tdenyDeletes = false\n")
                else:
                    moved = other.root / "old.git"
                    other.remote.rename(moved)
                    git(other.root, "init", "--bare", "--quiet", other.remote)
                with self.assertRaises(broker.BrokerError):
                    other.publish(identity)
                self.assertEqual(
                    git(
                        other.root,
                        "--git-dir",
                        other.remote,
                        "show-ref",
                        "--verify",
                        "--quiet",
                        broker._plan_ref(205, "authority"),
                        check=False,
                    ).returncode,
                    1,
                )
            finally:
                shutil.rmtree(other_root, ignore_errors=True)

    def test_server_hook_rejects_generic_direct_updates(self):
        result = git(
            self.source_for_direct_push(),
            "push",
            self.fixture.installation["endpoint"],
            f"{self.fixture.commit}:refs/heads/master",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.fixture.remote_refs(), {})

    def source_for_direct_push(self):
        return self.fixture.source

    def test_disconnect_burns_no_ref_and_leaves_no_pack(self):
        identity, _ = self.fixture.make_plan()
        pid, connection = self.fixture.spawn()
        request = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "request_nonce": os.urandom(32).hex(),
            "plan_identity": identity,
            "pack_sha256": hashlib.sha256(self.fixture.pack.read_bytes()).hexdigest(),
            "pack_size": self.fixture.pack.stat().st_size,
            "deadline": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        connection.sendall(reporter.normalized_json(request) + self.fixture.pack.read_bytes()[:10])
        connection.close()
        os.waitpid(pid, 0)
        self.assertEqual(self.fixture.remote_refs(), {})
        staging = self.fixture.state / "staging"
        self.assertFalse(staging.exists() and any(staging.iterdir()))

    def test_preflight_rejects_candidate_owned_installation_and_same_uid(self):
        client_manifest = self.root / "client-installation.json"
        public_copy = self.root / "installed-broker-public.pem"
        public_copy.write_bytes(self.fixture.broker_public.read_bytes())
        manifest = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "installation_id": self.fixture.installation["installation_id"],
            "repository": self.fixture.installation["repository"],
            "endpoint": "https://github.com/laqieer/fireemblem8-expansion.git",
            "expected_broker_uid": os.geteuid(),
            "expected_capability_uid": os.geteuid(),
            "broker_key_id": "broker-v1",
            "broker_public_key": os.fspath(public_copy),
            "pack_max_bytes": 1024,
            "operation_timeout_seconds": 10,
        }
        client_manifest.write_bytes(reporter.normalized_json(manifest))
        completed = run(
            [
                sys.executable,
                "-I",
                ROOT / "scripts/workflow_pilot/isolated_launcher.py",
                "git-broker-preflight",
                "--installation",
                client_manifest,
            ],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            completed.stderr.decode("ascii").strip(),
            "git-publication-broker: insecure-installation",
        )
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                broker.build_parser().parse_args(
                    [
                        "serve",
                        "--installation",
                        os.fspath(client_manifest),
                        "--connection-fd",
                        "3",
                        "--socket",
                        "@bad",
                    ]
                )

        service_manifest = self.root / "service-installation.json"
        service = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "installation_id": self.fixture.installation["installation_id"],
            "repository": self.fixture.installation["repository"],
            "endpoint": self.fixture.installation["endpoint"],
            "expected_capability_uid": os.geteuid(),
            "broker_key_id": "broker-v1",
            "broker_private_key": os.fspath(self.fixture.broker_private),
            "plan_signers": {
                "authority-v1": {
                    "public_key": os.fspath(self.fixture.authority_public),
                    "signer": "external-installation",
                    "actor": "workflow-coordinator",
                }
            },
            "plan_store": os.fspath(self.fixture.plan_store),
            "state_directory": os.fspath(self.fixture.state),
            "authentication": {"mode": "local-test"},
            "protected_remote": {
                key: os.fspath(value) if isinstance(value, Path) else value
                for key, value in self.fixture.installation[
                    "protected_remote"
                ].items()
            },
            "pack_max_bytes": 1024,
            "operation_timeout_seconds": 10,
            "plan_lifetime_seconds": 300,
            "test_only": True,
        }
        service_manifest.write_bytes(reporter.normalized_json(service))
        parent, child = socket.socketpair()
        try:
            service_result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    ROOT / "scripts/workflow_pilot/isolated_launcher.py",
                    "git-broker-serve",
                    "--installation",
                    service_manifest,
                    "--connection-fd",
                    str(child.fileno()),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                pass_fds=(child.fileno(),),
            )
        finally:
            parent.close()
            child.close()
        self.assertEqual(service_result.returncode, 2)
        self.assertEqual(
            service_result.stderr.decode("ascii").strip(),
            "git-publication-broker: insecure-installation",
        )

    def test_https_askpass_path_publishes_without_credential_disclosure(self):
        server = AuthenticatedGitServer(self.fixture)
        try:
            credential = self.fixture.configure_https_authentication(server)
            identity, _ = self.fixture.make_plan()
            pid, connection = self.fixture.spawn()
            server.start()
            try:
                refs = broker.publish_via_connection(
                    connection,
                    self.fixture.client_installation,
                    identity,
                    self.fixture.pack,
                    enforce_peer=False,
                )
            finally:
                connection.close()
                os.waitpid(pid, 0)
            self.assertEqual(len(refs), 2)
            secret = credential.read_text(encoding="ascii")
            self.assertNotIn(secret, repr(refs))
            self.assertNotIn(secret, repr(dict(os.environ)))
            self.assertNotIn(
                secret,
                repr(
                    broker._git_environment(
                        self.fixture.installation, self.fixture.state
                    )
                ),
            )
            self.assertNotIn(secret, "\n".join(server.log))
            self.assertNotIn(
                secret,
                (self.fixture.state / "journal.jsonl").read_text(encoding="ascii"),
            )
            self.assertFalse(list(self.fixture.root.glob("core*")))
            leaks = []
            for path in self.fixture.root.rglob("*"):
                if path == credential or not path.is_file():
                    continue
                if secret.encode("ascii") in path.read_bytes():
                    leaks.append(path.relative_to(self.fixture.root).as_posix())
            self.assertEqual(leaks, [])
        finally:
            server.close()

    def test_timeout_kills_git_process_group_and_leaves_refs_unchanged(self):
        ready, pid_file = self.fixture.install_stalling_hook()
        self.fixture.installation["operation_timeout_seconds"] = 1
        self.fixture.client_installation["operation_timeout_seconds"] = 5
        identity, _ = self.fixture.make_plan()
        with self.assertRaisesRegex(broker.BrokerError, "git-timeout"):
            self.fixture.publish(identity)
        self.assertTrue(ready.exists())
        hook_pid = int(pid_file.read_text(encoding="ascii"))
        for _ in range(50):
            if not Path(f"/proc/{hook_pid}").exists():
                break
            time.sleep(0.02)
        self.assertFalse(Path(f"/proc/{hook_pid}").exists())
        self.assertEqual(self.fixture.remote_refs(), {})

    def test_killing_broker_kills_credential_bearing_git_child(self):
        ready, pid_file = self.fixture.install_stalling_hook()
        self.fixture.installation["operation_timeout_seconds"] = 30
        identity, _ = self.fixture.make_plan()
        pid, connection = self.fixture.spawn()
        outcome = []

        def request():
            try:
                broker.publish_via_connection(
                    connection,
                    self.fixture.client_installation,
                    identity,
                    self.fixture.pack,
                    enforce_peer=False,
                )
            except BaseException as error:
                outcome.append(type(error).__name__)

        thread = threading.Thread(target=request)
        thread.start()
        for _ in range(500):
            if ready.exists():
                break
            time.sleep(0.01)
        self.assertTrue(ready.exists())
        hook_pid = int(pid_file.read_text(encoding="ascii"))
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        connection.close()
        thread.join(10)
        for _ in range(100):
            if not Path(f"/proc/{hook_pid}").exists():
                break
            time.sleep(0.02)
        self.assertFalse(Path(f"/proc/{hook_pid}").exists())
        self.assertEqual(self.fixture.remote_refs(), {})
        self.assertTrue(outcome)

    def test_invalid_plan_signature_and_pack_identity_reject(self):
        identity, plan = self.fixture.make_plan()
        bad = copy.deepcopy(plan)
        bad["signature"]["value"] = base64.b64encode(b"x" * 64).decode("ascii")
        bad_identity = hashlib.sha256(reporter.normalized_json(bad)).hexdigest()
        (self.fixture.plan_store / f"{bad_identity}.json").write_bytes(
            reporter.normalized_json(bad)
        )
        with self.assertRaisesRegex(broker.BrokerError, "invalid-signature"):
            self.fixture.publish(bad_identity)
        identity, _ = self.fixture.make_plan(pack_sha256="0" * 64)
        with self.assertRaisesRegex(broker.BrokerError, "wrong-pack"):
            self.fixture.publish(identity)
        self.assertEqual(self.fixture.remote_refs(), {})

    def test_request_pack_and_header_size_limits_reject(self):
        identity, _ = self.fixture.make_plan()
        client = dict(self.fixture.client_installation)
        client["pack_max_bytes"] = self.fixture.pack.stat().st_size - 1
        pid, connection = self.fixture.spawn()
        try:
            with self.assertRaisesRegex(broker.BrokerError, "pack size"):
                broker.publish_via_connection(
                    connection,
                    client,
                    identity,
                    self.fixture.pack,
                    enforce_peer=False,
                )
        finally:
            connection.close()
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)

        pid, connection = self.fixture.spawn()
        connection.sendall(b"{" + b"x" * broker.REQUEST_MAX_BYTES + b"\n")
        connection.close()
        os.waitpid(pid, 0)
        self.assertEqual(self.fixture.remote_refs(), {})

        client_side, fake_broker = socket.socketpair()

        def oversized_response():
            try:
                fake_broker.recv(65536)
                fake_broker.sendall(b"x" * (broker.RESPONSE_MAX_BYTES + 1))
            finally:
                fake_broker.close()

        thread = threading.Thread(target=oversized_response)
        thread.start()
        try:
            with self.assertRaisesRegex(broker.BrokerError, "response exceeds"):
                broker.publish_via_connection(
                    client_side,
                    self.fixture.client_installation,
                    identity,
                    self.fixture.pack,
                    enforce_peer=False,
                )
        finally:
            client_side.close()
            thread.join(10)

    def test_subprocess_output_is_bounded(self):
        with self.assertRaisesRegex(broker.BrokerError, "subprocess output"):
            broker._run_bounded(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os;"
                        "os.write(1,b'x'*"
                        f"({broker.SUBPROCESS_OUTPUT_MAX_BYTES}+65536))"
                    ),
                ],
                environment={"PATH": "/usr/bin:/bin"},
                timeout=10,
            )

    def test_production_descriptor_scrub_keeps_only_allowlisted_fds(self):
        report_parent, report_child = socket.socketpair()
        extra_read, extra_write = os.pipe()
        pid = os.fork()
        if pid == 0:
            report_parent.close()
            broker._close_unrelated_fds({0, 1, 2, report_child.fileno()})
            closed = []
            for descriptor in (extra_read, extra_write):
                try:
                    os.fstat(descriptor)
                except OSError:
                    closed.append(descriptor)
            report_child.sendall(reporter.normalized_json(closed))
            report_child.close()
            os._exit(0)
        report_child.close()
        os.close(extra_read)
        os.close(extra_write)
        raw = report_parent.recv(4096)
        report_parent.close()
        os.waitpid(pid, 0)
        closed = json.loads(raw)
        self.assertEqual(closed, [extra_read, extra_write])


class TimestampContractTests(unittest.TestCase):
    def test_exact_utc_second_precision_and_calendar_ranges(self):
        accepted = reporter.parse_time("2026-09-05T13:25:47Z", "timestamp")
        self.assertEqual(
            accepted,
            datetime.datetime(2026, 9, 5, 13, 25, 47, tzinfo=datetime.timezone.utc),
        )
        for value in (
            "2026-09-05T24:00:00Z",
            "2026-09-05T13:25:47+00:00",
            "2026-09-05T13:25:47.0Z",
            "2026-02-29T00:00:00Z",
            "2026-09-05T13:60:00Z",
            "2026-09-05t13:25:47Z",
            "2026-9-05T13:25:47Z",
        ):
            with self.subTest(value=value):
                with self.assertRaises(reporter.PilotDataError):
                    reporter.parse_time(value, "timestamp")


class DeploymentContractTests(unittest.TestCase):
    def test_redacted_installation_examples_match_closed_protocol_shapes(self):
        fixture_root = ROOT / "scripts" / "workflow_pilot" / "tests" / "fixtures"
        client = json.loads(
            (fixture_root / "git_broker_client.example.json").read_text(
                encoding="ascii"
            )
        )
        service = json.loads(
            (fixture_root / "git_broker_service.example.json").read_text(
                encoding="ascii"
            )
        )
        self.assertEqual(
            set(client),
            {
                "schema_version",
                "protocol",
                "installation_id",
                "repository",
                "endpoint",
                "expected_broker_uid",
                "expected_capability_uid",
                "broker_key_id",
                "broker_public_key",
                "pack_max_bytes",
                "operation_timeout_seconds",
            },
        )
        self.assertEqual(
            set(service),
            {
                "schema_version",
                "protocol",
                "installation_id",
                "repository",
                "endpoint",
                "expected_capability_uid",
                "broker_key_id",
                "broker_private_key",
                "plan_signers",
                "plan_store",
                "state_directory",
                "authentication",
                "protected_remote",
                "pack_max_bytes",
                "operation_timeout_seconds",
                "plan_lifetime_seconds",
                "test_only",
            },
        )
        self.assertEqual(client["protocol"], broker.PROTOCOL)
        self.assertEqual(service["protocol"], broker.PROTOCOL)
        self.assertNotEqual(
            client["expected_broker_uid"], service["expected_capability_uid"]
        )
        self.assertFalse(service["test_only"])
        self.assertEqual(service["authentication"]["mode"], "https-askpass")
        serialized = reporter.normalized_json(
            {"client": client, "service": service}
        ).lower()
        self.assertNotIn(b"begin private key", serialized)
        self.assertNotIn(b"ghp_", serialized)

    def test_plan_schema_matches_parser_fields_and_strict_timestamp_shape(self):
        schema = json.loads(
            (
                ROOT
                / "scripts"
                / "workflow_pilot"
                / "git_publication_plan.schema.json"
            ).read_text(encoding="ascii")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(
            schema["properties"]["protocol"]["const"], broker.PROTOCOL
        )
        timestamp_pattern = re.compile(
            schema["properties"]["issued_at"]["pattern"]
        )
        self.assertIsNotNone(timestamp_pattern.fullmatch("2026-09-05T23:59:59Z"))
        self.assertIsNone(timestamp_pattern.fullmatch("2026-09-05T24:00:00Z"))


if __name__ == "__main__":
    unittest.main()
