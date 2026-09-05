#!/usr/bin/env python3
"""Installed Linux broker/client entry point. Never execute from a candidate."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import signal
import socket
import sqlite3
import ssl
import stat
import struct
import sys
import time
from datetime import timedelta
from pathlib import Path


if __name__ == "__main__":
    if not sys.flags.isolated:
        print("git-broker: isolated Python startup (-I) required", file=sys.stderr)
        raise SystemExit(2)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.workflow_pilot.git_broker_protocol import (
    HELLO_DOMAIN, MAX_JSON, MAX_LIFETIME, MAX_PACK, MAX_RESPONSE, PROTOCOL,
    RESPONSE_DOMAIN, Policy, plan_digest, validate_hello, validate_plan, validate_response,
)
from scripts.workflow_pilot.git_broker_store import PublicationStore, clean_environment, run_bounded
from scripts.workflow_pilot.signed_records import (
    RecordError, canonical_json, digest, fields, format_utc, integer, parse_utc,
    public_key, signed_payload, strict_json, utc_now, verify_signature,
)


INSTALLATION_MAX = 64 * 1024
SERVER_FIELDS = {
    "schema_version", "role", "policy", "broker_uid", "coordinator_uid", "candidate_uids",
    "socket", "state", "certificate", "private_key", "ca_certificate",
    "server_certificate_sha256", "response_public_key", "response_private_key", "transport",
}
CLIENT_FIELDS = {
    "schema_version", "role", "policy", "broker_uid", "coordinator_uid", "candidate_uids",
    "socket", "certificate", "private_key", "ca_certificate",
    "server_certificate_sha256", "response_public_key",
}
INSTALLED_MODULES = (
    "__init__.py", "signed_records.py", "git_broker_protocol.py",
    "git_broker_store.py", "git_broker.py",
)


def protected_path(path: str | Path, owners: set[int], *, directory: bool = False, secret: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise RecordError("installation paths must be absolute and traversal-free")
    current = Path("/")
    for index, component in enumerate(path.parts[1:]):
        current /= component
        metadata = current.lstat()
        last = index == len(path.parts) - 2
        if (
            metadata.st_uid not in owners or metadata.st_mode & 0o022
            or stat.S_ISLNK(metadata.st_mode)
            or (not last and not stat.S_ISDIR(metadata.st_mode))
        ):
            raise RecordError("installation is candidate-writable or follows a link")
        if last:
            if directory and not stat.S_ISDIR(metadata.st_mode):
                raise RecordError("protected directory required")
            if not directory and not stat.S_ISREG(metadata.st_mode):
                raise RecordError("protected regular file required")
            if not directory and metadata.st_nlink != 1:
                raise RecordError("protected file must have one link")
            if secret and metadata.st_mode & 0o077:
                raise RecordError("private broker/coordinator state is not private")
    return path


def read_regular(path: Path, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise RecordError("regular input size bound")
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(raw)))
            if not chunk:
                return bytes(raw)
            raw.extend(chunk)
        raise RecordError("input grew beyond size bound")
    finally:
        os.close(descriptor)


def certificate_fingerprint(path: Path) -> str:
    raw = read_regular(path, 32768).decode("ascii")
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(raw)).hexdigest()


def load_installation(path: Path, role: str) -> tuple[dict, Policy]:
    protected_path(path, {0})
    manifest = strict_json(read_regular(path, INSTALLATION_MAX), INSTALLATION_MAX)
    fields(manifest, SERVER_FIELDS if role == "server" else CLIENT_FIELDS)
    if manifest["schema_version"] != 1 or type(manifest["schema_version"]) is not int or manifest["role"] != role:
        raise RecordError("wrong protected installation role/version")
    policy = Policy.parse(manifest["policy"])
    broker = integer(manifest["broker_uid"], 1, 2**31 - 1)
    coordinator = integer(manifest["coordinator_uid"], 1, 2**31 - 1)
    candidates = manifest["candidate_uids"]
    if not isinstance(candidates, list) or not candidates or len(candidates) > 64:
        raise RecordError("candidate principal set is required")
    for candidate in candidates:
        integer(candidate, 1, 2**31 - 1)
    if (
        len(set(candidates)) != len(candidates) or broker == coordinator
        or broker in candidates or coordinator in candidates
        or os.geteuid() != (broker if role == "server" else coordinator)
    ):
        raise RecordError("broker, coordinator and candidates require separate OS principals")
    module_root = Path(__file__).resolve().parent
    for name in INSTALLED_MODULES:
        protected_path(module_root / name, {0})
    protected_path(module_root.parent, {0}, directory=True)
    actor = broker if role == "server" else coordinator
    for name in ("certificate", "ca_certificate"):
        protected_path(manifest[name], {0, actor})
    protected_path(manifest["private_key"], {0, actor}, secret=True)
    digest(manifest["server_certificate_sha256"])
    public_key(manifest["response_public_key"])
    endpoint = manifest["socket"]
    if not isinstance(endpoint, str) or not endpoint.startswith("/") or len(endpoint.encode()) > 100:
        raise RecordError("only protected filesystem Unix endpoints are supported")
    protected_path(Path(endpoint).parent, {0, broker}, directory=True)
    if role == "client":
        if certificate_fingerprint(Path(manifest["certificate"])) != policy.client_certificate_sha256:
            raise RecordError("client certificate is not authorized by installed policy")
    else:
        for executable in ("/usr/bin/git", "/usr/bin/openssl", "/usr/bin/timeout"):
            resolved = protected_path(Path(executable).resolve(strict=True), {0})
            if not os.access(resolved, os.X_OK):
                raise RecordError("protected executable dependency is unavailable")
        protected_path("/usr/lib/git-core", {0}, directory=True)
        if certificate_fingerprint(Path(manifest["certificate"])) != manifest["server_certificate_sha256"]:
            raise RecordError("server certificate does not match installed identity")
        protected_path(manifest["state"], {0, broker}, directory=True, secret=True)
        protected_path(manifest["response_private_key"], {0, broker}, secret=True)
        transport = manifest["transport"]
        if not isinstance(transport, dict):
            raise RecordError("installed transport required")
        kind = transport.get("kind")
        if kind == "local":
            fields(transport, {"kind"})
            if not policy.endpoint.startswith("file:///"):
                raise RecordError("local transport/endpoint mismatch")
            remote = protected_path(policy.endpoint[7:], {0, broker}, directory=True, secret=True)
            # A symlink, alternate object store, special file or writable hook
            # invalidates this mode, even if Git would otherwise ignore it.
            count = 0
            for root, directories, files in os.walk(remote, followlinks=False):
                for name in directories + files:
                    count += 1
                    if count > 20000:
                        raise RecordError("protected local remote resource bound")
                    child = Path(root) / name
                    protected_path(child, {0, broker}, directory=child.is_dir())
            for relative in ("objects/info/alternates", "objects/info/http-alternates", "info/grafts", "shallow"):
                if os.path.lexists(remote / relative):
                    raise RecordError("alternate/incomplete local authority store")
        elif kind == "https":
            fields(transport, {"kind", "token_file", "helper"})
            if policy.endpoint != f"https://github.com/{policy.repository}.git":
                raise RecordError("HTTPS endpoint mismatch")
            protected_path(transport["token_file"], {0, broker}, secret=True)
            if Path(transport["helper"]) != Path(__file__).resolve():
                raise RecordError("credential helper must be this exact trusted installed entry point")
            protected_path(transport["helper"], {0})
            protected_path(Path("/usr/lib/git-core/git-remote-https").resolve(strict=True), {0})
        elif kind == "ssh":
            fields(transport, {"kind", "key", "known_hosts"})
            if policy.endpoint != f"ssh://git@github.com/{policy.repository}.git":
                raise RecordError("SSH endpoint mismatch")
            protected_path(transport["key"], {0, broker}, secret=True)
            protected_path(transport["known_hosts"], {0})
            protected_path(Path("/usr/bin/ssh").resolve(strict=True), {0})
        else:
            raise RecordError("unknown transport")
    return manifest, policy


def peer_uid(connection: socket.socket, expected: int) -> None:
    if connection.family != socket.AF_UNIX or not hasattr(socket, "SO_PEERCRED"):
        raise RecordError("kernel-authenticated Unix peer required")
    pid, uid, _gid = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
    )
    if pid <= 0 or uid != expected or uid == os.geteuid():
        raise RecordError("same-UID or unauthorized peer")


def tls_context(manifest: dict, server: bool) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=manifest["ca_certificate"])
    context.load_cert_chain(manifest["certificate"], manifest["private_key"])
    if not server:
        context.check_hostname = True
    return context


class Channel:
    def __init__(self, connection: ssl.SSLSocket, deadline, *, maximum_seconds=MAX_LIFETIME):
        self.connection = connection
        self.deadline = deadline
        self.end = time.monotonic() + min(maximum_seconds, (deadline - utc_now()).total_seconds())

    def _timeout(self) -> None:
        remaining = min(self.end - time.monotonic(), (self.deadline - utc_now()).total_seconds())
        if remaining <= 0:
            raise RecordError("connection deadline expired")
        self.connection.settimeout(remaining)

    def bounded_deadline(self):
        self._timeout()
        return min(self.deadline, utc_now() + timedelta(seconds=self.end - time.monotonic()))

    def receive(self, length: int) -> bytes:
        result = bytearray()
        while len(result) < length:
            self._timeout()
            chunk = self.connection.recv(min(65536, length - len(result)))
            if not chunk:
                raise RecordError("incomplete one-use request")
            result.extend(chunk)
        return bytes(result)

    def read_frame(self, maximum: int) -> bytes:
        size = struct.unpack(">I", self.receive(4))[0]
        if not 0 < size <= maximum:
            raise RecordError("frame size bound")
        return self.receive(size)

    def send_frame(self, raw: bytes, maximum: int) -> None:
        if not 0 < len(raw) <= maximum:
            raise RecordError("frame size bound")
        self._timeout()
        self.connection.sendall(struct.pack(">I", len(raw)) + raw)


def sign_response(manifest: dict, payload: bytes, deadline) -> str:
    key = Path(manifest["response_private_key"])
    descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        signature = run_bounded(
            ["/usr/bin/openssl", "dgst", "-sha256", "-sign", f"/proc/self/fd/{descriptor}"],
            cwd=Path(manifest["state"]), environment=clean_environment(Path(manifest["state"]) / "home"),
            deadline=deadline, input_bytes=payload, pass_fds=(descriptor,), maximum_output=2048,
        )
    finally:
        os.close(descriptor)
    encoded = base64.b64encode(signature).decode("ascii")
    verify_signature(manifest["response_public_key"], payload, encoded)
    return encoded


def exchange(connection: ssl.SSLSocket, store: PublicationStore, manifest: dict) -> None:
    """One authenticated exchange. The listener always verifies SO_PEERCRED first."""
    peer = hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest()
    if peer != store.policy.client_certificate_sha256:
        raise RecordError("unrecognized mutual-TLS coordinator")
    issued = utc_now()
    deadline = issued + timedelta(seconds=MAX_LIFETIME)
    channel = Channel(connection, deadline)
    hello = {
        "protocol": PROTOCOL, "deployment_id": store.policy.deployment_id,
        "session_nonce": os.urandom(32).hex(),
        "issued_at": format_utc(issued), "expires_at": format_utc(deadline),
    }
    hello["signature"] = sign_response(manifest, signed_payload(HELLO_DOMAIN, hello), channel.bounded_deadline())
    channel.send_frame(canonical_json(hello), MAX_RESPONSE)
    request = strict_json(channel.read_frame(MAX_JSON), MAX_JSON)
    fields(request, {"protocol", "session_nonce", "operation", "plan"})
    if request["protocol"] != PROTOCOL or request["session_nonce"] != hello["session_nonce"]:
        raise RecordError("request copied from another authenticated session")
    plan = request["plan"]
    if request["operation"] == "publish":
        store.reserve(plan, peer)
        try:
            channel.deadline = min(channel.deadline, parse_utc(plan["expires_at"]))
            pack = channel.read_frame(min(MAX_PACK, plan["pack"]["size"]))
            # Revalidate the installation/remote after receiving untrusted input.
            # The state is never writable by the candidate in either interval.
            if manifest.get("_installation") is not None:
                load_installation(Path(manifest["_installation"]), "server")
            status, refs, completed = store.publish_reserved(
                plan, pack, channel.bounded_deadline(), monotonic_end=channel.end,
            )
        finally:
            store.abort(plan)
    elif request["operation"] == "readback":
        status, refs, completed = store.readback(
            plan, peer, channel.bounded_deadline(), monotonic_end=channel.end,
        )
    else:
        raise RecordError("unknown request operation")
    response = {
        "protocol": PROTOCOL, "deployment_id": store.policy.deployment_id,
        "session_nonce": hello["session_nonce"], "request_digest": plan_digest(plan),
        "nonce": plan["nonce"], "status": status, "refs": refs,
        "observed_at": format_utc(utc_now()), "completed_at": completed,
        "deadline": hello["expires_at"],
    }
    response["signature"] = sign_response(
        manifest, signed_payload(RESPONSE_DOMAIN, response), channel.bounded_deadline(),
    )
    channel.send_frame(canonical_json(response), MAX_RESPONSE)


def serve(installation: Path, *, once: bool = False) -> None:
    manifest, policy = load_installation(installation, "server")
    manifest["_installation"] = str(installation)
    context = tls_context(manifest, True)
    store = PublicationStore(policy, Path(manifest["state"]), installation, manifest["transport"])
    endpoint = Path(manifest["socket"])
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound = None
    try:
        store.local_protection(utc_now() + timedelta(seconds=MAX_LIFETIME))
        # Verify the response key before exposing any ready endpoint.
        sign_response(manifest, b"workflow-pilot-broker-preflight\0", utc_now() + timedelta(seconds=5))
        # Never unlink an existing socket: it may belong to a live service.
        listener.bind(str(endpoint))
        bound = endpoint.lstat().st_ino
        os.chmod(endpoint, 0o666)
        listener.listen(4)
        while True:
            raw, _ = listener.accept()
            with raw:
                try:
                    peer_uid(raw, manifest["coordinator_uid"])
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as connection:
                        exchange(connection, store, manifest)
                except (RecordError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                    # Never emit raw transport/server/helper errors or peer input.
                    pass
            if once:
                return
    finally:
        listener.close()
        if bound is not None and endpoint.exists() and endpoint.lstat().st_ino == bound:
            endpoint.unlink()
        store.close()


class BrokerClient:
    """Production consumer: pinned installation, authenticated peer, exact plan only."""

    def __init__(self, installation: Path):
        self.manifest, self.policy = load_installation(installation, "client")

    def request(self, plan: dict | None = None, pack: bytes | None = None, *, readback: bool = False) -> dict:
        manifest = self.manifest
        endpoint = Path(manifest["socket"])
        metadata = endpoint.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != manifest["broker_uid"]:
            raise RecordError("broker endpoint was substituted")
        context = tls_context(manifest, False)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.settimeout(5)
            raw.connect(str(endpoint))
            peer_uid(raw, manifest["broker_uid"])
            with context.wrap_socket(raw, server_hostname="workflow-pilot-git-broker") as connection:
                actual = hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest()
                if actual != manifest["server_certificate_sha256"]:
                    raise RecordError("broker TLS identity was substituted")
                return self._request_authenticated(connection, plan, pack, readback=readback)

    def _request_authenticated(self, connection, plan, pack, *, readback=False):
        channel = Channel(connection, utc_now() + timedelta(seconds=MAX_LIFETIME))
        hello = validate_hello(
            strict_json(channel.read_frame(MAX_RESPONSE), MAX_RESPONSE),
            self.policy.deployment_id, self.manifest["response_public_key"], utc_now(),
        )
        channel.deadline = parse_utc(hello["expires_at"])
        if plan is None:
            return {"ready": True, "protocol": PROTOCOL, "deployment_id": self.policy.deployment_id}
        validate_plan(
            plan, self.policy, self.policy.client_certificate_sha256,
            parse_utc(plan["issued_at"]) if readback else utc_now(),
        )
        channel.send_frame(canonical_json({
            "protocol": PROTOCOL, "session_nonce": hello["session_nonce"],
            "operation": "readback" if readback else "publish", "plan": plan,
        }), MAX_JSON)
        if not readback:
            if not isinstance(pack, bytes):
                raise RecordError("exact signed object pack required")
            channel.send_frame(pack, MAX_PACK)
        response = strict_json(channel.read_frame(MAX_RESPONSE), MAX_RESPONSE)
        return validate_response(
            response, self.policy, self.manifest["response_public_key"], plan,
            hello, utc_now(), readback=readback,
        )


def credential_helper(action: str) -> None:
    if action not in {"get", "store", "erase"}:
        raise RecordError("unsupported credential operation")
    installation = Path(os.environ.get("FE8_BROKER_INSTALLATION", ""))
    manifest, policy = load_installation(installation, "server")
    if manifest["transport"]["kind"] != "https":
        raise RecordError("HTTPS helper used for another transport")
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise RecordError("credential protocol bound")
    if action != "get":
        return
    values = {}
    for line in raw.decode("ascii").splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise RecordError("invalid credential protocol")
        values[key] = value
    if set(values) - {"protocol", "host", "path", "username"} or (
        values.get("protocol") != "https" or values.get("host") != "github.com"
        or values.get("path") != policy.repository + ".git"
        or values.get("username", "x-access-token") != "x-access-token"
    ):
        raise RecordError("credential request is outside exact installed endpoint")
    token = read_regular(Path(manifest["transport"]["token_file"]), 1024).rstrip(b"\n")
    if not token or any(byte <= 32 or byte >= 127 for byte in token):
        raise RecordError("invalid protected token")
    sys.stdout.buffer.write(b"username=x-access-token\npassword=" + token + b"\n\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "preflight-server", "preflight-client", "publish", "readback"):
        command = commands.add_parser(name)
        command.add_argument("--installation", type=Path, required=True)
        if name == "serve":
            command.add_argument("--once", action="store_true")
        if name in {"publish", "readback"}:
            command.add_argument("--plan", type=Path, required=True)
        if name == "publish":
            command.add_argument("--pack", type=Path, required=True)
    credential = commands.add_parser("credential")
    credential.add_argument("action", choices=("get", "store", "erase"))
    arguments = parser.parse_args(argv)
    try:
        os.umask(0o077)
        if arguments.command == "credential":
            credential_helper(arguments.action)
            return 0
        if arguments.command == "serve":
            def stop(_signum, _frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, stop)
            serve(arguments.installation, once=arguments.once)
            return 0
        if arguments.command == "preflight-server":
            manifest, policy = load_installation(arguments.installation, "server")
            tls_context(manifest, True)
            store = PublicationStore(
                policy, Path(manifest["state"]), arguments.installation, manifest["transport"],
            )
            try:
                store.local_protection(utc_now() + timedelta(seconds=MAX_LIFETIME))
            finally:
                store.close()
            sign_response(manifest, b"workflow-pilot-broker-preflight\0", utc_now() + timedelta(seconds=5))
            result = {"ready": True, "protocol": PROTOCOL, "deployment_id": policy.deployment_id}
        else:
            client = BrokerClient(arguments.installation)
            if arguments.command == "preflight-client":
                result = client.request()
            else:
                plan = strict_json(read_regular(arguments.plan, MAX_JSON), MAX_JSON)
                pack = read_regular(arguments.pack, MAX_PACK) if arguments.command == "publish" else None
                result = client.request(plan, pack, readback=arguments.command == "readback")
        sys.stdout.buffer.write(canonical_json(result))
        return 0 if result.get("ready") or result.get("status") == "published" else 1
    except (RecordError, OSError, ValueError, TypeError, KeyError, sqlite3.Error, KeyboardInterrupt):
        print("git-broker: protected preflight/protocol failed closed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
