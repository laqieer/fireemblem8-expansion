import ast
import base64
import copy
import datetime
import fcntl
import hashlib
import http.server
import io
import json
import os
import pwd
import re
import shutil
import signal
import socket
import ssl
import stat
import struct
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
import uuid
import warnings
from contextlib import redirect_stderr
from pathlib import Path

from scripts.workflow_pilot import git_publication_broker as broker
from scripts.workflow_pilot import reporter
from scripts.workflow_pilot import signed_schema


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


def process_is_running(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
    except OSError:
        return False
    return len(fields) > 2 and fields[2] != "Z"


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
        parsed = urllib.parse.urlsplit(self.path)
        receive_pack = (
            "git-receive-pack" in parsed.path
            or "service=git-receive-pack" in parsed.query
        )
        authenticated = self.headers.get("Authorization") == expected
        if (
            not authenticated
            and (receive_pack or not self.server.fixture_anonymous_read)
        ):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="workflow-pilot"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if receive_pack and not self.server.fixture_write_allowed:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
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


class QuietThreadingHTTPServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


class AuthenticatedGitServer:
    def __init__(self, fixture, *, write_allowed=True, anonymous_read=False):
        self.fixture = fixture
        self.secret = "fixture-" + os.urandom(24).hex()
        self.log = []
        self.server = QuietThreadingHTTPServer(
            ("127.0.0.1", 0), AuthenticatedGitHandler
        )
        self.server.fixture_secret = self.secret
        self.server.fixture_log = self.log
        self.server.fixture_project_root = fixture.root
        self.server.fixture_write_allowed = write_allowed
        self.server.fixture_anonymous_read = anonymous_read
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
            "candidate_uid": os.geteuid() + 1,
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
            "plan_store_fd": os.open(
                self.plan_store,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            ),
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
            "reconciliation_timeout_seconds": 10,
            "plan_lifetime_seconds": 300,
            "test_only": True,
        }
        self.installation["protected_remote"] = (
            broker._bind_protected_remote_descriptors(
                self.installation["protected_remote"],
                owners={os.geteuid()},
                candidate_uid=self.installation["candidate_uid"],
            )
        )
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
            "test_only": True,
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

    def close(self):
        for key in ("plan_store_fd",):
            descriptor = self.installation.pop(key, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        remote = self.installation.get("protected_remote") or {}
        for key in (
            "git_dir_fd",
            "objects_fd",
            "refs_fd",
            "hooks_fd",
            "config_fd",
            "packed_refs_fd",
        ):
            descriptor = remote.pop(key, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def __del__(self):
        try:
            self.close()
        except BaseException:
            pass

    def make_capability(self, identity, operation="publish", **changes):
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        capability = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "installation_id": self.installation["installation_id"],
            "repository": self.installation["repository"],
            "issue": 205,
            "plan_identity": identity,
            "operation": operation,
            "capability_nonce": os.urandom(32).hex(),
            "issued_at": (now - datetime.timedelta(seconds=5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "expires_at": (now + datetime.timedelta(seconds=120)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "signer": "external-installation",
            "actor": "workflow-coordinator",
        }
        capability.update(changes)
        signature = sign(
            self.authority_private,
            broker.CAPABILITY_DOMAIN + reporter.normalized_json(capability),
        )
        capability["signature"] = {
            "algorithm": "ed25519",
            "key_id": "authority-v1",
            "value": base64.b64encode(signature).decode("ascii"),
        }
        fd = os.memfd_create(
            "workflow-pilot-git-capability",
            flags=os.MFD_ALLOW_SEALING,
        )
        os.write(fd, reporter.normalized_json(capability))
        os.lseek(fd, 0, os.SEEK_SET)
        fcntl.fcntl(
            fd,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE,
        )
        return fd, capability

    def spawn(self, identity, operation="publish", installation=None):
        parent, child = socket.socketpair()
        capability_fd, _capability = self.make_capability(
            identity, operation=operation
        )
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"This process .* is multi-threaded, use of fork.*",
                category=DeprecationWarning,
            )
            pid = os.fork()
        if pid == 0:
            parent.close()
            status = 0
            try:
                capability = broker._read_sealed_capability(capability_fd)
                os.close(capability_fd)
                broker.serve_connection(
                    child,
                    installation or self.installation,
                    capability,
                    enforce_peer=False,
                )
            except BaseException:
                status = 3
            finally:
                child.close()
            os._exit(status)
        child.close()
        os.close(capability_fd)
        return pid, parent

    def publish(self, identity, *, installation=None, client_installation=None):
        pid, connection = self.spawn(identity, installation=installation)
        try:
            return broker.publish_via_connection(
                connection,
                client_installation or self.client_installation,
                205,
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
        self.rebind_protected_remote()
        self.client_installation["endpoint"] = endpoint
        return credential

    def rebind_protected_remote(self):
        remote = self.installation["protected_remote"]
        for key in (
            "git_dir_fd",
            "objects_fd",
            "refs_fd",
            "hooks_fd",
            "config_fd",
            "packed_refs_fd",
        ):
            descriptor = remote.pop(key, None)
            if descriptor is not None:
                os.close(descriptor)
        public = {
            key: remote[key]
            for key in (
                "git_dir",
                "git_dir_device",
                "git_dir_inode",
                "objects_device",
                "objects_inode",
                "config_sha256",
                "hooks_sha256",
            )
        }
        self.installation["protected_remote"] = (
            broker._bind_protected_remote_descriptors(
                public,
                owners={os.geteuid()},
                candidate_uid=self.installation["candidate_uid"],
            )
        )

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

    def install_deadline_hook(self, seconds=30):
        ready = self.root / "deadline-ready"
        deadline_log = self.root / "deadline.txt"
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "test -n \"${WORKFLOW_PILOT_EFFECTIVE_DEADLINE:-}\"\n"
            f"printf '%s' \"$WORKFLOW_PILOT_EFFECTIVE_DEADLINE\" > {deadline_log}\n"
            f"printf ready > {ready}\n"
            f"/bin/sleep {seconds}\n"
            "exit 1\n",
            encoding="ascii",
        )
        hook.chmod(0o700)
        self.installation["protected_remote"]["hooks_sha256"] = (
            broker._tree_digest(self.remote / "hooks")
        )
        self.rebind_protected_remote()
        return ready, deadline_log


class GitPublicationBrokerTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TEST_ROOT / uuid.uuid4().hex
        self.fixture = BrokerFixture(self.root)

    def tearDown(self):
        self.fixture.close()
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
            installation = dict(self.fixture.installation)
            installation["expected_capability_uid"] = os.geteuid() + 1
            with self.assertRaisesRegex(broker.BrokerError, "capability issuer principal"):
                broker.serve_connection(
                    server,
                    installation,
                    {},
                    enforce_peer=True,
                )
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
                self.assertEqual(
                    git(
                        self.root,
                        "--git-dir",
                        self.fixture.remote,
                        "show-ref",
                        "--quiet",
                        check=False,
                    ).returncode,
                    1,
                )

    def test_issued_connection_binds_one_plan_repository_and_issue(self):
        first_identity, _ = self.fixture.make_plan()
        second_identity, _ = self.fixture.make_plan()
        self.fixture.publish(first_identity)
        journal = (
            self.fixture.state / "journal.jsonl"
        ).read_text(encoding="ascii")
        self.assertIn(first_identity, journal)
        self.assertNotIn(second_identity, journal)

        other_root = TEST_ROOT / uuid.uuid4().hex
        other = BrokerFixture(other_root)
        try:
            identity, _ = other.make_plan()
            pid, connection = other.spawn(identity)
            try:
                with self.assertRaisesRegex(
                    broker.BrokerError, "capability-mismatch"
                ):
                    broker.publish_via_connection(
                        connection,
                        other.client_installation,
                        206,
                        other.pack,
                        enforce_peer=False,
                    )
            finally:
                connection.close()
                os.waitpid(pid, 0)
            staging = other.state / "staging"
            self.assertFalse(staging.exists() and any(staging.iterdir()))
            self.assertEqual(other.remote_refs(), {})
        finally:
            shutil.rmtree(other_root, ignore_errors=True)

    def test_launch_capability_must_be_sealed_and_authority_signed(self):
        identity, _ = self.fixture.make_plan()
        capability_fd, capability = self.fixture.make_capability(identity)
        try:
            loaded = broker._read_sealed_capability(capability_fd)
            self.assertEqual(loaded, capability)
        finally:
            os.close(capability_fd)

        unsealed = os.memfd_create(
            "workflow-pilot-git-capability",
            flags=os.MFD_ALLOW_SEALING,
        )
        try:
            os.write(unsealed, reporter.normalized_json(capability))
            with self.assertRaisesRegex(broker.BrokerError, "sealed"):
                broker._read_sealed_capability(unsealed)
        finally:
            os.close(unsealed)

        tampered = copy.deepcopy(capability)
        tampered["issue"] = 206
        with self.assertRaisesRegex(broker.BrokerError, "signature"):
            broker._validate_capability(
                tampered,
                self.fixture.installation,
                now=datetime.datetime.now(datetime.timezone.utc),
            )

    def test_credential_and_replay_state_paths_fail_closed(self):
        https_installation = dict(self.fixture.installation)
        https_installation["endpoint"] = (
            "https://github.com/laqieer/fireemblem8-expansion.git"
        )
        askpass = self.root / "private-askpass"
        credential = self.root / "credential"
        askpass.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
        askpass.chmod(0o700)
        credential.write_text("not-a-real-token", encoding="ascii")
        credential.chmod(0o644)
        with self.assertRaisesRegex(broker.BrokerError, "must be private"):
            broker._load_authentication(
                {
                    "mode": "https-askpass",
                    "askpass": os.fspath(askpass),
                    "credential_file": os.fspath(credential),
                    "ca_file": None,
                },
                self.root,
                {0, os.geteuid()},
                https_installation,
            )

        outside = self.root / "outside-journal"
        outside.write_text("unchanged", encoding="ascii")
        (self.fixture.state / "journal.jsonl").symlink_to(outside)
        identity, _ = self.fixture.make_plan()
        with self.assertRaises(broker.BrokerError):
            self.fixture.publish(identity)
        self.assertEqual(outside.read_text(encoding="ascii"), "unchanged")
        self.assertEqual(
            git(
                self.root,
                "--git-dir",
                self.fixture.remote,
                "show-ref",
                "--quiet",
                check=False,
            ).returncode,
            1,
        )

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

    def test_indeterminate_push_is_quarantined_and_exactly_reconciled(self):
        original_publish = broker._publish
        identity, _ = self.fixture.make_plan()

        def committed_late(
            installation, plan, plan_identity, pack_path, deadline
        ):
            original_publish(
                installation,
                plan,
                plan_identity,
                pack_path,
                broker.OperationDeadline(
                    datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(seconds=30)
                ),
            )
            raise broker.IndeterminatePublication(
                "indeterminate", "simulated lost push result"
            )

        broker._publish = committed_late
        try:
            refs = self.fixture.publish(identity)
        finally:
            broker._publish = original_publish
        self.assertEqual(len(refs), 2)
        journal = (
            self.fixture.state / "journal.jsonl"
        ).read_text(encoding="ascii")
        self.assertIn('"result":"indeterminate"', journal)
        self.assertIn('"result":"committed-late"', journal)

        other_root = TEST_ROOT / uuid.uuid4().hex
        other = BrokerFixture(other_root)
        try:
            identity, _ = other.make_plan()
            original_reconcile = broker._reconcile_remote

            def unresolved(*_arguments, **_keywords):
                return "indeterminate", None

            def uncertain(*_arguments, **_keywords):
                raise broker.IndeterminatePublication(
                    "indeterminate", "simulated transport loss"
                )

            broker._publish = uncertain
            broker._reconcile_remote = unresolved
            try:
                with self.assertRaisesRegex(
                    broker.BrokerError, "indeterminate"
                ):
                    other.publish(identity)
            finally:
                broker._publish = original_publish
                broker._reconcile_remote = original_reconcile
            higher_identity, _ = other.make_plan(sequence=2)
            with self.assertRaisesRegex(
                broker.BrokerError, "indeterminate"
            ):
                other.publish(higher_identity)
            pid, connection = other.spawn(identity, operation="reconcile")
            try:
                outcome, reconciled = broker.reconcile_via_connection(
                    connection,
                    other.client_installation,
                    205,
                    enforce_peer=False,
                )
            finally:
                connection.close()
                os.waitpid(pid, 0)
            self.assertEqual(outcome, "safe-failed")
            self.assertEqual(set(reconciled.values()), {None})
            with self.assertRaisesRegex(broker.BrokerError, "replay"):
                other.publish(identity)
            self.assertEqual(len(other.publish(higher_identity)), 2)
        finally:
            other.close()
            shutil.rmtree(other_root, ignore_errors=True)

    def test_mixed_reconciliation_enters_security_hold(self):
        original_publish = broker._publish
        identity, _ = self.fixture.make_plan()

        def mixed_state(installation, plan, plan_identity, pack_path, deadline):
            original_publish(
                installation,
                plan,
                plan_identity,
                pack_path,
                broker.OperationDeadline(
                    datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(seconds=30)
                ),
            )
            git(
                self.root,
                "--git-dir",
                self.fixture.remote,
                "update-ref",
                "-d",
                plan["anchor_ref"],
            )
            raise broker.IndeterminatePublication(
                "indeterminate", "simulated mixed remote result"
            )

        broker._publish = mixed_state
        try:
            with self.assertRaisesRegex(
                broker.BrokerError, "security-hold"
            ):
                self.fixture.publish(identity)
        finally:
            broker._publish = original_publish
        journal = (
            self.fixture.state / "journal.jsonl"
        ).read_text(encoding="ascii")
        self.assertIn('"result":"security-hold"', journal)
        higher_identity, _ = self.fixture.make_plan(sequence=2)
        with self.assertRaisesRegex(broker.BrokerError, "security-hold"):
            self.fixture.publish(higher_identity)

    def test_concurrent_nonce_only_one_request_succeeds(self):
        identity, _ = self.fixture.make_plan()
        outcomes = []
        barrier = threading.Barrier(3)
        services = [self.fixture.spawn(identity) for _ in range(2)]

        def worker(service):
            pid, connection = service
            barrier.wait()
            try:
                broker.publish_via_connection(
                    connection,
                    self.fixture.client_installation,
                    205,
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
            "phase": "request",
            "request_nonce": "1" * 64,
            "repository": self.fixture.installation["repository"],
            "issue": 205,
            "operation": "publish",
            "pack_sha256": "3" * 64,
            "pack_size": 1,
            "request_deadline": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        response = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "phase": "ack",
            "request_digest": "0" * 64,
            "request_nonce": request["request_nonce"],
            "repository": request["repository"],
            "issue": 205,
            "plan_identity": "2" * 64,
            "capability_nonce": "4" * 64,
            "installation_id": self.fixture.installation["installation_id"],
            "broker_key_id": "broker-v1",
            "broker_pid": os.getpid(),
            "broker_uid": os.geteuid(),
            "broker_namespace_uid": os.geteuid(),
            "broker_user_namespace": os.stat(
                "/proc/self/ns/user", follow_symlinks=True
            ).st_ino,
            "effective_deadline": request["request_deadline"],
            "status": "ready",
            "code": "ready",
            "refs": None,
            "completed_at": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "signature": {
                "algorithm": "ed25519",
                "key_id": "broker-v1",
                "value": base64.b64encode(b"x" * 64).decode("ascii"),
            },
        }
        signed_schema.validate_record(
            response, "result", "broker response"
        )
        with self.assertRaisesRegex(broker.BrokerError, "does not bind"):
            broker._verify_response(
                response,
                request,
                self.fixture.client_installation,
                expected_phase="ack",
                now=datetime.datetime.now(datetime.timezone.utc),
                enforce_broker_process=False,
            )
        missing_signature = dict(response)
        del missing_signature["signature"]
        with self.assertRaisesRegex(broker.BrokerError, "fields differ"):
            broker._verify_response(
                missing_signature,
                request,
                self.fixture.client_installation,
                expected_phase="ack",
                now=datetime.datetime.now(datetime.timezone.utc),
                enforce_broker_process=False,
            )

    def test_pack_write_failure_drains_authenticated_final_rejection(self):
        identity, _ = self.fixture.make_plan()
        capability_fd, capability_record = self.fixture.make_capability(identity)
        os.close(capability_fd)
        capability = broker._validate_capability(
            capability_record,
            self.fixture.installation,
            now=datetime.datetime.now(datetime.timezone.utc),
        )
        large_pack = self.root / "large.pack"
        large_pack.write_bytes(b"p" * (4 * 1024 * 1024))
        client_side, broker_side = socket.socketpair()

        def rejecting_broker():
            try:
                request, _ = broker._recv_frame(
                    broker_side,
                    maximum=broker.REQUEST_MAX_BYTES,
                    label="request",
                    deadline=None,
                )
                deadline = broker.OperationDeadline(
                    broker._time(
                        request["request_deadline"], "request deadline"
                    )
                )
                ack = broker._response(
                    self.fixture.installation,
                    request,
                    capability,
                    deadline,
                    phase="ack",
                    status="ready",
                    code="ready",
                    refs=None,
                )
                final = broker._response(
                    self.fixture.installation,
                    request,
                    capability,
                    deadline,
                    phase="result",
                    status="error",
                    code="wrong-pack",
                    refs=None,
                )
                broker._send_frame(
                    broker_side,
                    ack,
                    maximum=broker.RESPONSE_MAX_BYTES,
                    deadline=deadline,
                )
                broker._recv_frame(
                    broker_side,
                    maximum=broker.REQUEST_MAX_BYTES,
                    label="continuation",
                    deadline=deadline,
                )
                broker_side.shutdown(socket.SHUT_RD)
                broker._send_frame(
                    broker_side,
                    final,
                    maximum=broker.RESPONSE_MAX_BYTES,
                    deadline=deadline,
                )
            finally:
                broker_side.close()

        thread = threading.Thread(target=rejecting_broker)
        thread.start()
        try:
            with self.assertRaisesRegex(broker.BrokerError, "wrong-pack"):
                broker.publish_via_connection(
                    client_side,
                    self.fixture.client_installation,
                    205,
                    large_pack,
                    enforce_peer=False,
                )
        finally:
            client_side.close()
            thread.join(10)

    def test_malformed_header_gets_signed_reject_before_pack(self):
        identity, _ = self.fixture.make_plan()
        pid, connection = self.fixture.spawn(identity)
        request = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "phase": "request",
            "request_nonce": os.urandom(32).hex(),
            "repository": self.fixture.installation["repository"],
            "issue": 205,
            "operation": "publish",
            "pack_sha256": hashlib.sha256(
                self.fixture.pack.read_bytes()
            ).hexdigest(),
            "pack_size": self.fixture.pack.stat().st_size,
            "request_deadline": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "candidate_extension": "forbidden",
        }
        broker._send_frame(
            connection,
            request,
            maximum=broker.REQUEST_MAX_BYTES,
            deadline=None,
        )
        response, _ = broker._recv_frame(
            connection,
            maximum=broker.RESPONSE_MAX_BYTES,
            label="signed rejection",
            deadline=None,
        )
        connection.close()
        os.waitpid(pid, 0)
        self.assertEqual(
            (response["phase"], response["status"], response["code"]),
            ("ack", "error", "invalid-record"),
        )
        key_id, signature = broker._load_signature_record(
            response["signature"], "response.signature"
        )
        self.assertEqual(key_id, "broker-v1")
        broker._verify_ed25519(
            self.fixture.broker_public,
            broker._signed_payload(broker.RESPONSE_DOMAIN, response),
            signature,
        )
        staging = self.fixture.state / "staging"
        self.assertFalse(staging.exists() and any(staging.iterdir()))

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
                if case == "directory":
                    other.publish(identity)
                    self.assertEqual(
                        git(
                            other.root,
                            "--git-dir",
                            moved,
                            "rev-parse",
                            broker._plan_ref(205, "authority"),
                        ).stdout.decode("ascii").strip(),
                        other.commit,
                    )
                else:
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

    def test_post_ack_remote_path_swap_cannot_replace_server_or_hook(self):
        identity, _ = self.fixture.make_plan()
        pid, connection = self.fixture.spawn(identity)
        deadline_text = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=30)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        request = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "phase": "request",
            "request_nonce": os.urandom(32).hex(),
            "repository": self.fixture.installation["repository"],
            "issue": 205,
            "operation": "publish",
            "pack_sha256": hashlib.sha256(
                self.fixture.pack.read_bytes()
            ).hexdigest(),
            "pack_size": self.fixture.pack.stat().st_size,
            "request_deadline": deadline_text,
        }
        broker._send_frame(
            connection,
            request,
            maximum=broker.REQUEST_MAX_BYTES,
            deadline=broker.OperationDeadline(
                broker._time(deadline_text, "request deadline")
            ),
        )
        ack, _ = broker._recv_frame(
            connection,
            maximum=broker.RESPONSE_MAX_BYTES,
            label="ack",
            deadline=None,
        )
        context, _refs, _pidfd = broker._verify_response(
            ack,
            request,
            self.fixture.client_installation,
            expected_phase="ack",
            now=datetime.datetime.now(datetime.timezone.utc),
            enforce_broker_process=False,
        )
        broker._send_frame(
            connection,
            {
                "schema_version": 1,
                "protocol": broker.PROTOCOL,
                "phase": "continue",
                "request_nonce": request["request_nonce"],
                "plan_identity": context["plan_identity"],
            },
            maximum=broker.REQUEST_MAX_BYTES,
            deadline=None,
        )
        original = self.root / "descriptor-bound.git"
        self.fixture.remote.rename(original)
        git(self.root, "init", "--bare", "--quiet", self.fixture.remote)
        marker = self.root / "malicious-hook-ran"
        malicious = self.fixture.remote / "hooks" / "pre-receive"
        malicious.write_text(
            f"#!/bin/sh\nprintf pwned > {marker}\nexit 0\n",
            encoding="ascii",
        )
        malicious.chmod(0o700)
        connection.sendall(self.fixture.pack.read_bytes())
        final, _ = broker._recv_frame(
            connection,
            maximum=broker.RESPONSE_MAX_BYTES,
            label="result",
            deadline=None,
        )
        _context, refs, _unused = broker._verify_response(
            final,
            request,
            self.fixture.client_installation,
            expected_phase="result",
            now=datetime.datetime.now(datetime.timezone.utc),
            expected_context=context,
            enforce_broker_process=False,
        )
        connection.close()
        os.waitpid(pid, 0)
        self.assertEqual(
            refs[broker._plan_ref(205, "authority")],
            self.fixture.commit,
        )
        self.assertFalse(marker.exists())
        self.assertEqual(
            git(
                self.root,
                "--git-dir",
                original,
                "rev-parse",
                broker._plan_ref(205, "authority"),
            ).stdout.decode("ascii").strip(),
            self.fixture.commit,
        )

    def test_recursive_remote_authority_rejects_nested_writes_symlinks_and_alternates(self):
        cases = ("writable-ref", "hook-symlink", "alternate", "commondir")
        for case in cases:
            with self.subTest(case=case):
                other_root = TEST_ROOT / uuid.uuid4().hex
                other = BrokerFixture(other_root)
                try:
                    remote = other.installation["protected_remote"]
                    for key in (
                        "git_dir_fd",
                        "objects_fd",
                        "refs_fd",
                        "hooks_fd",
                        "config_fd",
                        "packed_refs_fd",
                    ):
                        descriptor = remote.pop(key, None)
                        if descriptor is not None:
                            os.close(descriptor)
                    if case == "writable-ref":
                        (other.remote / "refs" / "heads").chmod(0o775)
                    elif case == "hook-symlink":
                        (other.remote / "hooks" / "candidate-hook").symlink_to(
                            other.root / "outside"
                        )
                    elif case == "alternate":
                        (other.remote / "objects" / "info" / "alternates").write_text(
                            os.fspath(other.root / "outside") + "\n",
                            encoding="ascii",
                        )
                    else:
                        (other.remote / "commondir").write_text(
                            os.fspath(other.root / "outside") + "\n",
                            encoding="ascii",
                        )
                    public = {
                        key: remote[key]
                        for key in (
                            "git_dir",
                            "git_dir_device",
                            "git_dir_inode",
                            "objects_device",
                            "objects_inode",
                            "config_sha256",
                            "hooks_sha256",
                        )
                    }
                    with self.assertRaises(broker.BrokerError):
                        broker._bind_protected_remote_descriptors(
                            public,
                            owners={os.geteuid()},
                            candidate_uid=other.installation["candidate_uid"],
                        )
                finally:
                    shutil.rmtree(other_root, ignore_errors=True)

    def test_packed_refs_support_preflight_and_publication(self):
        first_identity, _ = self.fixture.make_plan()
        first_commit = self.fixture.commit
        self.fixture.publish(first_identity)
        git(
            self.root,
            "--git-dir",
            self.fixture.remote,
            "pack-refs",
            "--all",
        )
        self.fixture.rebind_protected_remote()
        (self.fixture.source / "payload.txt").write_text(
            "authority two\n", encoding="ascii"
        )
        git(self.fixture.source, "add", "payload.txt")
        git(self.fixture.source, "commit", "--quiet", "-m", "authority two")
        self.fixture.commit = (
            git(self.fixture.source, "rev-parse", "HEAD")
            .stdout.decode("ascii")
            .strip()
        )
        self.fixture.pack.write_bytes(
            git(
                self.fixture.source,
                "pack-objects",
                "--stdout",
                "--revs",
                input_bytes=f"{self.fixture.commit}\n".encode("ascii"),
            ).stdout
        )
        self.fixture.object_ids = sorted(
            git(
                self.fixture.source,
                "rev-list",
                "--objects",
                "--no-object-names",
                self.fixture.commit,
            )
            .stdout.decode("ascii")
            .splitlines()
        )
        identity, _ = self.fixture.make_plan(
            expected_authority_oid=first_commit,
            expected_anchor_oid=first_commit,
            sequence=2,
        )
        pid, connection = self.fixture.spawn(
            identity, operation="preflight"
        )
        try:
            broker.preflight_via_connection(
                connection,
                self.fixture.client_installation,
                205,
                enforce_peer=False,
            )
        finally:
            connection.close()
            os.waitpid(pid, 0)
        refs = self.fixture.publish(identity)
        self.assertEqual(set(refs.values()), {self.fixture.commit})
        self.assertTrue(
            self.fixture.installation["protected_remote"][
                "_packed_refs_present"
            ]
        )
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
        pid, connection = self.fixture.spawn(identity)
        request = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "phase": "request",
            "request_nonce": os.urandom(32).hex(),
            "repository": self.fixture.installation["repository"],
            "issue": 205,
            "operation": "publish",
            "pack_sha256": hashlib.sha256(self.fixture.pack.read_bytes()).hexdigest(),
            "pack_size": self.fixture.pack.stat().st_size,
            "request_deadline": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        raw_request = reporter.normalized_json(request)
        connection.sendall(struct.pack(">I", len(raw_request)) + raw_request)
        ack_size = struct.unpack(">I", connection.recv(4))[0]
        ack = json.loads(connection.recv(ack_size))
        continuation = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "phase": "continue",
            "request_nonce": request["request_nonce"],
            "plan_identity": ack["plan_identity"],
        }
        raw_continuation = reporter.normalized_json(continuation)
        connection.sendall(
            struct.pack(">I", len(raw_continuation)) + raw_continuation
        )
        connection.sendall(self.fixture.pack.read_bytes()[:10])
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
            "test_only": False,
        }
        client_manifest.write_bytes(reporter.normalized_json(manifest))
        preflight_parent, preflight_child = socket.socketpair()
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    ROOT / "scripts/workflow_pilot/isolated_launcher.py",
                    "git-broker-preflight",
                    "--installation",
                    client_manifest,
                    "--connection-fd",
                    str(preflight_child.fileno()),
                    "--issue",
                    "205",
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                pass_fds=(preflight_child.fileno(),),
            )
        finally:
            preflight_parent.close()
            preflight_child.close()
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
                        "--capability-fd",
                        "4",
                        "--socket",
                        "@bad",
                    ]
                )
            with self.assertRaises(SystemExit):
                broker.build_parser().parse_args(
                    [
                        "publish",
                        "--installation",
                        os.fspath(client_manifest),
                        "--connection-fd",
                        "3",
                        "--issue",
                        "205",
                        "--pack",
                        os.fspath(self.fixture.pack),
                        "--plan-identity",
                        "0" * 64,
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
            "candidate_uid": os.geteuid(),
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
                key: (
                    os.fspath(
                        self.fixture.installation["protected_remote"][key]
                    )
                    if isinstance(
                        self.fixture.installation["protected_remote"][key],
                        Path,
                    )
                    else self.fixture.installation["protected_remote"][key]
                )
                for key in (
                    "git_dir",
                    "git_dir_device",
                    "git_dir_inode",
                    "objects_device",
                    "objects_inode",
                    "config_sha256",
                    "hooks_sha256",
                )
            },
            "pack_max_bytes": 1024,
            "operation_timeout_seconds": 10,
            "reconciliation_timeout_seconds": 10,
            "plan_lifetime_seconds": 300,
            "test_only": True,
        }
        service_manifest.write_bytes(reporter.normalized_json(service))
        parent, child = socket.socketpair()
        identity, _ = self.fixture.make_plan()
        capability_fd, _ = self.fixture.make_capability(identity)
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
                    "--capability-fd",
                    str(capability_fd),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                pass_fds=(child.fileno(), capability_fd),
            )
        finally:
            parent.close()
            child.close()
            os.close(capability_fd)
        self.assertEqual(service_result.returncode, 2)
        self.assertEqual(
            service_result.stderr.decode("ascii").strip(),
            "git-publication-broker: insecure-installation",
        )

    def test_signed_preflight_is_non_publication_and_observes_broker_principal(self):
        missing_client, missing_broker = socket.socketpair()
        missing_broker.close()
        try:
            with self.assertRaises(broker.BrokerError):
                broker.preflight_via_connection(
                    missing_client,
                    self.fixture.client_installation,
                    205,
                    enforce_peer=False,
                )
        finally:
            missing_client.close()

        identity, _ = self.fixture.make_plan()
        pid, connection = self.fixture.spawn(identity, operation="preflight")
        try:
            broker.preflight_via_connection(
                connection,
                self.fixture.client_installation,
                205,
                enforce_peer=False,
            )
        finally:
            connection.close()
            os.waitpid(pid, 0)
        self.assertEqual(self.fixture.remote_refs(), {})
        self.assertFalse((self.fixture.state / "journal.jsonl").exists())

        pid, connection = self.fixture.spawn(identity, operation="preflight")
        try:
            wrong_client = dict(self.fixture.client_installation)
            wrong_client["expected_broker_uid"] = os.geteuid() + 1
            with self.assertRaisesRegex(
                broker.BrokerError, "signed broker UID differs"
            ):
                broker.preflight_via_connection(
                    connection,
                    wrong_client,
                    205,
                    enforce_peer=True,
                )
        finally:
            connection.close()
            os.waitpid(pid, 0)

        pid, connection = self.fixture.spawn(identity, operation="preflight")
        try:
            with self.assertRaisesRegex(
                broker.BrokerError, "outer host UID"
            ):
                broker.preflight_via_connection(
                    connection,
                    self.fixture.client_installation,
                    205,
                    enforce_peer=True,
                )
        finally:
            connection.close()
            os.waitpid(pid, 0)

    def test_mapped_namespace_same_outer_uid_is_not_broker_authority(self):
        identity, _ = self.fixture.make_plan()
        capability_fd, _ = self.fixture.make_capability(
            identity, operation="preflight"
        )
        parent, child = socket.socketpair()
        remote = self.fixture.installation["protected_remote"]
        runtime = {
            "installation_id": self.fixture.installation["installation_id"],
            "repository": self.fixture.installation["repository"],
            "endpoint": self.fixture.installation["endpoint"],
            "expected_capability_uid": 991,
            "candidate_uid": 1000,
            "broker_key_id": "broker-v1",
            "broker_private_key": os.fspath(self.fixture.broker_private),
            "authority_public": os.fspath(self.fixture.authority_public),
            "plan_store": os.fspath(self.fixture.plan_store),
            "state_directory": os.fspath(self.fixture.state),
            "protected_remote": {
                key: (
                    os.fspath(remote[key])
                    if isinstance(remote[key], Path)
                    else remote[key]
                )
                for key in (
                    "git_dir",
                    "git_dir_device",
                    "git_dir_inode",
                    "objects_device",
                    "objects_inode",
                    "config_sha256",
                    "hooks_sha256",
                )
            },
        }
        runtime_path = self.root / "namespace-runtime.json"
        runtime_path.write_bytes(reporter.normalized_json(runtime))
        script = (
            "import json,os,socket,sys;"
            f"sys.path.insert(0,{str(ROOT)!r});"
            "from pathlib import Path;"
            "from scripts.workflow_pilot import git_publication_broker as b;"
            "d=json.loads(Path(sys.argv[1]).read_text());"
            "r=d['protected_remote'];r['git_dir']=Path(r['git_dir']);"
            "i={'installation_id':d['installation_id'],"
            "'repository':d['repository'],'endpoint':d['endpoint'],"
            "'expected_capability_uid':d['expected_capability_uid'],"
            "'candidate_uid':d['candidate_uid'],'broker_key_id':d['broker_key_id'],"
            "'broker_private_key':Path(d['broker_private_key']),"
            "'plan_signers':{'authority-v1':{'public_key':Path(d['authority_public']),"
            "'signer':'external-installation','actor':'workflow-coordinator'}},"
            "'plan_store':Path(d['plan_store']),'state_directory':Path(d['state_directory']),"
            "'authentication':{'mode':'local-test'},'pack_max_bytes':8388608,"
            "'operation_timeout_seconds':10,'reconciliation_timeout_seconds':10,"
            "'plan_lifetime_seconds':300,'test_only':True};"
            "i['_authority_owners']={os.geteuid(),os.stat('/').st_uid};"
            "i['plan_store_fd']=os.open(i['plan_store'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);"
            "i['protected_remote']=b._bind_protected_remote_descriptors("
            "r,owners={os.geteuid()},candidate_uid=i['candidate_uid']);"
            "b._check_protected_remote(i['protected_remote']);"
            "c=b._read_sealed_capability(int(sys.argv[3]));"
            "s=socket.socket(fileno=int(sys.argv[2]));"
            "b.serve_connection(s,i,c,enforce_peer=True)"
        )
        process = subprocess.Popen(
            [
                "unshare",
                "--user",
                "--map-user=991",
                sys.executable,
                "-I",
                "-c",
                script,
                runtime_path,
                str(child.fileno()),
                str(capability_fd),
            ],
            cwd=ROOT,
            pass_fds=(child.fileno(), capability_fd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        child.close()
        os.close(capability_fd)
        client = dict(self.fixture.client_installation)
        client["expected_broker_uid"] = os.geteuid()
        client["expected_capability_uid"] = os.geteuid()
        client_error = None
        try:
            broker.preflight_via_connection(
                parent, client, 205, enforce_peer=True
            )
        except BaseException as error:
            client_error = error
        finally:
            parent.close()
        stdout, stderr = process.communicate(timeout=20)
        self.assertIsInstance(client_error, broker.BrokerError)
        self.assertIn("outer host UID", str(client_error))
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr, b"")
        self.assertEqual(self.fixture.remote_refs(), {})

    def test_https_askpass_path_publishes_without_credential_disclosure(self):
        server = AuthenticatedGitServer(self.fixture)
        try:
            credential = self.fixture.configure_https_authentication(server)
            identity, _ = self.fixture.make_plan()
            preflight_pid, preflight_connection = self.fixture.spawn(
                identity, operation="preflight"
            )
            server.start()
            try:
                broker.preflight_via_connection(
                    preflight_connection,
                    self.fixture.client_installation,
                    205,
                    enforce_peer=False,
                )
            finally:
                preflight_connection.close()
                os.waitpid(preflight_pid, 0)
            self.assertEqual(
                git(
                    self.root,
                    "--git-dir",
                    self.fixture.remote,
                    "show-ref",
                    "--quiet",
                    check=False,
                ).returncode,
                1,
            )
            pid, connection = self.fixture.spawn(identity)
            try:
                refs = broker.publish_via_connection(
                    connection,
                    self.fixture.client_installation,
                    205,
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

    def test_preflight_rejects_anonymous_read_only_and_expired_https_credentials(self):
        cases = (
            ("anonymous-public-read", False, True, True),
            ("read-only-token", False, False, False),
            ("expired-token", True, False, True),
        )
        for name, write_allowed, anonymous_read, replace_credential in cases:
            with self.subTest(name=name):
                other_root = TEST_ROOT / uuid.uuid4().hex
                other = BrokerFixture(other_root)
                server = AuthenticatedGitServer(
                    other,
                    write_allowed=write_allowed,
                    anonymous_read=anonymous_read,
                )
                try:
                    credential = other.configure_https_authentication(server)
                    if replace_credential:
                        credential.write_text(
                            f"{name}-credential", encoding="ascii"
                        )
                    identity, _ = other.make_plan()
                    pid, connection = other.spawn(
                        identity, operation="preflight"
                    )
                    server.start()
                    try:
                        with self.assertRaises(broker.BrokerError):
                            broker.preflight_via_connection(
                                connection,
                                other.client_installation,
                                205,
                                enforce_peer=False,
                            )
                    finally:
                        connection.close()
                        os.waitpid(pid, 0)
                    self.assertEqual(
                        git(
                            other.root,
                            "--git-dir",
                            other.remote,
                            "show-ref",
                            "--quiet",
                            check=False,
                        ).returncode,
                        1,
                    )
                finally:
                    server.close()
                    other.close()
                    shutil.rmtree(other_root, ignore_errors=True)

    def test_ssh_agent_read_only_transport_fails_exact_write_probe(self):
        agent_socket = ROOT / "build" / f"ssh-agent-{uuid.uuid4().hex[:8]}"
        agent_socket.parent.mkdir(parents=True, exist_ok=True)
        agent = subprocess.Popen(
            ["/usr/bin/ssh-agent", "-D", "-a", agent_socket],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if agent_socket.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(agent_socket.exists())
            private_key = self.root / "ssh-test-key"
            run(
                [
                    "/usr/bin/ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-f",
                    private_key,
                ]
            )
            environment = {
                "PATH": "/usr/bin:/bin",
                "SSH_AUTH_SOCK": os.fspath(agent_socket),
            }
            run(["/usr/bin/ssh-add", private_key], env=environment)
            ssh_config = self.root / "ssh-config"
            ssh_config.write_text(
                "Host example.invalid\n"
                "  BatchMode yes\n"
                "  StrictHostKeyChecking yes\n",
                encoding="ascii",
            )
            installation = dict(self.fixture.installation)
            installation["endpoint"] = (
                "ssh://git@example.invalid/"
                "laqieer/fireemblem8-expansion.git"
            )
            installation["authentication"] = {
                "mode": "ssh-agent",
                "agent_socket": agent_socket,
                "ssh_config": ssh_config,
            }
            identity, plan = self.fixture.make_plan()
            del identity
            original_git = broker._git
            original_remote_refs = broker._remote_refs
            observed_dry_run = []

            def exact_remote(*_arguments, **_keywords):
                return {
                    plan["authority_ref"]: plan["expected_authority_oid"],
                    plan["anchor_ref"]: plan["expected_anchor_oid"],
                }

            def read_only_git(
                current_installation,
                home,
                arguments,
                **keywords,
            ):
                if "push" in arguments and "--dry-run" in arguments:
                    observed_dry_run.append(tuple(arguments))
                    raise broker.BrokerError(
                        "git-failed", "SSH principal is read-only"
                    )
                return original_git(
                    current_installation,
                    home,
                    arguments,
                    **keywords,
                )

            broker._remote_refs = exact_remote
            broker._git = read_only_git
            try:
                with self.assertRaisesRegex(broker.BrokerError, "read-only"):
                    broker._credential_readiness(
                        installation,
                        plan,
                        broker.OperationDeadline(
                            datetime.datetime.now(datetime.timezone.utc)
                            + datetime.timedelta(seconds=20)
                        ),
                    )
            finally:
                broker._git = original_git
                broker._remote_refs = original_remote_refs
            self.assertEqual(len(observed_dry_run), 1)
            command = observed_dry_run[0]
            self.assertIn("--atomic", command)
            self.assertIn("--dry-run", command)
        finally:
            agent.terminate()
            agent.wait(timeout=10)
            agent_socket.unlink(missing_ok=True)

    def test_timeout_kills_git_process_group_and_leaves_refs_unchanged(self):
        ready, pid_file = self.fixture.install_stalling_hook()
        self.fixture.installation["operation_timeout_seconds"] = 1
        self.fixture.client_installation["operation_timeout_seconds"] = 5
        identity, _ = self.fixture.make_plan()
        with self.assertRaisesRegex(broker.BrokerError, "safe-failed"):
            self.fixture.publish(identity)
        self.assertTrue(ready.exists())
        hook_pid = int(pid_file.read_text(encoding="ascii"))
        for _ in range(50):
            if not Path(f"/proc/{hook_pid}").exists():
                break
            time.sleep(0.02)
        self.assertFalse(Path(f"/proc/{hook_pid}").exists())
        self.assertEqual(self.fixture.remote_refs(), {})

    def test_effective_deadline_clamps_push_and_reaches_server_hook(self):
        ready, deadline_log = self.fixture.install_deadline_hook()
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        identity, _ = self.fixture.make_plan(
            issued_at=(now - datetime.timedelta(seconds=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            expires_at=(now + datetime.timedelta(seconds=4)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )
        with self.assertRaisesRegex(broker.BrokerError, "safe-failed"):
            self.fixture.publish(identity)
        self.assertTrue(ready.exists())
        mutation_deadline = reporter.parse_time(
            deadline_log.read_text(encoding="ascii"), "hook deadline"
        )
        self.assertLessEqual(
            mutation_deadline,
            now + datetime.timedelta(seconds=4),
        )
        self.assertEqual(self.fixture.remote_refs(), {})
        journal = (
            self.fixture.state / "journal.jsonl"
        ).read_text(encoding="ascii")
        self.assertIn('"result":"indeterminate"', journal)
        self.assertIn('"result":"safe-failed"', journal)

    def test_killing_broker_kills_credential_bearing_git_child(self):
        ready, pid_file = self.fixture.install_stalling_hook()
        self.fixture.installation["operation_timeout_seconds"] = 30
        identity, _ = self.fixture.make_plan()
        pid, connection = self.fixture.spawn(identity)
        outcome = []

        def request():
            try:
                broker.publish_via_connection(
                    connection,
                    self.fixture.client_installation,
                    205,
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
            if not process_is_running(hook_pid):
                break
            time.sleep(0.02)
        self.assertFalse(process_is_running(hook_pid))
        self.assertEqual(self.fixture.remote_refs(), {})
        self.assertTrue(outcome)
        reconcile_pid, reconcile_connection = self.fixture.spawn(
            identity, operation="reconcile"
        )
        try:
            reconciliation, refs = broker.reconcile_via_connection(
                reconcile_connection,
                self.fixture.client_installation,
                205,
                enforce_peer=False,
            )
        finally:
            reconcile_connection.close()
            os.waitpid(reconcile_pid, 0)
        self.assertEqual(reconciliation, "safe-failed")
        self.assertEqual(set(refs.values()), {None})

    def test_git_child_parent_death_covers_pre_watchdog_window(self):
        ready_read, ready_write = os.pipe()
        worker = os.fork()
        if worker == 0:
            os.close(ready_read)

            def blocked_watchdog_fork():
                os.write(ready_write, b"ready")
                time.sleep(30)
                os._exit(91)

            broker.os.fork = blocked_watchdog_fork
            broker._run_bounded(
                [sys.executable, "-c", "import time;time.sleep(30)"],
                environment={"PATH": "/usr/bin:/bin"},
                timeout=30,
            )
            os._exit(92)
        os.close(ready_write)
        self.assertEqual(os.read(ready_read, 5), b"ready")
        os.close(ready_read)
        children = [
            int(value)
            for value in Path(
                f"/proc/{worker}/task/{worker}/children"
            ).read_text(encoding="ascii").split()
        ]
        self.assertEqual(len(children), 1)
        git_child = children[0]
        os.kill(worker, signal.SIGKILL)
        os.waitpid(worker, 0)
        for _ in range(100):
            if not process_is_running(git_child):
                break
            time.sleep(0.01)
        self.assertFalse(process_is_running(git_child))

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
        pid, connection = self.fixture.spawn(identity)
        try:
            with self.assertRaisesRegex(broker.BrokerError, "pack size"):
                broker.publish_via_connection(
                    connection,
                    client,
                    205,
                    self.fixture.pack,
                    enforce_peer=False,
                )
        finally:
            connection.close()
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)

        pid, connection = self.fixture.spawn(identity)
        connection.sendall(struct.pack(">I", broker.REQUEST_MAX_BYTES + 1))
        connection.close()
        os.waitpid(pid, 0)
        self.assertEqual(self.fixture.remote_refs(), {})

        client_side, fake_broker = socket.socketpair()

        def oversized_response():
            try:
                fake_broker.recv(65536)
                try:
                    fake_broker.sendall(
                        b"x" * (broker.RESPONSE_MAX_BYTES + 1)
                    )
                except OSError:
                    pass
            finally:
                fake_broker.close()

        thread = threading.Thread(target=oversized_response)
        thread.start()
        try:
            with self.assertRaisesRegex(broker.BrokerError, "exceeds its size limit"):
                broker.publish_via_connection(
                    client_side,
                    self.fixture.client_installation,
                    205,
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


def run_separate_principal_integration():
    if os.geteuid() != 0:
        raise RuntimeError("separate-principal helper requires root")
    broker_account = pwd.getpwnam("nobody")
    candidate_account = pwd.getpwnam("daemon")
    if len(
        {
            0,
            broker_account.pw_uid,
            candidate_account.pw_uid,
        }
    ) != 3:
        raise RuntimeError("integration principals must be distinct")
    base = Path("/var/lib") / f"workflow-pilot-broker-test-{uuid.uuid4().hex}"
    broker_root = base / "broker"
    client_root = base / "client"
    service_code = base / "service-code"
    base.mkdir(mode=0o755)
    client_root.mkdir(mode=0o755)
    (service_code / "scripts").mkdir(parents=True, mode=0o755)
    shutil.copytree(
        ROOT / "scripts" / "workflow_pilot",
        service_code / "scripts" / "workflow_pilot",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for path in service_code.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    fixture = BrokerFixture(broker_root)
    try:
        remote = fixture.installation["protected_remote"]
        service = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "installation_id": fixture.installation["installation_id"],
            "repository": fixture.installation["repository"],
            "endpoint": fixture.installation["endpoint"],
            "expected_capability_uid": 0,
            "candidate_uid": candidate_account.pw_uid,
            "broker_key_id": "broker-v1",
            "broker_private_key": os.fspath(fixture.broker_private),
            "plan_signers": {
                "authority-v1": {
                    "public_key": os.fspath(fixture.authority_public),
                    "signer": "external-installation",
                    "actor": "workflow-coordinator",
                }
            },
            "plan_store": os.fspath(fixture.plan_store),
            "state_directory": os.fspath(fixture.state),
            "authentication": {"mode": "local-test"},
            "protected_remote": {
                key: (
                    os.fspath(remote[key])
                    if isinstance(remote[key], Path)
                    else remote[key]
                )
                for key in (
                    "git_dir",
                    "git_dir_device",
                    "git_dir_inode",
                    "objects_device",
                    "objects_inode",
                    "config_sha256",
                    "hooks_sha256",
                )
            },
            "pack_max_bytes": 8 * 1024 * 1024,
            "operation_timeout_seconds": 20,
            "reconciliation_timeout_seconds": 20,
            "plan_lifetime_seconds": 300,
            "test_only": True,
        }
        service_path = broker_root / "service.json"
        service_path.write_bytes(reporter.normalized_json(service))
        public_key = client_root / "broker.public.pem"
        public_key.write_bytes(fixture.broker_public.read_bytes())
        client_pack = client_root / "objects.pack"
        client_pack.write_bytes(fixture.pack.read_bytes())
        client = {
            "schema_version": 1,
            "protocol": broker.PROTOCOL,
            "installation_id": fixture.installation["installation_id"],
            "repository": fixture.installation["repository"],
            "endpoint": fixture.installation["endpoint"],
            "expected_broker_uid": broker_account.pw_uid,
            "expected_capability_uid": 0,
            "broker_key_id": "broker-v1",
            "broker_public_key": os.fspath(public_key),
            "pack_max_bytes": 8 * 1024 * 1024,
            "operation_timeout_seconds": 20,
            "test_only": True,
        }
        client_path = client_root / "client.json"
        client_path.write_bytes(reporter.normalized_json(client))
        fixture.close()

        for path in sorted(broker_root.rglob("*"), reverse=True):
            os.chown(
                path,
                broker_account.pw_uid,
                broker_account.pw_gid,
                follow_symlinks=False,
            )
            if path.is_dir():
                path.chmod(0o700)
            elif path == fixture.remote / "hooks" / "pre-receive":
                path.chmod(0o700)
            else:
                path.chmod(stat.S_IMODE(path.stat().st_mode) & 0o700 or 0o600)
        os.chown(
            broker_root,
            broker_account.pw_uid,
            broker_account.pw_gid,
        )
        broker_root.chmod(0o700)
        service["protected_remote"]["config_sha256"] = hashlib.sha256(
            (fixture.remote / "config").read_bytes()
        ).hexdigest()
        service["protected_remote"]["hooks_sha256"] = broker._tree_digest(
            fixture.remote / "hooks"
        )
        service_path.write_bytes(reporter.normalized_json(service))
        os.chown(
            service_path,
            broker_account.pw_uid,
            broker_account.pw_gid,
        )
        service_path.chmod(0o600)
        for path in client_root.iterdir():
            os.chown(path, 0, 0)
            path.chmod(0o644)
        os.chown(client_root, 0, 0)
        os.chown(base, 0, 0)

        def run_cli(operation):
            capability_fd, _ = fixture.make_capability(
                identity, operation=operation
            )
            issuer_side, candidate_side = socket.socketpair()
            os.set_inheritable(capability_fd, True)
            os.set_inheritable(issuer_side.fileno(), True)
            os.set_inheritable(candidate_side.fileno(), True)
            broker_pid = os.fork()
            if broker_pid == 0:
                issuer_side.close()
                os.setgroups([])
                os.setgid(broker_account.pw_gid)
                os.setuid(broker_account.pw_uid)
                os.execv(
                    sys.executable,
                    [
                        sys.executable,
                        "-I",
                        os.fspath(
                            service_code
                            / "scripts"
                            / "workflow_pilot"
                            / "isolated_launcher.py"
                        ),
                        "git-broker-serve",
                        "--installation",
                        os.fspath(service_path),
                        "--connection-fd",
                        str(candidate_side.fileno()),
                        "--capability-fd",
                        str(capability_fd),
                    ],
                )
            candidate_pid = os.fork()
            if candidate_pid == 0:
                candidate_side.close()
                os.setgroups([])
                os.setgid(candidate_account.pw_gid)
                os.setuid(candidate_account.pw_uid)
                null_fd = os.open("/dev/null", os.O_WRONLY)
                os.dup2(null_fd, 1)
                os.dup2(null_fd, 2)
                arguments = [
                    sys.executable,
                    "-I",
                    os.fspath(
                        ROOT
                        / "scripts"
                        / "workflow_pilot"
                        / "isolated_launcher.py"
                    ),
                    f"git-broker-{operation}",
                    "--installation",
                    os.fspath(client_path),
                    "--connection-fd",
                    str(issuer_side.fileno()),
                    "--issue",
                    "205",
                ]
                if operation == "publish":
                    arguments.extend(["--pack", os.fspath(client_pack)])
                os.execv(sys.executable, arguments)
            issuer_side.close()
            candidate_side.close()
            os.close(capability_fd)
            _broker_waited, broker_status = os.waitpid(broker_pid, 0)
            _candidate_waited, candidate_status = os.waitpid(candidate_pid, 0)
            if not os.WIFEXITED(broker_status):
                return 128
            if not os.WIFEXITED(candidate_status):
                return 128
            return os.WEXITSTATUS(candidate_status) or os.WEXITSTATUS(
                broker_status
            )

        identity, _ = fixture.make_plan()
        preflight_status = run_cli("preflight")
        publish_status = run_cli("publish")

        denied_pid = os.fork()
        if denied_pid == 0:
            os.setgroups([])
            os.setgid(candidate_account.pw_gid)
            os.setuid(candidate_account.pw_uid)
            try:
                os.open(fixture.broker_private, os.O_RDONLY)
            except PermissionError:
                os._exit(0)
            os._exit(1)
        _waited, denied_status = os.waitpid(denied_pid, 0)
        refs = fixture.remote_refs()
        print(
            json.dumps(
                {
                    "preflight": preflight_status,
                    "publish": publish_status,
                    "candidate_file_denied": (
                        os.WIFEXITED(denied_status)
                        and os.WEXITSTATUS(denied_status) == 0
                    ),
                    "refs": refs,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    finally:
        fixture.close()
        shutil.rmtree(base, ignore_errors=True)


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
                "test_only",
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
                "candidate_uid",
                "broker_key_id",
                "broker_private_key",
                "plan_signers",
                "plan_store",
                "state_directory",
                "authentication",
                "protected_remote",
                "pack_max_bytes",
                "operation_timeout_seconds",
                "reconciliation_timeout_seconds",
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

    def test_hosted_workflow_runs_sudo_capable_separate_principal_test(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py "
            "reporter-tests",
            workflow,
        )

    def test_real_separate_principal_cli_integration_when_sudo_is_available(self):
        if subprocess.run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode != 0:
            self.skipTest(
                "passwordless sudo unavailable; hosted host-tests runs this path"
            )
        code = (
            f"import sys;sys.path.insert(0,{str(ROOT)!r});"
            "from scripts.workflow_pilot.tests.test_git_publication_broker "
            "import run_separate_principal_integration;"
            "run_separate_principal_integration()"
        )
        completed = subprocess.run(
            ["sudo", "-n", sys.executable, "-I", "-c", code],
            cwd=ROOT,
            capture_output=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["preflight"], 0)
        self.assertEqual(result["publish"], 0)
        self.assertTrue(result["candidate_file_denied"])
        self.assertEqual(len(result["refs"]), 2)

    def test_signed_schemas_and_parser_have_semantic_timestamp_parity(self):
        for filename in (
            "git_publication_plan.schema.json",
            "git_publication_capability.schema.json",
            "git_publication_result.schema.json",
        ):
            schema = json.loads(
                (ROOT / "scripts" / "workflow_pilot" / filename).read_text(
                    encoding="ascii"
                )
            )
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
            self.assertEqual(
                schema["properties"]["protocol"]["const"], broker.PROTOCOL
            )
            fields = (
                ("effective_deadline", "completed_at")
                if filename == "git_publication_result.schema.json"
                else ("issued_at", "expires_at")
            )
            for field in fields:
                definition = schema["properties"][field]
                for value in (
                    "2024-02-29T23:59:59Z",
                    "2026-09-05T00:00:00Z",
                ):
                    self.assertEqual(
                        reporter.parse_schema_time(
                            value, f"{filename}.{field}", definition
                        ),
                        reporter.parse_time(value, f"{filename}.{field}"),
                    )
                for value in (
                    "0000-01-01T00:00:00Z",
                    "2026-02-29T00:00:00Z",
                    "2026-04-31T00:00:00Z",
                    "2026-09-05T24:00:00Z",
                    "2026-09-05T23:59:59.1Z",
                    "2026-09-05T23:59:59+00:00",
                ):
                    with self.assertRaises(reporter.PilotDataError):
                        reporter.parse_schema_time(
                            value, f"{filename}.{field}", definition
                        )
                    with self.assertRaises(reporter.PilotDataError):
                        reporter.parse_time(value, f"{filename}.{field}")

                pattern = definition["pattern"]
                self.assertIsNotNone(
                    re.fullmatch(pattern, "2026-04-31T00:00:00Z")
                )
                self.assertIsNone(
                    re.fullmatch(pattern, "2026-01-01T24:00:00Z")
                )
                self.assertIsNone(
                    re.fullmatch(pattern, "2026-01-01T99:99:99Z")
                )

    def test_result_schema_covers_every_broker_code(self):
        source = ast.parse(
            (
                ROOT
                / "scripts"
                / "workflow_pilot"
                / "git_publication_broker.py"
            ).read_text(encoding="utf-8")
        )
        emitted = {"ready", "published", "committed-late", "safe-failed"}
        for node in ast.walk(source):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if (
                name
                in {
                    "_fail",
                    "BrokerError",
                    "IndeterminatePublication",
                }
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                emitted.add(node.args[0].value)
        schema = signed_schema.load_schema("result")
        self.assertLessEqual(
            emitted,
            set(schema["properties"]["code"]["enum"]),
        )

    def test_internal_helper_calls_use_declared_keyword_names(self):
        source = ast.parse(
            (
                ROOT
                / "scripts"
                / "workflow_pilot"
                / "git_publication_broker.py"
            ).read_text(encoding="utf-8")
        )
        parameters = {}
        for node in source.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parameters[node.name] = {
                    argument.arg
                    for argument in (
                        node.args.posonlyargs
                        + node.args.args
                        + node.args.kwonlyargs
                    )
                }
        violations = []
        for node in ast.walk(source):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Name)
                or node.func.id not in parameters
            ):
                continue
            unknown = {
                keyword.arg
                for keyword in node.keywords
                if keyword.arg is not None
            } - parameters[node.func.id]
            if unknown:
                violations.append((node.func.id, sorted(unknown)))
        self.assertEqual(violations, [])

    def test_normal_plan_and_capability_validation_use_registered_format(self):
        valid_identity, valid_plan = self.fixture_for_schema.make_plan()
        valid_path = (
            self.fixture_for_schema.plan_store
            / f"{valid_identity}.json"
        )
        completed = run(
            [
                sys.executable,
                "-I",
                ROOT / "scripts/workflow_pilot/isolated_launcher.py",
                "validate-signed-record",
                "--schema",
                "plan",
                "--input",
                valid_path,
            ],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        signed_schema.validate_record(
            valid_plan, "plan", "valid signed plan"
        )

        identity, plan = self.fixture_for_schema.make_plan(
            issued_at="2026-04-31T00:00:00Z"
        )
        invalid_path = (
            self.fixture_for_schema.plan_store / f"{identity}.json"
        )
        completed = run(
            [
                sys.executable,
                "-I",
                ROOT / "scripts/workflow_pilot/isolated_launcher.py",
                "validate-signed-record",
                "--schema",
                "plan",
                "--input",
                invalid_path,
            ],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        with self.assertRaisesRegex(broker.BrokerError, "valid timestamp"):
            broker._validate_plan(
                plan,
                self.fixture_for_schema.installation,
                now=datetime.datetime.now(datetime.timezone.utc),
            )
        capability_fd, capability = self.fixture_for_schema.make_capability(
            identity,
            issued_at="0000-01-01T00:00:00Z",
        )
        os.close(capability_fd)
        with self.assertRaisesRegex(broker.BrokerError, "valid timestamp"):
            broker._validate_capability(
                capability,
                self.fixture_for_schema.installation,
                now=datetime.datetime.now(datetime.timezone.utc),
            )

    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.schema_root = TEST_ROOT / uuid.uuid4().hex
        self.fixture_for_schema = BrokerFixture(self.schema_root)

    def tearDown(self):
        self.fixture_for_schema.close()
        shutil.rmtree(self.schema_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
